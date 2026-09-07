#!/usr/bin/env python3
"""readiness 统一预检回归——缓存/预热/fail-fast/seller memo（漏斗 v2 收尾）。

背景：此前各命令独立检测登录态/cookie 且互不共享——aibuy 预热只在 check 里做、
seller 登录等待可能同进程重复黑等。readiness 模块提供 probe_* 原子探针 +
ensure_pipeline_ready（按管线裁剪 + 成功才缓存 TTL600s + 静默修复）。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_readiness.py -q
"""
from __future__ import annotations

import io
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402
from scripts.lib import readiness as rd  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch):
    """密闭：摘除 pytest 快通道 + 清 memo + 隔离磁盘缓存。"""
    monkeypatch.setattr(rd, "_under_pytest", lambda: False)
    monkeypatch.setattr(osa, "_LOGIN_CONFIRMED_MONO", 0.0)
    monkeypatch.setattr(osa, "_WAIT_ATTEMPTED_MONO", 0.0)
    monkeypatch.setattr("scripts.lib.cache.cache_get", lambda ns, key: None)
    monkeypatch.setattr("scripts.lib.cache.cache_set",
                        lambda ns, key, data, ttl=86400: None)


# ── ensure_pipeline_ready：管线裁剪与硬失败 ──


def test_unknown_pipeline_raises():
    with pytest.raises(ValueError):
        rd.ensure_pipeline_ready("bogus")


def test_cache_hit_skips_probe(monkeypatch):
    """成功结果缓存 600s 内：探针不再被调用，cached 列表可见。"""
    monkeypatch.setattr("scripts.lib.cache.cache_get",
                        lambda ns, key: {"ok": True} if key == "aibuy_token" else None)
    calls: list[str] = []

    def _fake_probe(cdp_url):
        calls.append(cdp_url)
        return True

    monkeypatch.setitem(rd._PROBES, "aibuy_token", _fake_probe)
    monkeypatch.setattr(rd, "probe_chrome_cdp", lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    report = rd.ensure_pipeline_ready("discover", interactive=False)
    assert report["ok"] and report["results"]["aibuy_token"] is True
    assert report["cached"] == ["aibuy_token"]
    assert calls == [], "缓存命中不应再实检探针"


def test_aibuy_prewarm_once_then_cached(monkeypatch):
    """aibuy 冷启动：静默读失败 → 导航预热一次成功 → repaired 可见。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp", lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    monkeypatch.setattr("scripts.lib.ozon_image_search._read_1688_cookies_silent",
                        lambda cdp_url=None: {})
    warmed: list[int] = []

    def _fake_warm(cdp_url):
        warmed.append(1)
        return {"_m_h5_tk": "x"}

    monkeypatch.setattr("scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome",
                        _fake_warm)
    monkeypatch.setitem(rd._PROBES, "seller_login", lambda cdp_url: True)
    report = rd.ensure_pipeline_ready("discover", interactive=False)
    assert warmed == [1], "预热恰好一次"
    assert report["results"]["aibuy_token"] is True
    assert any("预热" in r for r in report["repaired"])


def test_discover_task_seller_fail_fast(monkeypatch):
    """无人值守：seller 未登录 → ok=False 硬失败（替代流程深处 90s 黑等）。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp", lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    monkeypatch.setattr("scripts.lib.ozon_image_search._read_1688_cookies_silent",
                        lambda cdp_url=None: {"_m_h5_tk": "x"})
    monkeypatch.setitem(rd._PROBES, "seller_login", lambda cdp_url: False)

    report = rd.ensure_pipeline_ready("discover-task", interactive=False)
    assert report["ok"] is False
    assert report["hints"]["seller_login"]


def test_discover_non_interactive_seller_soft(monkeypatch):
    """交互 discover 非 TTY：seller 未登录只预告不阻断（流程内保留既有 90s 窗口）。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp", lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    monkeypatch.setattr("scripts.lib.ozon_image_search._read_1688_cookies_silent",
                        lambda cdp_url=None: {"_m_h5_tk": "x"})
    monkeypatch.setitem(rd._PROBES, "seller_login", lambda cdp_url: False)

    report = rd.ensure_pipeline_ready("discover", interactive=False)
    assert report["ok"] is True, "非 discover-task 管线 seller 失败不硬失败"
    assert report["hints"]["seller_login"]


def test_interactive_seller_wait_success_repairs(monkeypatch):
    """交互 discover：seller 未登录 → wait_for_seller_login 给登录窗口，成功记 repaired。"""

    class _FakeConn:
        def __init__(self, url=""):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rd, "probe_chrome_cdp", lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    monkeypatch.setattr("scripts.lib.ozon_image_search._read_1688_cookies_silent",
                        lambda cdp_url=None: {"_m_h5_tk": "x"})
    monkeypatch.setitem(rd._PROBES, "seller_login", lambda cdp_url: False)
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", _FakeConn)

    waited: list[bool] = []

    def _fake_wait(cdp):
        waited.append(True)
        osa.mark_seller_login_confirmed()
        return True

    monkeypatch.setattr(osa, "wait_for_seller_login", _fake_wait)
    report = rd.ensure_pipeline_ready("discover", interactive=True)
    assert waited == [1]
    assert report["results"]["seller_login"] is True
    assert any("登录已确认" in r for r in report["repaired"])


# ── seller memo（同进程免重复等待）──


def test_wait_short_circuits_when_confirmed(monkeypatch):
    """确认 memo 在场：wait_for_seller_login 秒回 True，不再 check/等待。"""
    osa.mark_seller_login_confirmed()
    monkeypatch.setattr(osa, "check_seller_login",
                        lambda cdp: (_ for _ in ()).throw(AssertionError("不应再检测")))
    assert osa.wait_for_seller_login(object()) is True


def test_wait_recent_attempt_only_rechecks(monkeypatch):
    """刚等过一轮（<180s）：只复查一次，不进轮询等待。"""
    osa.mark_seller_login_wait_attempted()
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: False)
    assert osa.wait_for_seller_login(object(), timeout_seconds=1) is False


def test_wait_success_marks_confirmed(monkeypatch):
    """等待成功路径：memo 置位（后续调用秒回）。"""
    seq = {"n": 0}

    def _check(cdp):
        seq["n"] += 1
        return seq["n"] > 2  # 首检 False → 轮询第 2 次成功

    monkeypatch.setattr(osa, "check_seller_login", _check)
    monkeypatch.setattr(osa, "_tab_for_seller", lambda cdp: (None, False))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert osa.wait_for_seller_login(object(), timeout_seconds=10, poll_interval=0) is True
    assert osa.seller_login_confirmed_recently()
    assert osa.wait_for_seller_login(object()) is True


# ── 报告渲染 ──


def test_print_report_renders_labels_and_cache_note():
    report = {"ok": True,
              "results": {"chrome_cdp": True, "seller_login": True,
                          "aibuy_token": True},
              "cached": ["aibuy_token"], "repaired": ["已自动预热"],
              "hints": {}}
    buf = io.StringIO()
    with mock.patch("sys.stdout", buf):
        rd.print_readiness_report(report)
    out = buf.getvalue()
    assert "Chrome CDP" in out and "seller 卖家后台登录" in out
    assert "1688 反爬 cookie 缓存命中" in out
    assert "已自动预热" in out


# ── pytest 快通道 ──


def test_pytest_fastlane_returns_ok_without_probes(monkeypatch):
    """命令层测试环境（PYTEST 守卫在场）：不触任何探针，直接 ok 报告。"""
    monkeypatch.setattr(rd, "_under_pytest", lambda: True)
    report = rd.ensure_pipeline_ready("discover-task")
    assert report["ok"] is True
    assert set(report["results"]) == {"chrome_cdp", "seller_login", "aibuy_token"}
    assert report["hints"] == {}

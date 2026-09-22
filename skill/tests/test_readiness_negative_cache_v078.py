#!/usr/bin/env python3
"""批A A2（fix/skill-silent-cdp-v1）：readiness 失败负缓存回归。

背景：readiness 缓存此前只缓存成功（TTL 600s），失败结果永不缓存 → 每条命令
重新实检探针 + 冷启动反复触发 prewarm 导航 1688 首页（前台弹窗根因之一）。
本文件锁定：失败也写缓存（ok:False，同 600s TTL）；负缓存命中时跳过探针**且
跳过 prewarm 导航**；成功缓存语义逐字保持。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_readiness_negative_cache_v078.py -q
"""
from __future__ import annotations

import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import readiness as rd  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_prewarm(monkeypatch):
    """环境隔离：探针恒失败时 readiness 会走 prewarm 修复链——真跑
    `_fetch_aibuy_cookies_from_chrome` 会在本机 Chrome 已登录 1688 时拿到
    cookie 覆写探针结果（CI 无 Chrome 才恒 False，环境泄漏）。桩死它，
    保持本文件只测负缓存/预热调度语义本身。"""
    monkeypatch.setattr(
        "scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome",
        lambda *a, **k: {})


@pytest.fixture()
def cache_store(monkeypatch) -> dict:
    """密闭：摘除 pytest 快通道 + 磁盘缓存换进程内 dict（可检 TTL/值形状）。"""
    monkeypatch.setattr(rd, "_under_pytest", lambda: False)
    # seller 登录 memo 层（seller_login 负缓存豁免链路）同为生产语义：
    # 摘除 osa 的 pytest 守卫（memo 落盘读才可达）+ 清进程内 memo 防跨文件泄漏
    import scripts.lib.ozon_seller_analytics as _osa
    monkeypatch.setattr(_osa, "_under_pytest", lambda: False)
    monkeypatch.setattr(_osa, "_LOGIN_CONFIRMED_MONO", 0.0)
    store: dict = {}

    def _get(ns, key):
        rec = store.get((ns, key))
        if rec is None:
            return None
        if time.time() > rec["expires_at"]:
            return None
        return rec["value"]

    def _set(ns, key, data, ttl=86400):
        store[(ns, key)] = {"value": data, "expires_at": time.time() + ttl}

    monkeypatch.setattr("scripts.lib.cache.cache_get", _get)
    monkeypatch.setattr("scripts.lib.cache.cache_set", _set)
    return store


def test_failure_cached_second_call_skips_probe_and_prewarm(cache_store, monkeypatch, caplog):
    """探针恒失败：第一次实检 + prewarm 一次；第二次 TTL 内零探针、零 prewarm。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp",
                        lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    probe_calls: dict[str, int] = {}

    def _fake_probe(name):
        def _probe(cdp_url):
            probe_calls[name] = probe_calls.get(name, 0) + 1
            return False
        return _probe

    for name in ("ozon_datadome", "alibaba_login", "aibuy_token"):
        monkeypatch.setitem(rd._PROBES, name, _fake_probe(name))
    prewarm_calls: list[int] = []
    monkeypatch.setattr("scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome",
                        lambda cdp_url: prewarm_calls.append(1) or {})
    monkeypatch.setattr("scripts.lib.cookie_harvest.try_auto_import",
                        lambda probe, cdp_url: False)

    with caplog.at_level(logging.INFO, logger="scripts.lib.readiness"):
        r1 = rd.ensure_pipeline_ready("follow", interactive=False)
        assert r1["results"] == {"chrome_cdp": True, "ozon_datadome": False,
                                 "alibaba_login": False, "aibuy_token": False}
        assert prewarm_calls == [1], "冷启动 prewarm 恰好一次"
        r2 = rd.ensure_pipeline_ready("follow", interactive=False)

    assert r2["cached"] == [], "负缓存命中不属于成功缓存列表"
    assert r2["results"]["aibuy_token"] is False
    assert probe_calls == {"ozon_datadome": 1, "alibaba_login": 1,
                           "aibuy_token": 1}, "负缓存窗内不得重试探针"
    assert prewarm_calls == [1], "负缓存命中必须跳过 prewarm 导航（前台弹窗根治点）"
    assert any("负缓存命中" in r.message for r in caplog.records), \
        "负缓存命中须打一行日志"


def test_negative_cache_value_shape_and_ttl(cache_store, monkeypatch):
    """失败缓存值形状 {ok: False}、TTL 与成功缓存同（600s）。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp",
                        lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    monkeypatch.setitem(rd._PROBES, "ozon_datadome", lambda cdp_url: False)
    monkeypatch.setitem(rd._PROBES, "alibaba_login", lambda cdp_url: False)
    monkeypatch.setitem(rd._PROBES, "aibuy_token", lambda cdp_url: False)
    monkeypatch.setattr("scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome",
                        lambda cdp_url: {})
    monkeypatch.setattr("scripts.lib.cookie_harvest.try_auto_import",
                        lambda probe, cdp_url: False)
    rd.ensure_pipeline_ready("follow", interactive=False)
    rec = cache_store[("readiness", "ozon_datadome")]
    assert rec["value"] == {"ok": False}
    ttl = rec["expires_at"] - time.time()
    assert 590 <= ttl <= 600, f"负缓存 TTL 应为 600s，实际 {ttl:.0f}s"


def test_success_cache_semantics_unchanged(cache_store, monkeypatch):
    """成功路径缓存语义逐字保持：第一次实检 → 第二次 cached 命中、零实检。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp",
                        lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    probe_calls: dict[str, int] = {}

    def _fake_probe(name):
        def _probe(cdp_url):
            probe_calls[name] = probe_calls.get(name, 0) + 1
            return True
        return _probe

    for name in ("ozon_datadome", "alibaba_login", "aibuy_token"):
        monkeypatch.setitem(rd._PROBES, name, _fake_probe(name))

    r1 = rd.ensure_pipeline_ready("follow", interactive=False)
    assert r1["ok"] is True and r1["cached"] == []
    r2 = rd.ensure_pipeline_ready("follow", interactive=False)
    assert r2["ok"] is True
    assert r2["cached"] == ["ozon_datadome", "alibaba_login", "aibuy_token"]
    assert probe_calls == {"ozon_datadome": 1, "alibaba_login": 1, "aibuy_token": 1}
    for name in ("ozon_datadome", "alibaba_login", "aibuy_token"):
        assert cache_store[("readiness", name)]["value"] == {"ok": True}


def test_expired_negative_cache_probes_again(cache_store, monkeypatch):
    """负缓存过期后恢复正常实检（600s 窗外不 suppressed）。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp",
                        lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    calls: list[int] = []

    def _probe(cdp_url):
        calls.append(1)
        return False

    monkeypatch.setitem(rd._PROBES, "aibuy_token", _probe)
    monkeypatch.setitem(rd._PROBES, "seller_login", lambda cdp_url: True)
    # 预置一条已过期的负缓存
    cache_store[("readiness", "aibuy_token")] = {
        "value": {"ok": False}, "expires_at": time.time() - 1}
    r = rd.ensure_pipeline_ready("discover", interactive=False)
    assert r["results"]["aibuy_token"] is False
    assert len(calls) == 1, "过期负缓存不得拦截实检"


# ── 修正轮（评审 Important）：seller_login 负缓存不得压过登录确认 memo ──


def test_seller_login_negative_cache_defers_to_login_memo(cache_store, monkeypatch):
    """seller_login 负缓存 + 登录 memo 近期确认 → 视作缓存未命中重探放行。

    病景：用户完成登录重跑 discover-task，若负缓存照旧 early-exit，命令仍
    exit 1 提示「登录后重跑」——登录 memo（进程内 + 落盘双层）在场时必须
    重探（probe_seller_login 经 check_seller_login 的 memo 秒回 True）。
    """
    monkeypatch.setattr(rd, "probe_chrome_cdp",
                        lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    seller_calls: list[int] = []

    def _seller_probe(cdp_url):
        seller_calls.append(1)
        return True

    monkeypatch.setitem(rd._PROBES, "seller_login", _seller_probe)
    monkeypatch.setitem(rd._PROBES, "aibuy_token", lambda cdp_url: True)
    # 预置：seller_login 负缓存（600s 窗内）+ 登录 memo 落盘在案
    cache_store[("readiness", "seller_login")] = {
        "value": {"ok": False}, "expires_at": time.time() + 600}
    cache_store[("seller_login", "confirmed")] = {
        "value": {"ok": True}, "expires_at": time.time() + 600}

    r = rd.ensure_pipeline_ready("discover-task", interactive=False)

    assert seller_calls == [1], "memo 已确认 → 负缓存视作未命中，必须重探"
    assert r["results"]["seller_login"] is True
    assert r["ok"] is True, "discover-task 不再被陈旧负缓存 fail-fast"


def test_login_memo_only_unblocks_seller_login(cache_store, monkeypatch):
    """memo 豁免仅限 seller_login：其他探针负缓存照常早退（不放大窗口）。"""
    monkeypatch.setattr(rd, "probe_chrome_cdp",
                        lambda profile_dir=None, cdp_url=rd.CDP_URL: True)
    aibuy_calls: list[int] = []

    def _aibuy_probe(cdp_url):
        aibuy_calls.append(1)
        return True

    monkeypatch.setitem(rd._PROBES, "aibuy_token", _aibuy_probe)
    monkeypatch.setitem(rd._PROBES, "seller_login", lambda cdp_url: True)
    cache_store[("readiness", "aibuy_token")] = {
        "value": {"ok": False}, "expires_at": time.time() + 600}
    cache_store[("seller_login", "confirmed")] = {
        "value": {"ok": True}, "expires_at": time.time() + 600}

    r = rd.ensure_pipeline_ready("discover-task", interactive=False)

    assert aibuy_calls == [], "非 seller_login 探针的负缓存不因 memo 解锁"
    assert r["results"]["aibuy_token"] is False

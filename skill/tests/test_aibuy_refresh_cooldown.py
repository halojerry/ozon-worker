#!/usr/bin/env python3
"""P0-2 aibuy token 刷新风暴根治单测（2026-09-09 审计靶点一配套）。

用户实机症状：1688 登录态/反爬 cookie 失效后，每次 aibuy 图搜都导航
www.1688.com + 轮询 8s——discover 一批 N 个候选就导航 N 次，跨进程亦无记忆。

修复契约（本文件锁定，search_by_image_aibuy 刷新链）：
  1. 静默读先行——settings token 失效时先 `_read_1688_cookies_silent`
     （只读不导航），命中即免导航；
  2. 导航刷新跨进程 600s 冷却——失败后落 claim（占位文件
     `data/config/.aibuy_refresh_claim.json`，T3 起 O_EXCL 原子建，
     fix/skill-concurrency-v1；旧 settings.json 键已删除），冷却内后续调用
     零导航直接返回 [] 降级 CDP/AK；
  3. 刷新成功清 claim，恢复正常刷新节奏。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_aibuy_refresh_cooldown.py -q
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts._const  # noqa: E402
from scripts.lib import config_store, ozon_image_search as ois  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离：settings.json 指向临时文件 + 结果缓存旁路 + 搜索网络层 mock。"""
    monkeypatch.setattr(config_store, "SETTINGS_FILE", tmp_path / "settings.json")
    # claim 占位文件目录重定向到 tmp（_aibuy_refresh_claim_path 函数内取 CONFIG_DIR）
    monkeypatch.setattr(scripts._const, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ois, "cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(ois, "cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(ois, "_aibuy_image_upload", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ois, "_aibuy_image_search", lambda *_a, **_k: [{"id": "1", "title": "x"}]
    )
    return tmp_path


def _write_claim(ts: float) -> None:
    """直接落一份占位文件（模拟本/其他进程刚试过导航刷新）。"""
    p = ois._aibuy_refresh_claim_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ts": ts, "pid": 999}), encoding="utf-8")


def _valid_cookies() -> dict:
    return {k: f"v_{k}" for k in ois._AIBUY_COOKIE_KEYS}


def _chrome_fetch_mock(calls: list, cookies: dict):
    def fake(cdp_url="http://127.0.0.1:9222"):
        calls.append(1)
        return cookies

    return fake


def test_failed_refresh_sets_claim_second_call_skips_navigation(isolated, monkeypatch):
    """首次刷新失败落 claim；第二次调用（不同 image_url）零导航直接降级。"""
    calls: list = []
    monkeypatch.setattr(ois, "_read_aibuy_token", lambda: None)
    monkeypatch.setattr(ois, "_read_1688_cookies_silent", lambda cdp_url=None: {})
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(calls, {})
    )
    assert ois.search_by_image_aibuy("img_1") == []
    assert ois.search_by_image_aibuy("img_2") == []
    assert len(calls) == 1  # 只导航一次
    assert ois._aibuy_refresh_claim_path().exists()


def test_successful_refresh_clears_claim(isolated, monkeypatch):
    """刷新成功清 claim——之后 token 再次失效仍允许重新导航刷新。"""
    calls: list = []
    monkeypatch.setattr(ois, "_read_aibuy_token", lambda: None)
    monkeypatch.setattr(ois, "_read_1688_cookies_silent", lambda cdp_url=None: {})
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(calls, _valid_cookies())
    )
    assert ois.search_by_image_aibuy("img_1") != []
    assert not ois._aibuy_refresh_claim_path().exists()
    # 模拟 token 又失效：允许再次导航
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(calls, {})
    )
    assert ois.search_by_image_aibuy("img_2") == []
    assert len(calls) == 2


def test_claim_from_other_process_blocks_navigation(isolated, monkeypatch):
    """另一进程刚试过刷新（claim 新鲜）→ 本进程零导航直接降级。"""
    _write_claim(time.time())
    calls: list = []
    monkeypatch.setattr(ois, "_read_aibuy_token", lambda: None)
    monkeypatch.setattr(ois, "_read_1688_cookies_silent", lambda cdp_url=None: {})
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(calls, _valid_cookies())
    )
    assert ois.search_by_image_aibuy("img_1") == []
    assert calls == []


def test_silent_read_used_before_navigation(isolated, monkeypatch):
    """settings token 失效但 Chrome 会话里 cookie 其实有效：静默读命中，
    零导航恢复工作并回写缓存。"""
    nav_calls: list = []
    silent_calls: list = []

    def fake_silent(cdp_url=None):
        silent_calls.append(1)
        return _valid_cookies()

    monkeypatch.setattr(ois, "_read_aibuy_token", lambda: None)
    monkeypatch.setattr(ois, "_read_1688_cookies_silent", fake_silent)
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(nav_calls, {})
    )
    assert ois.search_by_image_aibuy("img_1") != []
    assert nav_calls == []
    assert len(silent_calls) == 1
    saved = config_store.get_setting(ois.AIBUY_TOKEN_KEY)
    assert saved and saved.get("_m_h5_tk")


def test_stale_claim_ignored(isolated, monkeypatch):
    """超过冷却期（600s）的旧 claim 不拦刷新。"""
    _write_claim(time.time() - 3600)
    calls: list = []
    monkeypatch.setattr(ois, "_read_aibuy_token", lambda: None)
    monkeypatch.setattr(ois, "_read_1688_cookies_silent", lambda cdp_url=None: {})
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(calls, {})
    )
    assert ois.search_by_image_aibuy("img_1") == []
    assert len(calls) == 1


def test_valid_cached_token_skips_all_refresh_paths(isolated, monkeypatch):
    """缓存 token 有效：既不静默读也不导航（既有行为锁定，防回归）。"""
    ois._save_aibuy_token(_valid_cookies())
    nav_calls: list = []
    silent_calls: list = []
    monkeypatch.setattr(
        ois, "_read_1688_cookies_silent",
        lambda cdp_url=None: silent_calls.append(1) or {},
    )
    monkeypatch.setattr(
        ois, "_fetch_aibuy_cookies_from_chrome", _chrome_fetch_mock(nav_calls, {})
    )
    assert ois.search_by_image_aibuy("img_1") != []
    assert nav_calls == [] and silent_calls == []

# -*- coding: utf-8 -*-
"""v0.69 seller 登录检测 silent-first 根治回归（用户反馈：seller.ozon.ru 页面反复打开）。

锁定四个行为：
1. check_seller_login 纯 cookie 罐读取（about:blank + Network.getCookies），
   命中/未命中都**零导航**——不再 _tab_for_seller 开页（此前每条命令、
   wait 轮询每 5s 都可能弹 seller 页 = 用户反馈的「反复打开」）。
2. 登录确认落盘缓存（600s，与 readiness 对齐）+ DataDome 短路落盘——
   跨 CLI 进程生效（此前进程内 memo 每条命令归零）。
3. wait_for_seller_login 登录页只开一次；轮询走 silent 检测，
   用户中途关页不再被弹回。
4. pytest 守卫：_under_pytest() 为 True 时落盘层整体禁用（防测试污染
   真实 data/cache/）。

背景 bug：旧 check_seller_login 的 Network.getCookies 兜底把 tab._send 返回的
msg_id 当响应用（恒 AttributeError 被吞）——检测实际全靠 document.cookie；
本批 silent 读取用 _send + _recv_until_id 正规姿势（HttpOnly 也可读）。

运行: cd skill && .venv314/bin/python -m pytest tests/test_seller_silent_login_v069.py -q
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.ozon_seller_analytics as osa  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    """每测前重置 memo/短路全局（防同进程用例互相短路）。"""
    osa._LOGIN_CONFIRMED_MONO = 0.0
    osa._WAIT_ATTEMPTED_MONO = 0.0
    osa._DIRECT_BLOCKED_UNTIL = 0.0
    yield
    osa._LOGIN_CONFIRMED_MONO = 0.0
    osa._WAIT_ATTEMPTED_MONO = 0.0
    osa._DIRECT_BLOCKED_UNTIL = 0.0


class _FakeTab:
    """about:blank 假 tab：记录 _send 参数，回放 Network.getCookies 结果。"""

    def __init__(self, cookies):
        self._cookies = cookies
        self.sent = []
        self.closed = False

    def _send(self, method, params=None, msg_id=None):
        self.sent.append((method, params))
        return 42

    def _recv_until_id(self, target_id, timeout=15):
        return {"result": {"cookies": self._cookies}}

    def close(self, close_remote=True):
        self.closed = True


class _FakeCdp:
    """无 find_tab/navigate 的假连接——检测若走开页路径会 AttributeError 暴露。"""

    def __init__(self, tab):
        self.tab = tab
        self.new_tab_calls = 0

    def new_tab(self, url="about:blank", background=False):
        self.new_tab_calls += 1
        return self.tab


def _cookie(name, value):
    return {"name": name, "value": value}


# ═══════════ 1. silent-first：检测零导航 ═══════════


def test_check_seller_login_silent_hit_no_navigation():
    """cookie 罐有 sc_company_id → True；全程只开 about:blank，绝不碰 seller 页。"""
    tab = _FakeTab([_cookie("sc_company_id", "123"), _cookie("other", "x")])
    cdp = _FakeCdp(tab)
    assert osa.check_seller_login(cdp) is True
    assert cdp.new_tab_calls == 1
    assert tab.sent == [("Network.getCookies", {"urls": [osa.SELLER_URL]})]
    assert tab.closed, "临时 about:blank tab 用完即关"
    # memo 同步确认（同进程后续秒回）
    assert osa.seller_login_confirmed_recently() is True


def test_check_seller_login_miss_returns_false_no_page_open():
    """未登录（无 sc_company_id）→ False 且不开任何 seller 页。"""
    tab = _FakeTab([_cookie("other", "x")])
    cdp = _FakeCdp(tab)
    assert osa.check_seller_login(cdp) is False
    assert cdp.new_tab_calls == 1  # 只有 about:blank 读取


def test_check_seller_login_cdp_failure_returns_false():
    """CDP 异常 → False（按未登录处理，走既有降级，不 raise）。"""
    cdp = mock.Mock()
    cdp.new_tab.side_effect = RuntimeError("connection refused")
    assert osa.check_seller_login(cdp) is False


# ═══════════ 2. 落盘层（pytest 守卫 + 跨进程语义）═══════════


def test_disk_layer_disabled_under_pytest(monkeypatch):
    """pytest 下 mark/read 均不触磁盘（防测试污染真实 data/cache）。"""
    monkeypatch.setattr("scripts.lib.cache.cache_set",
                        lambda *a, **k: pytest.fail("pytest 下不得写盘"))
    monkeypatch.setattr("scripts.lib.cache.cache_get",
                        lambda *a, **k: pytest.fail("pytest 下不得读盘"))
    osa.mark_seller_login_confirmed()
    assert osa._LOGIN_CONFIRMED_MONO > 0, "进程内 memo 照常生效"
    assert osa.seller_login_confirmed_recently() is True


def test_mark_confirmed_writes_disk_cache(monkeypatch):
    """非 pytest：确认登录 → 落盘 ttl=600（与 readiness 对齐）。"""
    calls = {}
    monkeypatch.setattr(osa, "_under_pytest", lambda: False)
    monkeypatch.setattr("scripts.lib.cache.cache_set",
                        lambda ns, key, val, ttl=0: calls.update(
                            ns=ns, key=key, val=val, ttl=ttl))
    osa.mark_seller_login_confirmed()
    assert calls["ns"] == "seller_login" and calls["key"] == "confirmed"
    assert calls["ttl"] == 600


def test_disk_cache_shortcircuits_check_in_fresh_process(monkeypatch):
    """新进程 memo 归零，但落盘确认在 TTL 内 → 检测免 CDP 直接 True。"""
    monkeypatch.setattr(osa, "_under_pytest", lambda: False)
    monkeypatch.setattr("scripts.lib.cache.cache_get",
                        lambda ns, key: {"ok": True} if ns == "seller_login" else None)

    class _Explodes:
        def new_tab(self, *a, **k):
            raise AssertionError("落盘命中不得触 CDP")

    assert osa.check_seller_login(_Explodes()) is True


def test_datadome_block_persists_to_disk(monkeypatch):
    """DataDome 挑战 → 短路标记落盘（until 时间戳 + ttl=600）。"""
    calls = {}
    monkeypatch.setattr(osa, "_under_pytest", lambda: False)
    monkeypatch.setattr("scripts.lib.cache.cache_set",
                        lambda ns, key, val, ttl=0: calls.update(
                            ns=ns, key=key, val=val, ttl=ttl))
    before = time.time()
    osa._mark_direct_blocked()
    assert calls["ns"] == "seller_direct" and calls["key"] == "blocked_until"
    assert calls["ttl"] == 600
    assert calls["val"]["until"] >= before + 600


def test_direct_blocked_reads_disk_when_memory_cold(monkeypatch):
    """新进程短路标记归零 → 读落盘回填并判 True（跨命令短路生效）。"""
    monkeypatch.setattr(osa, "_under_pytest", lambda: False)
    until = time.time() + 300
    monkeypatch.setattr("scripts.lib.cache.cache_get",
                        lambda ns, key: {"until": until}
                        if ns == "seller_direct" else None)
    assert osa._DIRECT_BLOCKED_UNTIL == 0.0
    assert osa._direct_blocked() is True
    assert osa._DIRECT_BLOCKED_UNTIL == until, "落盘值回填进程内"


def test_seller_direct_post_skips_http_when_disk_blocked(monkeypatch):
    """落盘短路窗口内：直调不发 HTTP，直接降级。"""
    monkeypatch.setattr(osa, "_under_pytest", lambda: False)
    monkeypatch.setattr("scripts.lib.cache.cache_get",
                        lambda ns, key: {"until": time.time() + 300}
                        if ns == "seller_direct" else None)
    with mock.patch.object(osa.requests, "post") as m_post:
        data, ok = osa._seller_direct_post(
            "/api/x", {}, {"sc_company_id": "123"})
    assert (data, ok) == ({}, False)
    assert not m_post.called


# ═══════════ 3. wait_for_seller_login：登录页只开一次 ═══════════


def test_wait_polls_silently_and_opens_page_once():
    """未登录 → 开页一次 → 轮询命中。轮询体不再触发任何第二次开页。"""
    tab = mock.Mock()
    with mock.patch.object(osa, "check_seller_login", side_effect=[False, True]) as m_check, \
            mock.patch.object(osa, "_tab_for_seller", return_value=(tab, False)) as m_tab, \
            mock.patch("time.sleep", new=lambda *_a, **_k: None):
        assert osa.wait_for_seller_login(object(), timeout_seconds=10) is True
    assert m_tab.call_count == 1, "登录页只开一次"
    assert m_check.call_count == 2


def test_wait_does_not_reopen_page_user_closed_mid_poll():
    """用户中途关页（检测持续 False 几轮后再命中）→ 不重开页面，轮询到命中。"""
    tab = mock.Mock()
    with mock.patch.object(osa, "check_seller_login",
                           side_effect=[False, False, False, True]) as m_check, \
            mock.patch.object(osa, "_tab_for_seller", return_value=(tab, False)) as m_tab, \
            mock.patch("time.sleep", new=lambda *_a, **_k: None):
        assert osa.wait_for_seller_login(object(), timeout_seconds=60) is True
    assert m_tab.call_count == 1, "关页后不得弹回（v0.69 主修复点）"
    assert m_check.call_count == 4

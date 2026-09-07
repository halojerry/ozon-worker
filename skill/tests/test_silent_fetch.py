#!/usr/bin/env python3
"""静默模型改造回归（漏斗 v2 收尾·对齐 shopbang）。

三个行为锁定：
1. widget fetch 的 shared_tab 快路径——调用方自带页面上下文时零导航零 tab
   管理（实测 highlight 页上下文 fetch 其他商品 widget 200，逐商品导航多余）；
2. seller 直调 DataDome 挑战（403 + fab_chlg）→ 10 分钟短路，direct 系函数
   不再重复浪费请求（此前每次 403 后降级 CDP 制造 seller 页反复打开）；
3. seller tab 常驻——新建检测/数据 tab 保留不关远程，后续 find_tab 复用。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_silent_fetch.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402
from scripts.lib import ozon_widget  # noqa: E402
from scripts.lib.ozon_discovery import _analyze_product  # noqa: E402


class _FakeTab:
    def __init__(self, payloads: list[str]):
        self.payloads = payloads
        self.navigated: list[str] = []
        self.evaluated = 0

    def evaluate(self, js, await_promise=False, timeout=20):
        self.evaluated += 1
        return self.payloads[min(self.evaluated, len(self.payloads)) - 1]

    def navigate(self, url, **kw):
        self.navigated.append(url)


@pytest.fixture(autouse=True)
def _reset_direct_block():
    osa._DIRECT_BLOCKED_UNTIL = 0.0
    yield
    osa._DIRECT_BLOCKED_UNTIL = 0.0


def test_analyze_product_shared_tab_never_navigates(monkeypatch):
    """shared_tab 模式：不走 _ensure_ozon_tab（否则视为导航/tab 管理回归）。"""
    tab = _FakeTab([
        '{"title": "Товар", "price": "100 ₽", "brand": "", "images": []}',
        '{"count": 3, "min_price": 90, "sellers": [{"sku": "1", "price": 90, "seller_name": "x"}]}',
    ])

    def _boom(*a, **k):
        raise AssertionError("shared_tab 模式不应触发 _ensure_ozon_tab（导航/tab 管理）")

    monkeypatch.setattr(ozon_widget, "_ensure_ozon_tab", _boom)
    monkeypatch.setattr("scripts.lib.cache.cache_get", lambda ns, key: None)
    monkeypatch.setattr("scripts.lib.cache.cache_set", lambda *a, **k: None)

    c = _analyze_product("http://127.0.0.1:9222", object(), "123", shared_tab=tab)
    assert c.status == "ok", c.error
    assert c.competing_sellers == 3
    assert tab.evaluated == 2 and tab.navigated == [], "应纯页内 fetch，零导航"


def test_seller_direct_challenge_sets_shortcircuit(monkeypatch):
    """403 + fab_chlg 响应 → 置短路标记，窗口内再调不发请求。"""
    class _Resp:
        status_code = 403
        text = '{"incidentId": "fab_chlg_x", "challengeURL": "https://seller.ozon.ru/challenge.html"}'

    calls = {"n": 0}

    def _fake_post(*a, **k):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(osa.requests, "post", _fake_post)
    cookies = {"sc_company_id": "42"}
    data, ok = osa._seller_direct_post("/api/x", {}, cookies)
    assert ok is False and data == {}
    assert calls["n"] == 1
    assert osa._direct_blocked(), "挑战后应进入短路窗口"

    # 窗口内：direct 系函数（经 _seller_direct_post）不再发请求
    out = osa.fetch_sales_analytics_direct(cookies, ["123"])
    assert out == {}
    assert calls["n"] == 1, "短路窗口内不应再发请求"


def test_seller_direct_normal_403_no_shortcircuit(monkeypatch):
    """普通 403（无挑战特征）不置短路（可能是临时限流，下轮可重试）。"""

    class _Resp:
        status_code = 403
        text = '{"error": "quota"}'

    monkeypatch.setattr(osa.requests, "post", lambda *a, **k: _Resp())
    data, ok = osa._seller_direct_post("/api/x", {}, {"sc_company_id": "42"})
    assert ok is False
    assert not osa._direct_blocked()


def test_close_seller_tab_keeps_remote_tab():
    """seller tab 常驻：新建 tab 归还时也只 release，不关远程（后续复用零重开）。"""
    cdp = mock.Mock()
    tab = mock.Mock()
    osa._close_seller_tab(cdp, tab, reused=False)
    tab.close.assert_called_once_with(close_remote=False)
    cdp.release.assert_called_once_with(tab)

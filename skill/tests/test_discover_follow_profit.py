"""B 批次（上品帮对标）：跟卖利润空间测算 + 竞品划线价 + 货源国内运费透传。

冻结契约键：follow_profit_cny / follow_margin / ozon_old_price / match_1688_freight_cny
语义红线：ozon_old_price / match_1688_freight_cny 默认 None（未知≠真实 0）；
follow_profit_cny / follow_margin 默认 0.0（无跟卖=0 是真实数据）；
不写 draft.original_price（上架划线价归 worker 三档定价）。
"""
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.lib.ozon_discovery import ProductCandidate, _calculate_profit


@pytest.fixture(autouse=True)
def _offline_logistics(monkeypatch):
    """测试离线红线：费率表查询一律不出口（禁打生产 worker），回落本地兜底估算。"""
    monkeypatch.setattr(
        "scripts.lib.ozon_discovery._query_logistics_from_worker",
        lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _isolate_cross_source_hook(monkeypatch):
    """批4 gate 校准 round3（2026-09-10）：跨源副钩 _cross_source_compare 在
    match_selected 内会拿 match_1688_title 真实触网（本机 Chrome 有登录态即真
    搜索，胜者换源改写 match_1688_* 槽位）→ 本文件单测一律 mock 掉；跨源行为
    由 test_discover_cross_source_wiring.py 专项锁定。"""
    monkeypatch.setattr(
        "scripts.lib.ozon_discovery._cross_source_compare",
        lambda *a, **k: None)


def _cand(**kw):
    c = ProductCandidate(ozon_product_id="p1", ozon_title="t", ozon_price=953.0)
    c.match_1688_price = 60.0
    c.min_competing_price = 900.0
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_follow_profit_computed_from_min_competing_price():
    c = _cand()
    c.estimated_logistics_cny = 40.0
    c.estimated_commission = 0.0   # 让 commission 分支走 rate 重算前的显式值
    _calculate_profit(c, fx_rate=12.0, commission_rate=0.10)
    # 竞品现价 953×12=11436 → 主利润已算；跟卖口径 900×12=10800
    assert c.follow_profit_cny > 0
    assert c.follow_profit_cny < (c.ozon_price * 12 - (60 + 40) * 1)  # 比主口径低
    assert 0 < c.follow_margin < c.profit_margin


def test_follow_profit_zero_without_competitors():
    c = _cand(min_competing_price=0.0)
    _calculate_profit(c, fx_rate=12.0, commission_rate=0.10)
    assert c.follow_profit_cny == 0.0 and c.follow_margin == 0.0


# ---------------------------------------------------------------------------
# Task B2: 竞品划线价接线（widget originalPrice → candidate.ozon_old_price）
# ---------------------------------------------------------------------------


def test_old_price_plumbed_from_info():
    from scripts.lib.utils import parse_price

    # widget info 的 originalPrice → candidate；空/缺 → None（未知，不冒充 0）
    for raw, want in (("7695", 7695.0), ("", None), (None, None)):
        val = parse_price(raw or "") or None
        assert val == want, f"originalPrice={raw!r}"


def test_old_price_wired_into_candidate(monkeypatch):
    """_analyze_product 消费 info.originalPrice → candidate.ozon_old_price。

    widget 导入是函数内 lazy import，须 patch 源模块 scripts.lib.ozon_widget。
    """
    import scripts.lib.ozon_widget as widget
    from scripts.lib import ozon_discovery as od

    monkeypatch.setattr(
        widget, "fetch_product_info",
        lambda *a, **k: {"title": "Товар", "price": "953 ₽",
                         "originalPrice": "7695 ₽", "images": []})
    monkeypatch.setattr(
        widget, "fetch_competing_sellers",
        lambda *a, **k: {"count": 0, "min_price": 0, "sellers": []})

    c = od._analyze_product("http://127.0.0.1:9222", None, "p1")
    assert c.status == "ok"
    assert c.ozon_old_price == 7695.0
    assert c.ozon_price == 953.0


def test_old_price_missing_is_none_not_zero(monkeypatch):
    """originalPrice 缺失/空 → None（未知≠真实 0，语义红线）。"""
    import scripts.lib.ozon_widget as widget
    from scripts.lib import ozon_discovery as od

    for raw in ("", None):
        monkeypatch.setattr(
            widget, "fetch_product_info",
            lambda *a, _raw=raw, **k: {"title": "Товар", "price": "953 ₽",
                                       "originalPrice": _raw, "images": []})
        monkeypatch.setattr(
            widget, "fetch_competing_sellers",
            lambda *a, **k: {"count": 0, "min_price": 0, "sellers": []})
        c = od._analyze_product("http://127.0.0.1:9222", None, "p1")
        assert c.status == "ok"
        assert c.ozon_old_price is None, f"originalPrice={raw!r}"


# ---------------------------------------------------------------------------
# Task B3: 货源国内运费透传（match 源 dict freightCny → candidate）
# ---------------------------------------------------------------------------


def test_freight_plumbed_from_match_dict():
    # :922 赋值表达式的三态镜像断言：正 freight → 值；0/缺失 → None（未知≠真实 0）
    for raw, want in (({"freightCny": 2.0}, 2.0), ({"freightCny": 0}, None), ({}, None)):
        got = float(raw.get("freightCny", 0) or 0) or None
        assert got == want, f"freightCny={raw!r}"


def test_candidate_freight_defaults_none():
    c = _cand()
    assert c.match_1688_freight_cny is None
    assert c.ozon_old_price is None


# ---------------------------------------------------------------------------
# 加固：widget competing sellers 返回 min_price=null → 归一 0.0
# （None 直落 candidate 时下游 _calculate_profit 的 min_competing_price > 0
#   比较会 TypeError——现被外层 try/except 吞成 error 状态）
# ---------------------------------------------------------------------------


def test_min_price_null_coerced_to_zero(monkeypatch):
    import scripts.lib.ozon_widget as widget
    from scripts.lib import ozon_discovery as od

    monkeypatch.setattr(
        widget, "fetch_product_info",
        lambda *a, **k: {"title": "Товар", "price": "953 ₽", "images": []})
    monkeypatch.setattr(
        widget, "fetch_competing_sellers",
        lambda *a, **k: {"count": 2, "min_price": None, "sellers": []})

    c = od._analyze_product("http://127.0.0.1:9222", None, "p1")
    assert c.status == "ok"
    assert c.min_competing_price == 0.0

    _calculate_profit(c, fx_rate=12.0, commission_rate=0.10)  # 不抛 TypeError
    assert c.follow_profit_cny == 0.0 and c.follow_margin == 0.0


def test_freight_wired_through_match_selected(monkeypatch):
    """match_selected 主线程处理把 match.freightCny 写回 candidate（三态）。

    仿 test_match_selected_parallel 既有模式：mock _search_1688_source 返回
    带 confidence≥门槛 的匹配 dict，串行路径（workers=1）全程离线。
    """
    from scripts.lib import ozon_discovery as od

    def _run(match_extra):
        c = ProductCandidate(ozon_product_id="p1", ozon_title="Товар p1",
                             ozon_price=1000.0)
        c.status = "ok"
        c.ozon_images = ["https://img.example/a.jpg"]

        def fake_search(cdp_url, images, title, **kwargs):
            return {"url": "https://detail.1688.com/offer/1.html",
                    "title": "Товар p1", "price": 50.0, "images": [],
                    "confidence": 0.8, "badge_eff": 0.0, "score": 50.0,
                    **match_extra}

        with mock.patch.object(od, "_discover_workers", return_value=1), \
             mock.patch.object(od, "_search_1688_source", side_effect=fake_search), \
             mock.patch.object(od, "_save_discovery_log"), \
             mock.patch.object(od, "_log_review_record"), \
             mock.patch("time.sleep"):
            od.match_selected([c], "http://127.0.0.1:9222", min_margin_pct=1)
        return c

    assert _run({"freightCny": 2.0}).match_1688_freight_cny == 2.0
    assert _run({"freightCny": 0}).match_1688_freight_cny is None
    assert _run({}).match_1688_freight_cny is None

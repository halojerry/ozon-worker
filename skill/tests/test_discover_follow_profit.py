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

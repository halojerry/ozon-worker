#!/usr/bin/env python3
"""运营指标扩容回归（discover 漏斗 v2 · Task 5）。

附录 A 实测结论：畅销榜池 item 已带转化/促销/退货全量字段（_extract_metrics
也早已解析出 qty_view_pdp/conv_to_cart_*/days_in_promo/... ），但
apply_analytics_to_candidate 只拷贝子集，ProductCandidate 没有这些字段
→ _SELECTION_FIELDS 的 13 个粗筛字段 getattr 恒 None（规则恒放行空转）。

本批把 9 个字段补进 dataclass + apply 链，命名对齐 _BASE_FILTER_RULES 键。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_analytics_metrics_expansion.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate, _SELECTION_FIELDS  # noqa: E402
from scripts.lib.ozon_seller_analytics import (  # noqa: E402
    _extract_metrics,
    apply_analytics_to_candidate,
)

# 附录 A 实测畅销榜 item 原始键（camelCase 透传形态，节选与本批相关字段）
WHAT_TO_SELL_ITEM = {
    "sku": "5684309818",
    "soldCount": 320,
    "gmvSum": 412800.0,
    "salesDynamics": 15.0,
    "drr": 9.5,
    "createDays": 120,
    "qtyViewPdp": 5400,
    "convToCartPdp": 6.2,
    "convToCartSearch": 4.1,
    "daysInPromo": 18,
    "discount": 12.0,
    "promoRevenueShare": 22.0,
    "daysWithTrafarets": 25,
    "nullableRedemptionRate": 61.0,
    "salesSchema": "FBO",
    "attributes": [{"id": 4497, "value": 850}],
}


def _candidate_with_metrics(item: dict) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id="1", ozon_title="t", ozon_price=1000.0)
    apply_analytics_to_candidate(c, _extract_metrics(item))
    return c


def test_apply_populates_conversion_fields():
    """畅销榜指标 → 9 个新字段全部落进候选（命名对齐粗筛规则键）。"""
    c = _candidate_with_metrics(WHAT_TO_SELL_ITEM)
    assert c.session_count == 5400            # qty_view_pdp → session_count
    assert c.conv_to_cart_pdp == 6.2
    assert c.conv_to_cart_search == 4.1
    assert c.days_in_promo == 18
    assert c.discount == 12.0
    assert c.promo_revenue_share == 22.0
    assert c.days_with_trafarets == 25
    assert c.nullable_redemption_rate == 61.0
    assert c.return_cancel_rate == 39.0       # return_rate = 100 - 61（_extract_metrics 计算）
    assert c.monthly_sales == 320             # 既有字段不回归
    assert c.weight_g == 850


def test_missing_metrics_keep_defaults():
    """无转化字段的老形态 item → 新字段保持 None（未知 = 粗筛「不限」语义）。"""
    c = _candidate_with_metrics({"sku": "2", "soldCount": 10, "soldCount2": 0})
    assert c.session_count is None
    assert c.conv_to_cart_pdp is None
    assert c.return_cancel_rate is None
    assert c.custom_click_rate is None      # data-pool 批7：点击率同组 None=未知语义
    assert c.monthly_sales == 10


def test_apply_maps_click_rate_to_candidate():
    """data-pool 批7：点击率派生值落候选 custom_click_rate（漏斗组 None=未知）。

    另两键（月销售动态增长率 sales_growth/广告份额 drr）为 maozi 直连键
    salesDynamics/drr 既有映射，v0.70 起已三出口透出——此处防回归锚。"""
    c = _candidate_with_metrics({"sku": "3", "views": 100000, "qtyViewPdp": 45000})
    assert c.custom_click_rate == 45.0
    c2 = _candidate_with_metrics(WHAT_TO_SELL_ITEM)
    assert c2.sales_growth == 15.0
    assert c2.drr == 9.5


def test_selection_fields_accessors_see_values():
    """_SELECTION_FIELDS 粗筛取值器不再恒 None——规则链路打通。"""
    c = _candidate_with_metrics(WHAT_TO_SELL_ITEM)
    assert _SELECTION_FIELDS["session_count"](c) == 5400
    assert _SELECTION_FIELDS["conv_to_cart_pdp"](c) == 6.2
    assert _SELECTION_FIELDS["return_cancel_rate"](c) == 39.0
    assert _SELECTION_FIELDS["days_in_promo"](c) == 18


def test_empty_metrics_noop():
    """空 metrics → False 且不炸（既有语义）。"""
    c = ProductCandidate(ozon_product_id="1", ozon_title="t", ozon_price=1.0)
    assert apply_analytics_to_candidate(c, {}) is False


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

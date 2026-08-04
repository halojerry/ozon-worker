#!/usr/bin/env python3
"""完成结果产品明细单测（v0.22）— 1688链接/利润率/售价/采购价/运费预估。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_product_summary.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.product_summary import build_product_summary


def test_single_product_summary():
    """单产品完成 → 明细含 1688链接/利润率/售价/采购价/运费预估/利润率。"""
    graph_result = {
        "product_id": "5806108346",
        "pricing_info": {
            "cost_cny": 75.6,
            "logistics_cost_cny": 260.0,
            "margin_rate": 0.25,
            "commission_rate": 0.10,
            "price": "469",
            "profit_estimation": {"profit_rate": 0.32, "profit_cny": 110.5},
        },
    }
    draft = {
        "purchase_url": "https://detail.1688.com/offer/1011966008290.html",
        "purchase_cost": 75.6,
        "item_id": "1011966008290",
    }
    summary = build_product_summary(graph_result, draft)
    assert len(summary) == 1
    row = summary[0]
    assert row["purchase_url"] == "https://detail.1688.com/offer/1011966008290.html"
    assert row["purchase_cost"] == 75.6
    assert row["margin_rate"] == 0.25
    assert row["price"] == "469"
    assert row["logistics_cost"] == 260.0
    assert row["profit_rate"] == 0.32
    assert row["product_id"] == "5806108346"


def test_summary_includes_variants():
    """多 SKU 变体 → 每变体一条明细。"""
    graph_result = {
        "product_id": "p1",
        "pricing_info": {
            "cost_cny": 50.0,
            "logistics_cost_cny": 100.0,
            "margin_rate": 0.25,
            "price": "200",
            "variant_prices": [
                {"sku_id": "s1", "price": 180, "old_price": 200, "currency_code": "CNY"},
                {"sku_id": "s2", "price": 220, "old_price": 250, "currency_code": "CNY"},
            ],
        },
    }
    draft = {"purchase_url": "https://detail.1688.com/offer/x.html", "purchase_cost": 50.0}
    summary = build_product_summary(graph_result, draft)
    assert len(summary) == 2
    assert summary[0]["sku_id"] == "s1"
    assert summary[0]["price"] == 180
    assert summary[1]["sku_id"] == "s2"


def test_summary_empty_pricing_info():
    """任务失败/无定价 → 明细仍有 1688链接+采购价，价格字段为空不报错。"""
    graph_result = {"error_message": "failed"}
    draft = {"purchase_url": "https://detail.1688.com/offer/x.html", "purchase_cost": 30.0}
    summary = build_product_summary(graph_result, draft)
    assert len(summary) == 1
    assert summary[0]["purchase_url"] == "https://detail.1688.com/offer/x.html"
    assert summary[0]["purchase_cost"] == 30.0
    assert summary[0]["price"] == ""


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
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

#!/usr/bin/env python3
"""P1b (Task): apply_analytics_to_candidate 填充 candidate.ozon_category（TDD）。

背景: 818-820 行只写了 candidate.category（str），从未写 ozon_category —— discover
流程 build_envelope_from_discovery 读到空 ozon_category → worker 靠 pg_trgm 猜类目
（sim=0.353 误匹配 → DESCRIPTION_DECLINE）。本测试锁定: Seller 权威类目
（category2Id=dc / category3Id=type）写入候选品 ozon_category，shape 与 follow
链路一致 {description_category_id, type_id}。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_ozon_category_populate.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate
from scripts.lib.ozon_seller_analytics import apply_analytics_to_candidate


def _candidate() -> ProductCandidate:
    return ProductCandidate(ozon_product_id="1", ozon_title="Товар", ozon_price=100.0)


def test_apply_analytics_sets_ozon_category():
    """metrics 含 category2_id=17028929 / category3_id=504866264 →
    candidate.ozon_category 为 Seller 权威类目 dict（dc/type 字符串化）。"""
    candidate = _candidate()
    metrics = {
        "category2_id": 17028929,
        "category3_id": 504866264,
        "sold_count": 10,
        "gmv_sum": 100.0,
    }
    ok = apply_analytics_to_candidate(candidate, metrics)
    assert ok is True
    assert candidate.ozon_category == {
        "description_category_id": "17028929",
        "type_id": "504866264",
    }


def test_apply_analytics_preserves_category_and_flags():
    """既有行为保留: candidate.category 字符串 + has_analytics 标志照常设置。"""
    candidate = _candidate()
    metrics = {
        "category2_id": 17028929,
        "category3_id": 504866264,
        "sold_count": 10,
        "gmv_sum": 100.0,
        "sales_dynamics": 5.0,
        "drr": 2.0,
        "create_days": 30,
    }
    apply_analytics_to_candidate(candidate, metrics)
    assert candidate.category == "17028929"
    assert candidate.has_analytics is True
    assert candidate.monthly_sales == 10
    assert candidate.monthly_revenue == 100.0
    assert candidate.sales_growth == 5.0
    assert candidate.drr == 2.0
    assert candidate.create_days == 30


def test_apply_analytics_without_category_ids_keeps_defaults():
    """无类目 ID → ozon_category 保持默认 {}，category 保持 ''（不写半截类目）。"""
    candidate = _candidate()
    ok = apply_analytics_to_candidate(candidate, {"sold_count": 5})
    assert ok is True
    assert candidate.ozon_category == {}
    assert candidate.category == ""


def test_apply_analytics_empty_metrics_returns_false():
    """空 metrics → False，字段零改动。"""
    candidate = _candidate()
    assert apply_analytics_to_candidate(candidate, {}) is False
    assert candidate.ozon_category == {}


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

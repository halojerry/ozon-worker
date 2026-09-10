#!/usr/bin/env python3
"""discover CSV 本地落盘 v0.70 扩列回归（对标上品帮选品记录 62 列）。

承诺：
① 现有 26 列序不动，只在尾部追加 18 列（链接/主图/货源标题/货源类目/
   匹配置信度/物流佣金测算/漏斗扩容组）；
② 漏斗字段 None=无数据 → 空串；真实 0 保留（0 是数据不是缺失）；
③ 主图/货源图取首张派生（完整图列表不进 CSV）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discovery_export_csv.py -q
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate, export_to_csv  # noqa: E402

# v0.70 前的 26 列（现有列序不动——回归锁）
_LEGACY_FIELDS = [
    "product_id", "title", "price_rub", "category", "brand",
    "commission_fbp", "commission_rfbs", "monthly_sales", "monthly_revenue",
    "sales_growth", "drr", "create_days", "rating", "review_count",
    "sales_schema", "has_analytics",
    "competing_sellers", "min_competitor_price", "weight_g", "dimensions",
    "match_1688_url", "match_1688_price", "profit_margin", "blue_ocean_score",
    "verdict", "logistics_estimated", "logistics_fallback_chain",
]

_V070_FIELDS = [
    "ozon_url", "ozon_image", "match_1688_title", "match_1688_image",
    "match_1688_category_name", "match_confidence",
    "estimated_logistics_cny", "estimated_commission",
    "session_count", "conv_to_cart_pdp", "conv_to_cart_search",
    "days_in_promo", "discount", "days_with_trafarets",
    "promo_revenue_share", "nullable_redemption_rate", "return_cancel_rate",
]


def _mk_candidate() -> ProductCandidate:
    c = ProductCandidate(
        ozon_product_id="1005570349",
        ozon_title="Катунь Serving Board",
        ozon_price=953.0,
    )
    c.status = "profitable"
    c.ozon_url = "https://www.ozon.ru/product/1005570349"
    c.ozon_images = ["https://ir-20.ozonstatic.cn/main.jpg",
                     "https://ir-20.ozonstatic.cn/second.jpg"]
    c.match_1688_url = "https://detail.1688.com/offer/1043458962058.html"
    c.match_1688_title = "卡吞餐盘"
    c.match_1688_category_name = "餐具"
    c.match_1688_images = ["https://cbu01.alicdn.com/source.webp"]
    c.session_count = 0            # 真实 0
    c.conv_to_cart_pdp = 5.62
    c.days_in_promo = 12
    c.nullable_redemption_rate = 88.0
    return c


def test_export_v070_columns_appended_and_values(tmp_path):
    out = tmp_path / "export.csv"
    export_to_csv([_mk_candidate()], str(out))
    with open(out, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    assert fields[:len(_LEGACY_FIELDS)] == _LEGACY_FIELDS, "现有 26 列序不得变动"
    for col in _V070_FIELDS:
        assert col in fields, f"缺扩列: {col}"
    row = rows[0]
    assert row["ozon_url"] == "https://www.ozon.ru/product/1005570349"
    assert row["ozon_image"] == "https://ir-20.ozonstatic.cn/main.jpg"      # 首张
    assert row["match_1688_title"] == "卡吞餐盘"
    assert row["match_1688_image"] == "https://cbu01.alicdn.com/source.webp"
    assert row["match_1688_category_name"] == "餐具"
    assert row["session_count"] == "0"                                       # 真实 0 保留
    assert row["conv_to_cart_pdp"] == "5.62"
    assert row["days_in_promo"] == "12"
    assert row["nullable_redemption_rate"] == "88.0"


def test_export_none_funnel_fields_blank_not_zero(tmp_path):
    """漏斗字段 None（畅销榜池未命中）→ 空串，不得落成 0 假数据。"""
    c = _mk_candidate()
    c.session_count = None
    c.conv_to_cart_pdp = None
    c.days_in_promo = None
    c.discount = None
    c.days_with_trafarets = None
    c.promo_revenue_share = None
    c.nullable_redemption_rate = None
    c.return_cancel_rate = None
    c.conv_to_cart_search = None
    out = tmp_path / "export.csv"
    export_to_csv([c], str(out))
    with open(out, encoding="utf-8-sig") as f:
        row = list(csv.DictReader(f))[0]
    for col in ("session_count", "conv_to_cart_pdp", "days_in_promo", "discount",
                "days_with_trafarets", "promo_revenue_share",
                "nullable_redemption_rate", "return_cancel_rate"):
        assert row[col] == "", f"{col} 应为空串, got {row[col]!r}"


def test_export_no_images_blank_derived(tmp_path):
    """无图列表 → 派生图键空串，不报错。"""
    c = _mk_candidate()
    c.ozon_images = []
    c.match_1688_images = []
    out = tmp_path / "export.csv"
    export_to_csv([c], str(out))
    with open(out, encoding="utf-8-sig") as f:
        row = list(csv.DictReader(f))[0]
    assert row["ozon_image"] == ""
    assert row["match_1688_image"] == ""


def test_export_b_batch_columns(tmp_path):
    """B 契约 4 列尾追加（现有列序不动）：follow_* 有值落值；
    ozon_old_price/match_1688_freight_cny 默认 None → 空串（未知≠真实 0）。"""
    c = _mk_candidate()
    c.follow_profit_cny = 12.3
    c.ozon_old_price = 7695.0
    out = tmp_path / "e.csv"
    export_to_csv([c], str(out))
    with open(out, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        row = list(reader)[0]
    assert {"follow_profit_cny", "follow_margin", "ozon_old_price",
            "match_1688_freight_cny"} <= set(fields)
    assert fields[-5:] == ["follow_profit_cny", "follow_margin",
                           "ozon_old_price", "match_1688_freight_cny",
                           "custom_click_rate"], "尾追加列序"
    assert row["follow_profit_cny"] == "12.3"
    assert row["follow_margin"] == "0.0"          # 真实 0 保留
    assert row["ozon_old_price"] == "7695.0"
    assert row["match_1688_freight_cny"] == ""    # None → 空串


def test_export_click_rate_column(tmp_path):
    """data-pool 批7：点击率列尾追加（卡片缺口三键之一；增长率 sales_growth/
    广告份额 drr 为既有列不回归）。None=未知 → 空串，区别于真实 0。"""
    c = _mk_candidate()
    c.custom_click_rate = 45.0
    out = tmp_path / "e.csv"
    export_to_csv([c], str(out))
    with open(out, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        row = list(reader)[0]
    assert fields[-1] == "custom_click_rate"
    assert row["sales_growth"] == "0.0"           # 既有列不回归（dataclass 默认 0.0）
    assert row["drr"] == "0.0"
    assert row["custom_click_rate"] == "45.0"

    c2 = _mk_candidate()
    c2.custom_click_rate = None
    out2 = tmp_path / "e2.csv"
    export_to_csv([c2], str(out2))
    with open(out2, encoding="utf-8-sig") as f:
        row2 = list(csv.DictReader(f))[0]
    assert row2["custom_click_rate"] == ""        # None=未知 → 空串


if __name__ == "__main__":
    import tempfile
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(tempfile.mkdtemp())
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

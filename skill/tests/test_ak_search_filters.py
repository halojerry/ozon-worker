"""AK 搜索结构化字段 + 筛选单测（v0.25 S1）。"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ak_1688_client import _parse_product_item, _parse_rate_48h, _parse_location


def _raw_item():
    return {
        "itemId": "123", "title": "不锈钢刀", "currentPrice": "25.0",
        "quantityBegin": 5, "soldOut": 1234, "storeAmount": 99,
        "company": "义乌市xx厂",
        "serviceInfos": [{"label": "发货地", "value": "浙江 金华"},
                         {"label": "48小时发货", "value": "95%"}],
        "promotionTags": ["实力商家"],
        "sellingPoints": [],
    }


def test_parse_product_item_structured_fields():
    p = _parse_product_item(_raw_item())
    assert p["moq"] == 5
    assert p["sales"] == 1234
    assert p["ship_rate_48h"] == 95.0
    assert p["location"] == "浙江 金华"
    assert p["supplier_tags"] == ["实力商家"]


def test_parse_rate_48h_patterns():
    assert _parse_rate_48h("48小时发货 95%") == 95.0
    assert _parse_rate_48h("48H揽收率 88.5%") == 88.5
    assert _parse_rate_48h("无") is None


def test_search_products_filters():
    import ak_1688_client as mod
    raw = {"data": {"data": [_raw_item()]}}
    with mock.patch.object(mod, "_post_1688", return_value=raw), \
         mock.patch("scripts.lib.config_store._require_auth"):
        items = mod.search_products("刀", max_price=30, max_moq=10,
                                    min_ship_rate_48h=90, min_sales=100)
    assert len(items) == 1
    with mock.patch.object(mod, "_post_1688", return_value=raw), \
         mock.patch("scripts.lib.config_store._require_auth"):
        items2 = mod.search_products("刀", max_price=20, min_ship_rate_48h=98)
    assert items2 == []  # 价格/48H 不满足 → 过滤


def test_extract_source_category_id():
    from cloud_probe import _extract_source_category_id
    cats = [{"name": "服饰配件", "id": 1001},
            {"name": "袜子", "leafId": 2002},
            {"name": "女袜", "thirdCategoryId": 3003}]
    assert _extract_source_category_id(cats) == 3003  # 取最末级（最后一条）的 id


def test_extract_source_category_id_empty():
    from cloud_probe import _extract_source_category_id
    assert _extract_source_category_id([]) is None
    assert _extract_source_category_id([{"name": "x"}]) is None


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""T7/S1: graph 信封 Ozon 反查同款 → 竞品数据混合键注入回归。

背景：build_graph_envelope 注入段（6.5）只写 margin/commission/fx/cdp_degraded +
ozon_category（来自 search_categories）。S1 加 Ozon 反查同款段：1688 标题 →
discover_from_keyword 搜索页候选 → _ru_zh_title_overlap/_llm_semantic_match
语义匹配 top1 → fetch_competing_sellers(min price) + fetch_product_info
(weight/dims/俄语 attributes) → 混合键注入信封（与 follow_sell_cloud 100% 对齐）:
  extensions.competitor_weight_g / competitor_dimensions_mm
  draft.ozon_attributes / competitor_price / follow_min_price

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_graph_envelope_competitor.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402

ITEM_ID = "980815374096"
DETAIL_URL = f"https://detail.1688.com/offer/{ITEM_ID}.html"
ALICDN_IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"
OZON_URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"


def _api_enriched(**overrides) -> dict:
    """CDP enrich 结果（1688 数据，图片齐全；source='cdp' 放行反查段）。"""
    data = {
        "title": "宠物自动饮水器 2L",
        "price": "5.50",
        "brand": "",
        "seller": "",
        "images": [ALICDN_IMG],
        "weight_grams": None,
        "packaging_rows": [],
        "shipping": {},
        "description": "",
        "sku_details": [],
        "attributes": [],
        "option_groups": [],
        "category_id": "",
    }
    data.update(overrides.pop("data", {}))
    base = {
        "ok": True,
        "degraded": False,
        "degraded_reason": "",
        "user_action": None,
        "data": data,
        "source": "cdp",
    }
    base.update(overrides)
    return base


def _comp_info(**overrides) -> dict:
    """fetch_product_info 返回（Ozon 竞品：weight/dims/俄语属性）。"""
    info = {
        "title": "Автопоилка для кошек 2л",
        "price": "1290",
        "cardPrice": "1190",
        "characteristics": [
            {"title": {"textRs": [{"content": "Вес"}]}, "values": [{"text": "900 г"}]},
            {"title": {"textRs": [{"content": "Габариты"}]}, "values": [{"text": "20x15x10 см"}]},
            {"title": {"textRs": [{"content": "Цвет"}]}, "values": [{"text": "Белый"}]},
        ],
    }
    info.update(overrides)
    return info


def _build(enriched=None, **kw) -> dict:
    """以 api_only enrich 结果调用 build_graph_envelope（mock 全部外部依赖）。"""
    enriched = enriched if enriched is not None else _api_enriched()
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=enriched), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "123", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        return cloud_probe.build_graph_envelope(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            poll_category=False,
            **kw,
        )


def _mock_reverse_lookup(urls=None, info=None, sellers=None, overlap=0.9):
    """mock Ozon 反查同款最底层：搜索页候选 / 竞品 info / 卖家列表 / 标题重叠分。"""
    urls = [OZON_URL] if urls is None else urls
    info = _comp_info() if info is None else info
    sellers = {"count": 3, "min_price": 1100.0,
               "sellers": [{"sku": "s1", "price": 1100.0, "seller_name": "x"}]} \
        if sellers is None else sellers
    return mock.patch("scripts.lib.ozon_discovery.discover_from_keyword",
                      return_value=urls), \
        mock.patch("scripts.lib.ozon_discovery._ru_zh_title_overlap",
                   return_value=overlap), \
        mock.patch("scripts.lib.ozon_discovery._llm_semantic_match",
                   return_value=False), \
        mock.patch("scripts.lib.ozon_widget.fetch_product_info",
                   return_value=info), \
        mock.patch("scripts.lib.ozon_widget.fetch_competing_sellers",
                   return_value=sellers)


def test_injects_competitor_mixed_keys():
    """反查命中 → 注入混合键：extensions.competitor_weight_g/competitor_dimensions_mm
    + draft.ozon_attributes/competitor_price/follow_min_price。"""
    patches = _mock_reverse_lookup()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        graph = _build()
    env = graph["envelope"]
    ext = env["extensions"]
    draft = env["draft"]
    # extensions 侧（worker assemble 兜底消费）
    assert ext["competitor_weight_g"] == 900
    assert ext["competitor_dimensions_mm"] == {"length": 200, "width": 150, "height": 100}
    # draft 侧（worker prepare/follow_sell_import 消费）
    assert draft["competitor_price"] == "1290"
    assert draft["follow_min_price"] == 1100.0
    assert draft["ozon_attributes"].get("Вес") == "900 г"
    assert draft["ozon_attributes"].get("Цвет") == "Белый"


def test_fail_open_when_reverse_lookup_raises():
    """反查任何异常 → fail-open：信封照常组装，无竞品键。"""
    with mock.patch("scripts.lib.ozon_discovery.discover_from_keyword",
                    side_effect=RuntimeError("CDP 不可用")):
        graph = _build()
    env = graph["envelope"]
    assert env["draft"]["title"] == "宠物自动饮水器 2L"
    assert "competitor_weight_g" not in env["extensions"]
    assert "follow_min_price" not in env["draft"]


def test_no_match_no_injection():
    """搜索页无候选 → 无竞品键注入（不污染信封）。"""
    patches = _mock_reverse_lookup(urls=[])
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        graph = _build()
    env = graph["envelope"]
    assert "competitor_weight_g" not in env["extensions"]
    assert "competitor_dimensions_mm" not in env["extensions"]
    assert "ozon_attributes" not in env["draft"]
    assert "follow_min_price" not in env["draft"]


def test_llm_rescue_on_weak_overlap():
    """弱词对重叠（overlap<0.6）+ LLM 判定同品 → 仍注入（护栏救回）。"""
    patches = _mock_reverse_lookup(overlap=0.3)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        graph = _build()
    env = graph["envelope"]
    assert env["extensions"]["competitor_weight_g"] == 900


def test_round_trip_json_serializable():
    """信封 JSON 可序列化（提交 Worker 契约）。"""
    patches = _mock_reverse_lookup()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        graph = _build()
    import json
    json.dumps(graph, ensure_ascii=False)


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

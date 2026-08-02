# -*- coding: utf-8 -*-
"""
v0.13 集成验证：assemble → prepare 全链路
- mock 外部 API（Ozon ozon_post / mxou chat / mxou image）
- 断言最终 payload：
  1. 无「字典属性 + dictionary_value_id=0」条目
  2. 自由文本属性无中文值（颜色名称等）
  3. 含中文值且翻译失败的自由文本属性被跳过

运行：docker run -v worker:/app ozon-worker:latest python tests/integration_attribute_fill_v013.py
"""
import os
import sys
import logging

sys.path.insert(0, "/app/src")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

from unittest.mock import patch, MagicMock
from graphs.state import PrepareOzonUploadInput
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
from graphs.nodes.assemble_ozon_product_node import (
    _build_items_deterministically,
    _validate_and_enrich_items,
)

# ── 测试数据：schema 含 商品颜色(字典) / 颜色名称(自由文本) / 风格(字典) / 用途(字典必填) ──
SCHEMA = [
    {"id": 10096, "name": "商品颜色", "dictionary_id": 5, "is_required": False},
    {"id": 10097, "name": "颜色名称", "dictionary_id": 0, "is_required": False},   # 自由文本！
    {"id": 4958, "name": "适用对象", "dictionary_id": 7, "is_required": True},
    {"id": 12345, "name": "风格", "dictionary_id": 9, "is_required": False},
    {"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
    {"id": 4180, "name": "Ключевые слова", "dictionary_id": 0, "is_required": False},
    {"id": 23487, "name": "Производитель", "dictionary_id": 0, "is_required": True},
    {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True},
    {"id": 4389, "name": "Страна", "dictionary_id": 12, "is_required": True},
    {"id": 8229, "name": "Тип товара", "dictionary_id": 0, "is_required": False},
    {"id": 23171, "name": "hashtag", "dictionary_id": 0, "is_required": False},
    {"id": 9048, "name": "Название модели", "dictionary_id": 0, "is_required": True},
]

DICT_LOOKUP = {
    10096: [{"id": 61581, "value": "蓝色"}, {"id": 61583, "value": "绿色"}],
    4958: [{"id": 3, "value": "家庭"}, {"id": 4, "value": "офис"}],
    12345: [{"id": 100, "value": "современный"}, {"id": 101, "value": "классический"}],
    85: [{"id": 126745801, "value": "Нет бренда"}],
    4389: [{"id": 90296, "value": "Китай"}],
}

DRAFT = {
    "item_id": "test002",
    "title": "猫咪玩具 逗猫棒",
    "images": ["http://img.test/1.jpg", "http://img.test/2.jpg"],
    "weight": 200,
    "dimensions": {"length": 80, "width": 50, "height": 30},
    "attributes": {
        "商品颜色": "绿色",        # 字典命中
        "颜色名称": "翠绿",        # 自由文本中文 → 翻译失败 → 应被跳过
        "风格": "现代简约",        # 字典未命中 → 跳过
        "适用对象": "家庭",        # 字典命中
    },
    "sku_id": "test002",
    "price": "1290",
    "original_price": "1590",
    "supplier": "义乌市玩具厂",
    "description": "逗猫棒 羽毛 铃铛 猫咪玩具",
}


def run():
    items = _build_items_deterministically(
        draft=DRAFT,
        description_category_id=17028830,
        type_id=971206780,
        attr_list=SCHEMA,
        dict_lookup=DICT_LOOKUP,
        images=DRAFT["images"],
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        weight_grams=int(DRAFT["weight"]),
        dimensions=DRAFT["dimensions"],
        price_rub=str(DRAFT["price"]),
        old_price_rub=str(DRAFT["original_price"]),
        currency_code="RUB",
        token="sk-test",
    )
    items = _validate_and_enrich_items(
        items=items,
        attr_list=SCHEMA,
        dict_lookup=DICT_LOOKUP,
        images=DRAFT["images"],
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        description_category_id=17028830,
        type_id=971206780,
        weight_grams=int(DRAFT["weight"]),
        dimensions=DRAFT["dimensions"],
        draft_title=DRAFT["title"],
        supplier=DRAFT["supplier"],
        ru_category_path="Товары для животных>Игрушки для кошек>Дразнилка",
    )

    # 提取 final_attributes（与 assemble Step 6 一致）
    final_attributes = []
    if items and items[0].get("attributes"):
        for attr in items[0]["attributes"]:
            for v in (attr.get("values") or []):
                final_attributes.append({
                    "attribute_id": attr["id"],
                    "value": v.get("value", ""),
                    "dictionary_value_id": v.get("dictionary_value_id", 0),
                    "source": "llm",
                })

    # ── mock 外部 API：翻译全部失败（返回中文）→ 验证防上传逻辑 ──
    def fake_translate(text, token, source_lang="auto", text_type="description"):
        return text  # 翻译失败，返回原文（中文）

    state = PrepareOzonUploadInput(
        draft=DRAFT,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes,
        attributes_schema=SCHEMA,
        dictionary_values={str(k): v for k, v in DICT_LOOKUP.items()},
        token="sk-test",
        original_images=DRAFT["images"],
    )

    with patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               side_effect=fake_translate), \
         patch("utils.mxou_llm.call_mxou_chat_api", return_value="Тестовый ответ на русском"), \
         patch("utils.mxou_api.call_mxou_chat_api", return_value="Тестовый ответ на русском"):
        output = prepare_ozon_upload_node(state, None, None)

    payload = output.ozon_payload or {}
    payload_attrs = []
    for item in payload.get("items", []):
        payload_attrs.extend(item.get("attributes", []))

    # ── 断言 ──
    failures = []
    schema_dict_ids = {int(a["id"]) for a in SCHEMA if a.get("dictionary_id", 0) > 0}
    attr_map = {}
    for a in payload_attrs:
        attr_map[int(a["id"])] = a

    # 1. 字典属性必须有 dict_id > 0
    for aid in schema_dict_ids:
        if aid in attr_map:
            for v in attr_map[aid].get("values", []):
                if int(v.get("dictionary_value_id", 0)) <= 0:
                    failures.append(f"字典属性 {aid} 存在 dict_id=0 条目: {attr_map[aid]}")

    # 2. 自由文本属性（dict_id=0）不得含中文值
    #    （字典属性 value 中文合法：Ozon 以 dictionary_value_id 为准，跨语言通用）
    for aid, a in attr_map.items():
        for v in a.get("values", []):
            val = str(v.get("value", ""))
            if int(v.get("dictionary_value_id", 0)) == 0 and any('\u4e00' <= ch <= '\u9fff' for ch in val):
                failures.append(f"自由文本属性 {aid} 含中文值: {val}")

    # 3. 颜色名称(10097) 翻译失败 → 应被跳过
    if 10097 in attr_map:
        failures.append(f"颜色名称(10097) 翻译失败应被跳过，但出现在 payload: {attr_map[10097]}")

    # 4. 风格(12345) 字典未匹配 → 不应出现（assemble 跳过 + prepare 不补）
    if 12345 in attr_map:
        failures.append(f"风格(12345) 字典未匹配应被跳过，但出现在 payload: {attr_map[12345]}")

    # 5. 品牌 85/5076 必须保留 dict_id（防"请从列表中选择"）
    for _bid in (85, 5076):
        if _bid in attr_map:
            for v in attr_map[_bid].get("values", []):
                if int(v.get("dictionary_value_id", 0)) <= 0:
                    failures.append(f"品牌属性 {_bid} dict_id 丢失（应=126745801）: {attr_map[_bid]}")
        else:
            failures.append(f"品牌属性 {_bid} 缺失")

    print(f"\n最终 payload 属性数: {len(payload_attrs)}")
    for a in payload_attrs:
        v0 = a.get("values", [{}])[0]
        print(f"  attr={a['id']} dict_id={v0.get('dictionary_value_id')} value={str(v0.get('value'))[:40]!r}")

    if failures:
        print("\n❌ FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\n✅ 集成验证通过：无字典属性 dict_id=0、无中文自由文本、颜色名称/风格未匹配被正确跳过")


if __name__ == "__main__":
    run()

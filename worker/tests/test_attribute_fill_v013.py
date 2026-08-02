# -*- coding: utf-8 -*-
"""
v0.13 属性填充回归测试 — 防 Ozon「属性值不正确，请从列表中选择」/「请用俄文填写该字段」

覆盖三个修复点：
1. 字典属性未匹配 → 跳过（绝不写 dictionary_value_id=0 + 中文文本兜底）
2. 可选字典属性 → 不再"取字典第一个值"盲补（仅唯一字典值才补充）
3. 无任何「字典属性 + dictionary_value_id=0」条目泄漏到 payload

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_attribute_fill_v013.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import (
    _build_items_deterministically,
    _validate_and_enrich_items,
)


def _schema():
    return [
        {"id": 10096, "name": "商品颜色", "dictionary_id": 5, "is_required": False},
        {"id": 4958, "name": "适用对象", "dictionary_id": 7, "is_required": True},
        {"id": 12345, "name": "风格", "dictionary_id": 9, "is_required": False},
        {"id": 54321, "name": "唯一值属性", "dictionary_id": 10, "is_required": False},
        {"id": 9999, "name": "材质", "dictionary_id": 13, "is_required": False},
        {"id": 23487, "name": "Производитель", "dictionary_id": 0, "is_required": True},
        {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True},
        {"id": 4389, "name": "Страна", "dictionary_id": 12, "is_required": True},
        {"id": 8229, "name": "Тип товара", "dictionary_id": 0, "is_required": False},
        {"id": 23171, "name": "hashtag", "dictionary_id": 0, "is_required": False},
    ]


def _dict_lookup():
    return {
        10096: [{"id": 61581, "value": "蓝色"}, {"id": 61583, "value": "绿色"}],
        4958: [{"id": 3, "value": "家庭"}, {"id": 4, "value": "для офиса"}],
        12345: [{"id": 100, "value": "современный"}, {"id": 101, "value": "классический"}],
        54321: [{"id": 200, "value": "универсальный"}],          # 唯一值 → 应补充
        9999: [{"id": 300, "value": "металл"}, {"id": 301, "value": "дерево"}],  # 无"塑料"匹配
        85: [{"id": 126745801, "value": "Нет бренда"}],
        4389: [{"id": 90296, "value": "Китай"}],
    }


def _draft():
    return {
        "item_id": "test001",
        "title": "宠物玩具 猫抓板",
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {
            "商品颜色": "蓝色",          # 字典命中 → dict_id=61581
            "适用对象": "家庭",          # 字典命中 → dict_id=3
            "材质": "塑料",             # 字典未命中 → 跳过（不写文本兜底）
        },
        "sku_id": "test001",
        "price": "1990",
        "original_price": "2390",
    }


def _run_pipeline():
    draft = _draft()
    items = _build_items_deterministically(
        draft=draft,
        description_category_id=17028830,
        type_id=971206780,
        attr_list=_schema(),
        dict_lookup=_dict_lookup(),
        images=draft["images"],
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        weight_grams=int(draft["weight"]),
        dimensions=draft["dimensions"],
        price_rub=str(draft["price"]),
        old_price_rub=str(draft["original_price"]),
        currency_code="RUB",
        token="sk-test",
    )
    items = _validate_and_enrich_items(
        items=items,
        attr_list=_schema(),
        dict_lookup=_dict_lookup(),
        images=draft["images"],
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        description_category_id=17028830,
        type_id=971206780,
        weight_grams=int(draft["weight"]),
        dimensions=draft["dimensions"],
        draft_title=draft["title"],
        supplier="深圳市宠物用品有限公司",
        ru_category_path="Товары для животных>Товары для кошек>Когтеточка",
    )
    return items[0]["attributes"]


def _attr_map(attrs):
    return {int(a["id"]): a for a in attrs if isinstance(a, dict) and a.get("id")}


def test_dict_matched_keeps_dict_id():
    """字典属性匹配成功 → 保留 dictionary_value_id"""
    am = _attr_map(_run_pipeline())
    assert 10096 in am
    vals = am[10096]["values"]
    assert vals and vals[0]["dictionary_value_id"] == 61581


def test_dict_unmatched_skipped_no_text_fallback():
    """字典属性未匹配（材质"塑料"）→ 跳过，绝不写 dictionary_value_id=0 文本兜底"""
    am = _attr_map(_run_pipeline())
    assert 9999 not in am, "字典未匹配属性不应出现在 payload 中"


def test_optional_dict_multi_value_not_blind_filled():
    """可选字典属性多值且无匹配 → 不盲补第一个值（修复"风格被填无关值"）"""
    am = _attr_map(_run_pipeline())
    assert 12345 not in am, "可选字典属性多值不应被盲补"


def test_optional_dict_single_value_filled():
    """可选字典属性唯一值 → 安全补充"""
    am = _attr_map(_run_pipeline())
    assert 54321 in am
    vals = am[54321]["values"]
    assert vals and vals[0]["dictionary_value_id"] == 200


def test_no_dict_attr_with_zero_dict_id():
    """核心断言：payload 中不允许存在「字典属性 + dictionary_value_id=0」条目"""
    schema_dict_ids = {int(a["id"]) for a in _schema() if a.get("dictionary_id", 0) > 0}
    for attr in _run_pipeline():
        if int(attr["id"]) in schema_dict_ids:
            for v in attr.get("values", []):
                assert int(v.get("dictionary_value_id", 0)) > 0, \
                    f"字典属性 {attr['id']} 存在 dict_id=0 条目: {attr}"


def test_brand_forced_no_brand():
    """品牌强制"Нет бренда"（126745801）"""
    am = _attr_map(_run_pipeline())
    assert 85 in am
    assert am[85]["values"][0]["dictionary_value_id"] == 126745801


def test_country_forced_china():
    """原产国强制"Китай"（90296）"""
    am = _attr_map(_run_pipeline())
    assert 4389 in am
    assert am[4389]["values"][0]["dictionary_value_id"] == 90296


def test_manufacturer_uses_supplier():
    """制造商 23487 用 supplier 填充（自由文本）"""
    am = _attr_map(_run_pipeline())
    assert 23487 in am
    assert "宠物用品" in am[23487]["values"][0]["value"]


if __name__ == "__main__":
    # 纯 Python 入口（生产镜像无 pytest，Docker 内验证用）
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)

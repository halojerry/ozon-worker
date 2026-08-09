# -*- coding: utf-8 -*-
"""
v0.16 属性填充增强回归测试 — 填满类目属性 + 中文零容忍 + 海关编码跳过

覆盖：
1. 必填自由文本无默认值 → 跳过不写空串（防 error_attribute_values_empty）
2. 可选多值字典：标题词匹配唯一命中 → 补充；无匹配 → 跳过（不盲补首值）
3. 海关编码属性（ID=22604 / 名称含"ТН ВЭД"）→ 三处路径均不填
4. prepare 侧：_russian_required_attrs 翻译结果非俄语 → 跳过；9024 含中文 → 翻译
5. _generate_rich_description_fallback：中文属性名/值不拼进 HTML

运行：cd worker && PYTHONPATH=src python3 tests/test_attribute_fill_v016.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import patch

from graphs.nodes.assemble_ozon_product_node import (
    _build_items_deterministically,
    _validate_and_enrich_items,
)


def _schema():
    return [
        {"id": 10096, "name": "商品颜色", "dictionary_id": 5, "is_required": False},
        {"id": 4958, "name": "适用对象", "dictionary_id": 7, "is_required": True},
        {"id": 12345, "name": "风格", "dictionary_id": 9, "is_required": False},
        {"id": 77777, "name": "宠物类型", "dictionary_id": 15, "is_required": False},
        {"id": 8888, "name": "Артикул производителя", "dictionary_id": 0, "is_required": True},
        {"id": 9999, "name": "材质", "dictionary_id": 13, "is_required": False},
        {"id": 22604, "name": "ТН ВЭД", "dictionary_id": 5, "is_required": True},
        {"id": 22605, "name": "Таможенный код", "dictionary_id": 16, "is_required": False},
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
        77777: [{"id": 400, "value": "猫抓板"}, {"id": 401, "value": "逗猫棒"}],
        9999: [{"id": 300, "value": "металл"}, {"id": 301, "value": "дерево"}],
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
            "ТН ВЭД": "8505110000",     # 海关编码 1688 匹配到 → 应被跳过
        },
        "sku_id": "test001",
        "price": "1990",
        "original_price": "2390",
    }


def _run_pipeline(draft=None, schema=None, dict_lookup=None):
    draft = draft or _draft()
    schema = schema or _schema()
    dict_lookup = dict_lookup or _dict_lookup()
    items = _build_items_deterministically(
        draft=draft,
        description_category_id=17028830,
        type_id=971206780,
        attr_list=schema,
        dict_lookup=dict_lookup,
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
    # mock Ozon /values/search（可选补充兜底）→ 无网络依赖
    with patch("utils.ozon_client.ozon_post",
               return_value={"result": []}):
        items = _validate_and_enrich_items(
            items=items,
            attr_list=schema,
            dict_lookup=dict_lookup,
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


# ── 必填自由文本无默认值 ──
def test_required_free_text_no_default_skipped():
    """必填自由文本属性无 KNOWN_DEFAULTS → 跳过不写空串（防 error_attribute_values_empty）"""
    am = _attr_map(_run_pipeline())
    assert 8888 not in am, "无默认值的必填自由文本不应写空串上传"


# ── 可选多值字典标题匹配 ──
def test_optional_multi_dict_title_match_filled():
    """可选多值字典：标题词唯一命中 → 补充（v0.16 增强，替代纯跳过）"""
    am = _attr_map(_run_pipeline())
    assert 77777 in am, "标题词唯一命中猫抓板 应被补充"
    vals = am[77777]["values"]
    assert vals and vals[0]["dictionary_value_id"] == 400


def test_optional_multi_dict_no_match_skipped():
    """可选多值字典：无标题匹配 → 跳过（不盲补第一个值）"""
    am = _attr_map(_run_pipeline())
    assert 12345 not in am, "风格多值无匹配不应被盲补"


# ── 海关编码跳过 ──
def test_customs_attr_required_skipped():
    """必填海关编码（22604 ТН ВЭД）→ 跳过，绝不标题搜索乱填"""
    am = _attr_map(_run_pipeline())
    assert 22604 not in am, "必填海关编码属性不应被填充"


def test_customs_attr_name_keyword_skipped():
    """可选属性名含"Таможенный код"（22605）→ 不进入可选补充"""
    am = _attr_map(_run_pipeline())
    assert 22605 not in am, "名称含海关关键词的可选属性不应被填充"


def test_customs_attr_1688_match_not_filled():
    """1688 属性匹配到海关属性名（"ТН ВЭД": "8505110000"）→ _build_items 不填"""
    am = _attr_map(_run_pipeline())
    assert 22604 not in am, "1688 匹配到的海关编码不应被填入"


def test_customs_attr_utils_detection():
    """is_customs_attr 识别：ID 命中 + RU/ZH/EN 名称关键词"""
    from utils.attribute_utils import is_customs_attr
    assert is_customs_attr(22604)
    assert is_customs_attr(9999, "ТН ВЭД")
    assert is_customs_attr(9999, "Таможенный код")
    assert is_customs_attr(9999, "海关编码")
    assert is_customs_attr(9999, "HS code")
    assert not is_customs_attr(10096)
    assert not is_customs_attr(9999, "商品颜色")


def test_vocab_divergence_attr_enters_output():
    """1688「材料」→ Ozon schema「材质」：词汇分歧（非子串）经同义词组匹配 → 值进入输出
    （v0.32 修复前该分歧名 0 匹配 → 属性不进入 items）"""
    schema = [{"id": 55555, "name": "材质", "dictionary_id": 0, "is_required": False}]
    draft = {k: v for k, v in _draft().items() if k != "attributes"}
    draft["attributes"] = {"材料": "ABS"}
    items = _build_items_deterministically(
        draft=draft,
        description_category_id=17028830,
        type_id=971206780,
        attr_list=schema,
        dict_lookup={},
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
    am = {int(a["id"]): a for a in items[0]["attributes"]}
    assert 55555 in am, "词汇分歧属性应经同义词组匹配进入输出"
    assert am[55555]["values"][0]["value"] == "ABS"


# ── prepare 侧：中文零容忍 ──
def test_prepare_russian_required_translation_failure_skipped():
    """_russian_required_attrs（4191）翻译返回拉丁 → 跳过该属性（绝不拉丁原文上传）"""
    from graphs.state import PrepareOzonUploadInput
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    final_attributes = [
        {"attribute_id": 4191, "value": "Pet toy for cat", "dictionary_value_id": 0, "source": "llm"},
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
    ]
    schema = [{"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
              {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}]
    state = PrepareOzonUploadInput(
        draft=_draft(),
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes,
        attributes_schema=schema,
        dictionary_values={},
        token="sk-test",
        original_images=_draft()["images"],
    )
    # 翻译失败：返回拉丁（无西里尔无中文）
    def fake_translate_latin(text, token, source_lang="auto", text_type="description"):
        return "LatinOnlyText"

    with patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               side_effect=fake_translate_latin), \
         patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар для дома"):
        output = prepare_ozon_upload_node(state, None, None)

    payload_attrs = []
    for item in (output.ozon_payload or {}).get("items", []):
        payload_attrs.extend(item.get("attributes", []))
    payload_ids = {int(a["id"]) for a in payload_attrs}
    assert 4191 not in payload_ids, "俄语必填属性翻译失败（拉丁）应被跳过"


def test_prepare_sku_chinese_translated():
    """9024(SKU) 含中文 → 不再豁免，走翻译（v0.16 修复）"""
    from graphs.state import PrepareOzonUploadInput
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    final_attributes = [
        {"attribute_id": 9024, "value": "中文SKU编码", "dictionary_value_id": 0, "source": "llm"},
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
    ]
    schema = [{"id": 9024, "name": "SKU", "dictionary_id": 0, "is_required": True},
              {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}]
    state = PrepareOzonUploadInput(
        draft=_draft(),
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes,
        attributes_schema=schema,
        dictionary_values={},
        token="sk-test",
        original_images=_draft()["images"],
    )

    def fake_translate_ru(text, token, source_lang="auto", text_type="description"):
        return "SKU-Тест123"  # 俄语成功

    with patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               side_effect=fake_translate_ru), \
         patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар для дома"):
        output = prepare_ozon_upload_node(state, None, None)

    payload_attrs = []
    for item in (output.ozon_payload or {}).get("items", []):
        payload_attrs.extend(item.get("attributes", []))
    attr_map = {int(a["id"]): a for a in payload_attrs}
    assert 9024 in attr_map, "9024 含中文但翻译成功应保留"
    val = attr_map[9024]["values"][0]["value"]
    assert "中文" not in val, f"9024 值不应含中文: {val}"
    assert val == "SKU-Тест123"


# ── rich description fallback 中文清洗 ──
def test_rich_desc_fallback_no_chinese():
    """_generate_rich_description_fallback：中文属性名/值不拼进 HTML"""
    from graphs.nodes.prepare_ozon_upload_node import _generate_rich_description_fallback
    html = _generate_rich_description_fallback(
        product_name="Когтеточка",
        attributes={"商品颜色": "蓝色", "材质": "金属", "weight": "300 г"},
        description="",
    )
    assert "商品颜色" not in html, "中文属性名不应拼进富文本"
    assert "蓝色" not in html, "中文属性值不应拼进富文本"
    assert "металл" in html or "300 г" in html, "非中文属性应保留"
    assert "Когтеточка" in html


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)

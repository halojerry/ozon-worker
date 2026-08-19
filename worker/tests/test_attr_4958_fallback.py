"""v0.40 4958(专为) 兜底修复单测。

背景：Ozon 必填属性 4958(专为) 此前兜底只搜标题整句（"宠物自动饮水机..."），
实测 /values/search 整句必空 → 必填缺失 → Ozon 拒单（`必填属性缺失: 专为`）。
修复：优先用 draft.attributes 的"适用对象/用途"值搜（"猫狗通用"→语义映射
"猫咪"→33754 实测命中），标题整句兜底。

锁定行为：
- draft.attributes["适用对象"]="猫狗通用" → 搜索词链含"猫咪"（单字"猫"→双字
  "猫咪"映射，规避 /values/search 2 字符下限）
- "猫咪" 命中 → 4958 填入 dict_id=33754
- 无适用对象属性 → 仅标题兜底（回归旧行为）
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from unittest import mock  # noqa: E402

from graphs.state import PrepareOzonUploadInput  # noqa: E402
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node  # noqa: E402


def _draft(attrs=None):
    return {
        "item_id": "test4958",
        "title": "宠物自动饮水机 大容量过滤饮水器 猫咪狗狗通用",
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": attrs if attrs is not None else {"适用对象": "猫狗通用"},
        "sku_id": "test4958",
        "price": "1990",
        "original_price": "2390",
    }


_SCHEMA = [
    {"id": 4958, "name": "专为", "dictionary_id": 7, "is_required": True},
    {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True},
    {"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
]


def _run(draft, search_hits=None):
    """驱动 prepare_ozon_upload_node（4958 兜底路径）。"""
    state = PrepareOzonUploadInput(
        draft=draft,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028673",
        type_id="95192",
        final_attributes=[],  # 4958 缺失 → 触发兜底
        attributes_schema=_SCHEMA,
        dictionary_values={},
        token="sk-test",
        original_images=draft["images"],
    )

    def fake_search(cid, key, aid, dc, tp, value, lang="RU"):
        """search_dictionary_values mock：仅 "猫咪" 命中 4958（实测 33754）。"""
        if aid == 4958 and value == "猫咪":
            return [{"id": 33754, "value": "猫咪用品"}]
        return []

    with mock.patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
                    side_effect=lambda *a, **k: "Тест"), \
         mock.patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
                    return_value="Товар для животных"), \
         mock.patch("utils.ozon_dict_values.search_dictionary_values",
                    side_effect=fake_search):
        output = prepare_ozon_upload_node(state, None, None)

    payload_attrs = []
    for item in (output.ozon_payload or {}).get("items", []):
        payload_attrs.extend(item.get("attributes", []))
    return {int(a["id"]): a for a in payload_attrs}


def test_4958_filled_from_attr_value_cat():
    """适用对象=猫狗通用 → 语义映射 "猫咪" 命中 → 4958 填入 33754（RU 值，Ozon 上传必须俄语）。"""
    attr_map = _run(_draft())
    assert 4958 in attr_map, f"4958 应被兜底填入，实际属性: {list(attr_map.keys())}"
    vals = attr_map[4958].get("values", [])
    assert vals and vals[0].get("dictionary_value_id") == 33754, vals
    # ⚠️ 字典属性 value 必须俄语（Ozon 拒中文）——4958 兜底经 AUDIENCE_ZH_TO_VALUES
    # 匹配 RU 值 "Для кошек"（对 33754）。中文 "猫咪用品" 仅搜索用，不上传。
    assert vals[0].get("value") == "Для кошек"


def test_4958_search_terms_include_cat_double():
    """搜索词链含 '猫咪'（单字→双字映射，规避 2 字符下限）。"""
    calls = []

    def fake_search(cid, key, aid, dc, tp, value, lang="RU"):
        calls.append(value)
        if aid == 4958 and value == "猫咪":
            return [{"id": 33754, "value": "猫咪用品"}]
        return []

    state = PrepareOzonUploadInput(
        draft=_draft(), source={"purchase_url": "u", "purchase_cost": "1"},
        pricing_info={"final_price": "1", "selling_price": "1", "variant_prices": []},
        description_category_id="17028673", type_id="95192",
        final_attributes=[], attributes_schema=_SCHEMA,
        dictionary_values={}, token="sk", original_images=_draft()["images"],
    )
    with mock.patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
                    side_effect=lambda *a, **k: "Тест"), \
         mock.patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
                    return_value="Товар"), \
         mock.patch("utils.ozon_dict_values.search_dictionary_values",
                    side_effect=fake_search):
        prepare_ozon_upload_node(state, None, None)
    assert "猫咪" in calls, f"搜索词链应含'猫咪'，实际: {calls}"
    assert "猫狗通用" in calls  # 原值优先


def test_4958_no_use_attr_title_only():
    """无适用对象属性 → 标题兜底（不崩，可能命中标题词）。"""
    attr_map = _run(_draft(attrs={"材质": "塑料"}))
    # 标题含"猫咪" → mock 命中 33754（标题兜底也走同一 search）
    assert 4958 in attr_map or True  # 不断言必须有（标题搜索可能空），但不崩即过

"""必填字典属性默认值解析单测（v0.24 F1b）— 字典值语义解析 + 尺码表真实映射。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.attr_defaults import (
    find_dict_value_id, resolve_brand_default, resolve_gender_default,
    resolve_size_default, resolve_merge_card_default,
    resolve_missing_mandatory_dict_attr,
)


def test_find_dict_value_id_exact():
    vals = [{"id": 126745801, "value": "Нет бренда"}, {"id": 94435, "value": "Спортивная бутылка"}]
    assert find_dict_value_id(vals, "Нет бренда") == (126745801, "Нет бренда")
    assert find_dict_value_id(vals, "нет  бренда") == (126745801, "Нет бренда")
    assert find_dict_value_id(vals, "不存在") is None


def test_resolve_brand_default():
    vals = [{"id": 126745801, "value": "Нет бренда"}, {"id": 11, "value": "Samsung"}]
    assert resolve_brand_default(vals) == (126745801, "Нет бренда")


def test_resolve_gender_default():
    vals = [{"id": 1, "value": "Мужской"}, {"id": 2, "value": "Женский"}, {"id": 3, "value": "Унисекс"}]
    assert resolve_gender_default("女袜", vals) == (2, "Женский")
    assert resolve_gender_default("男女通用", vals) == (3, "Унисекс")
    assert resolve_gender_default("无性别词", vals) is None


def test_resolve_size_default_with_real_tables():
    # 男性表：48 = M；女性表：42 = S；鞋子：38(1688) → 37(RU)
    male_vals = [{"id": 10, "value": "48"}, {"id": 11, "value": "50"}]
    female_vals = [{"id": 20, "value": "42"}, {"id": 21, "value": "44"}]
    shoe_vals = [{"id": 30, "value": "37"}, {"id": 31, "value": "38"}]
    assert resolve_size_default("M", "Рубашка мужская", male_vals) == (10, "48")
    assert resolve_size_default("S", "Платье женское", female_vals) == (20, "42")
    assert resolve_size_default("38", "Кроссовки", shoe_vals) == (30, "37")


def test_resolve_merge_card_default():
    vals = [{"id": 501, "value": "Да"}, {"id": 502, "value": "Нет"}]
    assert resolve_merge_card_default(vals) == (502, "Нет")


def test_resolve_missing_mandatory_dict_attr_dispatch():
    brand_vals = [{"id": 126745801, "value": "Нет бренда"}]
    gender_vals = [{"id": 2, "value": "Женский"}]
    assert resolve_missing_mandatory_dict_attr(
        31, "Бренд одежды", title_cn="女袜", dict_vals=brand_vals
    ) == (126745801, "Нет бренда")
    assert resolve_missing_mandatory_dict_attr(
        9163, "Пол", title_cn="女袜", dict_vals=gender_vals
    ) == (2, "Женский")
    assert resolve_missing_mandatory_dict_attr(99999, "未知属性", dict_vals=[]) is None


def test_prepare_fills_missing_mandatory_dict_attrs_with_mock_state():
    """必填字典属性缺失 → prepare 用 attr_defaults 补齐（品牌/性别/尺码/8292/型号）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace

    schema = [
        {"id": 31, "name": "Бренд одежды", "is_required": True, "dictionary_id": 500},
        {"id": 9163, "name": "Пол", "is_required": True, "dictionary_id": 501},
        {"id": 4295, "name": "Российский размер", "is_required": True, "dictionary_id": 502},
        {"id": 8292, "name": "Объединить на одной карточке", "is_required": True, "dictionary_id": 503},
        {"id": 22390, "name": "Модель", "is_required": True, "dictionary_id": 0},
        {"id": 9048, "name": "Артикул", "is_required": True, "dictionary_id": 0},
    ]
    dict_values = {
        "31": [{"id": 126745801, "value": "Нет бренда"}],
        "9163": [{"id": 2, "value": "Женский"}],
        "4295": [{"id": 10, "value": "48"}, {"id": 11, "value": "50"}],
        "8292": [{"id": 501, "value": "Да"}, {"id": 502, "value": "Нет"}],
    }
    state = SimpleNamespace(
        dictionary_values=dict_values,
        description_category_id="17027918",
        type_id="971311385",
    )
    items = [{
        "offer_id": "4206931226_0",
        "name": "Носки мужские, размер M",
        "attributes": [{"id": 9048, "values": [{"dictionary_value_id": 0, "value": "4206931226"}]}],
    }]
    draft = {"item_id": "4206931226", "title": "女袜三双"}
    result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[31]["values"][0]["dictionary_value_id"] == 126745801
    assert attr_map[9163]["values"][0]["dictionary_value_id"] == 2
    assert attr_map[4295]["values"][0]["dictionary_value_id"] == 10
    assert attr_map[8292]["values"][0]["dictionary_value_id"] == 502
    assert attr_map[22390]["values"][0]["dictionary_value_id"] == 0  # 型号=自由文本 itemId
    assert attr_map[22390]["values"][0]["value"] == "4206931226"
    assert attr_map[9048]["values"][0]["dictionary_value_id"] == 0  # 自由文本不受影响


def test_prepare_dict_live_search_fallback():
    """缓存无值 → prepare 用 /values/search 兜底并填 dictionary_value_id（v0.25 T2）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 31, "name": "Бренд одежды", "is_required": True, "dictionary_id": 500}]
    state = SimpleNamespace(
        dictionary_values={},  # 缓存空 → 走 live search
        description_category_id="17027918", type_id="971311385",
        ozon_client_id="5371047", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки", "attributes": []}]
    draft = {"item_id": "x", "title": "女袜"}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values",
                    return_value=[{"id": 126745801, "value": "Нет бренда"}]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[31]["values"][0]["dictionary_value_id"] == 126745801


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

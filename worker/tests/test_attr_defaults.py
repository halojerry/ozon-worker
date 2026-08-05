"""必填字典属性默认值解析单测（v0.24 F1b）— 字典值语义解析 + 尺码表真实映射。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.attr_defaults import (
    find_dict_value_id, resolve_brand_default, resolve_gender_default,
    resolve_size_default, resolve_merge_card_default,
    resolve_missing_mandatory_dict_attr, dict_search_terms, resolve_ozon_attr_value,
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
    assert resolve_gender_default("Носки женские капроновые", vals) == (2, "Женский")  # RU 标题
    assert resolve_gender_default("Перчатки мужские", vals) == (1, "Мужской")
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


def test_prepare_fills_optional_dict_attrs_via_synonyms():
    """非必填字典属性（材质）按同义词匹配 1688 属性并填 dictionary_value_id（v0.25 T3）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_optional_dict_attrs
    from types import SimpleNamespace

    schema = [{"id": 8050, "name": "Материал", "is_required": False, "dictionary_id": 600}]
    state = SimpleNamespace(
        dictionary_values={"8050": [{"id": 1001, "value": "Нержавеющая сталь"}]},
        description_category_id="17027918", type_id="971311385",
    )
    items = [{"offer_id": "x_0", "name": "Нож", "attributes": []}]
    draft = {"item_id": "x", "title": "不锈钢刀", "attributes": {"材质": "不锈钢"}}
    result = _fill_optional_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[8050]["values"][0]["dictionary_value_id"] == 1001
    assert attr_map[8050]["values"][0]["value"] == "Нержавеющая сталь"


def test_prepare_color_extracts_from_prefixed_string():
    """1688 颜色「209中圆点短丝袜 黑色,…」→ 取内含颜色词「黑色」→ черный（v0.25 修复）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 10096, "name": "Цвет", "is_required": True, "dictionary_id": 505}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93157",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки", "attributes": []}]
    draft = {"item_id": "x", "title": "女袜",
             "attributes": {"颜色": "209中圆点短丝袜 肤色,209中圆点短丝袜 黑色"}}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values",
                    return_value=[{"id": 61574, "value": "черный"}]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[10096]["values"][0]["dictionary_value_id"] == 61574


def test_append_spec_table_contains_attrs():
    """描述末尾追加规格参数表（俄语属性名/值 + 重量尺寸）（v0.25 T4）。"""
    from graphs.nodes.prepare_ozon_upload_node import _append_spec_table
    attrs = [
        {"id": 85, "name": "Бренд", "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
        {"id": 9163, "name": "Пол", "values": [{"dictionary_value_id": 2, "value": "Женский"}]},
    ]
    out = _append_spec_table("Описание товара.", attrs, weight_g=500,
                             dimensions={"length": 100, "width": 80, "height": 50})
    assert "Характеристики" in out
    assert "Нет бренда" in out
    assert "Женский" in out
    assert "500" in out
    assert "100" in out and "80" in out and "50" in out


def test_dict_search_terms_per_attr():
    assert dict_search_terms(31, "Бренд одежды") == ["Нет бренда"]
    assert dict_search_terms(9163, "Пол", title_cn="女袜") == ["Женский"]
    assert dict_search_terms(4295, "Российский размер", size_cn="M", product_name_ru="Рубашка мужская") == ["48"]
    assert "Нет" in dict_search_terms(8292, "Объединить на одной карточке")
    terms = dict_search_terms(8229, "Тип", product_name_ru="Носки мужские", title_cn="男袜")
    assert "Носки мужские" in terms and "男袜" in terms


def test_dict_search_terms_type_short_words_first():
    terms = dict_search_terms(8229, "Тип", product_name_ru="Носки женские капроновые, 6 пар", title_cn="女袜")
    assert terms[0] == "Носки"
    assert "Носки женские" in terms


def test_resolve_ozon_attr_value_matches_semantics():
    attrs = {"Пол": "Женский", "Цвет": "черный", "Тип": "Носки", "Размер": "36-38"}
    assert resolve_ozon_attr_value(9163, "Пол", attrs) == "Женский"
    assert resolve_ozon_attr_value(10096, "Цвет", attrs) == "черный"
    assert resolve_ozon_attr_value(8229, "Тип", attrs) == "Носки"
    assert resolve_ozon_attr_value(4295, "Российский размер", attrs) == "36-38"
    assert resolve_ozon_attr_value(999, "x", attrs) is None
    assert resolve_ozon_attr_value(9163, "Пол", {}) is None


def test_prepare_uses_ozon_attrs_first():
    """竞品 Ozon 属性（Пол/Тип）→ search 填 dictionary_value_id，优先于 1688 推断。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [
        {"id": 9163, "name": "Пол", "is_required": True, "dictionary_id": 501},
        {"id": 8229, "name": "Тип", "is_required": True, "dictionary_id": 504},
    ]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93157",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки женские", "attributes": []}]
    draft = {"item_id": "x", "title": "女袜", "ozon_attributes": {"Пол": "Женский", "Тип": "Носки"}}

    def fake_search(client_id, api_key, aid, dc, tp, value, language="RU"):
        if aid == 9163 and value == "Женский":
            return [{"id": 22881, "value": "Женский"}]
        if aid == 8229 and value == "Носки":
            return [{"id": 93157, "value": "Носки"}]
        return []

    with mock.patch("utils.ozon_dict_values.search_dictionary_values", side_effect=fake_search):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[9163]["values"][0]["dictionary_value_id"] == 22881
    assert attr_map[8229]["values"][0]["dictionary_value_id"] == 93157


def test_prepare_ozon_attr_exact_match_for_size():
    """竞品「Российский размер=36」→ 直接精确匹配字典值 36（v0.25 修复，不再依赖 size_cn）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 4295, "name": "Российский размер", "is_required": True, "dictionary_id": 502}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93157",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки женские капроновые, 6 пар", "attributes": []}]
    draft = {"item_id": "x", "title": "Носки женские", "ozon_attributes": {"Российский размер": "36"}}

    def fake_search(client_id, api_key, aid, dc, tp, value, language="RU"):
        return [{"id": 35430, "value": "36"}] if value == "36" else []

    with mock.patch("utils.ozon_dict_values.search_dictionary_values", side_effect=fake_search):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[4295]["values"][0]["dictionary_value_id"] == 35430
    assert attr_map[4295]["values"][0]["value"] == "36"


def test_resolve_type_default_takes_first_value():
    vals = [{"id": 94435, "value": "Спортивная бутылка"}]
    assert resolve_missing_mandatory_dict_attr(8229, "Тип", dict_vals=vals) == (94435, "Спортивная бутылка")


def test_prepare_fills_clothing_required_attrs_with_curated_search():
    """服装五必填（品牌/性别/尺码/8292/类型）缓存空 → 按语义关键词 live search 全部补齐（v0.25 修复）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [
        {"id": 31, "name": "Бренд одежды", "is_required": True, "dictionary_id": 500},
        {"id": 9163, "name": "Пол", "is_required": True, "dictionary_id": 501},
        {"id": 4295, "name": "Российский размер", "is_required": True, "dictionary_id": 502},
        {"id": 8292, "name": "Объединить на одной карточке", "is_required": True, "dictionary_id": 503},
        {"id": 8229, "name": "Тип", "is_required": True, "dictionary_id": 504},
        {"id": 10096, "name": "Цвет", "is_required": True, "dictionary_id": 505},
    ]
    state = SimpleNamespace(
        dictionary_values={},
        description_category_id="17027918", type_id="971311385",
        ozon_client_id="5371047", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки мужские, размер M", "attributes": []}]
    draft = {"item_id": "x", "title": "男袜", "attributes": {"颜色": "黑色"}}

    def fake_search(client_id, api_key, aid, dc, tp, value, language="RU"):
        table = {
            "Нет бренда": [{"id": 126745801, "value": "Нет бренда"}],
            "Мужской": [{"id": 1, "value": "Мужской"}],
            "48": [{"id": 10, "value": "48"}],
            "Нет": [{"id": 502, "value": "Нет"}],
            "Носки мужские, размер M": [{"id": 94435, "value": "Носки"}],
            "черный": [{"id": 61576, "value": "Черный"}],
        }
        return table.get(value, [])

    with mock.patch("utils.ozon_dict_values.search_dictionary_values", side_effect=fake_search):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[31]["values"][0]["dictionary_value_id"] == 126745801
    assert attr_map[9163]["values"][0]["dictionary_value_id"] == 1
    assert attr_map[4295]["values"][0]["dictionary_value_id"] == 10
    assert attr_map[8292]["values"][0]["dictionary_value_id"] == 502
    assert attr_map[8229]["values"][0]["dictionary_value_id"] == 94435
    assert attr_map[10096]["values"][0]["dictionary_value_id"] == 61576


def test_prepare_merge_card_falls_back_to_list_mode():
    """8292 search 搜不到 → 列表模式取「不合并」（v0.25 修复）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 8292, "name": "Объединить на одной карточке", "is_required": True, "dictionary_id": 503}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93157",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки", "attributes": []}]
    draft = {"item_id": "x", "title": "女袜"}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]), \
         mock.patch("utils.ozon_dict_values.list_dictionary_values",
                    return_value=[{"id": 501, "value": "Да"}, {"id": 502, "value": "Нет"}]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[8292]["values"][0]["dictionary_value_id"] == 502


def test_prepare_merge_card_free_text_when_dict_zero():
    """8292 在该类目是自由文本（dict_id=0）→ 填「Нет」(dictionary_value_id=0)（v0.25 修复）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 8292, "name": "Объединить на одной карточке", "is_required": True, "dictionary_id": 0}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93157",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки", "attributes": []}]
    draft = {"item_id": "x", "title": "女袜"}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]), \
         mock.patch("utils.ozon_dict_values.list_dictionary_values", return_value=[]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[8292]["values"][0]["dictionary_value_id"] == 0
    assert attr_map[8292]["values"][0]["value"] == "Нет"


def test_prepare_size_not_extracted_from_pack_count():
    """「6 双」的 6 不是尺码 → 4295 不填（避免误填儿童码 122，v0.25 修复）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 4295, "name": "Российский размер", "is_required": True, "dictionary_id": 502}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93157",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Носки женские капроновые, 6 пар", "attributes": []}]
    draft = {"item_id": "x", "title": "女袜"}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    assert all(a.get("id") != 4295 for it in result for a in it.get("attributes", []))


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

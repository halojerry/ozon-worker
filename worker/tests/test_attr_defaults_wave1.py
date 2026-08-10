"""C1 wave1: 10 个必填属性兜底链验证（sentry-attribute-fixes todo 1）。

对 8229/85/9163/10096/4295/31/8292/23487/4389/9782 逐个锁定
`resolve_missing_mandatory_dict_attr` 路由器的安全默认分支：
- 8229: type_id 精确匹配 / 判别式冲突拒绝
- 85/31: resolve_brand_default（Нет бренда 126745801）
- 9163: 中性词 → Унисекс；无中性词类目 → 男+女双值（prepare 路径）
- 10096: 中文/俄语标题颜色推断（修复前路由器恒 None，fail-before-fix）
- 4295: 尺码映射 + One-size 兜底
- 8292: resolve_merge_card_default（不合并）
- 23487: 自由文本 = supplier（不依赖字典路由器）
- 4389: 硬编码 Китай/90296（assemble 强制 + 路由器精确匹配，修复前路由器恒 None）
- 9782: get_safe_hazard_default 只返回非危险值

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_attr_defaults_wave1.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.attr_defaults import (
    COLOR_ZH_TO_RU,
    infer_color_zh,
    resolve_missing_mandatory_dict_attr,
)

BRAND = [{"id": 126745801, "value": "Нет бренда"}]
GENDER = [{"id": 1, "value": "Мужской"}, {"id": 2, "value": "Женский"}, {"id": 3, "value": "Унисекс"}]
EXPLOSIVES = {"id": 970593901, "value": "Категория 1. Взрывчатые вещества"}
SAFE = {"id": 970593900, "value": "Не опасный груз"}


# ═══ 8229 类型：type_id 精确匹配 / 判别式冲突拒绝 ═══

def test_8229_type_id_exact_match():
    vals = [
        {"id": 91965, "value": "套娃"},
        {"id": 148495146, "value": "手持风扇"},
        {"id": 93735, "value": "纪念品"},
    ]
    got = resolve_missing_mandatory_dict_attr(8229, "Тип товара", dict_vals=vals, type_id=148495146)
    assert got == (148495146, "手持风扇")


def test_8229_discriminant_conflict_rejected():
    """标题明确说「桌面」形态，但 type_id 匹配值是「手持」形态 → 高置信错配 → None。"""
    vals = [{"id": 148495146, "value": "手持风扇"}]
    got = resolve_missing_mandatory_dict_attr(
        8229, "Тип товара", title_cn="桌面风扇 台式", product_name_ru="Настольный вентилятор",
        dict_vals=vals, type_id=148495146,
    )
    assert got is None, "判别词冲突必须拒绝，绝不返回错误形态值"


# ═══ 85/31 品牌 ═══

def test_85_brand_returns_no_brand():
    got = resolve_missing_mandatory_dict_attr(85, "Бренд", dict_vals=BRAND)
    assert got == (126745801, "Нет бренда")


def test_31_clothing_brand_routes_to_brand_default():
    got = resolve_missing_mandatory_dict_attr(31, "Бренд одежды", dict_vals=BRAND)
    assert got == (126745801, "Нет бренда")


def test_brand_no_dict_value_returns_none():
    assert resolve_missing_mandatory_dict_attr(85, "Бренд", dict_vals=[]) is None


# ═══ 9163 性别 ═══

def test_9163_gender_neutral_default():
    got = resolve_missing_mandatory_dict_attr(9163, "Пол", title_cn="男女通用", dict_vals=GENDER)
    assert got == (3, "Унисекс")
    got2 = resolve_missing_mandatory_dict_attr(9163, "Пол", title_cn="女袜", dict_vals=GENDER)
    assert got2 == (2, "Женский")


def test_9163_gender_pair_when_no_neutral_in_prepare():
    """无中性词类目（字典只有 Мужской/Женский）→ prepare 用男+女双值兜底（v0.26）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 9163, "name": "Пол", "is_required": True, "dictionary_id": 501}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="41777465", type_id="93100",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Маска", "attributes": []}]
    draft = {"item_id": "x", "title": "面具", "attributes": {}}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]), \
         mock.patch("utils.ozon_dict_values.list_dictionary_values",
                    return_value=[{"id": 1, "value": "Мужской"}, {"id": 2, "value": "Женский"}]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    ids = sorted(v["dictionary_value_id"] for v in attr_map[9163]["values"])
    assert ids == [1, 2], f"应为男+女双值, 实际 {ids}"


# ═══ 10096 颜色（C1 新增路由器分支，修复前恒 None）═══

def test_10096_color_inferred_from_cn_title():
    """1688 中文标题「黑色…」→ черный → 字典值 id（fail-before-fix: 修复前恒 None）。"""
    vals = [{"id": 61574, "value": "черный"}, {"id": 61581, "value": "синий"}]
    got = resolve_missing_mandatory_dict_attr(10096, "Цвет", title_cn="黑色汽车挂饰", dict_vals=vals)
    assert got == (61574, "черный")


def test_10096_color_inferred_from_ru_title():
    """俄语标题「Колготки черные」→ черный（infer_color_ru 接线）。"""
    vals = [{"id": 61574, "value": "черный"}]
    got = resolve_missing_mandatory_dict_attr(
        10096, "Цвет", title_cn="女袜", product_name_ru="Колготки черные", dict_vals=vals,
    )
    assert got == (61574, "черный")


def test_10096_color_no_inference_returns_none():
    """标题无颜色词 → None（绝不盲补首值，颜色多值时首值语义随机）。"""
    vals = [{"id": 61574, "value": "черный"}, {"id": 61581, "value": "синий"}]
    assert resolve_missing_mandatory_dict_attr(10096, "Цвет", title_cn="汽车挂饰", dict_vals=vals) is None


def test_infer_color_zh_compound_before_base():
    assert infer_color_zh("深绿色袜子") == "темно-зеленый"
    assert infer_color_zh("黑色") == "черный"
    assert infer_color_zh("无颜色词") is None
    assert COLOR_ZH_TO_RU


# ═══ 4295 尺码 ═══

def test_4295_size_routed_to_size_default():
    vals = [{"id": 10, "value": "48"}, {"id": 11, "value": "50"}]
    got = resolve_missing_mandatory_dict_attr(
        4295, "Российский размер", size_cn="M", product_name_ru="Рубашка мужская", dict_vals=vals,
    )
    assert got == (10, "48")


def test_4295_one_size_fallback_in_prepare():
    """无尺寸来源 → 类目有「Один размер」则兜底。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace
    from unittest import mock

    schema = [{"id": 4295, "name": "Российский размер", "is_required": True, "dictionary_id": 835}]
    state = SimpleNamespace(
        dictionary_values={}, description_category_id="200001517", type_id="93100",
        ozon_client_id="5381204", ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": "Колготки, 40 ден, 1 шт", "attributes": []}]
    draft = {"item_id": "x", "title": "Колготки", "attributes": {}}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]), \
         mock.patch("utils.ozon_dict_values.list_dictionary_values",
                    return_value=[{"id": 35676, "value": "Один размер"}]):
        result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[4295]["values"][0]["dictionary_value_id"] == 35676
    assert attr_map[4295]["values"][0]["value"] == "Один размер"


# ═══ 8292 合并卡片 ═══

def test_8292_merge_card_returns_no_merge():
    vals = [{"id": 501, "value": "Да"}, {"id": 502, "value": "Нет"}, {"id": 503, "value": "не объединять"}]
    got = resolve_missing_mandatory_dict_attr(8292, "Объединить на одной карточке", dict_vals=vals)
    assert got is not None and got[0] == 502
    assert "нет" in str(got[1]).lower() or "не объедин" in str(got[1]).lower()


def test_8292_no_merge_value_returns_none():
    assert resolve_missing_mandatory_dict_attr(8292, "Объединить на одной карточке",
                                               dict_vals=[{"id": 501, "value": "Да"}]) is None


# ═══ 9782 危险品 ═══

def test_9782_hazard_safe_only_via_router():
    got = resolve_missing_mandatory_dict_attr(9782, "Класс опасности товара",
                                              dict_vals=[EXPLOSIVES, SAFE])
    assert got == (970593900, "Не опасный груз")
    zh = resolve_missing_mandatory_dict_attr(9782, "Класс опасности товара",
                                             dict_vals=[{"id": 1, "value": "爆炸物 Category 1"},
                                                        {"id": 2, "value": "非危险货物"}])
    assert zh == (2, "非危险货物")
    assert resolve_missing_mandatory_dict_attr(9782, "Класс опасности товара",
                                               dict_vals=[EXPLOSIVES]) is None


# ═══ 23487 制造商 = 自由文本 supplier ═══

def test_23487_not_in_dict_router_by_design():
    """制造商是自由文本（= supplier），字典路由器必须返回 None，不参与 dict 解析。"""
    assert resolve_missing_mandatory_dict_attr(23487, "Производитель", dict_vals=[SAFE]) is None


def test_23487_manufacturer_filled_from_supplier():
    """prepare 用 supplier 填充 23487（俄语供应商原样保留，dictionary_value_id=0）。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace

    schema = [{"id": 23487, "name": "Производитель", "is_required": True, "dictionary_id": 0}]
    state = SimpleNamespace(dictionary_values={}, description_category_id="1", type_id="1", token="")
    items = [{"offer_id": "x_0", "name": "Щётка", "attributes": []}]
    draft = {"item_id": "x", "title": "浴刷", "supplier": "ООО Вектор"}
    result = _fill_missing_required_dict_attrs(items, schema, draft, state)
    attr_map = {a["id"]: a for it in result for a in it.get("attributes", [])}
    assert attr_map[23487]["values"][0]["dictionary_value_id"] == 0
    assert attr_map[23487]["values"][0]["value"] == "ООО Вектор"


# ═══ 4389 原产国 = Китай/90296 ═══

def test_4389_router_country_branch():
    """路由器 4389 分支：字典值里精确存在 Китай → (90296, Китай)（fail-before-fix: 修复前恒 None）。"""
    vals = [{"id": 90296, "value": "Китай"}, {"id": 1, "value": "Россия"}]
    got = resolve_missing_mandatory_dict_attr(4389, "Страна производства", dict_vals=vals)
    assert got == (90296, "Китай")
    assert resolve_missing_mandatory_dict_attr(4389, "Страна производства",
                                               dict_vals=[{"id": 1, "value": "Россия"}]) is None


def test_4389_country_hardcoded_china_in_assemble():
    """assemble 强制 4389 = Китай/90296（缺失也补）。"""
    from graphs.nodes.assemble_ozon_product_node import _build_items_deterministically, _validate_and_enrich_items
    from unittest.mock import patch

    schema = [{"id": 4389, "name": "Страна производства", "dictionary_id": 12, "is_required": True}]
    draft = {
        "item_id": "test4389", "title": "宠物玩具 猫抓板",
        "images": ["http://img.test/1.jpg"], "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "sku_id": "test4389", "price": "1990", "original_price": "2390",
    }
    items = _build_items_deterministically(
        draft=draft, description_category_id=17028830, type_id=971206780,
        attr_list=schema, dict_lookup={}, images=draft["images"],
        ozon_client_id="test_client", ozon_api_key="test_key",
        weight_grams=300, dimensions=draft["dimensions"],
        price_rub="1990", old_price_rub="2390", currency_code="RUB", token="sk-test",
    )
    with patch("utils.ozon_client.ozon_post", return_value={"result": []}):
        items = _validate_and_enrich_items(
            items=items, attr_list=schema, dict_lookup={}, images=draft["images"],
            ozon_client_id="test_client", ozon_api_key="test_key",
            description_category_id=17028830, type_id=971206780,
            weight_grams=300, dimensions=draft["dimensions"],
            draft_title=draft["title"], supplier="ООО Вектор",
            ru_category_path="Товары для животных>Товары для кошек>Когтеточка",
        )
    am = {int(a["id"]): a for a in items[0]["attributes"] if isinstance(a, dict) and a.get("id")}
    assert 4389 in am, "assemble 必须补充 4389 原产国"
    assert am[4389]["values"][0]["dictionary_value_id"] == 90296
    assert am[4389]["values"][0]["value"] == "Китай"


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

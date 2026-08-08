"""T1: 跟卖属性合并链 — draft.ozon_attributes(RU名→attr_id→dict_id) → draft.attributes → 硬编码兜底

对抗验证后的最终方案：
- follow_sell_import_node 不再把硬编码 5 属性当竞品数据
- 合并链: draft.ozon_attributes(竞品 RU 名→值) → draft.attributes(1688 中文) → 硬编码兜底
- 字典属性解析 dict_id 失败 → 跳过（绝不注入原文）

用例：
(a) follow 信封带 draft.ozon_attributes → 组装 payload 含已解析竞品属性(dictionary_value_id>0)
(b) 无 ozon_attributes → 用 draft.attributes
(c) 双无 → 硬编码兜底(品牌85/5076 + 产地4389 + 型号9048 + 数量8962)
(d) 竞品文本值无字典匹配 → 跳过，绝不注入原文

运行: cd worker && PYTHONPATH=src python3 -m pytest tests/test_follow_attr_merge.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

# ── Fake state (跟 follow_sell_v5 一致) ──
class FakeState:
    def __init__(self, envelope, ozon_client_id="123", ozon_api_key="key", currency_code="CNY", token="sk-test"):
        self.envelope = envelope
        self.ozon_client_id = ozon_client_id
        self.ozon_api_key = ozon_api_key
        self.currency_code = currency_code
        self.token = token
        self.product_id = None
        self.description_category_id = ""
        self.type_id = ""
        self.final_attributes = []
        self.attributes_schema = []


class FakeProgress:
    def log_node_action(self, *a, **k):
        pass
    def log_node_start(self, *a, **k):
        pass


def _base_envelope(**draft_extra):
    draft = {
        "ozon_product_id": "3852000144",
        "title": "Тестовый товар",
        "ozon_title": "Оригинальный тестовый товар",
        "images": ["https://cdn.ozon.ru/img1.jpg"],
        "ozon_category": {
            "description_category_id": "17027918",
            "type_id": "971311385",
            "category_path": "Автозапчасти > Подвеска > Амортизаторы",
        },
        "competitor_price": "2500.00",
        "purchase_cost": 85.0,
        "currency": "CNY",
        "weight": 500,
        "dimensions": {"length": 200, "width": 150, "height": 100},
        "item_id": "980815374096",
    }
    draft.update(draft_extra)
    return {
        "draft": draft,
        "extensions": {"follow_sell": True, "follow_type": "hand",
                       "margin_rate": 0.25, "commission_rate": 0.10},
    }


def _mock_schema_schema():
    """模拟 follow 节点拉取的 attrs_schema（RU 名 + dictionary_id）。"""
    return [
        {"id": 85, "name": "Бренд", "dictionary_id": 100},
        {"id": 5076, "name": "Бренд товара", "dictionary_id": 101},
        {"id": 4389, "name": "Страна-изготовитель", "dictionary_id": 102},
        {"id": 10096, "name": "Цвет", "dictionary_id": 200},
        {"id": 4180, "name": "Материал", "dictionary_id": 201},
        {"id": 9048, "name": "Название модели", "dictionary_id": 0},
    ]


def _run_follow_node(envelope, search_side_effect):
    """跑 follow_sell_import_node，mock schema 拉取 + 字典搜索。"""
    import graphs.nodes.follow_sell_import_node as mod
    import requests as req

    # mock 类目解析
    mod._resolve_category_by_id = lambda dc_id, type_name_hint="", token="": ("17027918", "971311385")
    mod._resolve_category = lambda dc, tp, language="": ("17027918", "971311385")
    mod._verify_category_schema = lambda cid, akey, dc, tp: True

    _orig_post = req.post

    class _Resp:
        status_code = 200
        def __init__(self, json_data=None):
            self._json = json_data or {}
        def json(self):
            return self._json

    def _mock_post(url, headers=None, json=None, timeout=30):
        if "description-category/attribute" in url:
            return _Resp({"result": _mock_schema_schema()})
        if "import-by-sku" in url:
            return _Resp({"result": {"task_id": "1", "unmatched_sku_list": []}})
        if "import/info" in url:
            return _Resp({"result": {"items": []}})
        return _Resp()

    req.post = _mock_post
    try:
        with mock.patch("utils.ozon_dict_values.search_dictionary_values", side_effect=search_side_effect):
            state = FakeState(envelope=envelope)
            result = mod.follow_sell_import_node(state)
    finally:
        req.post = _orig_post
    return result


def _fake_dict_search(hits_by_value):
    """构造 search_dictionary_values 的 side_effect。"""
    def _search(client_id, api_key, attribute_id, dc, tp, value, language="RU"):
        return hits_by_value.get(value, [])
    return _search


# ═══════════════════════════════════════════════════════════
# (a) draft.ozon_attributes → payload 含已解析竞品属性
# ═══════════════════════════════════════════════════════════
def test_a_ozon_attributes_resolved_into_final_attrs():
    """follow 信封带 draft.ozon_attributes(RU 名) → final_attributes 含 dict_id>0 的竞品属性。"""
    env = _base_envelope(ozon_attributes={"Цвет": "черный", "Материал": "ABS"})
    hits = {
        "черный": [{"id": 61571, "value": "черный"}],
        "ABS": [{"id": 12001, "value": "ABS"}],
    }
    result = _run_follow_node(env, _fake_dict_search(hits))

    attrs = {int(a["id"]): a for a in result["final_attributes"]}
    assert 10096 in attrs, "竞品 Цвет 属性应被解析进入 final_attributes"
    color_val = attrs[10096]["values"][0]
    assert color_val["dictionary_value_id"] == 61571
    assert 4180 in attrs, "竞品 Материал 属性应被解析进入 final_attributes"
    assert attrs[4180]["values"][0]["dictionary_value_id"] == 12001
    assert result["error_message"] == ""


# ═══════════════════════════════════════════════════════════
# (a2) assemble 消费 final_attributes → payload 含竞品属性
# ═══════════════════════════════════════════════════════════
def test_a2_assemble_payload_contains_competitor_attrs():
    """follow 输出 final_attributes → _assemble_follow_sell payload 含已解析竞品属性(dict_id>0)。"""
    from graphs.nodes.assemble_ozon_product_node import _assemble_follow_sell

    env = _base_envelope(ozon_attributes={"Цвет": "черный"})
    hits = {"черный": [{"id": 61571, "value": "черный"}]}
    result = _run_follow_node(env, _fake_dict_search(hits))
    assert 10096 in {int(a["id"]) for a in result["final_attributes"]}

    state = FakeState(
        envelope=env,
        ozon_client_id="123", ozon_api_key="key", currency_code="CNY",
    )
    state.final_attributes = result["final_attributes"]
    state.description_category_id = "17027918"
    state.type_id = "0"          # UPDATE 模式跳过 schema 拉取（避免 DB/HTTP）
    state.product_id = "999888777"  # UPDATE 模式

    out = _assemble_follow_sell(
        state,
        env["draft"],
        "Тестовый товар",
        ["https://cdn.ozon.ru/img1.jpg"],
        {"price": "2500", "old_price": "3000"},
        FakeProgress(),
    )
    payload_attrs = out["ozon_payload"]["items"][0]["attributes"]
    by_id = {int(a["id"]): a for a in payload_attrs}
    assert 10096 in by_id, "payload 应含竞品 Цвет 属性"
    assert by_id[10096]["values"][0]["dictionary_value_id"] == 61571
    # final_attrs_flat 也应含 attribute_id + dict_id
    flat = {int(a["attribute_id"]): a for a in out["final_attributes"]}
    assert 10096 in flat and flat[10096]["dictionary_value_id"] == 61571


# ═══════════════════════════════════════════════════════════
# (b) 无 ozon_attributes → 用 draft.attributes (1688 中文)
# ═══════════════════════════════════════════════════════════
def test_b_no_ozon_attributes_uses_draft_attributes():
    """无 ozon_attributes → 从 draft.attributes(中文 颜色) 解析竞品等价属性。"""
    env = _base_envelope(attributes={"颜色": "黑色"})  # 无 ozon_attributes
    hits = {"黑色": [{"id": 61576, "value": "черный"}]}
    result = _run_follow_node(env, _fake_dict_search(hits))

    attrs = {int(a["id"]): a for a in result["final_attributes"]}
    # 颜色属性从 draft.attributes 解析（resolve_ozon_attr_value 语义匹配 颜色→Цвет）
    assert 10096 in attrs, "draft.attributes 颜色 应解析进 final_attributes"
    assert attrs[10096]["values"][0]["dictionary_value_id"] == 61576


# ═══════════════════════════════════════════════════════════
# (c) 双无 → 硬编码兜底 5 属性
# ═══════════════════════════════════════════════════════════
def test_c_no_source_hardcoded_fallback():
    """无 ozon_attributes 且无 draft.attributes → 硬编码 5 属性兜底（品牌/产地/型号/数量）。"""
    env = _base_envelope()  # 双无
    result = _run_follow_node(env, _fake_dict_search({}))

    attrs = result["final_attributes"]
    ids = {int(a["id"]) for a in attrs}
    assert 85 in ids and 5076 in ids, "品牌兜底缺失"
    assert 4389 in ids, "产地兜底缺失"
    assert 9048 in ids, "型号兜底缺失"
    assert 8962 in ids, "数量兜底缺失"
    brand = {int(a["id"]): a for a in attrs}[85]
    assert brand["values"][0]["dictionary_value_id"] == 126745801  # Нет бренда


# ═══════════════════════════════════════════════════════════
# (d) 竞品文本值无字典匹配 → 跳过，绝不注入原文
# ═══════════════════════════════════════════════════════════
def test_d_unmatched_competitor_value_skipped_no_raw_text():
    """竞品属性值无 dict_id 命中 → 该属性被跳过，绝不注入文本兜底。"""
    env = _base_envelope(ozon_attributes={"Цвет": "какой-то неизвестный оттенок"})
    result = _run_follow_node(env, _fake_dict_search({}))  # 搜索全空

    ids = {int(a["id"]) for a in result["final_attributes"]}
    assert 10096 not in ids, "无字典匹配的颜色属性必须跳过（不注入原文）"
    for a in result["final_attributes"]:
        val = a["values"][0]
        # 字典属性不能注入原文；本用例 10096 不应出现
        if int(a["id"]) == 10096:
            raise AssertionError("10096 不应出现")
        # 硬编码兜底 dict 属性必须有合法 dict_id（126745801/90296）
        if int(a["id"]) in (85, 5076):
            assert val["dictionary_value_id"] > 0
    assert result["error_message"] == ""


# ═══════════════════════════════════════════════════════════
# (a3) attr_defaults 通用解析器扩展：RU 名→attr_id 语义匹配
# ═══════════════════════════════════════════════════════════
def test_attr_defaults_generic_ru_name_resolution():
    """resolve_ozon_attr_value 扩展后：RU 名(Цвет) 可直接命中 ozon_attributes 竞品值。"""
    from utils.attr_defaults import resolve_ozon_attr_value

    attrs = {"Цвет": "черный", "Материал": "ABS", "Размер": "36"}
    # 颜色
    assert resolve_ozon_attr_value(10096, "Цвет", attrs) == "черный"
    # 材质
    assert resolve_ozon_attr_value(4180, "Материал", attrs) == "ABS"
    # 尺码
    assert resolve_ozon_attr_value(4295, "Российский размер", attrs) == "36"
    # 未知 attr_id + 未知名称 → None（不误命中）
    assert resolve_ozon_attr_value(999999, "Неизвестный параметр", attrs) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

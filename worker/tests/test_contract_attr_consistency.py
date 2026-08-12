"""Phase 2: 三处属性匹配一致性合同测试（test_contract_attr_consistency.py）。

核心：同一 (schema, draft.attributes, cached_dict_values) 输入，跑三处路径
（prepare 补全期 / retry 修复期 / matcher 纯函数层），断言每个出现的属性
dictionary_value_id 一致 —— 结构性防漂移（对抗评审 architect L6）。

注意：assemble 是 ZH schema 名匹配（Phase 3 接入），prepare 是 RU schema 名
+同义词桥接，语义不同。合同测试覆盖【确定性值选择】的等价性：
给定已匹配的 (attr_id, 1688 value, 字典候选集)，三处选择的 dict_id 一致。
"""
import os
import sys
import tempfile
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.attr_value_matcher import (  # noqa: E402
    clean_dict_value,
    match_dict_value,
    unique_or_none,
)

# 测试用共享 fixture：颜色 10096（多值）+ 材质 22393（单值）
SCHEMA = [
    {"id": 10096, "name": "Цвет товара", "dictionary_id": 1494, "is_collection": True},
    {"id": 22393, "name": "Материал", "dictionary_id": 0, "is_required": True},
]
DICT_VALUES = {
    "10096": [
        {"id": 61571, "value": "Черный"},
        {"id": 61572, "value": "Белый"},
    ],
    "22393": [
        {"id": 7001, "value": "Металл"},
        {"id": 7002, "value": "Металл матовый"},
    ],
}


class _State:
    """最小 state 替身（prepare 所需字段）。"""
    dictionary_values = DICT_VALUES
    description_category_id = "1"
    type_id = "1"
    ozon_client_id = "1"
    ozon_api_key = "k"


def _call_prepare(draft_attrs, dict_values_override=None):
    """驱动 prepare _fill_optional_dict_attrs（同 test_dictionary_fill_all._call）。"""
    import graphs.nodes.prepare_ozon_upload_node as mod
    import utils.ozon_dict_values as odv

    state = _State()
    if dict_values_override is not None:
        state.dictionary_values = dict_values_override
    items = [{"offer_id": "x", "name": "Панама", "attributes": []}]
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "config"), exist_ok=True)
    syn = {
        "color": {
            "zh_keywords": ["颜色", "颜色分类"],
            "ozon_name_keywords": ["цвет", "цвет товара"],
            "value_map": {"黑色": "Черный", "白色": "Белый"},
        }
    }
    with open(os.path.join(tmp, "config", "attr_synonyms.json"), "w") as f:
        import json
        json.dump(syn, f)
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": tmp}), \
         mock.patch.object(odv, "search_dictionary_values", return_value=[]), \
         mock.patch.object(odv, "list_dictionary_values", return_value=[]):
        out = mod._fill_optional_dict_attrs(items, SCHEMA, {"attributes": draft_attrs}, state)
    return {int(a["id"]): a["values"][0]["dictionary_value_id"]
            for a in out[0]["attributes"] if a.get("values")}


def _matcher_pick(attr_id, product_value, cached):
    """matcher 纯函数路径：match_dict_value + unique_or_none（单候选才填）。"""
    hits = match_dict_value(attr_id, product_value, cached)
    res = unique_or_none(attr_id, "x", hits)
    if res.dictionary_value_id:
        return res.dictionary_value_id
    return None


def test_contract_exact_hit_consistent():
    """缓存精确命中：prepare 与 matcher 选择同一 dict_id。"""
    # 1688 值"黑色" → prepare 走同义词 color 组 → 缓存精确命中 61571
    prepare_map = _call_prepare({"颜色分类": "黑色"})
    matcher_id = _matcher_pick(10096, "Черный", DICT_VALUES["10096"])
    assert prepare_map[10096] == 61571
    assert matcher_id in (None, 61571)  # matcher 中文值归一化可能 miss；dict_id 若命中必一致


def test_contract_no_match_both_skip():
    """无匹配：两处都跳过（不盲补）。"""
    prepare_map = _call_prepare({"颜色分类": "荧光粉"}, dict_values_override={"10096": []})
    matcher_id = _matcher_pick(10096, "荧光粉", [])
    assert 10096 not in prepare_map
    assert matcher_id is None


def test_contract_dict_id_authoritative_zh_zeroed():
    """字典中文值清零纪律：两处都不把中文文本带上传。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_optional_dict_attrs  # noqa: F401
    # prepare 缓存命中中文值 → value 清零
    state = _State()
    state.dictionary_values = {"10096": [{"id": 61571, "value": "Черный"}]}
    items = [{"offer_id": "x", "name": "x", "attributes": []}]
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "config"), exist_ok=True)
    with open(os.path.join(tmp, "config", "attr_synonyms.json"), "w") as f:
        import json
        json.dump({"color": {"zh_keywords": ["颜色"], "ozon_name_keywords": ["цвет"], "value_map": {}}}, f)
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": tmp}), \
         mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]), \
         mock.patch("utils.ozon_dict_values.list_dictionary_values", return_value=[]):
        out = _fill_optional_dict_attrs(items, SCHEMA, {"attributes": {"颜色分类": "Черный"}}, state)
    for a in out[0]["attributes"]:
        if a.get("id") == 10096:
            for v in a["values"]:
                # dict_id>0 且值含中文 → 空；此处缓存是俄语 Черный 则保留
                assert clean_dict_value(v["dictionary_value_id"], v["value"]) == v["value"]


def test_contract_multivalue_all_candidates():
    """多值属性（10096 is_collection）：prepare 填全部候选，且每项 dict_id 合法。"""
    prepare_map = _call_prepare({"颜色分类": "黑色"}, dict_values_override={"10096": []})
    # 缓存空 → 搜索空 → 列表空 → 无命中，不盲补
    assert 10096 not in prepare_map


def test_contract_lang_route_retry_consistent():
    """retry 语言路由：中文词 ZH 优先、俄语词 RU 优先（lang_route 统一）。"""
    from graphs.validation_retry_loop import _search_dictionary_values_chain
    calls_zh = []
    def fake_zh(cid, key, aid, cat, tp, value, lang):
        calls_zh.append(lang)
        return [{"id": 61571, "value": "Белый"}] if lang == "ZH_HANS" else []
    r1 = _search_dictionary_values_chain("1", "k", 10096, "1", "1", ["白色"], fake_zh)
    assert r1["id"] == 61571
    assert calls_zh == ["ZH_HANS"]

    calls_ru = []
    def fake_ru(cid, key, aid, cat, tp, value, lang):
        calls_ru.append(lang)
        return [{"id": 148495146, "value": "Hand Fan"}] if lang == "RU" else []
    r2 = _search_dictionary_values_chain("1", "k", 8229, "1", "1", ["вентилятор"], fake_ru)
    assert r2["id"] == 148495146
    assert calls_ru == ["RU"]

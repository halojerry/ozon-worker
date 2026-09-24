"""v0.84 A5 回归：schema 驱动 LLM 兜底（prompt 构造 + 提案确定性验证）。

用户驱动：「每个类目的特征属性都缓存了，这样 LLM 就知道怎么填写了」——
缓存 schema 的未填属性清单（中文名+描述+类型+字典标记）交给 vision LLM 提案，
提案过确定性验证才落卡：字典唯一精确/颜色全等放宽/Boolean 直通/自由文本去中文/
数值 sanity + 禁填清单（22390/HS/重量/富媒体/系统派生）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.attr_fill_extras import (
    _LLM_FILL_BANNED_ATTR_IDS,
    apply_llm_schema_fill,
    build_llm_schema_prompt,
)

_SCHEMA = [
    {"id": 4384, "name": "配套", "description": "商品包含的部件", "type": "String",
     "dictionary_id": 0, "is_collection": False},
    {"id": 6829, "name": "炊具的特点", "description": "特点", "type": "String",
     "dictionary_id": 412, "is_collection": True},
    {"id": 11772, "name": "盖子直径，cm", "description": "如有盖子", "type": "String",
     "dictionary_id": 1324, "is_collection": True},
    {"id": 6788, "name": "体积/容量，毫升", "description": "容量", "type": "String",
     "dictionary_id": 1838, "is_collection": False},
    {"id": 21809, "name": "体积，ml（短）", "description": "", "type": "String",
     "dictionary_id": 124412103, "is_collection": False},
    {"id": 23171, "name": "#主题标签", "description": "", "type": "String",
     "dictionary_id": 0, "is_collection": False},  # 禁填（ban 表）
    {"id": 22232, "name": "欧亚经济联盟的HS编码", "description": "", "type": "String",
     "dictionary_id": 124412395, "is_collection": False},  # 禁填
    {"id": 4191, "name": "简介", "description": "", "type": "String",
     "dictionary_id": 0, "is_collection": False},  # 禁填（描述链）
]

_ITEMS = [{"attributes": [], "depth": 140, "width": 140, "height": 140}]
_DRAFT = {"title": "食品级塑料保鲜盒六件套 透明", "attributes": {"材质": "塑料", "箱装数量": "500"},
          "images": ["https://img/1.jpg"]}


def _state(**kw):
    base = dict(description_category_id="17027933", type_id="93712",
                ozon_client_id="1", ozon_api_key="k", user_id="t1",
                token="tk", task_id="task-llm")
    base.update(kw)
    return SimpleNamespace(**base)


def test_prompt_lists_only_unfilled_non_banned():
    prompt, todo = build_llm_schema_prompt(_ITEMS, _SCHEMA, _DRAFT)
    assert prompt and todo
    ids = {a["id"] for a in todo}
    assert 4384 in ids and 6829 in ids and 11772 in ids and 6788 in ids
    assert not ids & _LLM_FILL_BANNED_ATTR_IDS          # 23171/22232/4191 全被 ban
    assert "配套" in prompt and "六件套" in prompt        # 中文名 + 商品证据都在 prompt 里
    assert len(todo) <= 15


def test_prompt_empty_when_all_filled():
    items = [{"attributes": [{"id": a["id"], "values": [{"value": "x"}]} for a in _SCHEMA
                              if a["id"] not in _LLM_FILL_BANNED_ATTR_IDS]}]
    prompt, todo = build_llm_schema_prompt(items, _SCHEMA, _DRAFT)
    assert prompt is None and todo == []


def test_fill_dict_unique_and_boolean_and_free_text(monkeypatch):
    import utils.ozon_dict_values as m
    monkeypatch.setattr(
        m, "search_dictionary_values",
        lambda cid, key, aid, dc, tp, term: {
            "可微波": [{"id": 9001, "value": "Можно в микроволновке"}],
            "不存在的值": [],
            "1500": [{"id": 555, "value": "1500"}],  # 6788 是字典属性(dict=1838)，数值也须字典命中
        }.get(term, []), raising=True)
    raw = '{"fills": [' \
          '{"id": 6829, "value": "可微波"},' \
          '{"id": 4224, "value": "false"},' \
          '{"id": 4384, "value": "контейнер, крышка"},' \
          '{"id": 6788, "value": "1500"},' \
          '{"id": 6829, "value": "不存在的值"}' \
          ']}'
    todo = build_llm_schema_prompt(_ITEMS, _SCHEMA, _DRAFT)[1] + \
        [{"id": 4224, "name": "包括盖子", "desc": "", "type": "Boolean", "dict": 0, "collection": False}]
    items = [dict(_ITEMS[0], attributes=[])]
    out = apply_llm_schema_fill(items, todo, raw, _DRAFT, _state())
    got = {a["id"]: a["values"][0] for a in out[0]["attributes"]}
    assert got[6829]["dictionary_value_id"] == 9001            # 字典唯一命中
    assert got[4224]["value"] == "false"                       # Boolean 直通
    assert got[4384]["value"] == "контейнер, крышка"           # 自由文本俄语
    assert got[6788]["dictionary_value_id"] == 555             # 数值也须过字典（dict=1838）


def test_fill_strips_when_no_dict_hit_or_chinese_or_banned(monkeypatch):
    import utils.ozon_dict_values as m
    monkeypatch.setattr(m, "search_dictionary_values", lambda *a, **k: [], raising=True)
    raw = '{"fills": [' \
          '{"id": 6829, "value": "微波炉适用"},' \
          '{"id": 4384, "value": "塑料盒和盖子"},' \
          '{"id": 23171, "value": "#товар"},' \
          '{"id": 6788, "value": "9999999"},' \
          '{"id": 8513, "value": "9999"}' \
          ']}'
    todo = [  # 8513 不在 todo → 应被剥
        {"id": 6829, "name": "炊具的特点", "type": "String", "dict": 412, "collection": True},
        {"id": 4384, "name": "配套", "type": "String", "dict": 0, "collection": False},
        {"id": 6788, "name": "体积/容量，毫升", "type": "String", "dict": 1838, "collection": False},
    ]
    out = apply_llm_schema_fill([{"attributes": []}], todo, raw, _DRAFT, _state())
    got = {a["id"] for a in out[0]["attributes"]}
    assert 6829 not in got        # 字典无命中 → 剥
    assert 4384 not in got        # 自由文本含中文 → 剥
    assert 23171 not in got       # 禁填表 → 剥
    assert 6788 not in got        # 数值超 sanity → 剥
    assert 8513 not in got        # 不在待填单 → 剥


def test_fill_rejects_malformed_and_never_overwrites():
    todo = [{"id": 4384, "name": "配套", "type": "String", "dict": 0, "collection": False}]
    # 非 JSON / 空 → 原样返回
    assert apply_llm_schema_fill([{"attributes": []}], todo, "мусор", _DRAFT, _state())[0]["attributes"] == []
    # 已填不覆盖
    items = [{"attributes": [{"id": 4384, "values": [{"dictionary_value_id": 0, "value": "базовый"}]}]}]
    out = apply_llm_schema_fill(items, todo, '{"fills": [{"id": 4384, "value": "другой"}]}', _DRAFT, _state())
    assert out[0]["attributes"][0]["values"][0]["value"] == "базовый"


def test_fill_color_exact_equality_relaxation(monkeypatch):
    import utils.ozon_dict_values as m
    monkeypatch.setattr(
        m, "search_dictionary_values",
        lambda cid, key, aid, dc, tp, term: (
            [{"id": 61572, "value": "прозрачный"}, {"id": 61, "value": "прозрачный с узором"}]
            if term == "прозрачный" else []), raising=True)
    todo = [{"id": 10096, "name": "商品颜色", "type": "String", "dict": 1494, "collection": False}]
    out = apply_llm_schema_fill([{"attributes": []}], todo,
                                '{"fills": [{"id": 10096, "value": "прозрачный"}]}', _DRAFT, _state())
    got = out[0]["attributes"][0]["values"][0]
    assert got["dictionary_value_id"] == 61572   # 全等那条越过唯一性（v082 T2 语义）


def test_chinese_free_text_translated_then_filled(monkeypatch):
    """v0.80 gate 实证（护手霜 8048 / 调料罐 4384）：LLM 用 1688 中文证据提案，
    值有效只是语言错——走一次翻译重验（fail-closed）而非直接剥除。"""
    import utils.attr_fill_extras as m
    monkeypatch.setattr(m, "_translate_ru",
                        lambda text, token: "20 баночек; 34 наклейки", raising=True)
    todo = [{"id": 4384, "name": "配套", "type": "String", "dict": 0, "collection": False}]
    out = apply_llm_schema_fill([{"attributes": []}], todo,
                                '{"fills": [{"id": 4384, "value": "20个罐子；34张贴纸"}]}',
                                _DRAFT, _state())
    got = out[0]["attributes"][0]["values"][0]
    assert got["value"] == "20 баночек; 34 наклейки"


def test_chinese_free_text_dropped_when_translation_fails(monkeypatch):
    """翻译失败/译文仍含中文 → 照剥（宁缺红线不变）。"""
    import utils.attr_fill_extras as m
    monkeypatch.setattr(m, "_translate_ru", lambda text, token: "", raising=True)
    todo = [{"id": 4384, "name": "配套", "type": "String", "dict": 0, "collection": False}]
    out = apply_llm_schema_fill([{"attributes": []}], todo,
                                '{"fills": [{"id": 4384, "value": "20个罐子"}]}',
                                _DRAFT, _state())
    assert out[0]["attributes"] == []

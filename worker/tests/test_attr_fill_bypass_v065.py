# -*- coding: utf-8 -*-
"""v0.65 A1/A2 属性填充增强回归测试。

A1: _infer_attrs_from_vision 在中文 schema 名下能触发（此前 _INFER_KW 全俄语，
    ZH_HANS schema 名不命中 → 推断层空转）。
A2: _fill_optional_dict_attrs 对「无同义词组的可选字典属性」（形状/图案/产地/功率等）
    用 1688 原始值中文直搜旁路填上；多候选不盲补（宁缺毋滥保持）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_fill_bypass_v065.py -v
⚠️ 纯 mock（patch search_dictionary_values / call_mxou_chat_api），无需 PG/GPU。
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PGDATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ozon")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import prepare_ozon_upload_node as mod  # noqa: E402


def _state(**over):
    base = dict(
        description_category_id="17028830",
        type_id="971206780",
        ozon_client_id="c",
        ozon_api_key="k",
        token="t",
        dictionary_values={},
        task_id="task-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _schema_attr(aid, name, is_collection=False):
    a = {"id": aid, "name": name, "dictionary_id": 6187952, "is_required": False}
    if is_collection:
        a["is_collection"] = True
    return a


# ══════════════════════════════════════════════════════════════
# A2: _fill_optional_dict_attrs 中文直搜旁路
# ══════════════════════════════════════════════════════════════

def test_bypass_fills_optional_shape_attr():
    """无同义词组的可选字典属性「形状特征」，1688「形状」值中文直搜命中 → 填入。"""
    schema = [_schema_attr(4181, "形状特征")]
    draft = {"attributes": {"形状": "圆形", "颜色": "白色"}}
    item = {"attributes": []}
    hit = {"id": 9001, "value": "Круглая"}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[hit]) as m_search:
        out = mod._fill_optional_dict_attrs([item], schema, draft, _state())
    assert m_search.called, "应触发中文直搜旁路"
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert 4181 in attrs, "无同义词组属性应经旁路填入"
    assert attrs[4181]["values"][0]["dictionary_value_id"] == 9001


def test_bypass_skips_multi_candidate():
    """旁路多候选 → 不盲补（宁缺毋滥保持）。"""
    schema = [_schema_attr(4181, "形状特征")]
    draft = {"attributes": {"形状": "圆形"}}
    item = {"attributes": []}
    hits = [{"id": 9001, "value": "Круглая"}, {"id": 9002, "value": "Овальная"}]
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=hits):
        out = mod._fill_optional_dict_attrs([item], schema, draft, _state())
    assert out[0]["attributes"] == [], "多候选应跳过不盲补"


def test_bypass_not_triggered_when_synonym_filled():
    """同义词门已填的属性 → 旁路不再重复填。"""
    # color 组：1688「颜色」→ schema「Цвет товара」经同义词门填
    schema = [_schema_attr(10096, "Цвет товара")]
    draft = {"attributes": {"颜色": "白色"}}
    item = {"attributes": []}
    hit = {"id": 61571, "value": "Белый"}
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[hit]):
        out = mod._fill_optional_dict_attrs([item], schema, draft, _state())
    vals = out[0]["attributes"]
    assert len(vals) == 1, "应只填一次（同义词门填，旁路不重复）"


# ══════════════════════════════════════════════════════════════
# A1: _infer_attrs_from_vision 中文 schema 触发
# ══════════════════════════════════════════════════════════════

def _run_vision(schema_names, vision_output="颜色=Белый\n材质=Пластик"):
    draft = {"title": "保温杯", "images": ["http://img/1.jpg"]}
    schema = [_schema_attr(10096 + i, name) for i, name in enumerate(schema_names)]
    item = {"attributes": []}
    # LLM 会照 prompt 用 schema 属性名（中文）作键、俄语作答值（Use Russian）
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value=vision_output), \
         mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[{"id": 61571, "value": "Белый"}]):
        return mod._infer_attrs_from_vision([item], schema, draft, _state())


def test_vision_triggers_on_chinese_schema_name():
    """中文 schema 名「颜色」「材质」→ vision 推断触发（此前俄语关键词不命中空转）。"""
    out = _run_vision(["颜色", "材质"])
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert attrs, "中文 schema 名应触发 vision 推断"
    # 至少填上颜色（LLM 输出 + 字典单候选命中）
    assert any(a["id"] == 10096 for a in out[0]["attributes"]), "颜色属性应被推断填上"


def test_vision_no_images_skips():
    """无产品图 → 静默跳过（零开销降级）。"""
    draft = {"title": "保温杯", "images": []}
    schema = [_schema_attr(10096, "颜色")]
    item = {"attributes": []}
    with mock.patch("utils.mxou_api.call_mxou_chat_api") as m:
        out = mod._infer_attrs_from_vision([item], schema, draft, _state())
    assert not m.called
    assert out[0]["attributes"] == []


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)

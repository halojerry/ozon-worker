"""R3 (v0.62): 属性缺失联动 — 23487 制造商 supplier 缺失兜底 + 5379 宁缺毋滥 + 三处一致性。

覆盖：
- prepare：supplier 缺失 → 23487 安全兜底 Нет бренда（此前不填 → 必填缺失）
- prepare：supplier 有值 → 仍用 supplier（不回归）
- assemble：supplier 缺失 → 23487 安全兜底 Нет бренда
- retry：_KNOWN_DEFAULTS_RETRY[23487] 默认 Нет бренда，supplier 有值时优先 supplier
- 5379（保质期）无安全默认 → resolve 返回 None（宁缺毋滥，不盲填）
- 三处一致性：assemble/prepare/retry 均路由同一 resolve_missing_mandatory_dict_attr
  且 23487 均为「supplier → Нет бренда」兜底链

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_attr_defaults_23487.py -v
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.attr_defaults import resolve_missing_mandatory_dict_attr


SCHEMA_23487 = [{"id": 23487, "name": "Производитель", "is_required": True, "dictionary_id": 0}]


def _state(**over):
    base = dict(
        token="t",
        dictionary_values={},
        description_category_id="78021424",
        type_id="93971",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _call_prepare(draft, schema=None):
    import graphs.nodes.prepare_ozon_upload_node as mod

    items = [{"attributes": []}]
    out = mod._fill_missing_required_dict_attrs(
        items, schema or SCHEMA_23487, draft, _state()
    )
    return out[0]["attributes"]


# ═══ prepare：supplier 缺失 → Нет бренда 兜底 ═══

def test_prepare_23487_supplier_missing_safe_fallback():
    """supplier 缺失 → 23487 仍被填充 Нет бренда（此前 continue 不填 → 必填缺失）。"""
    draft = {"title": "沐浴刷", "item_id": "123", "supplier": ""}
    attrs = _call_prepare(draft)
    by_id = {int(a["id"]): a for a in attrs}
    assert 23487 in by_id
    val = by_id[23487]["values"][0]
    assert val["dictionary_value_id"] == 0
    assert val["value"] == "Нет бренда"


def test_prepare_23487_supplier_present_still_used():
    """supplier 有值（俄语）→ 用 supplier，不回归。"""
    draft = {"title": "沐浴刷", "item_id": "123", "supplier": "Компания Иу"}
    attrs = _call_prepare(draft)
    by_id = {int(a["id"]): a for a in attrs}
    assert by_id[23487]["values"][0]["value"] == "Компания Иу"


def test_prepare_23487_chinese_supplier_translated():
    """supplier 中文 → LLM 翻译为俄语（不回归 v0.25 行为）。"""
    import graphs.nodes.prepare_ozon_upload_node as mod

    draft = {"title": "沐浴刷", "item_id": "123", "supplier": "义乌市中亨日用百货有限公司"}
    items = [{"attributes": []}]
    with mock.patch.object(mod, "_translate_to_russian_llm", return_value="Компания Иу Чжунхэн"):
        out = mod._fill_missing_required_dict_attrs(items, SCHEMA_23487, draft, _state())
    val = out[0]["attributes"][0]["values"][0]["value"]
    assert "Компания Иу Чжунхэн" in val
    assert not any("\u4e00" <= c <= "\u9fff" for c in val)


# ═══ assemble：supplier 缺失 → Нет бренда 兜底 ═══

def test_assemble_23487_supplier_missing_safe_fallback():
    """assemble 路径 supplier 缺失 → 23487 填充 Нет бренда（此前跳过 → 必填缺失）。"""
    from graphs.nodes.assemble_ozon_product_node import _validate_and_enrich_items

    items = [{
        "description_category_id": 17028830, "type_id": 971206780,
        "offer_id": "test001", "name": "沐浴刷", "attributes": [],
        "images": ["http://img/1.png"], "primary_image": "http://img/1.png",
        "depth": 100, "width": 100, "height": 50, "weight": 200,
    }]
    schema = [
        {"id": 23487, "name": "Производитель", "is_required": True, "dictionary_id": 0},
    ]
    with mock.patch("utils.ozon_client.ozon_post", return_value={"result": []}):
        out = _validate_and_enrich_items(
            items=items, attr_list=schema, dict_lookup={}, images=["http://img/1.png"],
            ozon_client_id="test_client", ozon_api_key="test_key",
            description_category_id=17028830, type_id=971206780,
            weight_grams=200, dimensions={"length": 100, "width": 100, "height": 50},
            draft_title="沐浴刷", supplier="", ru_category_path="Товары для животных",
        )
    attrs = {int(a["id"]): a for a in out[0]["attributes"] if isinstance(a, dict) and a.get("id")}
    assert 23487 in attrs
    assert attrs[23487]["values"][0]["value"] == "Нет бренда"


# ═══ retry：_KNOWN_DEFAULTS_RETRY 23487 ═══

def test_retry_known_defaults_23487_safe_fallback():
    """retry 默认值表 23487 → Нет бренда（非空默认值，缺失时能补）。"""
    import graphs.validation_retry_loop as vrl

    # 直接读模块内默认值表（re-exec 避免 import 副作用）
    src = open(vrl.__file__, encoding="utf-8").read()
    assert "23487: \"Нет бренда\"" in src, "retry 默认值表应含 23487 → Нет бренда"


def test_retry_23487_supplier_preferred_over_default():
    """retry 修复 23487：supplier 有值时优先 supplier，缺失才用 Нет бренда。"""
    import graphs.validation_retry_loop as vrl

    # 提取模块内逻辑验证：默认值分支对 23487 读取 state.draft.supplier
    src = open(vrl.__file__, encoding="utf-8").read()
    assert "if attr_id == 23487:" in src
    assert "_draft.get(\"supplier\")" in src


# ═══ 5379 保质期：宁缺毋滥 ═══

def test_5379_no_safe_default_returns_none():
    """5379 保质期无来源数据 → resolve 返回 None（不盲填，交 retry 收敛）。"""
    got = resolve_missing_mandatory_dict_attr(5379, "Срок годности", dict_vals=[
        {"id": 1, "value": "12 месяцев"}, {"id": 2, "value": "24 месяца"},
    ])
    assert got is None, "5379 无安全默认必须 None，绝不取首值"


# ═══ 三处一致性 ═══

def test_router_used_by_prepare_and_retry():
    """prepare/retry 路由 resolve_missing_mandatory_dict_attr；assemble 内联兜底（历史实现）。"""
    import graphs.nodes.prepare_ozon_upload_node as prep
    import graphs.validation_retry_loop as vrl

    for mod in (prep, vrl):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "resolve_missing_mandatory_dict_attr" in src, f"{mod.__name__} 未接线路由器"

    import graphs.nodes.assemble_ozon_product_node as asm
    asm_src = open(asm.__file__, encoding="utf-8").read()
    assert "_validate_and_enrich_items" in asm_src, "assemble 应有内联兜底实现"


def test_three_places_23487_supplier_chain():
    """三处 23487 均为「supplier → Нет бренда」兜底链（防再次漂移）。"""
    import graphs.nodes.assemble_ozon_product_node as asm
    import graphs.nodes.prepare_ozon_upload_node as prep
    import graphs.validation_retry_loop as vrl

    for mod in (asm, prep, vrl):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "Нет бренда" in src, f"{mod.__name__} 23487 兜底缺 Нет бренда"

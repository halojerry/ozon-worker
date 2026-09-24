"""feat/attr-fill-max-v1 三期回归：标题证据词 + 类目级保守默认 + 数值派生。

对账驱动（2026-09-24，13 卡/11 类目 fill 审计）：
  材料 6/9 类目缺（证据在 1688 标题里）、保证 9/9 全缺、颜色 5/9 缺
  → A1 标题证据词合成伪 draft.attributes 走既有同义词安全链；
  → A2 类目默认（保证/目标受众/性别兜底）白名单 + 字典精确命中才填；
  → A3 数值派生（容量 ml/每包数量）+ 尺寸串。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.attr_fill_extras import (
    _title_numeric_facts,
    apply_class_defaults_and_numerics,
    augment_draft_with_title_evidence,
)


def _state(**kw):
    base = dict(
        description_category_id="17027933", type_id="93712",
        ozon_client_id="1", ozon_api_key="k", user_id="t1",
        token="", task_id="task-x", dictionary_values={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── A1 标题证据词 ────────────────────────────────────────────

def test_augment_material_from_title():
    d = augment_draft_with_title_evidence(
        {"title": "厂家加厚无磁不锈钢沥水篮密孔双耳多用篮", "attributes": {}})
    assert d["attributes"].get("材质") == "不锈钢"

    # 纳米玻璃 → 玻璃（搓脚板实measured标题）
    d2 = augment_draft_with_title_evidence(
        {"title": "出日本单 黑科技！去死皮！纳米玻璃搓脚板", "attributes": {}})
    assert d2["attributes"].get("材质") == "玻璃"
    # 黑科技已剥 → 不误判颜色
    assert "颜色" not in d2["attributes"]

    # 塑料 / 硅胶 / 竹 / 陶瓷
    assert augment_draft_with_title_evidence(
        {"title": "双层塑料洗菜盆大号", "attributes": {}})["attributes"]["材质"] == "塑料"
    assert augment_draft_with_title_evidence(
        {"title": "食品级硅胶折叠保鲜盒", "attributes": {}})["attributes"]["材质"] == "硅胶"
    assert augment_draft_with_title_evidence(
        {"title": "竹制厨房筷子", "attributes": {}})["attributes"]["材质"] == "竹"


def test_augment_does_not_override_real_evidence():
    d = augment_draft_with_title_evidence(
        {"title": "不锈钢沥水篮", "attributes": {"材质": " PP食品级 "}})
    assert d["attributes"]["材质"] == " PP食品级 "


def test_augment_color_giveup_and_variants():
    # 双色 → 放弃颜色
    d = augment_draft_with_title_evidence({"title": "双色收纳盒塑料", "attributes": {}})
    assert "颜色" not in d["attributes"]
    # 单色命中
    d2 = augment_draft_with_title_evidence({"title": "军用绿色防水收纳袋", "attributes": {}})
    assert d2["attributes"].get("颜色") == "绿色"
    # 透明（食品容器高频）
    d3 = augment_draft_with_title_evidence({"title": "透明塑料保鲜盒", "attributes": {}})
    assert d3["attributes"].get("颜色") == "透明"


def test_augment_shape_and_gender():
    d = augment_draft_with_title_evidence(
        {"title": "方形透明收纳盒 男女通用", "attributes": {}})
    assert d["attributes"].get("形状") == "方形"
    assert d["attributes"].get("性别") == "男女通用"


def test_augment_no_evidence_no_change():
    d = augment_draft_with_title_evidence({"title": "厨房多功能神器", "attributes": {}})
    assert "attributes" not in d or not d.get("attributes")
    d2 = augment_draft_with_title_evidence({"title": ""})
    assert "attributes" not in d2


# ── A3 数值派生 ──────────────────────────────────────────────

def test_numeric_facts_volume_and_count():
    assert _title_numeric_facts("大容量2L保鲜盒")["volume_ml"] == 2000
    assert _title_numeric_facts("Refrigerator Organizer, 5000 ml, 2 pcs")["volume_ml"] == 5000
    assert _title_numeric_facts("6件套厨房工具")["package_count"] == 6
    # 中文数字（1688 标题高频形态）
    assert _title_numeric_facts("食品级塑料叠层六件套保鲜盒")["package_count"] == 6
    assert _title_numeric_facts("十五件套厨房工具")["package_count"] == 15
    assert _title_numeric_facts("二十支装")["package_count"] == 20
    assert _title_numeric_facts("2 pcs 装")["package_count"] == 2
    f = _title_numeric_facts("304不锈钢沥水篮 直径20.5厘米")
    assert "volume_ml" not in f and "package_count" not in f


# ── A2 类目默认 + A3 属性落点 ─────────────────────────────────

_SCHEMA = [
    {"id": 10400, "name": "保证", "dictionary_id": 46700526, "is_required": False},
    {"id": 4224, "name": "目标受众", "dictionary_id": 0, "is_required": False},
    {"id": 6788, "name": "体积/容量，毫升", "dictionary_id": 1838, "is_required": False},
    {"id": 8513, "name": "每包数量,pcs", "dictionary_id": 0, "is_required": False},
    {"id": 4382, "name": "尺寸，毫米", "dictionary_id": 0, "is_required": False},
]


def _patch_dict_search(monkeypatch, hits_by_term):
    import utils.ozon_dict_values as m
    monkeypatch.setattr(
        m, "search_dictionary_values",
        lambda cid, key, aid, dc, tp, term: hits_by_term.get(term, []), raising=True)


def test_class_default_fills_on_exact_hit(monkeypatch):
    _patch_dict_search(monkeypatch, {"нет гарантии": [{"id": 12345, "value": "нет гарантии"}]})
    items = [{"attributes": []}]
    out = apply_class_defaults_and_numerics(
        items, _SCHEMA, {"title": "塑料保鲜盒"}, _state())
    got = {a["id"]: a["values"][0] for a in out[0]["attributes"]}
    assert got[10400] == {"dictionary_value_id": 12345, "value": "нет гарантии"}
    # 无候选命中的目标受众 → 不填
    assert 4224 not in got


def test_class_default_skips_when_no_exact_hit(monkeypatch):
    _patch_dict_search(monkeypatch, {"нет гарантии": [{"id": 1, "value": "1 год"}]})
    out = apply_class_defaults_and_numerics(
        [{"attributes": []}], _SCHEMA, {"title": "塑料保鲜盒"}, _state())
    assert not any(a["id"] == 10400 for a in out[0]["attributes"])


def test_gender_default_and_title_signal(monkeypatch):
    # 标题无人群信号 → Унисекс 兜底
    _patch_dict_search(monkeypatch, {"Унисекс": [{"id": 77, "value": "Унисекс"}]})
    schema = [{"id": 4718, "name": "Пол", "dictionary_id": 5, "is_required": False}]
    out = apply_class_defaults_and_numerics(
        [{"attributes": []}], schema, {"title": "纳米玻璃搓脚板"}, _state())
    assert any(a["id"] == 4718 for a in out[0]["attributes"])

    # 标题有「女款」→ 默认让位证据链，不抢填
    out2 = apply_class_defaults_and_numerics(
        [{"attributes": []}], schema, {"title": "女款手链"}, _state())
    assert not any(a["id"] == 4718 for a in out2[0]["attributes"])

    # exact_names 保护：名字只近似不含 → 不填（防 'пол' 误击 'полотенце'）
    schema2 = [{"id": 999, "name": "Размер полотенца", "dictionary_id": 7, "is_required": False}]
    out3 = apply_class_defaults_and_numerics(
        [{"attributes": []}], schema2, {"title": "毛巾"}, _state())
    assert not any(a["id"] == 999 for a in out3[0]["attributes"])


def test_volume_and_count_and_dims_fill(monkeypatch):
    _patch_dict_search(monkeypatch, {})
    items = [{"attributes": [], "depth": 140, "width": 140, "height": 140}]
    out = apply_class_defaults_and_numerics(
        items, _SCHEMA, {"title": "透明塑料保鲜盒 大容量1.5L 2个装"}, _state())
    got = {a["id"]: a["values"][0].get("value") for a in out[0]["attributes"]}
    assert got[6788] == "1500"      # 体积/容量，毫升
    assert got[8513] == "2"         # 每包数量
    assert got[4382] == "140x140x140"  # 尺寸，毫米


def test_no_double_fill_when_present(monkeypatch):
    _patch_dict_search(monkeypatch, {"нет гарантии": [{"id": 12345, "value": "нет гарантии"}]})
    items = [{"attributes": [{"id": 10400, "values": [{"dictionary_value_id": 9, "value": "1 год"}]}]}]
    out = apply_class_defaults_and_numerics(items, _SCHEMA, {"title": "塑料盒"}, _state())
    vals = [a for a in out[0]["attributes"] if a["id"] == 10400]
    assert len(vals) == 1 and vals[0]["values"][0]["value"] == "1 год"


# ── 链路贯通：伪属性走既有同义词链 ─────────────────────────────

def test_pseudo_attr_flows_synonym_chain(monkeypatch):
    import utils.ozon_dict_values as m
    monkeypatch.setattr(
        m, "search_dictionary_values",
        lambda cid, key, aid, dc, tp, term: (
            [{"id": 4242, "value": "Пластик"}] if term == "塑料" else []))
    from graphs.nodes.prepare_ozon_upload_node import _fill_optional_dict_attrs
    schema = [{"id": 21832, "name": "主要容器材料", "dictionary_id": 124412115,
               "is_required": False, "is_collection": False, "max_value_count": 1}]
    draft = augment_draft_with_title_evidence(
        {"title": "双层塑料洗菜盆大号菜篮子", "attributes": {}})
    items = [{"attributes": [{"id": 85, "values": [{"dictionary_value_id": 1, "value": "Нет бренда"}]}]}]
    out = _fill_optional_dict_attrs(items, schema, draft, _state())
    got = {a["id"]: a for a in out[0]["attributes"]}
    assert 21832 in got
    assert got[21832]["values"][0]["dictionary_value_id"] == 4242
    assert got[21832]["values"][0]["value"] == "Пластик"

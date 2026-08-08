"""T2: 8229(Тип/类型)必填属性 follow 路径匹配修复回归测试。

对抗验证后的最终方案（v0.31 T2）：
- 修复点在 attr_defaults.resolve_missing_mandatory_dict_attr（L246-289），
  统一 3 条填充路径纪律（_match_product_attr / _validate_and_enrich_items /
  prepare post-fill）。
- 8229 字典属性：优先按 type_id 匹配（值 id == 类目 type_id，实测手持风扇
  148495146 / 杀虫剂 99385），绝不取第一个字典值（套娃错配）。
- 干扰属性名黑名单（专利类型/光源类型/开关类型/风扇类型/造型类型…）——
  名称含「类型/тип」但非 8229 产品类型本身，绝不套用 8229 的 type_id 匹配；
  但【纯「类型」本身绝不拦截】（test_language_routing.py 合法用例「类型: 杀虫剂」）。
- 判别词交叉验证：标题含判别词（桌面/手持/挂脖/落地/风扇灯/夹式/涡轮…）
  且与 type_id 匹配结果不一致 → 高置信错配 → 跳过（不盲填）；2-gram 泛词
  （如「风扇」交集恒过）禁用。
- retry 纪律：字典属性绝不盲补首值；未匹配 → 跳过（不写空/不写首值）。

用例：
(a) follow 信封 8229 字典属性经 attr_defaults type_id 匹配解析出 dict_id>0
(b) 干扰属性名（专利类型/风扇类型等）不映射 8229
(c) 判别词交叉验证：标题含判别词且与 type_id 匹配结果不一致 → 跳过
(d) 纯「类型」关键词始终参与匹配（不被黑名单拦截）

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python3 tests/test_8229_follow.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

from utils.attr_defaults import resolve_missing_mandatory_dict_attr

# 风扇类目 17039635/91443（任务已实测 schema: is_aspect=False, required=True, dict_id=1960）
FAN_DC = "17039635"
FAN_TYPE_ID = 91443


def _fan_dict_vals() -> list[dict]:
    """多值字典: 首值是同大类其他小类(套娃), type_id 值在后。"""
    return [
        {"id": 91965, "value": "Матрёшка"},
        {"id": FAN_TYPE_ID, "value": "Вентилятор ручной"},
        {"id": 93735, "value": "Сувенир"},
    ]


def _run_prepare_fill(schema, dict_values, *, draft=None, item_name="Вентилятор",
                      dc=FAN_DC, tp=str(FAN_TYPE_ID)):
    """跑 prepare post-fill（follow 真路径），mock search 防网络。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs
    from types import SimpleNamespace

    state = SimpleNamespace(
        dictionary_values=dict_values,
        description_category_id=dc,
        type_id=tp,
        ozon_client_id="5381204",
        ozon_api_key="key",
    )
    items = [{"offer_id": "x_0", "name": item_name, "attributes": []}]
    d = {"item_id": "x", "title": "手持小风扇"}
    if draft:
        d.update(draft)
    with mock.patch("utils.ozon_dict_values.search_dictionary_values", return_value=[]):
        result = _fill_missing_required_dict_attrs(items, schema, d, state)
    return {int(a["id"]): a for it in result for a in it.get("attributes", [])}


def _attr_map_of(attrs):
    return {int(a["id"]): a for a in attrs}


# ═══ (a) follow 信封 8229 经 type_id 匹配解析出 dict_id>0 ═══
def test_a_follow_8229_resolved_via_type_id():
    """follow 信封: 8229 字典属性在 prepare post-fill 经 type_id 匹配解析出 dict_id>0。

    字典值为俄语(真实 Ozon 返回), 中文标题无法 2-gram 命中 → 只有 type_id
    匹配能解析出正确值（91443）。
    """
    schema = [{"id": 8229, "name": "Тип", "is_required": True, "dictionary_id": 1960}]
    dict_values = {"8229": _fan_dict_vals()}
    attr_map = _run_prepare_fill(schema, dict_values)
    assert 8229 in attr_map, "8229 应被补齐(经 type_id 匹配)"
    assert attr_map[8229]["values"][0]["dictionary_value_id"] == FAN_TYPE_ID, \
        f"8229 应按 type_id={FAN_TYPE_ID} 匹配, 实际 {attr_map[8229]['values'][0]}"


def test_a2_type_id_preferred_over_first_value():
    """type_id 匹配为主: 首值是同大类其他小类(套娃), 绝不取首值。"""
    got = resolve_missing_mandatory_dict_attr(
        8229, "Тип", title_cn="手持小风扇", dict_vals=_fan_dict_vals(), type_id=FAN_TYPE_ID)
    assert got == (FAN_TYPE_ID, "Вентилятор ручной"), f"应命中 type_id, 实际 {got}"


# ═══ (b) 干扰属性名不映射 8229 ═══
def test_b_interfering_type_name_not_mapped_to_8229():
    """干扰属性名(专利类型)名称含「类型」但非 8229 → 不套用 8229 的 type_id 匹配。"""
    vals = [{"id": 148495146, "value": "手持风扇"}, {"id": 90001, "value": "外观专利"}]
    got = resolve_missing_mandatory_dict_attr(5555, "专利类型", dict_vals=vals, type_id=148495146)
    assert got is None or got[0] != 148495146, \
        f"专利类型不得映射到 8229 的 type_id 值: {got}"


def test_b2_interfering_attr_not_filled_from_type_id_in_prepare():
    """prepare post-fill: 干扰属性(风扇类型)绝不借 8229 的 type_id 填充, 8229 本身正常。"""
    from utils.attr_defaults import is_interfering_type_attr
    assert is_interfering_type_attr(5555, "风扇类型") is True
    assert is_interfering_type_attr(8229, "Тип") is False

    schema = [
        {"id": 8229, "name": "Тип", "is_required": True, "dictionary_id": 1960},
        {"id": 5555, "name": "风扇类型", "is_required": True, "dictionary_id": 77},
    ]
    dict_values = {
        "8229": _fan_dict_vals(),
        # 干扰属性字典值空间里恰好有 id==type_id 的值(模拟 8229 值泄漏)
        "5555": [{"id": FAN_TYPE_ID, "value": "Вентилятор ручной"}, {"id": 70001, "value": "Турбо"}],
    }
    attr_map = _run_prepare_fill(schema, dict_values)
    # 干扰属性不得被 type_id 填充
    if 5555 in attr_map:
        assert attr_map[5555]["values"][0]["dictionary_value_id"] != FAN_TYPE_ID, \
            f"干扰属性(风扇类型)不得借 8229 的 type_id={FAN_TYPE_ID} 填充"
    # 8229 本身正常按 type_id 填充(不被黑名单波及)
    assert 8229 in attr_map, "8229 必须仍被补齐"
    assert attr_map[8229]["values"][0]["dictionary_value_id"] == FAN_TYPE_ID


# ═══ (c) 判别词交叉验证 ═══
def test_c_discriminant_word_conflict_skips_mismatch():
    """标题含判别词且与 type_id 匹配结果不一致 → 跳过(高置信错配)。"""
    vals = [
        {"id": 91965, "value": "套娃"},
        {"id": 148495146, "value": "手持风扇"},
        {"id": 93735, "value": "纪念品"},
    ]
    # 标题明确「桌面」→ 与 type_id 匹配「手持风扇」不一致 → None
    got = resolve_missing_mandatory_dict_attr(
        8229, "类型", title_cn="桌面小风扇USB", dict_vals=vals, type_id=148495146)
    assert got is None, f"桌面风扇 vs 手持风扇 type_id 错配应跳过, 实际 {got}"
    # 标题含「手持」→ 与 type_id 匹配一致 → 命中
    ok = resolve_missing_mandatory_dict_attr(
        8229, "类型", title_cn="手持制冷迷你小风扇", dict_vals=vals, type_id=148495146)
    assert ok == (148495146, "手持风扇"), f"一致应命中, 实际 {ok}"


def test_c2_discriminant_cross_language():
    """判别词跨语言: 中文「手持」↔ 俄语「ручной」同形态一致; 「桌面」↔「ручной」错配。"""
    vals = [{"id": FAN_TYPE_ID, "value": "Вентилятор ручной"}]
    ok = resolve_missing_mandatory_dict_attr(
        8229, "Тип", title_cn="手持小风扇", dict_vals=vals, type_id=FAN_TYPE_ID)
    assert ok == (FAN_TYPE_ID, "Вентилятор ручной"), f"同形态应命中, 实际 {ok}"
    bad = resolve_missing_mandatory_dict_attr(
        8229, "Тип", title_cn="桌面小风扇", dict_vals=vals, type_id=FAN_TYPE_ID)
    assert bad is None, f"桌面 vs ручной 错配应跳过, 实际 {bad}"


# ═══ (d) 纯「类型」关键词始终参与匹配 ═══
def test_d_pure_type_keyword_not_blacklisted():
    """纯「类型」关键词始终参与匹配(不被干扰黑名单拦截)。"""
    from utils.attr_defaults import is_interfering_type_attr
    vals = [{"id": 99385, "value": "Инсектициды"}, {"id": 90001, "value": "прочее"}]
    got = resolve_missing_mandatory_dict_attr(8229, "类型", dict_vals=vals, type_id=99385)
    assert got == (99385, "Инсектициды"), f"纯类型必须命中, 实际 {got}"
    # 黑名单语义
    assert is_interfering_type_attr(8229, "类型") is False
    assert is_interfering_type_attr(8229, "Тип") is False
    assert is_interfering_type_attr(5555, "专利类型") is True
    assert is_interfering_type_attr(5555, "Тип патента") is True


# ═══ 纪律: 无 type_id 无关键词多值 → 不盲补首值 ═══
def test_discipline_no_first_value_fallback():
    """无 type_id 且无标题关键词 → 多值返回 None, 不取第一个(宁缺毋滥)。"""
    vals = [{"id": 91965, "value": "Матрёшка"}, {"id": 93735, "value": "Сувенир"}]
    assert resolve_missing_mandatory_dict_attr(8229, "Тип", dict_vals=vals) is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: 异常")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)

"""v0.69 R2b overlap 判据修复测试（TDD RED→GREEN）。

生产取证：10 单暖风机/取暖器全部被 R2b 阻断——dc 落 17039635（空调设备，域对），
树内有近叶（加热器 91448 / 水暖风机 971109685 / 电加热器 96040），但放行判据只认
源词 vs full_path 的 ≥2 字非泛词字面命中：「暖风机」vs「加热器」零字面重叠 →
LLM 看图选了也全阻断；西里尔源词 vs 中文树路径重叠结构性趋空。

三点判据升级（红线不动：R1 veto / R2b 仲裁池构造边界 / search_kw 恒非权威 /
Step 6.5 豁免语义 / _is_skill_authoritative）：
  a. 叶子名子串命中（水暖风机 ⊇ 暖风机，含修饰词剥离后的核心 token）；
  b. 西里尔源词 → 候选 dc+tp 查 RU 树路径做同样非泛词 overlap；
  c. LLM 带图明确选中且与池内源词命中候选同 top_level 大类 → 视为确认
     （LLM abstain/解析失败/建议词仍阻断）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_r2b_overlap_v069.py -q
全部纯 mock / 纯函数，不连真实 PG。
"""
import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── 暖风机生产取证场景锚（dc/tp 取自真实树近叶）──
_ADOPED_AC = {  # 拟采纳：空调设备（域对但非近叶）
    "description_category_id": 17039635, "type_id": 90414,
    "node_name": "空调", "full_path": "家用电器 > 空调设备 > 空调",
    "similarity": 0.46,
}
_HEATER = {  # LLM vision 选中：加热器（与源词零字面重叠）
    "description_category_id": 91448, "type_id": 90415,
    "node_name": "加热器", "full_path": "家用电器 > 空调设备 > 加热器",
    "similarity": 0.38,
}
_WATER_FAN_HEATER = {  # 源词命中候选：水暖风机（叶子 ⊇ 源词「暖风机」）
    "description_category_id": 971109685, "type_id": 90416,
    "node_name": "水暖风机", "full_path": "家用电器 > 空调设备 > 水暖风机",
    "similarity": 0.35,
}
_OTHER_TOP_HIT = {  # 源词命中但在别的顶层大类（域守卫反例）
    "description_category_id": 99001, "type_id": 99002,
    "node_name": "暖风机", "full_path": "工业设备 > 取暖设备 > 暖风机",
    "similarity": 0.30,
}
_IM_DRAFT = {"images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"]}
_NO_IM_DRAFT = {}


# ═══════════════════════════════════════════════════════════════════════
# 判据 a：叶子名子串命中
# ═══════════════════════════════════════════════════════════════════════
def test_a_leaf_substring_hit():
    """水暖风机 ⊇ 暖风机 —— 叶子名子串命中即 overlap。"""
    from graphs.nodes.assemble_ozon_product_node import _leaf_substring_overlap
    ov = _leaf_substring_overlap("水暖风机", ["暖风机 取暖器"])
    assert "暖风机" in ov, f"叶子名子串应命中: {ov}"
    # 无关叶子不误判
    assert _leaf_substring_overlap("加湿器", ["暖风机 取暖器"]) == set()


def test_a_core_token_after_modifier_strip():
    """源词含修饰词（儿童暖风机）→ 剥离后核心 token 暖风机 仍可命中叶子。"""
    from graphs.nodes.assemble_ozon_product_node import _leaf_substring_overlap
    ov = _leaf_substring_overlap("水暖风机", ["儿童暖风机"])
    assert "暖风机" in ov, f"核心 token 应命中: {ov}"


def test_a_confirm_adoption_leaf_passes_end_to_end():
    """暖风机场景：LLM 选中叶子名含源词的候选（水暖风机）→ R2b 放行。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    pool = [_ADOPED_AC, _HEATER, _WATER_FAN_HEATER]
    ov, vision, why = _r2b_confirm_adoption(
        dict(_WATER_FAN_HEATER), pool, "暖风机 取暖器", _NO_IM_DRAFT, query=None,
    )
    assert ov, f"水暖风机应放行: ov={ov}, vision={vision}, why={why}"
    assert vision is False


# ═══════════════════════════════════════════════════════════════════════
# 判据 b：西里尔源词 → RU 树路径 overlap
# ═══════════════════════════════════════════════════════════════════════
class _FakeQueryRU:
    """打桩 get_node：仅 RU 语言返回加热器的 RU 树路径。"""

    def __init__(self, ru_path):
        self._ru_path = ru_path
        self.calls = []

    def get_node(self, dc, tp, language="ZH_HANS"):
        self.calls.append((dc, tp, language))
        if language == "RU" and (dc, tp) == (_HEATER["description_category_id"], _HEATER["type_id"]):
            return {"description_category_id": dc, "type_id": tp,
                    "node_name": "Тепловентилятор", "full_path": self._ru_path}
        return None


def test_b_cyrillic_source_ru_path_passes():
    """西里尔源词 vs 中文候选零字面 → 用 dc+tp 查 RU 树路径做 overlap 放行。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    q = _FakeQueryRU("Бытовая техника > Обогреватели > Тепловентилятор")
    ov, vision, why = _r2b_confirm_adoption(
        dict(_HEATER), [_ADOPED_AC, _HEATER], "тепловентилятор обогреватель",
        _NO_IM_DRAFT, query=q,
    )
    assert "тепловентилятор" in ov, f"RU 路径 overlap 应放行: {ov} / {why}"
    assert vision is False
    # 查询必须按 RU 语言走树
    assert (91448, 90415, "RU") in q.calls


def test_b_cyrillic_ru_mismatch_blocks():
    """RU 路径也不匹配 → 不放行（无图时 vision 不兜）。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    q = _FakeQueryRU("Бытовая техника > Холодильники > Морозильники")
    ov, vision, _why = _r2b_confirm_adoption(
        dict(_HEATER), [_ADOPED_AC, _HEATER], "тепловентилятор обогреватель",
        _NO_IM_DRAFT, query=q,
    )
    assert ov == set() and vision is False


def test_b_ru_generic_words_not_overlap():
    """RU 泛词（для/товары/дома 等）不算 overlap——同中文泛词语义。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    q = _FakeQueryRU("Товары для дома > Прочие товары")
    ov, vision, _why = _r2b_confirm_adoption(
        dict(_HEATER), [_ADOPED_AC, _HEATER], "для дома прочие",
        _NO_IM_DRAFT, query=q,
    )
    assert ov == set() and vision is False, f"RU 泛词不得算 overlap: {ov}"


# ═══════════════════════════════════════════════════════════════════════
# 判据 c：LLM vision 选中 + 同 top_level 大类放行
# ═══════════════════════════════════════════════════════════════════════
def test_c_vision_same_top_confirms_heater():
    """生产主案例：LLM 带图选中加热器（零字面重叠），池内水暖风机（源词命中）
    与其同顶层大类（家用电器）→ 视为 _r2b_confirmed 放行。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    pool = [_ADOPED_AC, _HEATER, _WATER_FAN_HEATER]
    ov, vision, why = _r2b_confirm_adoption(
        dict(_HEATER), pool, "暖风机 取暖器", _IM_DRAFT, query=None,
    )
    assert ov == set()
    assert vision is True, f"同大类 vision 选中应放行: why={why}"


def test_c_vision_requires_images():
    """无图（image_urls 空）时不得走 vision 确认——零字面重叠仍阻断。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    pool = [_ADOPED_AC, _HEATER, _WATER_FAN_HEATER]
    ov, vision, _why = _r2b_confirm_adoption(
        dict(_HEATER), pool, "暖风机 取暖器", _NO_IM_DRAFT, query=None,
    )
    assert ov == set() and vision is False


def test_c_vision_cross_top_still_blocks():
    """池内源词命中候选与 LLM 选中候选顶层大类不同 → 不放行（域守卫）。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    pool = [_ADOPED_AC, _HEATER, _OTHER_TOP_HIT]
    ov, vision, _why = _r2b_confirm_adoption(
        dict(_HEATER), pool, "暖风机 取暖器", _IM_DRAFT, query=None,
    )
    assert ov == set() and vision is False, "跨大类 vision 选中不得放行"


def test_c_vision_no_source_hit_in_pool_blocks():
    """池内无任何源词命中候选 → vision 确认无域锚点，不放行。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    pool = [_ADOPED_AC, _HEATER]
    ov, vision, _why = _r2b_confirm_adoption(
        dict(_HEATER), pool, "暖风机 取暖器", _IM_DRAFT, query=None,
    )
    assert ov == set() and vision is False


def test_abstain_or_suggest_still_blocks():
    """LLM 未选（abstain/解析失败/建议词标记/无 dc）→ 一律不放行。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    pool = [_ADOPED_AC, _HEATER, _WATER_FAN_HEATER]
    for bad in (None, {"_llm_suggest": True, "suggest_keywords": "обогреватель"},
                {"description_category_id": 0, "type_id": 0,
                 "node_name": "x", "full_path": ""}):
        ov, vision, _why = _r2b_confirm_adoption(
            bad, pool, "暖风机 取暖器", _IM_DRAFT, query=None,
        )
        assert ov == set() and vision is False, f"未选中候选必须阻断: {bad}"


# ═══════════════════════════════════════════════════════════════════════
# 回归：旧判据语义 + 红线（R1 敏感子树 veto 一字不动）
# ═══════════════════════════════════════════════════════════════════════
def test_legacy_full_path_overlap_still_works():
    """① full_path 字面 overlap 原判据保持：摩托车后视镜 vs 后视镜。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    confirm = {"description_category_id": 1, "type_id": 2,
               "node_name": "摩托车后视镜", "full_path": "运动与休闲 > 摩托车后视镜"}
    ov, vision, _why = _r2b_confirm_adoption(confirm, [confirm], "后视镜 汽车", _NO_IM_DRAFT)
    assert "后视镜" in ov and vision is False


def test_legacy_modifier_exclusion_unchanged():
    """非泛词剔除语义不变：儿童滑梯不得靠「儿童」overlap 蒙混。"""
    from graphs.nodes.assemble_ozon_product_node import (
        _leaf_substring_overlap, _non_generic_overlap_words,
    )
    assert _non_generic_overlap_words("儿童用品 > 户外玩具 > 儿童滑梯", ["儿童 帽"]) == set()
    assert _leaf_substring_overlap("儿童滑梯", ["儿童 帽"]) == set()


def test_r1_redline_adult_cap_still_vetoed():
    """红线回归：成人帽 vs 18+ 子树仍被 R1 拦死——判据升级不得变成敏感后门。"""
    from utils.ozon_category_query import sensitive_adoption_blocked, sensitive_candidate_filter
    from graphs.nodes.assemble_ozon_product_node import _r1_veto
    candy = {"description_category_id": 200001462, "type_id": 971363842,
             "node_name": "成人糖果", "category_path": "成人用品 > 成人的糖果点心 > 成人糖果"}
    assert _r1_veto(candy, "成人帽 冬季保暖") is True
    assert sensitive_adoption_blocked("成人用品 > 成人的糖果点心 > 成人糖果", "成人帽 保暖") is True
    # R1 候选剔除仍生效：帽源词候选含成人糖果 → 剔除
    hat = {"description_category_id": 17028976, "type_id": 95701,
           "node_name": "儿童帽", "full_path": "儿童用品 > 帽子 > 儿童帽"}
    out = sensitive_candidate_filter([candy, hat], "成人帽 冬季保暖")
    assert 200001462 not in [c["description_category_id"] for c in out]


def test_gate_wiring_uses_new_adoption_judge():
    """R2b 闸必须改走 _r2b_confirm_adoption，且 R1 veto 仍在 R2b 之前（红线顺序）。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    assert src.count("_r2b_confirm_adoption(") >= 2, "闸内未接线新判据"
    i_r1 = src.index("if _r1_veto(category_result")
    assert src.rindex("_r2b_confirm_adoption(") > i_r1, "R1 veto 必须先于 R2b 闸"
    # 红线：R2b 仲裁池构造仍走 _build_r2b_confirm_pool（边界不动）
    assert "_build_r2b_confirm_pool(" in src
    # 红线：search_kw 恒非权威 / Step 6.5 豁免函数原样在位
    assert "def _is_skill_authoritative" in src
    assert "def _step65_consistency_exempt" in src


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

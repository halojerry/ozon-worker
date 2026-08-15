# -*- coding: utf-8 -*-
"""T7b: image_gen_plan 类型选择（受限映射，C3b 冻结）。

验收门（计划 §5 T7b / §2 C3b）：
(a) plan {white_bg:1, scene_1:1} → 仅执行这 2 节点，其余跳过（断言其余节点未调生图 API）
(b) 仅 Phase2 类型 plan（如 {scene_1:1}）→ validate_plan 拒绝（Momus W1：Phase2 依赖 Phase1 参考图）
(c) 默认 plan → 全 10 张回归（节点默认启用，管线行为不变）
(d) plan_to_slots：scene 计数 0-3 展开 + 未知类型（材质/尺寸 v1 置灰）忽略
(e) 不新增节点（断言 10 个既有节点函数 + slot 集合不变）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_gen_plan.py -v
⚠️ 纯 mock（patch 节点模块内绑定的 call_mxou_image_api），无需 PG/GPU。
"""
import logging
import os
import sys
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PGDATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ozon")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.WARNING)

import pytest

from utils.image_gen_plan import (  # noqa: E402
    ALL_SLOTS,
    DEFAULT_PLAN,
    PHASE1_SLOTS,
    plan_to_slots,
    slot_enabled,
    validate_plan,
)

TITLE = "保温杯"
REF_IMAGE = "https://example.com/ref.jpg"
_MOCK_URL = "https://example.com/mock_image.jpg"

# LangGraph 节点签名: node(state, config, runtime)。config 供 slot_enabled/
# _task_id_from_config 读 configurable；runtime.context 节点内未实际使用。
_CONFIG = {"metadata": {"execute_id": "test-run"}}
_RUNTIME = type("FakeRuntime", (), {"context": None})()


def _cfg_with_plan(plan):
    return {"configurable": {"image_gen_plan": plan}}


def _plan_config(plan=None):
    """构造 config：plan=None → 无 image_gen_plan（走默认全开）。"""
    return _cfg_with_plan(plan) if plan is not None else {"configurable": {}}


# ══════════════════════════════════════════════════════════════
# 纯函数层：validate_plan / plan_to_slots / slot_enabled
# ══════════════════════════════════════════════════════════════


def test_validate_plan_rejects_phase2_only():
    """Momus W1：仅 Phase2 类型 plan → 拒绝（提示需至少包含白底图或多角度图）。"""
    for bad in [
        {"scene_1": 1},
        {"main_image": 1},
        {"detail": 1, "comparison": 1, "social_proof": 1, "scene_1": 1},
        {"scene": 3},
        {},
        None,
    ]:
        with pytest.raises(ValueError, match="白底图或多角度图"):
            validate_plan(bad)


def test_validate_plan_rejects_zero_count_phase1():
    """count=0 的白底图/多角度图不视为 Phase1（0 = 不生成）。"""
    with pytest.raises(ValueError, match="白底图或多角度图"):
        validate_plan({"white_bg": 0, "scene_1": 1})
    with pytest.raises(ValueError, match="白底图或多角度图"):
        validate_plan({"multi_angle": 0, "detail": 1})


def test_validate_plan_accepts_phase1():
    """含 white_bg 或 multi_angle（count>=1）→ 通过；未知类型不阻断。"""
    for ok in [
        {"white_bg": 1},
        {"multi_angle": 1},
        {"white_bg": 1, "scene_1": 1},
        {"white_bg": 1, "scene": 3},
        {"material": 1, "white_bg": 1},  # 材质 v1 置灰，plan 里出现不阻断
        DEFAULT_PLAN,
    ]:
        validate_plan(ok)  # 不抛异常即通过


def test_plan_to_slots_scene_count_expansion():
    """scene 类型按 count 展开为 scene_1..scene_N（C3b：场景图计数 0-3）。"""
    assert plan_to_slots({"scene": 3}) == {"scene_1", "scene_2", "scene_3"}
    assert plan_to_slots({"scene": 1}) == {"scene_1"}
    assert plan_to_slots({"scene": 2}) == {"scene_1", "scene_2"}
    assert plan_to_slots({"scene": 5}) == {"scene_1", "scene_2", "scene_3"}  # 上限 3
    assert plan_to_slots({"scene": 0}) == set()


def test_plan_to_slots_direct_slot_keys():
    """白底图:1 + scene_1:1 → 恰好 {white_bg, scene_1}。"""
    assert plan_to_slots({"white_bg": 1, "scene_1": 1}) == {"white_bg", "scene_1"}


def test_plan_to_slots_ignores_unknown_and_zero():
    """未知类型（材质/尺寸，v1 置灰）忽略；count=0 的 slot 不启用。"""
    plan = {"material": 1, "size": 1, "main_image": 0, "white_bg": 1}
    assert plan_to_slots(plan) == {"white_bg"}


def test_default_plan_covers_all_10_slots():
    """默认 plan = 全 10 张：展开后等于 10 个既有 slot（向后兼容）。"""
    assert len(ALL_SLOTS) == 10
    assert plan_to_slots(DEFAULT_PLAN) == set(ALL_SLOTS)


def test_slot_enabled_default_all_on():
    """config 无 image_gen_plan → 默认全开（管线行为不变）。"""
    for slot in ALL_SLOTS:
        assert slot_enabled(_plan_config(), slot) is True


def test_slot_enabled_config_plan_injection():
    """config.configurable.image_gen_plan 注入 → 仅 plan 内 slot 启用。"""
    cfg = _cfg_with_plan({"white_bg": 1, "scene_1": 1})
    for slot in ("white_bg", "scene_1"):
        assert slot_enabled(cfg, slot) is True
    for slot in ("multi_angle", "main_image", "detail", "social_proof",
                 "comparison", "scene_2", "scene_3", "variant_primary_loop"):
        assert slot_enabled(cfg, slot) is False


def test_slot_enabled_malformed_plan_falls_back_default():
    """非法 plan（非 dict）→ 回退默认全开（fail-safe，不静默全禁）。"""
    for bad in ([1, 2], "white_bg", 3):
        cfg = _cfg_with_plan(bad)
        assert slot_enabled(cfg, "white_bg") is True
        assert slot_enabled(cfg, "scene_3") is True


def test_slot_enabled_state_fallback():
    """config 无 plan 时读 state.image_gen_plan（Input schema 字段）。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(
        draft={"title": TITLE}, token="t",
        image_gen_plan={"white_bg": 1, "scene_1": 1},
    )
    cfg = {"configurable": {}}
    assert slot_enabled(cfg, "white_bg", state) is True
    assert slot_enabled(cfg, "scene_1", state) is True
    assert slot_enabled(cfg, "detail", state) is False


# ══════════════════════════════════════════════════════════════
# 节点层：plan 控制执行/跳过（不调生图 API）
# ══════════════════════════════════════════════════════════════


def _node_cases():
    """10 个生图节点的 (节点函数, 模块, 最小合法 state, 输出 attr)。

    state 构造对齐 test_image_gen_title_injection.py（Phase1 节点无需参考图；
    Phase2 节点需 Phase1 参考图；variant 节点需至少 1 个变体）。
    """
    from graphs.state_image_gen import (
        WhiteBgInput, MultiAngleInput, MainImageInput,
        DetailImageInput, SocialProofInput, ComparisonInput,
        Scene1Input, Scene2Input, Scene3Input,
    )
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    import graphs.nodes.white_bg_gen_node as wb
    import graphs.nodes.multi_angle_gen_node as ma
    import graphs.nodes.main_image_gen_node as mi
    import graphs.nodes.detail_gen_node as dt
    import graphs.nodes.social_proof_gen_node as sp
    import graphs.nodes.comparison_gen_node as cp
    import graphs.nodes.scene_1_gen_node as s1
    import graphs.nodes.scene_2_gen_node as s2
    import graphs.nodes.scene_3_gen_node as s3
    import graphs.nodes.variant_primary_loop_node as vp

    return [
        (wb.white_bg_gen_node, wb, WhiteBgInput(draft={"title": TITLE}, token="t"), "white_bg_image"),
        (ma.multi_angle_gen_node, ma, MultiAngleInput(draft={"title": TITLE}, token="t"), "multi_angle_image"),
        (mi.main_image_gen_node, mi, MainImageInput(draft={"title": TITLE}, token="t", white_bg_image=REF_IMAGE), "main_image"),
        (dt.detail_gen_node, dt, DetailImageInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE), "detail_image"),
        (sp.social_proof_gen_node, sp, SocialProofInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE), "social_proof_image"),
        (cp.comparison_gen_node, cp, ComparisonInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE), "comparison_image"),
        (s1.scene_1_gen_node, s1, Scene1Input(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE, scene_context_1="家庭生活场景"), "scene_1_image"),
        (s2.scene_2_gen_node, s2, Scene2Input(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE, scene_context_2="户外休闲场景"), "scene_2_image"),
        (s3.scene_3_gen_node, s3, Scene3Input(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE, scene_context_3="工作办公场景"), "scene_3_image"),
        (vp.variant_primary_loop_node, vp,
         VariantPrimaryLoopInput(variants=[{"name": "v0", "image": REF_IMAGE}], draft={"title": TITLE}, token="t"),
         "variant_primary_images"),
    ]


def _assert_api_called(module, node_fn, state, config, expect_calls):
    """执行节点：expect_calls=True → 断言调用生图 API；False → 断言未调用。"""
    calls = []

    def _capture(token, prompt, ref_images=None, **kwargs):
        calls.append(1)
        return _MOCK_URL

    with patch.object(module, "call_mxou_image_api", side_effect=_capture):
        result = node_fn(state, config, _RUNTIME)

    if expect_calls:
        assert calls, f"{node_fn.__name__} 应调用生图 API（plan 含该 slot）"
    else:
        assert not calls, f"{node_fn.__name__} 不应调用生图 API（plan 无该 slot），实际 {len(calls)} 次"
    return result


def test_plan_white_bg_scene1_only_executes_two_nodes():
    """验收 (a)：plan {white_bg:1, scene_1:1} → 仅执行这 2 节点，其余跳过（未调生图 API）。"""
    cfg = _cfg_with_plan({"white_bg": 1, "scene_1": 1})
    for node_fn, module, state, out_attr in _node_cases():
        enabled = node_fn.__name__ in ("white_bg_gen_node", "scene_1_gen_node")
        result = _assert_api_called(module, node_fn, state, cfg, expect_calls=enabled)
        if node_fn.__name__ == "white_bg_gen_node":
            assert result.white_bg_image == _MOCK_URL
        elif node_fn.__name__ == "scene_1_gen_node":
            assert result.scene_1_image == _MOCK_URL
        else:
            # 跳过节点：输出为 None/空，且未调生图 API（上面已断言）
            value = getattr(result, out_attr)
            assert not value, f"{node_fn.__name__} 被跳过但输出非空: {value!r}"


def test_plan_white_bg_scene1_executes_both_nodes():
    """plan 内节点正常执行：white_bg + scene_1 各调 1 次生图 API。"""
    cfg = _cfg_with_plan({"white_bg": 1, "scene_1": 1})
    cases = [c for c in _node_cases() if c[0].__name__ in ("white_bg_gen_node", "scene_1_gen_node")]
    for node_fn, module, state, _ in cases:
        _assert_api_called(module, node_fn, state, cfg, expect_calls=True)


def test_default_plan_all_10_nodes_call_api():
    """验收 (c)：默认 plan（无注入）→ 全 10 张回归，10 节点全部调用生图 API。"""
    cfg = _plan_config()
    for node_fn, module, state, _ in _node_cases():
        _assert_api_called(module, node_fn, state, cfg, expect_calls=True)


def test_no_new_nodes_slots_stable():
    """验收 (e)：不新增节点——10 个节点函数 + slot 集合与 C3b 冻结表一致。"""
    from graphs.nodes.white_bg_gen_node import white_bg_gen_node
    from graphs.nodes.multi_angle_gen_node import multi_angle_gen_node
    from graphs.nodes.main_image_gen_node import main_image_gen_node
    from graphs.nodes.detail_gen_node import detail_gen_node
    from graphs.nodes.social_proof_gen_node import social_proof_gen_node
    from graphs.nodes.scene_1_gen_node import scene_1_gen_node
    from graphs.nodes.scene_2_gen_node import scene_2_gen_node
    from graphs.nodes.scene_3_gen_node import scene_3_gen_node
    from graphs.nodes.comparison_gen_node import comparison_gen_node
    from graphs.nodes.variant_primary_loop_node import variant_primary_loop_node

    node_fns = {
        "white_bg": white_bg_gen_node,
        "multi_angle": multi_angle_gen_node,
        "main_image": main_image_gen_node,
        "detail": detail_gen_node,
        "social_proof": social_proof_gen_node,
        "scene_1": scene_1_gen_node,
        "scene_2": scene_2_gen_node,
        "scene_3": scene_3_gen_node,
        "comparison": comparison_gen_node,
        "variant_primary_loop": variant_primary_loop_node,
    }
    assert set(node_fns) == set(ALL_SLOTS)
    # C3b 冻结：Phase1 = white_bg + multi_angle（Phase2 节点依赖其输出作参考图）
    assert PHASE1_SLOTS == {"white_bg", "multi_angle"}

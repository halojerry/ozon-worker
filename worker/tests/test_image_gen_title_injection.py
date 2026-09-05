# -*- coding: utf-8 -*-
"""
v0.31 Wave 0 — 生图流程优化：10 个生图节点渲染的 prompt 必须包含产品标题。

背景：7 个节点调用 get_image_prompt(...) 时不传 title → Jinja2 lenient Undefined
把 {{title}} 渲染为空串（实测「产品：。…」）。本测试直接调用 10 个节点函数，
mock call_mxou_image_api 捕获最终 prompt，断言每个 prompt 含产品标题
「产品X」且不以空标题（「产品：。」）开头。

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_image_gen_title_injection.py -v

⚠️ mock 说明：节点都是 `from utils.mxou_api import call_mxou_image_api` 直接导入
（模块级绑定），只 patch `utils.mxou_api.call_mxou_image_api` 不会截获节点调用。
因此这里 patch 各节点模块内的同名引用（patch.object(node_module, ...)），
patch 的 side_effect 与源模块 mock 语义等价——捕获 prompt、返回假 URL、不发网络。
"""
import logging
import os
import sys
from unittest.mock import patch

# 设置测试环境（不依赖真实 MXOU/Ozon API）
os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PGDATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ozon")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.WARNING)

from graphs.nodes.white_bg_gen_node import white_bg_gen_node  # noqa: E402
from graphs.nodes.multi_angle_gen_node import multi_angle_gen_node  # noqa: E402
from graphs.nodes.main_image_gen_node import main_image_gen_node  # noqa: E402
from graphs.nodes.detail_gen_node import detail_gen_node  # noqa: E402
from graphs.nodes.social_proof_gen_node import social_proof_gen_node  # noqa: E402
from graphs.nodes.scene_1_gen_node import scene_1_gen_node  # noqa: E402
from graphs.nodes.scene_2_gen_node import scene_2_gen_node  # noqa: E402
from graphs.nodes.scene_3_gen_node import scene_3_gen_node  # noqa: E402
from graphs.nodes.comparison_gen_node import comparison_gen_node  # noqa: E402
from graphs.nodes.variant_primary_loop_node import variant_primary_loop_node  # noqa: E402

import graphs.nodes.white_bg_gen_node as _white_bg_mod  # noqa: E402
import graphs.nodes.multi_angle_gen_node as _multi_angle_mod  # noqa: E402
import graphs.nodes.main_image_gen_node as _main_image_mod  # noqa: E402
import graphs.nodes.detail_gen_node as _detail_mod  # noqa: E402
import graphs.nodes.social_proof_gen_node as _social_proof_mod  # noqa: E402
import graphs.nodes.scene_1_gen_node as _scene_1_mod  # noqa: E402
import graphs.nodes.scene_2_gen_node as _scene_2_mod  # noqa: E402
import graphs.nodes.scene_3_gen_node as _scene_3_mod  # noqa: E402
import graphs.nodes.comparison_gen_node as _comparison_mod  # noqa: E402
import graphs.nodes.variant_primary_loop_node as _variant_mod  # noqa: E402

# Wave 1-D: assemble_prompt 真实现（spy 委托它保证渲染语义与生产一致）
from utils.prompt_assembler import assemble_prompt as _real_assemble_prompt  # noqa: E402

# ⚠️ 标题不能含 clean_title_for_image_prompt 的 junk 词（如「产品」「爆款」——
# 它会被清洗掉，导致已修好的节点也断言失败）。「保温杯」不在 junk 表，原样保留。
TITLE = "保温杯"
REF_IMAGE = "https://example.com/ref.jpg"

# LangGraph 节点签名: node(state, config, runtime)。config 供 _task_id_from_config
# 读 thread_id（空 → 跳过任务生图缓存，不碰 PG）；runtime.context 节点内未实际使用。
# ⚠️ image_gen_plan: 全 10 张 —— 本文件是「节点 prompt 质量护栏」，默认精简 plan(5 张)
# 会让 social_proof/comparison/scene_2/scene_3 节点短路跳过。这里显式注入全 10 张 plan，
# 保证被关节点（未来经 plan 重开时）的标题注入/场景差异化护栏仍持续生效。
_CONFIG = {
    "metadata": {"execute_id": "test-run"},
    "configurable": {
        "image_gen_plan": {
            "white_bg": 1, "multi_angle": 1, "main_image": 1, "detail": 1,
            "social_proof": 1, "comparison": 1, "scene": 3,
            "variant_primary_loop": 1,
        }
    },
}
_RUNTIME = type("FakeRuntime", (), {"context": None})()


def _run_node(node_fn, state, node_module):
    """执行节点，mock 其模块内绑定的 call_mxou_image_api，返回捕获的 prompts。"""
    captured = []

    def _capture(token, prompt, ref_images=None, **kwargs):
        captured.append(prompt)
        return "https://example.com/mock_image.jpg"

    with patch.object(node_module, "call_mxou_image_api", side_effect=_capture):
        node_fn(state, _CONFIG, _RUNTIME)
    assert captured, f"{node_fn.__name__} 未调用 call_mxou_image_api（state 构造可能触发短路分支）"
    return captured


def _assert_title_in_prompt(prompt, title=TITLE):
    """Given: 产品标题为 title / When: 节点渲染生图 prompt / Then: prompt 必须含标题。"""
    assert title in prompt, f"prompt 缺少产品标题 {title!r}: {prompt[:80]!r}"
    assert "产品：。" not in prompt, f"prompt 以空标题开头（{{{{title}}}} 渲染为空）: {prompt[:40]!r}"


# ── 已传 title 的 3 个节点（回归护栏，必须一直绿）──

def test_white_bg_prompt_contains_title():
    """white_bg_gen_node 的 prompt 必须含产品标题"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(draft={"title": TITLE}, token="t")
    for prompt in _run_node(white_bg_gen_node, state, _white_bg_mod):
        _assert_title_in_prompt(prompt)


def test_multi_angle_prompt_contains_title():
    """multi_angle_gen_node 的 prompt 必须含产品标题"""
    from graphs.state_image_gen import MultiAngleInput
    state = MultiAngleInput(draft={"title": TITLE}, token="t")
    for prompt in _run_node(multi_angle_gen_node, state, _multi_angle_mod):
        _assert_title_in_prompt(prompt)


def test_main_image_prompt_contains_title():
    """main_image_gen_node 的 prompt 必须含产品标题"""
    from graphs.state_image_gen import MainImageInput
    state = MainImageInput(draft={"title": TITLE}, token="t", white_bg_image=REF_IMAGE)
    for prompt in _run_node(main_image_gen_node, state, _main_image_mod):
        _assert_title_in_prompt(prompt)


# ── 不传 title 的 7 个节点（RED→GREEN 目标）──

def test_detail_prompt_contains_title():
    """detail_gen_node 的 prompt 必须含产品标题"""
    from graphs.state_image_gen import DetailImageInput
    state = DetailImageInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE)
    for prompt in _run_node(detail_gen_node, state, _detail_mod):
        _assert_title_in_prompt(prompt)


def test_social_proof_prompt_contains_title():
    """social_proof_gen_node 的 prompt 必须含产品标题"""
    from graphs.state_image_gen import SocialProofInput
    state = SocialProofInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE)
    for prompt in _run_node(social_proof_gen_node, state, _social_proof_mod):
        _assert_title_in_prompt(prompt)


def test_comparison_prompt_contains_title():
    """comparison_gen_node 的 prompt 必须含产品标题"""
    from graphs.state_image_gen import ComparisonInput
    state = ComparisonInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE)
    for prompt in _run_node(comparison_gen_node, state, _comparison_mod):
        _assert_title_in_prompt(prompt)


def test_scene_1_prompt_contains_title():
    """scene_1_gen_node 的 prompt 必须含产品标题（同时保留 scene 场景：v8 由 visual_vars.scene_1 透传渲染）"""
    from graphs.state_image_gen import Scene1Input
    state = Scene1Input(
        draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE,
        scene_context_1="家庭生活场景",
        visual_vars={"scene_1": "家庭生活场景"},
    )
    for prompt in _run_node(scene_1_gen_node, state, _scene_1_mod):
        _assert_title_in_prompt(prompt)
        assert "家庭生活场景" in prompt


def test_scene_2_prompt_contains_title():
    """scene_2_gen_node 的 prompt 必须含产品标题（同时保留 scene 场景：v8 由 visual_vars.scene_2 透传渲染）"""
    from graphs.state_image_gen import Scene2Input
    state = Scene2Input(
        draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE,
        scene_context_2="户外休闲场景",
        visual_vars={"scene_2": "户外休闲场景"},
    )
    for prompt in _run_node(scene_2_gen_node, state, _scene_2_mod):
        _assert_title_in_prompt(prompt)
        assert "户外休闲场景" in prompt


def test_scene_3_prompt_contains_title():
    """scene_3_gen_node 的 prompt 必须含产品标题（同时保留 scene 场景：v8 由 visual_vars.scene_3 透传渲染）"""
    from graphs.state_image_gen import Scene3Input
    state = Scene3Input(
        draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE,
        scene_context_3="工作办公场景",
        visual_vars={"scene_3": "工作办公场景"},
    )
    for prompt in _run_node(scene_3_gen_node, state, _scene_3_mod):
        _assert_title_in_prompt(prompt)
        assert "工作办公场景" in prompt


def test_variant_primary_loop_prompt_contains_title():
    """variant_primary_loop_node 的 prompt 必须含产品标题"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    state = VariantPrimaryLoopInput(
        variants=[{"name": "variant_0", "image": "https://example.com/v0.jpg"}],
        draft={"title": TITLE},
        token="t",
    )
    for prompt in _run_node(variant_primary_loop_node, state, _variant_mod):
        _assert_title_in_prompt(prompt)


# ── Wave 1-D: 10 节点迁移到 assemble_prompt（RED→GREEN 目标）──

def _node_cases():
    """10 个生图节点的 (节点函数, 最小合法 state, 模块, 期望 slot_key) 用例表"""
    from graphs.state_image_gen import (
        WhiteBgInput, MultiAngleInput, MainImageInput,
        DetailImageInput, SocialProofInput, ComparisonInput,
        Scene1Input, Scene2Input, Scene3Input,
    )
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    return [
        (white_bg_gen_node, WhiteBgInput(draft={"title": TITLE}, token="t"), _white_bg_mod, "white_bg"),
        (multi_angle_gen_node, MultiAngleInput(draft={"title": TITLE}, token="t"), _multi_angle_mod, "multi_angle"),
        (main_image_gen_node, MainImageInput(draft={"title": TITLE}, token="t", white_bg_image=REF_IMAGE), _main_image_mod, "main"),
        (detail_gen_node, DetailImageInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE), _detail_mod, "detail"),
        (social_proof_gen_node, SocialProofInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE), _social_proof_mod, "social_proof"),
        (comparison_gen_node, ComparisonInput(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE), _comparison_mod, "comparison"),
        (scene_1_gen_node, Scene1Input(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE, scene_context_1="家庭生活场景"), _scene_1_mod, "scene_1"),
        (scene_2_gen_node, Scene2Input(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE, scene_context_2="户外休闲场景"), _scene_2_mod, "scene_2"),
        (scene_3_gen_node, Scene3Input(draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE, scene_context_3="工作办公场景"), _scene_3_mod, "scene_3"),
        (variant_primary_loop_node, VariantPrimaryLoopInput(variants=[{"name": "variant_0", "image": "https://example.com/v0.jpg"}], draft={"title": TITLE}, token="t"), _variant_mod, "variant_white_bg"),
    ]


def _run_node_with_assembler_spy(node_fn, state, node_module):
    """执行节点，patch call_mxou_image_api 捕获 prompt + spy assemble_prompt 捕获调用。

    返回 (captured_prompts, assemble_calls)，assemble_calls 为 [(slot_key, kwargs), ...]。
    spy 委托真实 assemble_prompt（patch create=True：迁移前属性不存在 → 调用记录为空 → RED）。
    """
    captured_prompts = []
    assemble_calls = []

    def _capture(token, prompt, ref_images=None, **kwargs):
        captured_prompts.append(prompt)
        return "https://example.com/mock_image.jpg"

    def _spy(slot_key, **kwargs):
        assemble_calls.append((slot_key, kwargs))
        return _real_assemble_prompt(slot_key, **kwargs)

    with patch.object(node_module, "call_mxou_image_api", side_effect=_capture), \
         patch.object(node_module, "assemble_prompt", create=True, side_effect=_spy):
        node_fn(state, _CONFIG, _RUNTIME)
    assert captured_prompts, f"{node_fn.__name__} 未调用 call_mxou_image_api（state 构造可能触发短路分支）"
    return captured_prompts, assemble_calls


def test_all_nodes_call_assemble_prompt():
    """10 个生图节点都必须调用 assemble_prompt（mock 验证），slot_key 与模板一致"""
    for node_fn, state, mod, expected_key in _node_cases():
        _prompts, calls = _run_node_with_assembler_spy(node_fn, state, mod)
        assert calls, f"{node_fn.__name__} 未调用 assemble_prompt（仍走 get_image_prompt）"
        assert calls[0][0] == expected_key, \
            f"{node_fn.__name__} assemble_prompt slot_key 错误: {calls[0][0]!r}，期望 {expected_key!r}"


def test_scene_slots_pass_distinct_slot_scene_context():
    """scene_1/2/3 必须传各自的 scene_context 到 slot_scene_context（三张场景图差异化）"""
    scene_ctx = {"scene_1": "家庭生活场景", "scene_2": "户外休闲场景", "scene_3": "工作办公场景"}
    for node_fn, state, mod, expected_key in _node_cases():
        if not expected_key.startswith("scene_"):
            continue
        _prompts, calls = _run_node_with_assembler_spy(node_fn, state, mod)
        assert calls, f"{node_fn.__name__} 未调用 assemble_prompt"
        slot_kwargs = calls[0][1]
        assert slot_kwargs.get("slot_scene_context") == scene_ctx[expected_key], \
            f"{expected_key} slot_scene_context 错误: {slot_kwargs.get('slot_scene_context')!r}，" \
            f"期望 {scene_ctx[expected_key]!r}"
        assert slot_kwargs.get("scene_context") == scene_ctx[expected_key], \
            f"{expected_key} scene_context 应同时透传（兼容）"


def test_prompt_renders_material_from_draft():
    """draft.attributes 含材质 → white_bg 节点 prompt 渲染出材质（v6: main 用 product 描述，材质在 white_bg/detail 渲染）"""
    from graphs.state_image_gen import WhiteBgInput
    draft = {"title": TITLE, "attributes": {"材质": "ABS塑料", "颜色": "白色"}}
    state = WhiteBgInput(draft=draft, token="t")
    for prompt in _run_node(white_bg_gen_node, state, _white_bg_mod):
        assert "ABS塑料" in prompt, f"材质未渲染进 prompt: {prompt[:80]!r}"
        assert "白色" not in prompt, f"v0.32: color 不应渲染（参考图承担颜色）: {prompt[:80]!r}"
        assert "{{" not in prompt, f"存在未渲染占位符: {prompt[:80]!r}"


# ── Wave 2: 节点消费 state.visual_vars + resolve_color_preset ──

def test_node_consumes_llm_visual_vars():
    """state.visual_vars 含场景/氛围/前景变量 → 主图节点 prompt 渲染出 LLM 值
    （v8 main 占位符为 scene_1/atmosphere/model——v6 的 lighting/background 占位符已移除）"""
    from graphs.state_image_gen import MainImageInput
    state = MainImageInput(
        draft={"title": TITLE, "category": "宠物用品"},
        token="t", white_bg_image=REF_IMAGE,
        visual_vars={
            "scene_1": "warm golden hour light",
            "model": "cozy modern living room",
            "atmosphere": "premium and cozy",
        },
    )
    for prompt in _run_node(main_image_gen_node, state, _main_image_mod):
        for token in ("warm golden hour light", "cozy modern living room", "premium and cozy"):
            assert token in prompt, f"LLM 视觉变量 {token!r} 未渲染进 prompt: {prompt[:100]!r}"
        assert "{{" not in prompt, f"存在未渲染占位符: {prompt[:80]!r}"


def test_node_passes_color_preset_from_draft_category():
    """draft.category → resolve_color_preset → assemble_prompt 收到 color_preset（spy 捕获）"""
    from graphs.state_image_gen import MainImageInput
    state = MainImageInput(draft={"title": TITLE, "category": "宠物用品"}, token="t", white_bg_image=REF_IMAGE)
    _prompts, calls = _run_node_with_assembler_spy(main_image_gen_node, state, _main_image_mod)
    assert calls, "main_image_gen_node 未调用 assemble_prompt"
    assert calls[0][1].get("color_preset") == "PET_FUN", \
        f"color_preset 未透传: {calls[0][1].get('color_preset')!r}，期望 PET_FUN"


def test_llm_visual_vars_not_inject_color():
    """v6: 模板已移除 color 占位符 → LLM visual_vars 的 color 值静默忽略（参考图承担颜色）；material 在 white_bg 渲染"""
    from graphs.state_image_gen import WhiteBgInput
    draft = {"title": TITLE, "attributes": {"材质": "ABS塑料", "颜色": "白色"}}
    state = WhiteBgInput(
        draft=draft, token="t",
        visual_vars={"color": "navy blue + rose gold"},
    )
    for prompt in _run_node(white_bg_gen_node, state, _white_bg_mod):
        assert "navy blue + rose gold" not in prompt, \
            f"v0.32: color 值不应注入 prompt: {prompt[:100]!r}"
        assert "白色" not in prompt, f"color 不应渲染: {prompt[:100]!r}"
        assert "ABS塑料" in prompt, "material 仍应渲染"
        assert "ABS塑料" in prompt, f"material（无 LLM 值）应保留确定性提取: {prompt[:100]!r}"
        assert "{{" not in prompt


def test_empty_llm_vars_no_placeholder_residue():
    """LLM 值为空串/空白/缺失 → 节点侧过滤，不产生 {{ 残留"""
    from graphs.state_image_gen import MainImageInput
    state = MainImageInput(
        draft={"title": TITLE, "category": "宠物用品"}, token="t", white_bg_image=REF_IMAGE,
        visual_vars={"lighting": "", "background": "   "},  # atmosphere 键缺失（LLM 未提供）
    )
    for prompt in _run_node(main_image_gen_node, state, _main_image_mod):
        assert "{{" not in prompt, f"存在未渲染占位符: {prompt[:80]!r}"
        assert TITLE in prompt
        assert "None" not in prompt


def test_variant_loop_consumes_llm_visual_vars():
    """variant_primary_loop 也从 state.visual_vars 消费 LLM 变量（variant_white_bg 模板）
    （v8 variant_white_bg 无 {{lighting}} 占位符，V6-T5 lighting 守卫改由 {{appearance}} 承担）"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    state = VariantPrimaryLoopInput(
        variants=[{"name": "variant_0", "image": "https://example.com/v0.jpg"}],
        draft={"title": TITLE, "category": "宠物用品"},
        token="t",
        visual_vars={"appearance": "bright even studio light"},
    )
    for prompt in _run_node(variant_primary_loop_node, state, _variant_mod):
        assert "bright even studio light" in prompt, f"LLM 视觉变量未渲染进 variant prompt: {prompt[:100]!r}"
        assert "{{" not in prompt

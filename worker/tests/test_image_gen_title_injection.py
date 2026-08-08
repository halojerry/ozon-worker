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

# ⚠️ 标题不能含 clean_title_for_image_prompt 的 junk 词（如「产品」「爆款」——
# 它会被清洗掉，导致已修好的节点也断言失败）。「保温杯」不在 junk 表，原样保留。
TITLE = "保温杯"
REF_IMAGE = "https://example.com/ref.jpg"

# LangGraph 节点签名: node(state, config, runtime)。config 供 _task_id_from_config
# 读 thread_id（空 → 跳过任务生图缓存，不碰 PG）；runtime.context 节点内未实际使用。
_CONFIG = {"metadata": {"execute_id": "test-run"}}
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
    """scene_1_gen_node 的 prompt 必须含产品标题（同时保留 scene_context）"""
    from graphs.state_image_gen import Scene1Input
    state = Scene1Input(
        draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE,
        scene_context_1="家庭生活场景",
    )
    for prompt in _run_node(scene_1_gen_node, state, _scene_1_mod):
        _assert_title_in_prompt(prompt)
        assert "家庭生活场景" in prompt


def test_scene_2_prompt_contains_title():
    """scene_2_gen_node 的 prompt 必须含产品标题（同时保留 scene_context）"""
    from graphs.state_image_gen import Scene2Input
    state = Scene2Input(
        draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE,
        scene_context_2="户外休闲场景",
    )
    for prompt in _run_node(scene_2_gen_node, state, _scene_2_mod):
        _assert_title_in_prompt(prompt)
        assert "户外休闲场景" in prompt


def test_scene_3_prompt_contains_title():
    """scene_3_gen_node 的 prompt 必须含产品标题（同时保留 scene_context）"""
    from graphs.state_image_gen import Scene3Input
    state = Scene3Input(
        draft={"title": TITLE}, token="t", multi_angle_image=REF_IMAGE,
        scene_context_3="工作办公场景",
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

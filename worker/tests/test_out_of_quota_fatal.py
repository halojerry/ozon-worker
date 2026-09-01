"""v0.63.1: MXOU 永久错误（OutOfQuota）全链路 Fatal 化。

R1/R4 闭环：MxouOutOfQuotaError（MXOU 401/403，余额/鉴权/额度永久性错误）
不得被 except Exception 吞掉降级 —— 任务应在第一次 MXOU 调用即 fail-fast，
不白打 401、不产生「营销图片全部为空」等误导终态。

覆盖（17 处全部）：
- 生图节点 10 个（含 main_image/social_proof 内层降级模型循环不再尝试）
- 前置 chat 节点 2 个（scene_generation_llm / visual_vars_llm）
- assemble 类目 LLM 2 个函数（_llm_match_category / _llm_rank_categories）
- 富文本描述 / 类目翻译 / 去拉丁 / 属性消歧 4 处
- 反向锁定：generic 异常（RuntimeError）仍走原降级路径，只对永久错误 fatal
"""
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.mxou_api import MxouOutOfQuotaError  # noqa: E402


@contextmanager
def _workspace():
    """指向 worker/（读真实 config/*.json，不依赖调用方 cwd）。"""
    old = os.environ.get("APP_WORKSPACE_PATH")
    try:
        os.environ["APP_WORKSPACE_PATH"] = str(Path(__file__).resolve().parent.parent)
        yield
    finally:
        if old is None:
            os.environ.pop("APP_WORKSPACE_PATH", None)
        else:
            os.environ["APP_WORKSPACE_PATH"] = old


class _ProgressStub:
    """ProgressLogger 替代：只记录不落盘。"""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _patch_image_node(monkeypatch, mod, boom):
    """生图节点公共 mock：让流程稳定走到 call_mxou_* 并抛 boom。"""
    for name in ("slot_enabled", "get_image", "_task_id_from_config",
                 "_force_regen_from_config", "assemble_prompt",
                 "merge_visual_vars", "resolve_color_preset",
                 "get_image_model", "save_image", "evaluate_image_quality"):
        if hasattr(mod, name):
            # ⚠️ 不能用共享闭包（循环变量 name 会被最后一个值覆盖）→ 按名字逐一定义
            if name == "slot_enabled":
                monkeypatch.setattr(mod, name, lambda *a, **k: True)
            elif name in ("get_image", "_task_id_from_config"):
                monkeypatch.setattr(mod, name, lambda *a, **k: None)
            elif name == "get_image_model":
                monkeypatch.setattr(mod, name, lambda *a, **k: "gpt-image-2")
            elif name == "assemble_prompt":
                monkeypatch.setattr(mod, name, lambda *a, **k: "prompt")
            elif name == "merge_visual_vars":
                monkeypatch.setattr(mod, name, lambda *a, **k: {})
            else:
                monkeypatch.setattr(mod, name, lambda *a, **k: "")
    if hasattr(mod, "ProgressLogger"):
        monkeypatch.setattr(mod, "ProgressLogger", _ProgressStub)
    monkeypatch.setattr(mod, "call_mxou_image_api", boom)


def _img_state(input_cls, **extra):
    base = {
        "draft": {"title": "测试商品", "images": ["http://img/1.png"]},
        "token": "tok",
        "original_images": ["http://img/1.png"],
    }
    base.update(extra)
    return input_cls(**base)


def _raises_out_of_quota(fn, *a, **k):
    with pytest.raises(MxouOutOfQuotaError):
        fn(*a, **k)


# ═══════════════════════════════════════════════════════════
# Group 1: 生图节点 10 个
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("mod_name,input_cls,node_fn,extra", [
    ("white_bg_gen_node", "WhiteBgInput", "white_bg_gen_node", {}),
    ("multi_angle_gen_node", "MultiAngleInput", "multi_angle_gen_node", {}),
    ("detail_gen_node", "DetailImageInput", "detail_gen_node",
     {"white_bg_image": "http://img/wb.png", "multi_angle_image": None}),
    ("comparison_gen_node", "ComparisonInput", "comparison_gen_node",
     {"white_bg_image": "http://img/wb.png", "multi_angle_image": None}),
    ("scene_1_gen_node", "Scene1Input", "scene_1_gen_node",
     {"white_bg_image": "http://img/wb.png", "scene_context_1": "家庭生活场景"}),
    ("scene_2_gen_node", "Scene2Input", "scene_2_gen_node",
     {"white_bg_image": "http://img/wb.png", "scene_context_2": "办公室场景"}),
    ("scene_3_gen_node", "Scene3Input", "scene_3_gen_node",
     {"white_bg_image": "http://img/wb.png", "scene_context_3": "卧室场景"}),
])
def test_image_node_out_of_quota_fatal(monkeypatch, mod_name, input_cls, node_fn, extra):
    """生图节点：MxouOutOfQuotaError → re-raise（不返回 None 吞掉）。"""
    import importlib
    mod = importlib.import_module(f"graphs.nodes.{mod_name}")
    from graphs import state_image_gen as sig

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU image API rejected (HTTP 401)")

    _patch_image_node(monkeypatch, mod, _boom)
    state = _img_state(getattr(sig, input_cls), **extra)
    with _workspace():
        _raises_out_of_quota(getattr(mod, node_fn), state, {}, mock.Mock())


def test_main_image_out_of_quota_primary_fatal(monkeypatch):
    """main_image_gen：主模型 401 → 直接 re-raise，且不尝试降级模型（仅 1 次调用）。"""
    import graphs.nodes.main_image_gen_node as mod
    from graphs.state_image_gen import MainImageInput

    calls = []

    def _boom(*a, **k):
        calls.append(k.get("model", "?"))
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU image API rejected (HTTP 401)")

    _patch_image_node(monkeypatch, mod, _boom)
    state = MainImageInput(
        draft={"title": "测试商品", "images": ["http://img/1.png"]},
        token="tok",
        original_images=["http://img/1.png"],
        white_bg_image=None,
        multi_angle_image=None,
        visual_vars={},
    )
    with _workspace():
        _raises_out_of_quota(mod.main_image_gen_node, state, {}, mock.Mock())
    assert calls == ["gpt-image-2"], f"主模型 401 不应再尝试降级模型: {calls}"


def test_main_image_fallback_model_out_of_quota_stops(monkeypatch):
    """main_image_gen：主模型返回空、降级模型 401 → 内层 re-raise 停止后续降级。"""
    import graphs.nodes.main_image_gen_node as mod
    from graphs.state_image_gen import MainImageInput

    calls = []

    def _boom(*a, **k):
        calls.append(k.get("model", "?"))
        if len(calls) == 1:
            return None  # 主模型失败（瞬时，返回 None）→ 进入降级循环
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU image API rejected (HTTP 401)")

    _patch_image_node(monkeypatch, mod, _boom)
    state = MainImageInput(
        draft={"title": "测试商品", "images": ["http://img/1.png"]},
        token="tok",
        original_images=["http://img/1.png"],
        white_bg_image=None,
        multi_angle_image=None,
        visual_vars={},
    )
    with _workspace():
        _raises_out_of_quota(mod.main_image_gen_node, state, {}, mock.Mock())
    assert len(calls) == 2, f"第一个降级模型 401 后不应再尝试第二个: {calls}"
    assert calls == ["gpt-image-2", "nano-banana-fast"], f"调用顺序异常: {calls}"


def test_social_proof_out_of_quota_stops_fallback_loop(monkeypatch):
    """social_proof_gen：主模型 401 → 不进入降级模型循环（仅 1 次调用）。"""
    import graphs.nodes.social_proof_gen_node as mod
    from graphs.state_image_gen import SocialProofInput

    calls = []

    def _boom(*a, **k):
        calls.append(k.get("model", "?"))
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU image API rejected (HTTP 401)")

    _patch_image_node(monkeypatch, mod, _boom)
    state = _img_state(SocialProofInput, white_bg_image="http://img/wb.png",
                       multi_angle_image=None)
    with _workspace():
        _raises_out_of_quota(mod.social_proof_gen_node, state, {}, mock.Mock())
    assert calls == ["gpt-image-2"], f"主模型 401 不应再尝试降级模型: {calls}"


def test_variant_primary_loop_out_of_quota_fatal(monkeypatch):
    """variant_primary_loop：变体主图 401 → 任务明确失败（不原图兜底）。"""
    import graphs.nodes.variant_primary_loop_node as mod
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU image API rejected (HTTP 401)")

    _patch_image_node(monkeypatch, mod, _boom)
    state = VariantPrimaryLoopInput(
        variants=[{"name": "v1", "image": "http://img/v1.png"}],
        draft={"title": "测试商品"},
        token="tok",
        visual_vars={},
    )
    with _workspace():
        _raises_out_of_quota(mod.variant_primary_loop_node, state, {}, mock.Mock())


# ═══════════════════════════════════════════════════════════
# Group 2: 前置 chat 节点 2 个
# ═══════════════════════════════════════════════════════════

def test_scene_generation_llm_out_of_quota_fatal(monkeypatch):
    """scene_generation_llm：401 → 不回退默认场景，异常穿透。"""
    import graphs.nodes.scene_generation_llm_node as mod
    from graphs.state import SceneGenerationInput

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mod, "call_mxou_chat_api", _boom)
    state = SceneGenerationInput(
        draft={"title": "测试商品"},
        token="tok",
    )
    cfg = {"metadata": {"llm_cfg": "config/scene_generation_llm_cfg.json"}}
    with _workspace():
        _raises_out_of_quota(mod.scene_generation_llm_node, state, cfg, mock.Mock())


def test_visual_vars_llm_out_of_quota_fatal(monkeypatch):
    """visual_vars_llm：401 → 不回退确定性提取，异常穿透。"""
    import graphs.nodes.visual_vars_llm_node as mod
    from graphs.state import VisualVarsInput

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mod, "call_mxou_chat_api", _boom)
    state = VisualVarsInput(draft={"title": "测试商品"}, token="tok")
    cfg = {"metadata": {"llm_cfg": "config/visual_vars_llm_cfg.json"}}
    runtime = type("FakeRuntime", (), {"context": None})()
    with _workspace():
        _raises_out_of_quota(mod.visual_vars_llm_node, state, cfg, runtime)


# ═══════════════════════════════════════════════════════════
# Group 3: assemble 类目 LLM 2 个函数
# ═══════════════════════════════════════════════════════════

def test_llm_match_category_out_of_quota_fatal(monkeypatch):
    """_llm_match_category：401 → 不降级到下一匹配层，异常穿透。"""
    import graphs.nodes.assemble_ozon_product_node as mod

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mod, "call_mxou_chat_api", _boom)
    with _workspace():
        _raises_out_of_quota(
            mod._llm_match_category,
            "轮毂", "汽车轮毂", {},
            [{"description_category_id": "17028758", "full_path": "汽车用品 > 轮辋"}],
            "tok",
        )


def test_llm_rank_categories_out_of_quota_fatal(monkeypatch):
    """_llm_rank_categories：401 → 不返回 None，异常穿透。"""
    import graphs.nodes.assemble_ozon_product_node as mod
    import utils.mxou_api as mxou_api

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mxou_api, "call_mxou_chat_api", _boom)
    state = SimpleNamespace(token="tok")
    with _workspace():
        _raises_out_of_quota(
            mod._llm_rank_categories,
            [{"full_path": "汽车用品 > 轮辋", "similarity": 0.9}],
            "轮毂", {"title": "轮毂"}, state,
        )


# ═══════════════════════════════════════════════════════════
# Group 4: 富文本描述 / 类目翻译 / 去拉丁 / 属性消歧
# ═══════════════════════════════════════════════════════════

def test_rich_description_out_of_quota_fatal(monkeypatch):
    """prepare._generate_rich_description：401 → 不回退兜底 HTML，异常穿透。"""
    import graphs.nodes.prepare_ozon_upload_node as mod

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mod, "call_mxou_chat_api", _boom)
    with _workspace():
        _raises_out_of_quota(mod._generate_rich_description, "Товар", {}, "tok")


def test_prepare_llm_chain_out_of_quota_propagates(monkeypatch):
    """prepare 节点：LLM 链（去拉丁→富文本）中途 401 → 调用方不吞，异常穿透。

    覆盖：sanitize_title 的 _remove_latin_llm 与 _generate_rich_description 的
    调用方 wrapper（此前 except Exception 会把 re-raise 二次吞掉）。
    """
    import graphs.nodes.prepare_ozon_upload_node as mod
    from graphs.state import PrepareOzonUploadInput
    import utils.mxou_api as mxou_api

    calls = []

    def _chat(*a, **k):
        calls.append(k.get("user_prompt", "")[:20])
        if len(calls) == 1:
            return "Тестовый товар для кошек"  # 去拉丁：正常返回
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mxou_api, "call_mxou_chat_api", _chat)
    monkeypatch.setattr(mod, "_translate_to_russian_llm",
                        lambda *a, **k: "Тестовый товар для кошек")
    monkeypatch.setattr(mod, "_get_category_fallback_title",
                        lambda *a, **k: "Товар для дома")

    state = PrepareOzonUploadInput(
        draft={
            "item_id": "test001", "title": "宠物玩具", "images": ["http://img.test/1.jpg"],
            "weight": 300, "dimensions": {"length": 100, "width": 100, "height": 50},
            "attributes": {"商品颜色": "蓝色"}, "sku_id": "test001",
            "price": "1990", "original_price": "2390",
        },
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        extensions={},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=[],
        attributes_schema=[],
        dictionary_values={},
        token="sk-test",
        original_images=["http://img.test/1.jpg"],
    )
    with _workspace():
        _raises_out_of_quota(mod.prepare_ozon_upload_node, state, None, None)
    assert len(calls) >= 2, f"应在 LLM 链中途触发 401: {calls}"


def test_follow_sell_translate_out_of_quota_fatal(monkeypatch):
    """follow_sell_import._translate_to_russian：401 → 不回退原文，异常穿透。"""
    import graphs.nodes.follow_sell_import_node as mod
    import utils.mxou_api as mxou_api

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mxou_api, "call_mxou_chat_api", _boom)
    _raises_out_of_quota(mod._translate_to_russian, "汽车轮毂", "tok")


def test_title_sanitizer_latin_llm_out_of_quota_fatal(monkeypatch):
    """title_sanitizer._remove_latin_llm：401 → 不回退正则，异常穿透。"""
    import utils.title_sanitizer as mod
    import utils.mxou_api as mxou_api

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mxou_api, "call_mxou_chat_api", _boom)
    _raises_out_of_quota(mod._remove_latin_llm, "USB Кружка", "tok")


def test_attr_disambiguation_out_of_quota_fatal(monkeypatch):
    """attr_value_matcher 消歧：401 → 不 llm_error 跳过属性，异常穿透。"""
    from utils.attr_value_matcher import AttrResolution, disambiguate_candidates
    import utils.mxou_api as mxou_api

    def _boom(*a, **k):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 401)")

    monkeypatch.setattr(mxou_api, "call_mxou_chat_api", _boom)
    res = AttrResolution(
        status="llm_eligible",
        attr_name="Материал",
        product_value="ABS塑料",
        candidates=[{"id": 1, "value": "ABS"}, {"id": 2, "value": "PP"}],
    )
    with _workspace():
        _raises_out_of_quota(disambiguate_candidates, res, "tok", enabled=True)


# ═══════════════════════════════════════════════════════════
# 反向锁定：generic 异常仍走原降级（只对永久错误 fatal）
# ═══════════════════════════════════════════════════════════

def test_image_node_generic_error_still_degrades(monkeypatch):
    """白底图节点：RuntimeError（瞬时故障）→ 仍降级返回 None，不 raise。"""
    import graphs.nodes.white_bg_gen_node as mod
    from graphs.state_image_gen import WhiteBgInput

    def _boom(*a, **k):
        raise RuntimeError("upstream 5xx")

    _patch_image_node(monkeypatch, mod, _boom)
    state = _img_state(WhiteBgInput)
    with _workspace():
        out = mod.white_bg_gen_node(state, {}, mock.Mock())
    assert out.white_bg_image is None, "瞬时故障应降级返回 None"


def test_visual_vars_generic_error_still_falls_back(monkeypatch):
    """visual_vars_llm：RuntimeError → 仍回退确定性提取（不回退 = 任务可继续）。"""
    import graphs.nodes.visual_vars_llm_node as mod
    from graphs.state import VisualVarsInput

    def _boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(mod, "call_mxou_chat_api", _boom)
    state = VisualVarsInput(
        draft={"title": "测试商品", "material": "ABS塑料", "size": "150×50×30 cm"},
        token="tok",
    )
    cfg = {"metadata": {"llm_cfg": "config/visual_vars_llm_cfg.json"}}
    runtime = type("FakeRuntime", (), {"context": None})()
    with _workspace():
        out = mod.visual_vars_llm_node(state, cfg, runtime)
    assert out.visual_vars, "generic 异常应回退确定性提取"
    assert "material" in out.visual_vars, "fallback 应包含 material key"


def test_attr_disambiguation_generic_error_skips(monkeypatch):
    """attr_value_matcher：RuntimeError → 仍 llm_error 跳过（宁缺毋滥），不 raise。"""
    from utils.attr_value_matcher import AttrResolution, disambiguate_candidates
    import utils.mxou_api as mxou_api

    def _boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(mxou_api, "call_mxou_chat_api", _boom)
    res = AttrResolution(
        status="llm_eligible",
        attr_name="Материал",
        product_value="ABS塑料",
        candidates=[{"id": 1, "value": "ABS"}, {"id": 2, "value": "PP"}],
    )
    with _workspace():
        out = disambiguate_candidates(res, "tok", enabled=True)
    assert out.status == "skipped" and out.reason == "llm_error"

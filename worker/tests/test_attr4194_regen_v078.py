"""批H fix/attr4194-regen-v1（v0.78）——4194/4195 主图重生成 + 重传出口闸 + 断言 AI 判定接线。

生产取证 I2（docs/PLAN-image-source-hardening-v1.md §0）：Ozon 以 attr=4194/4195
（«На главном фото не показан товар»）拒单时，classify_error_node 直接 warn-and-pass，
不合规主图永挂卡片。本批：

- H1 classify 4194/4195 + 载荷含 AI 图 + 未重生成过 → 路由 regen_main_image
  （严格合规 prompt 重生成主图 + 替换 primary_image + 重传一次；防循环布尔闸）
- H2 _full_import_create 重传 POST 前 enforce_upload_policy 出口闸（复用批E
  IMAGE_GEN_ALL_FAILED 错误码与消息）
- H3 card_image_assert.is_all_ai_images 内联 /file/images/ marker 换
  utils.image_source.has_generated_images（mxou-b64/ 从盲变明，批F TODO 收口）

纯 mock 单测（无网络/无 CDP/无 PG）。运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python \\
        -m pytest tests/test_attr4194_regen_v078.py -q
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# 环境隔离：COS_*/IMAGE_SALVAGE_FALLBACK 可能来自生产 .env（storage/db.py 模块级
# load_dotenv 注入），import 期先清一次，后续由 autouse fixture 逐用例兜底。
for _k in list(os.environ.keys()):
    if _k.startswith("COS_") or _k == "IMAGE_SALVAGE_FALLBACK":
        os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ── URL 形状（与生产事实源一致，test_image_source_hardgate_v078 同款）───────
COS_AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai1.jpeg"
COS_AI_2 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai2.jpeg"
COS_AI_NEW = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/regen_new.jpeg"
COS_B64 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/mxou-b64/t123_abcd.png"
COS_SALVAGE = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-1688/salvage/abcd1234.jpg"
COS_MIRROR = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/8dbf02b9f.jpg"
ALICDN = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"


@pytest.fixture(autouse=True)
def _env_sandbox():
    """每用例前清空相关 env，结束后恢复现场（防生产 .env 泄漏进断言）。"""
    backup = {
        k: v for k, v in os.environ.items()
        if k.startswith("COS_") or k == "IMAGE_SALVAGE_FALLBACK"
    }
    for k in backup:
        os.environ.pop(k, None)
    yield
    for k in list(os.environ.keys()):
        if k.startswith("COS_") or k == "IMAGE_SALVAGE_FALLBACK":
            os.environ.pop(k, None)
    os.environ.update(backup)


def _mk_state(images: list, attr_id: int = 4194, done: bool = False):
    """构造子图真实 state（pydantic v2 对未声明字段赋值即抛——兼作声明完整性断言）。"""
    from graphs.validation_retry_loop import ValidationRetryLoopState

    return ValidationRetryLoopState(
        ozon_payload={
            "items": [{
                "offer_id": "sku-1",
                "primary_image": images[0] if images else "",
                "images": list(images),
            }]
        },
        error_code="DESCRIPTION_DECLINE",
        attribute_id=attr_id,
        error_message="На главном фото не показан товар",
        token="tok-test",
        draft={"images": [ALICDN]},
        original_images=[ALICDN],
        regen_main_image_done=done,
    )


# ═══════════════════════════════════════════════════════════════════
# H1: classify_error_node 4194/4195 路由
# ═══════════════════════════════════════════════════════════════════

class TestClassify4194RegenRouting:
    def test_4194_with_ai_images_routes_regen(self):
        """4194 + 载荷含 AI 图 + 未重生成 → 路由 regen_main_image（error_type 可路由）。"""
        from graphs.validation_retry_loop import classify_error_node

        st = classify_error_node(_mk_state([COS_AI, COS_AI_2]))
        assert st.repair_node == "regen_main_image"
        assert st.error_type == "fixable"
        # 未走 warn-and-pass：不得提前置成功带警告
        assert st.upload_status != "success_with_warning"
        assert st.is_valid is False

    def test_4194_b64_payload_counts_as_ai(self):
        """b64 兜底图计 AI（image_source 唯一入口语义）→ 同样路由重生成。"""
        from graphs.validation_retry_loop import classify_error_node

        st = classify_error_node(_mk_state([COS_B64]))
        assert st.repair_node == "regen_main_image"

    @pytest.mark.parametrize("attr_id", [4194, 4195])
    def test_4195_and_4194_without_ai_warn_pass(self, attr_id):
        """无 AI 图（镜像/外链载荷）→ 保持既有 warn-and-pass 逐字不变。"""
        from graphs.validation_retry_loop import classify_error_node

        st = classify_error_node(_mk_state([COS_MIRROR, ALICDN], attr_id=attr_id))
        assert st.error_type == "unfixable"
        assert st.repair_node == "final_result"
        assert st.is_valid is True
        assert st.upload_status == "success_with_warning"

    @pytest.mark.parametrize("attr_id", [4194, 4195])
    def test_second_decline_after_regen_warn_pass(self, attr_id):
        """防循环：重生成已做过（布尔门）→ 二次 4194/4195 直达 warn-and-pass。"""
        from graphs.validation_retry_loop import classify_error_node

        st = classify_error_node(_mk_state([COS_AI], attr_id=attr_id, done=True))
        assert st.error_type == "unfixable"
        assert st.repair_node == "final_result"
        assert st.is_valid is True
        assert st.upload_status == "success_with_warning"

    def test_selector_routes_regen_node(self):
        """repair_node_selector 认识 regen_main_image（builder 路由前置）。"""
        from graphs.validation_retry_loop import repair_node_selector

        st = _mk_state([COS_AI])
        st.error_type = "fixable"
        st.repair_node = "regen_main_image"
        assert repair_node_selector(st) == "regen_main_image"


# ═══════════════════════════════════════════════════════════════════
# H1: regen_main_image_node
# ═══════════════════════════════════════════════════════════════════

def _patch_gen(return_value=..., side_effect=None):
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = return_value
    return (
        mock.patch("utils.mxou_api.call_mxou_image_api", **kwargs),
        mock.patch("utils.image_models.get_image_model", return_value="model-main-test"),
        mock.patch(
            "graphs.nodes.prepare_ozon_upload_node._rewrite_payload_images_to_accelerate"
        ),
    )


class TestRegenMainImageNode:
    def test_success_replaces_primary_and_head_insert(self):
        """成功：primary 替换 + 旧主图从图廊移除 + 新图插首位 + 其余不动 + 门置位。"""
        from graphs.validation_retry_loop import (
            _regen_main_image_route,
            regen_main_image_node,
        )

        st = _mk_state([COS_AI, COS_AI_2])
        p_gen, p_model, p_rw = _patch_gen(return_value=COS_AI_NEW)
        with p_gen as m_gen, p_model, p_rw:
            out = regen_main_image_node(st)

        item0 = out.ozon_payload["items"][0]
        assert item0["primary_image"] == COS_AI_NEW
        # 旧主图（不合规 AI 图）从图廊移除，新图插首位，其余保持
        assert item0["images"] == [COS_AI_NEW, COS_AI_2]
        assert out.regen_main_image_done is True
        assert out.repair_node == "reupload"
        assert _regen_main_image_route(out) == "reupload"
        assert m_gen.call_count == 1

    def test_strict_prompt_and_main_model_args(self):
        """严格合规 prompt（白底/单品居中/无文字·角标·水印）+ 主模型与 main 槽一致。"""
        from graphs.validation_retry_loop import (
            REGEN_MAIN_IMAGE_PROMPT,
            regen_main_image_node,
        )

        # prompt 关键词锁定（调用参数层写死，不动 config/*.json）
        assert "白色背景" in REGEN_MAIN_IMAGE_PROMPT
        assert "无文字" in REGEN_MAIN_IMAGE_PROMPT
        assert "角标" in REGEN_MAIN_IMAGE_PROMPT
        assert "水印" in REGEN_MAIN_IMAGE_PROMPT
        assert "居中" in REGEN_MAIN_IMAGE_PROMPT

        st = _mk_state([COS_AI])
        p_gen, p_model, p_rw = _patch_gen(return_value=COS_AI_NEW)
        with p_gen as m_gen, p_model, p_rw:
            regen_main_image_node(st)

        kwargs = m_gen.call_args.kwargs
        assert kwargs["prompt"] == REGEN_MAIN_IMAGE_PROMPT
        assert kwargs["model"] == "model-main-test"  # get_image_model("main") 结果
        assert kwargs["token"] == "tok-test"
        # 参考图来自 original_images（filter_product_images 白名单过滤语义）
        assert kwargs["ref_images"] == [ALICDN]

    def test_failure_none_falls_back_warn_pass(self):
        """生图返回 None → 回落 warn-and-pass，载荷不动，任务不炸。"""
        from graphs.validation_retry_loop import (
            _regen_main_image_route,
            regen_main_image_node,
        )

        st = _mk_state([COS_AI])
        p_gen, p_model, p_rw = _patch_gen(return_value=None)
        with p_gen, p_model, p_rw:
            out = regen_main_image_node(st)

        assert out.repair_node == "final_result"
        assert out.error_type == "unfixable"
        assert out.is_valid is True
        assert out.upload_status == "success_with_warning"
        assert _regen_main_image_route(out) == "final_result"
        # 载荷保持原样（绝不回落原图/塞空）
        assert out.ozon_payload["items"][0]["primary_image"] == COS_AI
        assert out.regen_main_image_done is True  # 尝试过即置位（防循环）

    def test_failure_exception_never_kills_task(self):
        """生图抛任意异常 → logger.error + warn-and-pass，异常不外泄。"""
        from graphs.validation_retry_loop import regen_main_image_node

        st = _mk_state([COS_AI])
        p_gen, p_model, p_rw = _patch_gen(side_effect=RuntimeError("boom"))
        with p_gen, p_model, p_rw:
            out = regen_main_image_node(st)  # 不应 raise

        assert out.repair_node == "final_result"
        assert out.upload_status == "success_with_warning"

    def test_non_ai_product_url_rejected(self):
        """重生成产物非本方 AI 图（如外链）→ 视为失败回落，绝不入载荷。"""
        from graphs.validation_retry_loop import regen_main_image_node

        st = _mk_state([COS_AI])
        p_gen, p_model, p_rw = _patch_gen(return_value=ALICDN)
        with p_gen, p_model, p_rw:
            out = regen_main_image_node(st)

        assert out.repair_node == "final_result"
        assert out.ozon_payload["items"][0]["primary_image"] == COS_AI

    def test_ref_fallback_to_draft_images(self):
        """original_images 为空 → 回退 draft.images 合格参考图。"""
        from graphs.validation_retry_loop import regen_main_image_node

        st = _mk_state([COS_AI])
        st.original_images = []
        p_gen, p_model, p_rw = _patch_gen(return_value=COS_AI_NEW)
        with p_gen as m_gen, p_model, p_rw:
            regen_main_image_node(st)

        assert m_gen.call_args.kwargs["ref_images"] == [ALICDN]


# ═══════════════════════════════════════════════════════════════════
# H2: _full_import_create 重传出口闸
# ═══════════════════════════════════════════════════════════════════

class TestReuploadExitGate:
    def _mk_reupload_state(self, images: list):
        from graphs.validation_retry_loop import ValidationRetryLoopState

        return ValidationRetryLoopState(
            ozon_payload={"items": [{"primary_image": images[0] if images else "",
                                     "images": list(images)}]},
            error_code="IMAGE_ERROR",
            token="tok-test",
            ozon_client_id="cid",
            ozon_api_key="key",
        )

    def test_mirror_url_mixed_in_blocks_post(self):
        """镜像残余混入重传载荷 → 不 POST，IMAGE_GEN_ALL_FAILED 语义失败。"""
        from graphs.validation_retry_loop import _full_import_create

        st = self._mk_reupload_state([COS_AI, COS_MIRROR])
        with mock.patch("graphs.validation_retry_loop.ozon_post") as m_post:
            out = _full_import_create(st)

        m_post.assert_not_called()
        assert out.upload_status == "failed"
        assert out.error_code == "IMAGE_GEN_ALL_FAILED"
        assert "IMAGE_GEN_ALL_FAILED" in (out.error_message or "")

    def test_external_url_blocks_post(self):
        """外链原图混入 → 同样拦截（批E 评审 Important#1 最后缝隙）。"""
        from graphs.validation_retry_loop import _full_import_create

        st = self._mk_reupload_state([ALICDN])
        with mock.patch("graphs.validation_retry_loop.ozon_post") as m_post:
            out = _full_import_create(st)

        m_post.assert_not_called()
        assert out.upload_status == "failed"
        assert out.error_code == "IMAGE_GEN_ALL_FAILED"

    def test_pure_ai_payload_posts_normally(self):
        """纯 AI 载荷（file/images/ + mxou-b64/）→ POST 照常（回归）。"""
        from graphs.validation_retry_loop import _full_import_create

        st = self._mk_reupload_state([COS_AI, COS_B64])
        with mock.patch(
            "graphs.validation_retry_loop.ozon_post",
            return_value={"result": {"task_id": "98765"}},
        ) as m_post:
            out = _full_import_create(st)

        assert m_post.call_count == 1
        assert out.upload_status == "uploaded"
        assert out.task_id == "98765"

    def test_salvage_allowed_with_escape_hatch(self):
        """逃生门 IMAGE_SALVAGE_FALLBACK=1 → salvage 载荷放行（对齐批E 语义）。"""
        from graphs.validation_retry_loop import _full_import_create

        st = self._mk_reupload_state([COS_SALVAGE])
        with mock.patch(
            "graphs.validation_retry_loop.ozon_post",
            return_value={"result": {"task_id": "1"}},
        ) as m_post, mock.patch.dict(os.environ, {"IMAGE_SALVAGE_FALLBACK": "1"}):
            out = _full_import_create(st)

        assert m_post.call_count == 1
        assert out.upload_status == "uploaded"


# ═══════════════════════════════════════════════════════════════════
# H3: card_image_assert.is_all_ai_images 接 image_source
# ═══════════════════════════════════════════════════════════════════

class TestCardAssertAiDetection:
    def test_b64_counts_as_ai(self):
        """RED 先行：mxou-b64/ 兜底图现在算 AI（旧内联 marker 判 False）。"""
        from utils.card_image_assert import is_all_ai_images

        assert is_all_ai_images([COS_B64]) is True

    def test_file_images_regression(self):
        from utils.card_image_assert import is_all_ai_images

        assert is_all_ai_images([COS_AI, COS_AI_2]) is True

    def test_mixed_sources_false(self):
        from utils.card_image_assert import is_all_ai_images

        assert is_all_ai_images([COS_AI, COS_SALVAGE]) is False
        assert is_all_ai_images([COS_AI, COS_MIRROR]) is False
        assert is_all_ai_images([COS_AI, ALICDN]) is False

    def test_empty_false(self):
        from utils.card_image_assert import is_all_ai_images

        assert is_all_ai_images([]) is False
        assert is_all_ai_images(None) is False

    def test_delegates_to_image_source(self):
        """接线锁定：判定走 utils.image_source（禁再内联 URL marker）。"""
        import utils.card_image_assert as mod

        assert mod.image_source.has_generated_images is not None


# ═══════════════════════════════════════════════════════════════════
# 声明完整性（langgraph Input model 过滤吞字段，历史事故两次）+ builder 接线
# ═══════════════════════════════════════════════════════════════════

class TestDeclarationAndWiring:
    def test_subgraph_input_has_new_fields(self):
        from graphs.validation_retry_loop import ValidationRetryLoopInput

        assert "original_images" in ValidationRetryLoopInput.model_fields
        assert "regen_main_image_done" in ValidationRetryLoopInput.model_fields

    def test_subgraph_output_has_regen_gate(self):
        from graphs.validation_retry_loop import ValidationRetryLoopOutput

        assert "regen_main_image_done" in ValidationRetryLoopOutput.model_fields

    def test_wrapper_input_has_new_fields(self):
        from graphs.state import ValidationRetryWrapperInput

        assert "original_images" in ValidationRetryWrapperInput.model_fields
        assert "regen_main_image_done" in ValidationRetryWrapperInput.model_fields

    def test_wrapper_output_has_regen_gate(self):
        from graphs.state import ValidationRetryWrapperOutput

        assert "regen_main_image_done" in ValidationRetryWrapperOutput.model_fields

    def test_global_state_has_regen_gate(self):
        """跨入口防循环：GlobalState 通道承载布尔（validate/status 两次修复入口共享）。"""
        from graphs.state import GlobalState

        assert "regen_main_image_done" in GlobalState.model_fields

    def test_builder_registers_regen_node_and_routes(self):
        """builder 注册 regen_main_image 节点 + selector 路由 + 条件出边。"""
        from graphs import validation_retry_loop as mod

        src_builder = inspect.getsource(mod.create_validation_retry_loop)
        assert 'add_node("regen_main_image"' in src_builder
        assert '"regen_main_image": "regen_main_image"' in src_builder
        src_selector = inspect.getsource(mod.repair_node_selector)
        assert "regen_main_image" in src_selector

    def test_final_result_passes_gate_through(self):
        """final_result 显式透传布尔（output_schema 过滤防线）。"""
        from graphs import validation_retry_loop as mod

        src = inspect.getsource(mod.final_result)
        assert "regen_main_image_done" in src

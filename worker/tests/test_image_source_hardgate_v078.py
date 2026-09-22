"""fix/image-source-hardgate-v1 批A（v0.78）——上架图来源硬闸 + AI 图 marker 盲区修复。

生产取证（docs/PLAN-image-source-hardening-v1.md §0）：卡片出现 1688 原图的唯一
现存来源 = ①生图全败 → E1 兜底（原图转存 COS 上卡）+ ②AI 图识别 marker 盲区
（``"/file/images/"`` 字面子串——b64 兜底图落 ``mxou-b64/`` key 时 retry 守卫全盲
→ 草稿原图覆盖重传）。用户拍板：生图全败 → 任务级失败（不出 1688 图卡），
E1 默认停用；原图仅作生图参考。

覆盖：
- A1 ``utils/image_source.py`` 唯一分类器（ai/salvage/mirror_draft/external/invalid）
- A2 ``validation_retry_loop`` 两处 marker 判定换唯一入口（b64 从盲变明）
- A3 prepare 生图全败硬闸（E1 默认停用 + payload 出口 policy 闸）
- A4 assemble 无图补位收窄（只吃 ai，镜像草稿原图不再补位）

纯 mock 单测（无网络/无 CDP/无 PG）。运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python \\
        -m pytest tests/test_image_source_hardgate_v078.py -q
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

# 环境隔离：COS_*/IMAGE_SALVAGE_FALLBACK 可能来自生产 .env（storage/db.py 模块级
# load_dotenv 注入），import 期先清一次，后续由 autouse fixture 逐用例兜底。
for _k in list(os.environ.keys()):
    if _k.startswith("COS_") or _k == "IMAGE_SALVAGE_FALLBACK":
        os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ── URL 形状（与生产事实源一致，见模块内注释）──────────────────────────
# AI 生成图：生图节点主产物 key=file/images/（image_url_guard 测试同款 URL）
COS_AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/abc.jpeg"
# AI 生成图：prepare 出口会改写为全球加速域名（key 不变）
COS_AI_ACCEL = "https://yss-1256275613.cos.accelerate.myqcloud.com/file/images/abc.jpeg"
# AI 生成图：mxou 同步响应 b64_json 兜底转存（mxou_api._b64_to_cos_url）
COS_B64 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/mxou-b64/t123_abcd.png"
# E1 salvage 原图转存（cos_uploader._stable_key(prefix="ozon-1688")）
COS_SALVAGE = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-1688/salvage/abcd1234.jpg"
COS_SALVAGE_2 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-1688/salvage/efef5678.jpg"
# draft_image_mirror 镜像草稿（_mirror_one prefix="draft-images"）
COS_MIRROR = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/8dbf02b9f.jpg"
# 货源外链（alicdn 原尺寸）
ALICDN = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
# 竞品外链
OZONE = "https://ir.ozone.ru/s3/multimedia-1-x/wc50/12466146633.jpg"


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


# ═══════════════════════════════════════════════════════════════════
# A1: utils/image_source.py 唯一分类器
# ═══════════════════════════════════════════════════════════════════

class TestClassifyImageSource:
    def test_ai_file_images(self):
        from utils.image_source import classify_image_source
        assert classify_image_source(COS_AI) == "ai"

    def test_ai_accelerate_domain(self):
        """prepare 出口会改写成加速域名（key 不变）→ 仍判 ai。"""
        from utils.image_source import classify_image_source
        assert classify_image_source(COS_AI_ACCEL) == "ai"

    def test_ai_b64_key(self):
        """mxou-b64/ 兜底图 = AI 产物（旧 marker 盲区，本批转明）。"""
        from utils.image_source import classify_image_source
        assert classify_image_source(COS_B64) == "ai"

    def test_salvage_key(self):
        from utils.image_source import classify_image_source
        assert classify_image_source(COS_SALVAGE) == "salvage"

    def test_mirror_draft_key(self):
        from utils.image_source import classify_image_source
        assert classify_image_source(COS_MIRROR) == "mirror_draft"

    def test_external_alicdn_and_ozone(self):
        from utils.image_source import classify_image_source
        assert classify_image_source(ALICDN) == "external"
        assert classify_image_source(OZONE) == "external"

    def test_cos_hosted_unknown_key_is_external(self):
        """本方 COS 但 key 不属任何已知通道 → external（宁缺毋滥，不可上卡）。"""
        from utils.image_source import classify_image_source
        assert classify_image_source(
            "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/misc/foo.jpg"
        ) == "external"

    def test_invalid_inputs(self):
        from utils.image_source import classify_image_source
        for bad in (None, "", "   ", "ftp://x/y.jpg", "not-a-url", 123, b"bytes"):
            assert classify_image_source(bad) == "invalid", bad


class TestHasGeneratedImages:
    def test_true_with_file_images(self):
        from utils.image_source import has_generated_images
        assert has_generated_images([COS_AI, COS_MIRROR]) is True

    def test_true_with_b64(self):
        """b64 兜底图算 AI 产物（旧 marker 判 False——盲区根因）。"""
        from utils.image_source import has_generated_images
        assert has_generated_images([COS_B64]) is True

    def test_false_without_ai(self):
        from utils.image_source import has_generated_images
        assert has_generated_images([COS_MIRROR, ALICDN]) is False

    def test_false_empty_and_none(self):
        from utils.image_source import has_generated_images
        assert has_generated_images([]) is False
        assert has_generated_images(None) is False


class TestEnforceUploadPolicy:
    def test_all_ai_passes(self):
        from utils.image_source import enforce_upload_policy
        ok, violations = enforce_upload_policy([COS_AI, COS_B64, COS_AI_ACCEL], allow_salvage=False)
        assert ok is True
        assert violations == []

    def test_salvage_blocked_by_default(self):
        from utils.image_source import enforce_upload_policy
        ok, violations = enforce_upload_policy([COS_AI, COS_SALVAGE], allow_salvage=False)
        assert ok is False
        assert any("salvage" in v and "ozon-1688" in v for v in violations)

    def test_salvage_allowed_with_flag(self):
        from utils.image_source import enforce_upload_policy
        ok, violations = enforce_upload_policy([COS_SALVAGE, COS_SALVAGE_2], allow_salvage=True)
        assert ok is True
        assert violations == []

    def test_mirror_draft_violation_lists_url(self):
        from utils.image_source import enforce_upload_policy
        ok, violations = enforce_upload_policy([COS_AI, COS_MIRROR], allow_salvage=False)
        assert ok is False
        assert any(COS_MIRROR in v for v in violations)

    def test_external_violation(self):
        from utils.image_source import enforce_upload_policy
        ok, violations = enforce_upload_policy([ALICDN], allow_salvage=False)
        assert ok is False
        assert any(ALICDN in v for v in violations)

    def test_empty_passes(self):
        """空图列表不在本闸管辖（空图走既有空图分支语义），恒放行。"""
        from utils.image_source import enforce_upload_policy
        ok, violations = enforce_upload_policy([], allow_salvage=False)
        assert ok is True
        assert violations == []

    def test_allowlist_constant_shape(self):
        from utils.image_source import IMAGE_SOURCE_ALLOWLIST_UPLOAD
        assert "ai" in IMAGE_SOURCE_ALLOWLIST_UPLOAD
        assert "salvage" not in IMAGE_SOURCE_ALLOWLIST_UPLOAD


class TestSalvageFallbackEnv:
    def test_default_off(self):
        from utils.image_source import salvage_fallback_enabled
        assert salvage_fallback_enabled() is False

    def test_zero_off(self):
        from utils.image_source import salvage_fallback_enabled
        os.environ["IMAGE_SALVAGE_FALLBACK"] = "0"
        assert salvage_fallback_enabled() is False

    def test_one_on(self):
        from utils.image_source import salvage_fallback_enabled
        os.environ["IMAGE_SALVAGE_FALLBACK"] = "1"
        assert salvage_fallback_enabled() is True

    def test_true_on(self):
        from utils.image_source import salvage_fallback_enabled
        os.environ["IMAGE_SALVAGE_FALLBACK"] = "true"
        assert salvage_fallback_enabled() is True


# ═══════════════════════════════════════════════════════════════════
# A2: validation_retry_loop marker 盲区修复（b64 从盲变明）
# ═══════════════════════════════════════════════════════════════════

class TestRetryLoopGeneratedImageDetection:
    def test_payload_has_generated_images_file_images(self):
        """既有行为回归：file/images/ 判 True。"""
        from graphs.validation_retry_loop import _payload_has_generated_images
        state = SimpleNamespace(ozon_payload={"items": [{"images": [COS_AI]}]})
        assert _payload_has_generated_images(state) is True

    def test_payload_has_generated_images_b64(self):
        """RED→GREEN：b64 兜底图此前盲判 False → 现判 True（守卫不再被绕过）。"""
        from graphs.validation_retry_loop import _payload_has_generated_images
        state = SimpleNamespace(ozon_payload={"items": [{"images": [COS_B64]}]})
        assert _payload_has_generated_images(state) is True

    def test_payload_has_generated_images_mirror_only_false(self):
        from graphs.validation_retry_loop import _payload_has_generated_images
        state = SimpleNamespace(ozon_payload={"items": [{"images": [COS_MIRROR]}]})
        assert _payload_has_generated_images(state) is False

    def test_payload_empty_items_false(self):
        from graphs.validation_retry_loop import _payload_has_generated_images
        assert _payload_has_generated_images(SimpleNamespace(ozon_payload={})) is False
        assert _payload_has_generated_images(SimpleNamespace(ozon_payload=None)) is False

    def test_prefer_generated_prefers_file_images(self):
        from graphs.validation_retry_loop import _prefer_generated_payload_images
        out = _prefer_generated_payload_images([COS_AI], [COS_MIRROR])
        assert out == [COS_AI]

    def test_prefer_generated_prefers_b64(self):
        """RED→GREEN：b64 图此前不被认作生成图 → 被 draft 原图覆盖（marker 盲区）。"""
        from graphs.validation_retry_loop import _prefer_generated_payload_images
        out = _prefer_generated_payload_images([COS_B64], [COS_MIRROR])
        assert out == [COS_B64]

    def test_prefer_generated_falls_back_to_draft(self):
        from graphs.validation_retry_loop import _prefer_generated_payload_images
        out = _prefer_generated_payload_images([COS_MIRROR], [ALICDN])
        assert out == [ALICDN]


# ═══════════════════════════════════════════════════════════════════
# A3: prepare 生图全败硬闸
# ═══════════════════════════════════════════════════════════════════

def _payload() -> dict:
    return {"items": [{"primary_image": "", "images": []}]}


class TestPrepareNoPrimaryFallback:
    def test_default_raises_and_skips_salvage(self):
        """逃生门关（默认）：抛 IMAGE_GEN_ALL_FAILED，salvage 零调用（不转存原图）。"""
        import graphs.nodes.prepare_ozon_upload_node as mod
        state = SimpleNamespace(original_images=[ALICDN])
        payload, validation_errors = _payload(), []
        with mock.patch("utils.cos_uploader.salvage_original_images") as m_salvage:
            with pytest.raises(mod.ImageGenAllFailedError) as ei:
                mod._apply_no_primary_fallback(state, payload, validation_errors)
        m_salvage.assert_not_called()
        assert "IMAGE_GEN_ALL_FAILED" in str(ei.value)
        assert "生图全部失败" in str(ei.value)
        assert "不出原始图卡片" in str(ei.value)

    def test_escape_hatch_keeps_e1(self):
        """IMAGE_SALVAGE_FALLBACK=1：E1 照旧——salvage 被调用、产物装进 payload。"""
        import graphs.nodes.prepare_ozon_upload_node as mod
        state = SimpleNamespace(original_images=[ALICDN])
        payload, validation_errors = _payload(), []
        os.environ["IMAGE_SALVAGE_FALLBACK"] = "1"
        with mock.patch(
            "utils.cos_uploader.salvage_original_images",
            return_value=[COS_SALVAGE, COS_SALVAGE_2],
        ) as m_salvage:
            mod._apply_no_primary_fallback(state, payload, validation_errors)
        m_salvage.assert_called_once_with([ALICDN])
        assert payload["items"][0]["primary_image"] == COS_SALVAGE
        assert payload["items"][0]["images"] == [COS_SALVAGE_2]
        assert validation_errors == []

    def test_escape_hatch_salvage_empty_keeps_validation_error(self):
        """逃生门开但转存全失败：保持既有空图分支语义（validation error + 空图）。"""
        import graphs.nodes.prepare_ozon_upload_node as mod
        state = SimpleNamespace(original_images=[ALICDN])
        payload, validation_errors = _payload(), []
        os.environ["IMAGE_SALVAGE_FALLBACK"] = "1"
        with mock.patch(
            "utils.cos_uploader.salvage_original_images", return_value=[]
        ):
            mod._apply_no_primary_fallback(state, payload, validation_errors)
        assert payload["items"][0]["primary_image"] == ""
        assert payload["items"][0]["images"] == []
        assert validation_errors == ["营销图片全部为空，生图节点可能全部失败"]


class TestPreparePayloadExitGate:
    def test_ai_payload_passes(self):
        import graphs.nodes.prepare_ozon_upload_node as mod
        payload = {"items": [
            {"primary_image": COS_AI, "images": [COS_B64, COS_AI_ACCEL]},
        ]}
        mod._enforce_payload_image_policy(payload, allow_salvage=False)  # 不抛即过

    def test_mirror_violation_raises_with_violation_list(self):
        """RED→GREEN：镜像草稿原图混入 payload → 抛错且异常消息带违规清单。"""
        import graphs.nodes.prepare_ozon_upload_node as mod
        payload = {"items": [
            {"primary_image": COS_AI, "images": [COS_MIRROR]},
        ]}
        with pytest.raises(mod.ImageGenAllFailedError) as ei:
            mod._enforce_payload_image_policy(payload, allow_salvage=False)
        assert COS_MIRROR in str(ei.value)
        assert "IMAGE_GEN_ALL_FAILED" in str(ei.value)

    def test_salvage_violation_by_default(self):
        import graphs.nodes.prepare_ozon_upload_node as mod
        payload = {"items": [{"primary_image": COS_SALVAGE, "images": []}]}
        with pytest.raises(mod.ImageGenAllFailedError):
            mod._enforce_payload_image_policy(payload, allow_salvage=False)

    def test_salvage_allowed_with_escape_hatch(self):
        import graphs.nodes.prepare_ozon_upload_node as mod
        payload = {"items": [{"primary_image": COS_SALVAGE, "images": [COS_SALVAGE_2]}]}
        mod._enforce_payload_image_policy(payload, allow_salvage=True)  # 不抛即过

    def test_multi_item_and_non_string_tolerant(self):
        """多 item 全查 + 非 str 字段容忍（不误伤）。"""
        import graphs.nodes.prepare_ozon_upload_node as mod
        payload = {"items": [
            {"primary_image": COS_AI, "images": [COS_AI, None, 123]},
            {"primary_image": "", "images": []},
        ]}
        mod._enforce_payload_image_policy(payload, allow_salvage=False)  # 不抛即过

    def test_wiring_in_prepare_node(self):
        """源码级接线锁定：节点 else 分支走 _apply_no_primary_fallback，
        出口闸在 _rewrite_payload_images_to_accelerate 之后调用（最后闸）。"""
        import graphs.nodes.prepare_ozon_upload_node as mod
        src = inspect.getsource(mod.prepare_ozon_upload_node)
        assert "_apply_no_primary_fallback(state" in src
        assert "_enforce_payload_image_policy(ozon_payload" in src


class TestImageGenAllFailedNonPermanent:
    def test_not_classified_permanent_by_task_processor(self):
        """非永久锁定：task_processor._is_permanent_task_error 判 False
        → 整任务重试一轮（生图抖动值得重试），重试仍全败才终态 failed。"""
        from utils.task_processor import _is_permanent_task_error
        from utils.image_source import ImageGenAllFailedError
        assert _is_permanent_task_error(
            ImageGenAllFailedError("IMAGE_GEN_ALL_FAILED: 生图全部失败")) is False


# ═══════════════════════════════════════════════════════════════════
# A4: assemble 无图补位收窄（只吃 ai）
# ═══════════════════════════════════════════════════════════════════

def _enrich(images: list) -> list[dict]:
    import graphs.nodes.assemble_ozon_product_node as mod
    items = [{"offer_id": "sku-1"}]
    return mod._validate_and_enrich_items(
        items,
        [],                      # attr_list：无 schema → 无必填/无字典路径
        {},                      # dict_lookup
        images,
        "cid", "key",
        17028830, 971206780, 300,
        {"length": 100, "width": 100, "height": 50},
        ru_category_path="",     # 跳过 8229 填充（保持纯 mock）
    )


class TestAssembleCosFillNarrowing:
    def test_ai_images_fill(self):
        """AI 图仍是合格补位来源（回归保护）。"""
        out = _enrich([COS_AI, COS_B64])
        assert out[0]["images"] == [COS_AI, COS_B64]
        assert out[0]["primary_image"] == COS_AI

    def test_mirror_draft_no_longer_fills(self):
        """RED→GREEN：镜像草稿原图（draft-images/）不再具备补位资格。"""
        out = _enrich([COS_MIRROR])
        assert not out[0].get("images")
        assert not out[0].get("primary_image")

    def test_salvage_no_longer_fills(self):
        """E1 salvage 产物同样不再补位（硬闸后 salvage 只可能经逃生门进 prepare）。"""
        out = _enrich([COS_SALVAGE])
        assert not out[0].get("images")

    def test_external_still_not_filled(self):
        """外链诚实不补语义保持（既有行为回归）。"""
        out = _enrich([ALICDN])
        assert not out[0].get("images")

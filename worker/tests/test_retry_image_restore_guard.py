"""fix/retry-image-restore-v1 + fix/sentry-ga-retransmit-leak 回归锁。

两代不变量（后者收窄前者）：
1. retry-image-restore-v1（2026-09-18 商品 6381680593 实证）：重传/修复链绝不
   覆盖 AI 生成图——_restore_draft_images_to_payload 只救「空载荷」。
2. sentry-ga-retransmit-leak（2026-10-04，POUDING_OZON-GA n=27 实证）：恢复源
   在**写入 ozon_payload 之前**过 image_source.filter_uploadable_images——
   mirror_draft（draft-images/ 镜像）/salvage（逃生门关）/external（裸 1688 链）
   绝不进载荷；draft 有图但全不可上卡 → IMAGE_GEN_ALL_FAILED（与出口闸同语义，
   非永久 → 整任务重试），draft 真无图 → 既有 rejected_unfixable。
   出口闸降级为最后防线：「先写后拦」路径不复存在。
"""
from __future__ import annotations

import os
import types

import pytest

from graphs.validation_retry_loop import (
    _payload_has_generated_images,
    _prefer_generated_payload_images,
    _restore_draft_images_to_payload,
)
from api.errors import WorkerErrorCode

# 环境隔离（同 test_attr4194_regen_v078）：COS_*/IMAGE_SALVAGE_FALLBACK 可能来自
# 开发机 .env（storage/db.py import 期 load_dotenv 注入），逐用例清空防泄漏。
@pytest.fixture(autouse=True)
def _env_sandbox(monkeypatch):
    for _k in list(os.environ.keys()):
        if _k.startswith("COS_") or _k == "IMAGE_SALVAGE_FALLBACK":
            monkeypatch.delenv(_k, raising=False)

AI_IMG = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/abc.jpeg"
DRAFT_COS = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/deadbeef.jpg"
DRAFT_RAW = "https://cbu01.alicdn.com/img/ibank/O1CN01test.jpg"
SALVAGE_COS = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-1688/salvage/x1.jpg"

_IGAF = WorkerErrorCode.IMAGE_GEN_ALL_FAILED.value


def _state(payload_images, draft_images=None):
    return types.SimpleNamespace(
        ozon_payload={"items": [{"images": list(payload_images or [])}]},
        draft={"images": list(draft_images or [])},
        envelope={"draft": {"images": list(draft_images or [])}},
        error_code="IMAGE_ERROR",
        upload_status=None,
    )


# ── _payload_has_generated_images ──

def test_payload_with_generated_image_detected():
    assert _payload_has_generated_images(_state([AI_IMG])) is True


def test_payload_with_only_draft_mirror_not_generated():
    assert _payload_has_generated_images(_state([DRAFT_COS])) is False


def test_payload_empty_not_generated():
    assert _payload_has_generated_images(_state([])) is False


# ── _restore_draft_images_to_payload：绝不覆盖 AI 图（v1 不变量，逐字保留）──

def test_restore_refuses_when_payload_has_generated_images():
    st = _state([AI_IMG], [DRAFT_RAW])
    assert _restore_draft_images_to_payload(st) is False
    assert st.ozon_payload["items"][0]["images"] == [AI_IMG]
    # 「生成图优先」静默跳过：不置失败语义（区别于写入口剔除路径）
    assert st.error_code == "IMAGE_ERROR"


# ── 写入口净化（fix/sentry-ga-retransmit-leak 回归锁）──

def test_restore_raw_external_draft_fails_retryable_not_written():
    """draft 全裸 1688 外链 → 写入口剔除，不进载荷；IMAGE_GEN_ALL_FAILED 可重试语义。"""
    st = _state([], [DRAFT_RAW])
    assert _restore_draft_images_to_payload(st) is False
    item = st.ozon_payload["items"][0]
    assert item["images"] == [] and "primary_image" not in item
    assert st.error_code == _IGAF
    assert st.upload_status == "failed"
    assert _IGAF in (st.error_message or "")


def test_restore_mirror_draft_fails_retryable_not_written():
    """draft 全 mirror_draft（draft-images/ 镜像）→ 同上（GA 事件的泄漏源，写侧根修）。"""
    st = _state([], [DRAFT_COS])
    assert _restore_draft_images_to_payload(st) is False
    assert st.ozon_payload["items"][0]["images"] == []
    assert st.error_code == _IGAF and st.upload_status == "failed"


def test_restore_keeps_uploadable_and_drops_mirror():
    """混合 [mirror, AI] → 只恢复 AI（写入口剔除 mirror），载荷可过出口闸（路径不可达）。"""
    from graphs.validation_retry_loop import _reupload_gate_blocked

    st = _state([], [DRAFT_COS, AI_IMG])
    assert _restore_draft_images_to_payload(st) is True
    item = st.ozon_payload["items"][0]
    assert item["primary_image"] == item["images"][0]
    assert all("/file/images/" in u for u in item["images"])
    assert DRAFT_COS not in item["images"]
    # 端到端：恢复后的载荷不再触发出口闸（「先写后拦」路径不复存在）
    assert _reupload_gate_blocked(st) is False


def test_restore_ai_draft_needs_no_mirroring(monkeypatch):
    """draft 已是本方 AI 图 → 直接恢复；镜像/转存职责归 spawn_image_mirror，此处不下载。"""
    import services.draft_image_mirror as dim

    st = _state([], [AI_IMG])
    # 恢复链已无转存调用点；若误恢复旧转存块，此 monkeypatch 会让其显形
    monkeypatch.setattr(dim, "_mirror_one",
                        lambda u: pytest.fail("恢复链不应再做转存（幸存者恒为本方 COS）"))
    assert _restore_draft_images_to_payload(st) is True
    item = st.ozon_payload["items"][0]
    assert len(item["images"]) == 1 and "/file/images/" in item["images"][0]
    assert item["primary_image"] == item["images"][0]


def test_restore_salvage_draft_allowed_with_escape_hatch():
    """逃生门 IMAGE_SALVAGE_FALLBACK=1 → salvage 恢复源放行（与出口闸同源动态）。"""
    st = _state([], [SALVAGE_COS])
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("IMAGE_SALVAGE_FALLBACK", "1")
        assert _restore_draft_images_to_payload(st) is True
    out = st.ozon_payload["items"][0]["images"]
    # 尾部加速域名改写只换域不换 key 路径——按 key 断言
    assert len(out) == 1 and "/ozon-1688/salvage/x1.jpg" in out[0]


def test_restore_salvage_draft_blocked_without_escape_hatch():
    """逃生门默认关 → salvage 恢复源剔除（不可上卡），可重试失败语义。"""
    st = _state([], [SALVAGE_COS])
    assert _restore_draft_images_to_payload(st) is False
    assert st.ozon_payload["items"][0]["images"] == []
    assert st.error_code == _IGAF


def test_restore_returns_false_when_draft_also_empty():
    """draft 真无图 → False 且**不**置 IMAGE_GEN_ALL_FAILED（调用方落 rejected_unfixable）。"""
    st = _state([], [])
    assert _restore_draft_images_to_payload(st) is False
    assert st.error_code == "IMAGE_ERROR"
    assert st.upload_status is None


# ── R4 重建取图偏序 ──

def test_prefer_generated_over_draft():
    assert _prefer_generated_payload_images([AI_IMG], [DRAFT_RAW]) == [AI_IMG]


def test_prefer_draft_when_no_generated_in_payload():
    assert _prefer_generated_payload_images([DRAFT_COS], [DRAFT_RAW]) == [DRAFT_RAW]


def test_fallback_to_payload_when_draft_empty():
    assert _prefer_generated_payload_images([DRAFT_COS], []) == [DRAFT_COS]


def test_all_empty_returns_empty():
    assert _prefer_generated_payload_images([], []) == []

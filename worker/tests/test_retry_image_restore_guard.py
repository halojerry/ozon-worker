"""fix/retry-image-restore-v1 回归锁——重传/修复链绝不覆盖 AI 生成图。

实证（2026-09-18 商品 6381680593 / 任务 bf71442e）：首传载荷已装载 AI 图 5 张
（prepare 日志 primary_image=file/images/…），pictures 类错误触发
_restore_draft_images_to_payload 用 draft 原图整体覆盖 → 卡上全为 1688 原图。
"""
from __future__ import annotations

import types

import pytest

from graphs.validation_retry_loop import (
    _payload_has_generated_images,
    _prefer_generated_payload_images,
    _restore_draft_images_to_payload,
)

AI_IMG = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/abc.jpeg"
DRAFT_COS = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/deadbeef.jpg"
DRAFT_RAW = "https://cbu01.alicdn.com/img/ibank/O1CN01test.jpg"
MIRRORED = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/mirrored.jpg"


def _state(payload_images, draft_images=None):
    return types.SimpleNamespace(
        ozon_payload={"items": [{"images": list(payload_images or [])}]},
        draft={"images": list(draft_images or [])},
        envelope={"draft": {"images": list(draft_images or [])}},
    )


# ── _payload_has_generated_images ──

def test_payload_with_generated_image_detected():
    assert _payload_has_generated_images(_state([AI_IMG])) is True


def test_payload_with_only_draft_mirror_not_generated():
    assert _payload_has_generated_images(_state([DRAFT_COS])) is False


def test_payload_empty_not_generated():
    assert _payload_has_generated_images(_state([])) is False


# ── _restore_draft_images_to_payload：绝不覆盖 AI 图 ──

def test_restore_refuses_when_payload_has_generated_images():
    st = _state([AI_IMG], [DRAFT_RAW])
    assert _restore_draft_images_to_payload(st) is False
    assert st.ozon_payload["items"][0]["images"] == [AI_IMG]


def test_restore_proceeds_on_empty_payload_keeps_draft_urls(monkeypatch):
    import utils.cos_uploader as cu
    monkeypatch.setattr(cu, "cos_enabled", lambda: False)
    st = _state([], [DRAFT_RAW])
    assert _restore_draft_images_to_payload(st) is True
    assert st.ozon_payload["items"][0]["images"] == [DRAFT_RAW]
    assert st.ozon_payload["items"][0]["primary_image"] == DRAFT_RAW


def test_restore_sync_mirrors_raw_draft_urls(monkeypatch):
    import utils.cos_uploader as cu
    import services.draft_image_mirror as dim
    monkeypatch.setattr(cu, "cos_enabled", lambda: True)
    monkeypatch.setattr(dim, "_mirror_one", lambda u: MIRRORED if u == DRAFT_RAW else None)
    st = _state([], [DRAFT_RAW])
    assert _restore_draft_images_to_payload(st) is True
    # 尾部 _rewrite_payload_images_to_accelerate 会把区域域名改写为全球加速域名（审核抓图依赖）
    out = st.ozon_payload["items"][0]["images"]
    assert len(out) == 1 and "/draft-images/mirrored.jpg" in out[0] and "myqcloud.com" in out[0]


def test_restore_keeps_already_hosted_urls(monkeypatch):
    import utils.cos_uploader as cu
    import services.draft_image_mirror as dim
    monkeypatch.setattr(cu, "cos_enabled", lambda: True)
    monkeypatch.setattr(dim, "_mirror_one", lambda u: pytest.fail("已托管图不应再转存"))
    st = _state([], [DRAFT_COS])
    assert _restore_draft_images_to_payload(st) is True
    out = st.ozon_payload["items"][0]["images"]
    assert len(out) == 1 and "/draft-images/deadbeef.jpg" in out[0]


def test_restore_returns_false_when_draft_also_empty(monkeypatch):
    import utils.cos_uploader as cu
    monkeypatch.setattr(cu, "cos_enabled", lambda: False)
    assert _restore_draft_images_to_payload(_state([], [])) is False


# ── R4 重建取图偏序 ──

def test_prefer_generated_over_draft():
    assert _prefer_generated_payload_images([AI_IMG], [DRAFT_RAW]) == [AI_IMG]


def test_prefer_draft_when_no_generated_in_payload():
    assert _prefer_generated_payload_images([DRAFT_COS], [DRAFT_RAW]) == [DRAFT_RAW]


def test_fallback_to_payload_when_draft_empty():
    assert _prefer_generated_payload_images([DRAFT_COS], []) == [DRAFT_COS]


def test_all_empty_returns_empty():
    assert _prefer_generated_payload_images([], []) == []

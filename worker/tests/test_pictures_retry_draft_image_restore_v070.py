"""v0.70 实机 gate 回归 + fix/sentry-ga-retransmit-leak 收窄：pictures 无 product_id 的恢复路由。

历史（2026-09-08 gate 实证 task 0e4014cd vs 26fa8072）：采集箱路径生图全失败 →
prepare 产出空载荷首传即拒（IMAGE_ERROR images缺失）。v0.69 原逻辑对
「pictures + 无 product_id」直接 rejected_unfixable——恢复 draft 图成功 →
_full_import_create；draft 也无图才 unfixable。

✅ fix/sentry-ga-retransmit-leak（2026-10-04，POUDING_OZON-GA）收窄恢复源：
v0.78 起「非 AI 图恒不上卡」，恢复路由只对**可上卡来源**（ai；逃生门开时 salvage）
成立——draft 图为 mirror_draft（draft-images/ 镜像）时写入口剔除、置
IMAGE_GEN_ALL_FAILED（非永久 → 整任务重试重跑生图），绝不写载荷再被出口闸拦
（「先写后拦」= 白写一轮 + Sentry 噪音）。draft 真无图仍 rejected_unfixable。
纯 mock，无需 PG/GPU。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.validation_retry_loop import (  # noqa: E402
    ValidationRetryLoopState,
    _restore_draft_images_to_payload,
    reupload_node,
)
from api.errors import WorkerErrorCode  # noqa: E402

_IGAF = WorkerErrorCode.IMAGE_GEN_ALL_FAILED.value


def _state(**kw) -> ValidationRetryLoopState:
    base = dict(
        error_code="IMAGE_ERROR",
        product_id="",  # 空=无卡（字段为 str 必填，falsy 即无 product_id）
        draft={},
        ozon_payload={"items": [{"images": [], "primary_image": None, "attributes": []}]},
    )
    base.update(kw)
    return ValidationRetryLoopState(**base)


def test_pictures_no_product_with_draft_images_restores_and_full_create(monkeypatch):
    """gate 主案例：draft 有**可上卡（AI）图** → 恢复载荷 + 走全量 CREATE。

    （fix/sentry-ga-retransmit-leak 起恢复源必须是 ai 类；draft-images/ 镜像不再
    具备上卡资格，见下方 mirror-only 用例。）
    """
    calls = {}
    monkeypatch.setattr(
        "graphs.validation_retry_loop._full_import_create",
        lambda st: (calls.setdefault("ran", True), st)[1],
    )
    st = _state(draft={"images": [
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/a.jpeg",
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/b.jpeg",
    ]})
    out = reupload_node(st)
    assert calls.get("ran") is True
    assert out.upload_status != "rejected_unfixable"
    item = out.ozon_payload["items"][0]
    assert len(item["images"]) == 2
    assert item["primary_image"] == item["images"][0]
    # 与标准 prepare 同源：恢复后的图必须已是 COS 全球加速域名（审核抓图依赖）
    assert all("cos.accelerate.myqcloud.com" in u for u in item["images"])


def test_pictures_no_product_mirror_only_draft_fails_retryable(monkeypatch):
    """GA 根修回归：draft 全 mirror_draft → 写入口剔除不进载荷，不 POST，
    IMAGE_GEN_ALL_FAILED（非永久 → 整任务重试），绝不 rejected_unfixable。"""
    monkeypatch.setattr(
        "graphs.validation_retry_loop._full_import_create",
        lambda st: pytest.fail("恢复源不可上卡时不应触发全量 CREATE"),
    )
    mirror = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/8dbf02b9f.jpg"
    st = _state(draft={"images": [mirror]})
    out = reupload_node(st)
    item = out.ozon_payload["items"][0]
    assert item["images"] == [] and item["primary_image"] is None  # 载荷零污染
    assert out.upload_status == "failed"
    assert out.error_code == _IGAF
    assert out.is_valid is False  # 非终态（区别于 rejected_unfixable）


def test_pictures_no_product_no_images_anywhere_stays_unfixable(monkeypatch):
    """draft/envelope 都无图 → 维持 rejected_unfixable（诚实不硬修，不烧重试）。"""
    monkeypatch.setattr(
        "graphs.validation_retry_loop._full_import_create",
        lambda st: pytest.fail("无图时不应触发全量 CREATE"),
    )
    st = _state()
    out = reupload_node(st)
    assert out.upload_status == "rejected_unfixable"
    assert out.is_valid is True


def test_pictures_with_product_id_uses_pictures_import_unchanged(monkeypatch):
    """有 product_id 的图片错误仍走 pictures/import 整体替换（v0.69 行为不变）。"""
    monkeypatch.setattr(
        "graphs.validation_retry_loop._fix_via_pictures_import",
        lambda st: True,
    )
    st = _state(product_id="123", draft={"images": ["https://cos.example.com/x.jpg"]})
    out = reupload_node(st)
    assert out.upload_status == "success"


def test_restore_helper_payload_shaped_guard():
    """payload items 为空/首项非 dict 时恢复失败返回 False，不炸。"""
    st = _state(draft={"images": ["https://cos.example.com/x.jpg"]},
                ozon_payload={"items": []})
    assert _restore_draft_images_to_payload(st) is False
    st2 = _state(draft={"images": []})
    assert _restore_draft_images_to_payload(st2) is False

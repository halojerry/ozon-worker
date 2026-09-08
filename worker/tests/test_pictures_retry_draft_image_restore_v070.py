"""v0.70 实机 gate 回归：pictures 错误无 product_id 时恢复 draft 图后全量 CREATE。

背景（2026-09-08 gate 实证 task 0e4014cd vs 26fa8072）：采集箱路径生图全失败 →
prepare 产出空载荷首传即拒（IMAGE_ERROR images缺失）。v0.69 原逻辑对
「pictures + 无 product_id」直接 rejected_unfixable——但同批 26fa8072 经 R4 重建
恢复 draft 图（COS 转存，Ozon 可访问）后全量 CREATE 过审 approved，证明该死端
可以救。修复：恢复 draft 图成功 → _full_import_create；draft 也无图才 unfixable。
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
    """gate 主案例：draft 有 COS 图 → 恢复载荷 + 走全量 CREATE（不再 rejected_unfixable）。"""
    calls = {}
    monkeypatch.setattr(
        "graphs.validation_retry_loop._full_import_create",
        lambda st: (calls.setdefault("ran", True), st)[1],
    )
    st = _state(draft={"images": [
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/a.jpg",
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/b.jpg",
    ]})
    out = reupload_node(st)
    assert calls.get("ran") is True
    assert out.upload_status != "rejected_unfixable"
    item = out.ozon_payload["items"][0]
    assert len(item["images"]) == 2
    assert item["primary_image"] == item["images"][0]
    # 与标准 prepare 同源：恢复后的图必须已是 COS 全球加速域名（审核抓图依赖）
    assert all("cos.accelerate.myqcloud.com" in u for u in item["images"])


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

# -*- coding: utf-8 -*-
"""
v0.77.1 — 上传前零图硬闸（纯 mock，无 PG）。

生产实证（2026-09-18 升级核验）：5 例 Ozon 审核拒绝 IMAGE_ERROR（item[0].images
缺失，最新 09-18 15:56），抽查 task 3e22a11e-… 在 task_generated_images **0 行**
（生图零产物）却仍走到 /v3/product/import → Ozon 必拒 + 白烧创建配额。

契约：
- CREATE 项（item 无 product_id）images 缺失/全空 → 上传前显式阻断：
  upload_status="failed"、error_code="LOCAL_IMAGES_MISSING"、failed_stage="ozon_upload"，
  且 /v3/product/import 绝不发出（quota/offer 预检之后、import POST 之前）。
- UPDATE 项（item 带 product_id：编辑更新/跟卖 UPDATE）0 图**合法**——
  不动卡上既有图片，照常上传。
- 闸在 _maybe_upsert_existing_offer **之后**：offer 已存在被注入 product_id
  转 UPDATE 的卡不受闸误伤（老卡自带图片，0 图更新=保留）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_upload_image_guard_v077.py -q
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.ozon_upload_node import ozon_upload_node  # noqa: E402
from graphs.state import OzonUploadInput  # noqa: E402

TASK_ID = "7312849091235"
OFFER = "offer-v077-imgguard"
IMG = "https://img.example.com/1.jpg"


def _state(items):
    return OzonUploadInput(
        ozon_payload={"items": items},
        ozon_client_id="123",
        ozon_api_key="key",
        sku_id=OFFER,
    )


def _run(state, find_offer=None):
    """跑 upload 节点：mock 配额 + offer 查找 + import POST 捕获（对齐 test_upload_silent_v073）。"""
    calls = {"post": 0}

    def _post_spy(*a, **k):
        calls["post"] += 1
        return {"result": {"task_id": TASK_ID}}

    with patch("graphs.nodes.ozon_upload_node.ozon_check_quota",
               return_value={"ok": True, "daily_used": 1, "daily_limit": 100,
                             "total_used": 1, "total_limit": 1000,
                             "remaining_daily": 99, "remaining_total": 999}), \
         patch("graphs.nodes.ozon_upload_node.find_product_by_offer",
               return_value=find_offer), \
         patch("graphs.nodes.ozon_upload_node.session"), \
         patch("graphs.nodes.ozon_upload_node.ozon_post", side_effect=_post_spy):
        out = ozon_upload_node(state, None, SimpleNamespace(context=None))
    return out, calls


def _create_item(images=None, drop_images=False):
    item = {"name": "Тест", "offer_id": OFFER, "price": "100", "old_price": "120"}
    if not drop_images:
        item["images"] = images if images is not None else []
    return item


# ── ① CREATE + images=[] → 阻断，绝不发 import POST ──
def test_create_zero_images_blocked_before_import():
    out, calls = _run(_state([_create_item(images=[])]))
    assert out.upload_status == "failed", "CREATE 0 图必被 Ozon 拒，必须上传前阻断"
    assert calls["post"] == 0, "阻断必须发生在 /v3/product/import 之前（省配额）"
    assert out.error_code == "LOCAL_IMAGES_MISSING", (
        f"error_code 必须为 LOCAL_IMAGES_MISSING（GraphOutput 透传→listing_result_log），"
        f"实际 {out.error_code!r}"
    )
    assert out.failed_stage == "ozon_upload"
    assert "图片" in (out.error_message or ""), (
        f"error_message 应标注图片缺失原因，实际 {out.error_message!r}"
    )


# ── ② CREATE + images 键整体缺失 → 同一闸（生产 5 例即「images 缺失」形态）──
def test_create_missing_images_key_blocked():
    out, calls = _run(_state([_create_item(drop_images=True)]))
    assert out.upload_status == "failed"
    assert calls["post"] == 0
    assert out.error_code == "LOCAL_IMAGES_MISSING"


# ── ③ UPDATE 项（带 product_id）0 图 → 放行（保留卡上既有图片，合法语义）──
def test_update_item_zero_images_passes():
    item = _create_item(images=[])
    item["product_id"] = 555
    out, calls = _run(_state([item]))
    assert out.upload_status == "success", "UPDATE 0 图=不动卡上图片，合法，不得误拦"
    assert calls["post"] == 1
    assert not out.error_code


# ── ④ CREATE 带图 → 放行 ──
def test_create_with_images_passes():
    out, calls = _run(_state([_create_item(images=[IMG])]))
    assert out.upload_status == "success"
    assert calls["post"] == 1
    assert not out.error_code


# ── ⑤ images 全空白串/空白项视同空 → 阻断 ──
def test_create_whitespace_only_images_blocked():
    out, calls = _run(_state([_create_item(images=["", "   "])]))
    assert out.upload_status == "failed"
    assert calls["post"] == 0
    assert out.error_code == "LOCAL_IMAGES_MISSING"


# ── ⑥ 闸在 offer upsert 之后：死卡注入 product_id 转 UPDATE → 不受闸误伤 ──
def test_guard_runs_after_offer_upsert_rescue():
    dead_card = {"product_id": "549733785579", "offer_id": OFFER, "state": "declined"}
    out, calls = _run(_state([_create_item(images=[])]), find_offer=dead_card)
    assert out.upload_status == "success", (
        "offer 已存在→upsert 注入 product_id 转 UPDATE（0 图=保留老卡图片），闸不得误拦"
    )
    assert calls["post"] == 1

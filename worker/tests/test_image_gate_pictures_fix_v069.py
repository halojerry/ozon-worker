#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.69 图片镜像闸 + pictures/import 修复通道 + 8229 不变式 回归。

declined 单（IMAGE_ERROR images=0）根因修复的三道防线：
① 主因：外链图片漂移——draft 镜像失败保持外链 + submit 原样透传 + validate
   时点探测防不住「Ozon 异步抓图时已失效/防盗链」→ 卡片 0 图必拒。
   修复：submit 镜像闸（同步兜底 + 422）+ validate 全外链硬拦。
② 修复通道：官方 POST /v1/product/pictures/import 整体替换卡片图片——同链
   re-import 对「链接未变」会被 Ozon skipped，死链必须换 COS URL 才修得好。
③ 次因：8229 «Тип» 官方不变式 dictionary_value_id == type_id——attributes/update
   补发链强制携带 + revalidate 合并收口。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_gate_pictures_fix_v069.py -v
纯 mock/纯函数，无需 PG/GPU。
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest import mock

import pytest

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.validation_retry_loop import (  # noqa: E402
    ValidationRetryLoopState,
    _ensure_type_attr_8229,
    _fix_via_attributes_update,
    _fix_via_pictures_import,
    reupload_node,
    revalidate_node,
)
from services.draft_service import _ensure_images_mirrored_for_submit  # noqa: E402
from utils.cos_uploader import is_cos_url  # noqa: E402

COS = "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/a.jpg"
COS2 = "https://test-bucket.cos.accelerate.myqcloud.com/draft-images/b.jpg"
EXT = "https://cbu01.alicdn.com/img/ibank/x.jpg"


# ═══════════════════════════════════════════════════════════════════════
# 共享判定 is_cos_url（三处同源，禁止内联）
# ═══════════════════════════════════════════════════════════════════════

def test_is_cos_url_shared_predicate():
    assert is_cos_url(COS)
    assert is_cos_url(COS2)
    assert not is_cos_url(EXT)
    assert not is_cos_url("https://ir.ozone.ru/s3/multimedia/1.jpg")
    assert is_cos_url("") and is_cos_url(None)  # 空值不判外（由上层空值检查负责）


# ═══════════════════════════════════════════════════════════════════════
# ① submit 镜像闸（draft_service._ensure_images_mirrored_for_submit）
# ═══════════════════════════════════════════════════════════════════════

def _env(images):
    return {"draft": {"title": "t", "images": images}, "source": {}, "extensions": {}}


def test_gate_all_cos_passthrough_no_mirror():
    env = _env([COS])
    with mock.patch("services.draft_image_mirror.mirror_draft_images") as m_mirror:
        out = asyncio.run(_ensure_images_mirrored_for_submit("t1", "d1", {"version": 1}, env))
    assert out is env
    m_mirror.assert_not_called()


def test_gate_empty_images_passthrough():
    env = _env([])
    out = asyncio.run(_ensure_images_mirrored_for_submit("t1", "d1", {"version": 1}, env))
    assert out is env


def test_gate_mirrors_external_then_persists():
    env = _env([EXT])
    draft = {"version": 3}
    with mock.patch("utils.cos_uploader.cos_enabled", return_value=True), \
         mock.patch("services.draft_image_mirror.mirror_draft_images",
                    return_value=([COS], True)) as m_mirror, \
         mock.patch("services.draft_image_mirror._update_payload_guarded") as m_guard:
        out = asyncio.run(_ensure_images_mirrored_for_submit("t1", "d1", draft, env))
    m_mirror.assert_called_once()
    assert out["draft"]["images"] == [COS], "提交信封必须替换为 COS 图"
    m_guard.assert_called_once()
    assert m_guard.call_args[0][4] == "mirrored", "镜像成功应回写 image_mirror_state=mirrored"


def test_gate_raises_422_when_still_external():
    env = _env([EXT])
    with mock.patch("utils.cos_uploader.cos_enabled", return_value=True), \
         mock.patch("services.draft_image_mirror.mirror_draft_images",
                    return_value=([EXT], False)):
        with pytest.raises(Exception) as ei:
            asyncio.run(_ensure_images_mirrored_for_submit("t1", "d1", {"version": 1}, env))
    assert getattr(ei.value, "status_code", None) == 422, "外链镜像失败必须 422 拒绝提交"


def test_gate_degrades_when_cos_disabled():
    """COS 未配置的部署无镜像能力 → 降级放行（按旧行为），不 422 封死。"""
    env = _env([EXT])
    with mock.patch("utils.cos_uploader.cos_enabled", return_value=False):
        out = asyncio.run(_ensure_images_mirrored_for_submit("t1", "d1", {"version": 1}, env))
    assert out["draft"]["images"] == [EXT]


# ═══════════════════════════════════════════════════════════════════════
# ② retry pictures 修复通道（reupload_node + _fix_via_pictures_import）
# ═══════════════════════════════════════════════════════════════════════

def _img_state(error="all_image_failed", product_id="123", item=None):
    return ValidationRetryLoopState(
        error_code=error,
        product_id=product_id,
        ozon_client_id="cid",
        ozon_api_key="key",
        ozon_payload={"items": [item if item is not None else {
            "offer_id": "o1", "primary_image": COS, "images": [EXT],
        }]},
    )


def test_reupload_pictures_success_marks_success():
    with mock.patch("graphs.validation_retry_loop._fix_via_pictures_import",
                    return_value=True) as m_fix:
        out = reupload_node(_img_state())
    assert out.upload_status == "success"
    assert out.is_valid is True
    m_fix.assert_called_once()


def test_reupload_pictures_failure_falls_to_rejected_unfixable():
    """无 COS 图 / API 失败 → 保持旧 unfixable 收敛（诚实不硬修）。"""
    with mock.patch("graphs.validation_retry_loop._fix_via_pictures_import",
                    return_value=False):
        out = reupload_node(_img_state())
    assert out.upload_status == "rejected_unfixable"
    assert out.is_valid is True


def test_reupload_pictures_without_product_id_never_recreates():
    """图片错误且卡片不存在 → pictures/import 无 from，绝不能重发同链 CREATE。"""
    with mock.patch("graphs.validation_retry_loop._full_import_create") as m_create, \
         mock.patch("graphs.validation_retry_loop._fix_via_pictures_import") as m_fix:
        out = reupload_node(_img_state(product_id=""))
    assert out.upload_status == "rejected_unfixable"
    m_create.assert_not_called()
    m_fix.assert_not_called()


def test_pictures_fix_sends_cos_only_primary_first():
    captured = {}

    def fake(client_id, api_key, product_id, images, **kw):
        captured.update(images=list(images), pid=product_id)
        return {"result": {"pictures": [{"state": "uploaded"}]}}

    # primary 为外链（死链）→ 从推送列表剔除；COS 图按序补位
    state = _img_state(item={"offer_id": "o1", "primary_image": EXT,
                             "images": [COS, EXT, COS2]})
    with mock.patch("utils.ozon_client.ozon_import_product_pictures", side_effect=fake):
        assert _fix_via_pictures_import(state) is True
    assert captured["images"] == [COS, COS2], "外链必须过滤，COS 图按序推送"
    assert captured["pid"] == "123"


def test_pictures_fix_all_external_returns_false_without_call():
    state = _img_state(item={"offer_id": "o1", "primary_image": EXT, "images": [EXT]})
    with mock.patch("utils.ozon_client.ozon_import_product_pictures") as m_api:
        assert _fix_via_pictures_import(state) is False
    m_api.assert_not_called()


def test_import_product_pictures_client_body_shape():
    import utils.ozon_client as oc
    with mock.patch.object(oc, "ozon_post", return_value={"result": {}}) as m_p:
        oc.ozon_import_product_pictures("cid", "key", "123", ["u1", "", "u2"],
                                        color_image="c1")
    assert m_p.call_args[0][2] == "/v1/product/pictures/import"
    body = m_p.call_args[0][3]
    assert body == {"product_id": 123, "images": ["u1", "u2"], "color_image": "c1"}


# ═══════════════════════════════════════════════════════════════════════
# ③ 8229 «Тип» 不变式（dictionary_value_id == type_id）
# ═══════════════════════════════════════════════════════════════════════

def test_ensure_8229_keeps_existing():
    attrs = [{"id": 8229, "values": [{"dictionary_value_id": 9, "value": "x"}]}]
    assert _ensure_type_attr_8229(attrs, {}) is attrs


def test_ensure_8229_from_snapshot():
    item = {"attributes": [{"id": 8229,
                            "values": [{"dictionary_value_id": 92147, "value": "Тип"}]}]}
    out = _ensure_type_attr_8229([], item)
    assert out[-1]["id"] == 8229
    assert out[-1]["values"][0]["dictionary_value_id"] == 92147


def test_ensure_8229_falls_to_type_id_when_snapshot_dict_id_zero():
    item = {"type_id": 92147,
            "attributes": [{"id": 8229, "values": [{"dictionary_value_id": 0, "value": "Т"}]}]}
    out = _ensure_type_attr_8229([], item)
    assert out[-1]["values"][0]["dictionary_value_id"] == 92147


def test_ensure_8229_unresolvable_no_crash():
    assert _ensure_type_attr_8229([], {}) == []


def test_attributes_update_body_carries_8229():
    """final_attributes 缺 8229 → 增量补发 body 强制携带（官方合并语义下恒安全）。"""
    from utils.http_session import session as shared_session

    state = ValidationRetryLoopState(
        error_code="MISSING_REQUIRED_ATTRIBUTE", attribute_id=4389,
        product_id="123", ozon_client_id="cid", ozon_api_key="key",
        final_attributes=[{"id": 4389, "value": "Китай", "dictionary_value_id": 90296}],
        ozon_payload={"items": [{"offer_id": "o1", "type_id": 92147, "attributes": []}]},
    )

    class _R:
        status_code = 200
        text = ""

        def json(self):
            return {"task_id": 7}

    with mock.patch.object(shared_session, "post", return_value=_R()) as m_post:
        assert _fix_via_attributes_update(state) is True
    body = m_post.call_args.kwargs["json"]
    attrs = {a["id"]: a for a in body["items"][0]["attributes"]}
    assert 8229 in attrs, "补发 body 必须强制携带必填 8229"
    assert attrs[8229]["values"][0]["dictionary_value_id"] == 92147


def _revalidate_state(item_attrs, final_attrs, schema, type_id=92147):
    return ValidationRetryLoopState(
        ozon_payload={"items": [{
            "name": "Термокружка", "offer_id": "cup1", "price": "500",
            "type_id": type_id,
            "attributes": item_attrs,
        }]},
        final_attributes=final_attrs,
        attributes_schema=schema,
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028653", type_id=str(type_id),
    )


def test_revalidate_8229_rebuilt_from_type_id_when_lost():
    """收口：合并后 payload 仍缺 8229（重建丢属性）→ 按 item.type_id 补造。"""
    state = _revalidate_state(
        item_attrs=[{"complex_id": 0, "id": 9048,
                     "values": [{"dictionary_value_id": 0, "value": "cup1"}]}],
        final_attrs=[{"id": 9048, "value": "cup1", "dictionary_value_id": 0}],
        schema=[{"id": 9048, "name": "Артикул", "dictionary_id": 0}],
    )
    out = revalidate_node(state)
    ids = {int(a["id"]) for a in out.ozon_payload["items"][0]["attributes"]}
    assert 8229 in ids, "合并后缺 8229 必须按 type_id 收口补造（必填缺失=Ozon 必拒）"


def test_revalidate_8229_final_dict0_uses_type_id_not_skip():
    """final 8229 dict_id=0 + 快照无 8229 → 按 type_id 补造（不再静默跳过）。"""
    state = _revalidate_state(
        item_attrs=[{"complex_id": 0, "id": 9048,
                     "values": [{"dictionary_value_id": 0, "value": "cup1"}]}],
        final_attrs=[
            {"id": 9048, "value": "cup1", "dictionary_value_id": 0},
            {"id": 8229, "value": "Термокружка", "dictionary_value_id": 0},
        ],
        schema=[
            {"id": 9048, "name": "Артикул", "dictionary_id": 0},
            {"id": 8229, "name": "Тип", "dictionary_id": 504},
        ],
    )
    out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert 8229 in attrs
    assert attrs[8229]["values"][0]["dictionary_value_id"] == 92147


def test_revalidate_8229_final_dict0_keeps_snapshot_value():
    """final 8229 dict_id=0 + 快照有合法值 → 保留快照（dict_value 不变量）。"""
    state = _revalidate_state(
        item_attrs=[
            {"complex_id": 0, "id": 8229,
             "values": [{"dictionary_value_id": 92147, "value": "Термокружка"}]},
            {"complex_id": 0, "id": 9048,
             "values": [{"dictionary_value_id": 0, "value": "cup1"}]},
        ],
        final_attrs=[
            {"id": 9048, "value": "cup1", "dictionary_value_id": 0},
            {"id": 8229, "value": "Термокружка", "dictionary_value_id": 0},
        ],
        schema=[
            {"id": 9048, "name": "Артикул", "dictionary_id": 0},
            {"id": 8229, "name": "Тип", "dictionary_id": 504},
        ],
    )
    out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert attrs[8229]["values"][0]["dictionary_value_id"] == 92147, "快照合法值必须保留"


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

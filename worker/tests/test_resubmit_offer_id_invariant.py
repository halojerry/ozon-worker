"""M1.1: 重上不变式 — 复用原 draft_id + 原 offer_id（不产生双卡）。

锁定契约（WebUI 运营工作台 M1.1）：
- `draft_service._resolve_offer_id` 确定性：跟卖 follow_{竞品id}，否则 draft.item_id/sku_id
- 同一 draft 重复提交 → 两次解析的 offer_id 一致（submit 用同一 offer_id 做 sku_key）
- per-store 409 校验生效：目标店铺已存在该 offer_id → 409，不入队（不产生双卡）

只加测试锁定，不改 submit_draft 逻辑。mock engine + mock 外部 API，无需 PG。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_resubmit_offer_id_invariant.py -q
"""
import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import draft_service

ITEM_ID = "980815374096"
COMPETITOR_ID = "3852000144"
CLIENT_ID = "4718259"
API_KEY = "sk-api-key-AAAA1111"


def make_envelope(*, follow: bool = False) -> dict:
    draft = {
        "item_id": ITEM_ID,
        "title": "宠物自动饮水器",
        "images": ["https://cbu01.alicdn.com/x.jpg"],
        "weight": 227,
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "purchase_cost": 5.5,
        "purchase_url": f"https://detail.1688.com/offer/{ITEM_ID}.html",
    }
    extensions = {"margin_rate": 0.25, "commission_rate": 0.10}
    if follow:
        draft["ozon_product_id"] = COMPETITOR_ID
        extensions["follow_sell"] = True
    return {
        "draft": draft,
        "source": {"purchase_url": draft["purchase_url"], "purchase_cost": 5.5},
        "extensions": extensions,
    }


# ============================================================
# 1. _resolve_offer_id 确定性：同一 envelope 两次解析一致
# ============================================================

def test_resolve_offer_id_deterministic_non_follow():
    envelope = make_envelope()
    first = draft_service._resolve_offer_id(envelope)
    second = draft_service._resolve_offer_id(envelope)
    assert first == ITEM_ID
    assert first == second, "同一 draft 两次解析 offer_id 必须一致（重上复用原 offer_id）"


def test_resolve_offer_id_deterministic_follow():
    envelope = make_envelope(follow=True)
    first = draft_service._resolve_offer_id(envelope)
    second = draft_service._resolve_offer_id(envelope)
    assert first == f"follow_{COMPETITOR_ID}"
    assert first == second


def test_resolve_offer_id_falls_back_to_sku_id():
    envelope = make_envelope()
    envelope["draft"].pop("item_id")
    envelope["draft"]["sku_id"] = "sku_001"
    assert draft_service._resolve_offer_id(envelope) == "sku_001"


def test_resolve_offer_id_empty_when_no_id():
    envelope = make_envelope()
    envelope["draft"].pop("item_id")
    assert draft_service._resolve_offer_id(envelope) == ""


# ============================================================
# 2. 同一 draft 两次 submit_draft：offer_id 一致 + per-store 409（不双卡）
# ============================================================

def _mock_engine():
    """submit_draft 依赖：cross_store_scan（connect）+ submission INSERT（begin）。"""
    engine = MagicMock()
    conn = engine.connect.return_value
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = []   # 无跨店记录
    conn.execute.return_value.fetchone.return_value = None

    bconn = engine.begin.return_value
    bconn.__enter__.return_value = bconn
    sub_row = MagicMock()
    sub_row.id = uuid.uuid4()
    bconn.execute.return_value.fetchone.return_value = sub_row
    return engine


def _patch_deps(engine, ozon_result, envelope=None):
    """组装 submit_draft 的全部 mock 依赖。返回 (calls, _submit_task mock)。

    credential_id=None → submit_draft 走 get_default_credential（mock 默认店铺凭证）。
    """
    envelope = envelope if envelope is not None else make_envelope()
    calls = {"get_draft": [], "ozon": [], "submit": []}
    mock_task = MagicMock()

    async def _submit_task(tenant_id, payload, sku_key=""):
        calls["submit"].append(sku_key)
        return str(uuid.uuid4())

    mock_task.side_effect = _submit_task

    def _fake_get_draft(tenant_id, draft_id):
        calls["get_draft"].append((tenant_id, draft_id))
        return {"id": draft_id, "tenant_id": tenant_id, "payload": envelope}

    def _fake_ozon(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls["ozon"].append({"client_id": client_id, "endpoint": endpoint, "body": body})
        return ozon_result

    patches = [
        patch.object(draft_service, "get_engine", return_value=engine),
        patch.object(draft_service, "get_draft", side_effect=_fake_get_draft),
        patch.object(draft_service, "ozon_post", side_effect=_fake_ozon),
        patch.object(draft_service.credential_service, "get_default_credential",
                     return_value={"id": "cred-default-1", "ozon_client_id": CLIENT_ID,
                                   "api_key": API_KEY}),
        patch.object(draft_service, "_submit_task", mock_task),
    ]
    for p in patches:
        p.start()
    return calls, mock_task


def test_same_draft_two_submits_use_same_offer_id_then_409():
    """第一次提交（店铺无此商品）→ 正常入队，sku_key=原 offer_id；
    第二次提交（店铺已有该 offer_id）→ 409，不入队（不产生双卡）。"""
    draft_id = str(uuid.uuid4())

    # 第一次：Ozon 无此商品 → 放行入队
    engine = _mock_engine()
    calls, mock_task = _patch_deps(engine, ozon_result={"items": [], "total": 0})
    try:
        first = asyncio.run(draft_service.submit_draft("u1", draft_id, "sk-tok123", None))
    finally:
        patch.stopall()

    assert first["ok"] is True
    assert calls["submit"] == [f"u1:{CLIENT_ID}:{ITEM_ID}"], \
        "第一次提交 sku_key 必须含原 offer_id（sku_key=tenant:client:offer_id）"
    assert calls["ozon"][0]["body"]["offer_id"] == [ITEM_ID], \
        "per-store 校验必须用原 offer_id"
    offer_id_sent_first = calls["submit"][0].rsplit(":", 1)[-1]
    assert offer_id_sent_first == ITEM_ID

    # 第二次：Ozon 已存在该 offer_id → per-store 409，_submit_task 不再被调用
    engine2 = _mock_engine()
    calls2, mock_task2 = _patch_deps(engine2, ozon_result={
        "items": [{"offer_id": ITEM_ID, "product_id": "5476361418"}], "total": 1})
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(draft_service.submit_draft("u1", draft_id, "sk-tok123", None))
    finally:
        patch.stopall()

    assert exc.value.status_code == 409
    assert draft_service.DUP_MESSAGE in exc.value.detail
    assert calls2["submit"] == [], "409 必须阻止入队（不产生双卡）"
    assert calls2["ozon"][0]["body"]["offer_id"] == [offer_id_sent_first], \
        "第二次重复校验必须查同一个 offer_id（重上复用原 offer_id）"


def test_follow_submit_uses_follow_offer_id_consistently():
    """跟卖信封两次解析 offer_id 一致（follow_{竞品id}），409 校验同 offer_id。"""
    draft_id = str(uuid.uuid4())

    engine = _mock_engine()
    calls, _ = _patch_deps(engine, ozon_result={"items": [], "total": 0},
                           envelope=make_envelope(follow=True))
    try:
        asyncio.run(draft_service.submit_draft("u1", draft_id, "sk-tok123", None))
    finally:
        patch.stopall()

    expected = f"follow_{COMPETITOR_ID}"
    assert calls["submit"] == [f"u1:{CLIENT_ID}:{expected}"]
    assert calls["ozon"][0]["body"]["offer_id"] == [expected]

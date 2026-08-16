"""P0-4: 订单路由 — 参数解析 + 鉴权 + 调 order_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /orders?credential_id=&status=&limit=&offset=&since_days=

token 来源：Authorization: Bearer 优先，body token 兜底（与 credentials_routes 一致）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from api.schemas import (
    CancelReasonOut,
    CancelRequest,
    OrderActionResponse,
    OrderLabelResponse,
    OrderListResponse,
    OrderNoteOut,
    OrderNoteUpsert,
)
from services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    try:
        raw = await request.body()
        if raw:
            data = json.loads(raw.decode("utf-8"))
            token = str(data.get("token", "") or "")
            if token:
                return _authenticate_token(token)
    except Exception:
        pass
    return _authenticate_token("")


@router.get("", response_model=OrderListResponse)
async def list_orders(
    request: Request,
    credential_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    since_days: int = 30,
):
    tenant_id = await _authenticate(request)
    return order_service.list_orders(
        tenant_id, credential_id=credential_id, status=status,
        limit=limit, offset=offset, since_days=since_days,
    )


@router.get("/{posting_number}/notes", response_model=OrderNoteOut)
async def get_order_notes(posting_number: str, request: Request):
    tenant_id = await _authenticate(request)
    return order_service.get_order_notes(tenant_id, posting_number)


@router.put("/{posting_number}/notes", response_model=OrderNoteOut)
async def upsert_order_notes(posting_number: str, request: Request):
    tenant_id = await _authenticate(request)
    data = await request.json()
    return order_service.upsert_order_notes(tenant_id, posting_number, data)


@router.get("/{posting_number}/label", response_model=OrderLabelResponse)
async def get_order_label(
    posting_number: str,
    request: Request,
    credential_id: str | None = None,
):
    tenant_id = await _authenticate(request)
    return order_service.get_order_label(tenant_id, posting_number, credential_id)


@router.post("/{posting_number}/ship", response_model=OrderActionResponse)
async def ship_order(posting_number: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return order_service.ship_order(
        tenant_id, posting_number, credential_id=body.get("credential_id"))


@router.get("/{posting_number}/cancel-reasons", response_model=list[CancelReasonOut])
async def list_cancel_reasons(posting_number: str, request: Request):
    tenant_id = await _authenticate(request)
    return order_service.list_cancel_reasons(tenant_id, posting_number)


@router.post("/{posting_number}/cancel", response_model=OrderActionResponse)
async def cancel_order(posting_number: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    reason_id = int(body.get("cancel_reason_id") or 0)
    return order_service.cancel_order(
        tenant_id, posting_number, reason_id, credential_id=body.get("credential_id"))

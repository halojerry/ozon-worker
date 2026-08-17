"""P0-4: 订单路由 — 参数解析 + 鉴权 + 调 order_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /orders?credential_id=&status=&limit=&offset=&since_days=

token 来源：Authorization: Bearer 优先，body token 兜底（与 credentials_routes 一致）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

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
    refresh: int = 0,
):
    """订单列表：PG 缓存读取（v0.56）——未同步自动懒同步，?refresh=1 强制同步后返回。

    租户隔离：store_sync_service 内 get_decrypted 校验凭证归属（跨租户 404）。
    """
    from services import store_sync_service

    tenant_id = await _authenticate(request)
    if credential_id:
        if refresh:
            store_sync_service.sync_store(tenant_id, credential_id)
            return store_sync_service.list_cached_orders(
                tenant_id, credential_id, status=status,
                limit=limit, offset=offset, since_days=since_days, lazy_sync=False)
        return store_sync_service.list_cached_orders(
            tenant_id, credential_id, status=status,
            limit=limit, offset=offset, since_days=since_days)
    # 未指定店铺：走默认店铺（保持向后兼容）
    from services.credential_service import get_default_credential
    default = get_default_credential(tenant_id)
    if default is None:
        raise HTTPException(status_code=400,
                            detail="未配置默认店铺：请传 credential_id 或先在店铺管理设置默认店铺")
    return store_sync_service.list_cached_orders(
        tenant_id, str(default["id"]), status=status,
        limit=limit, offset=offset, since_days=since_days)


@router.post("/batch/labels")
async def batch_labels(request: Request):
    """P1-3 批量面单：{posting_numbers: [...], credential_id?} → items + failed（失败隔离）。"""
    tenant_id = await _authenticate(request)
    body = await request.json()
    return order_service.batch_order_labels(
        tenant_id, body.get("posting_numbers") or [], credential_id=body.get("credential_id"))


@router.post("/batch/ship")
async def batch_ship(request: Request):
    """P1-3 批量备货：{posting_numbers: [...], credential_id?} → shipped + failed（失败隔离）。"""
    tenant_id = await _authenticate(request)
    body = await request.json()
    return order_service.batch_ship_orders(
        tenant_id, body.get("posting_numbers") or [], credential_id=body.get("credential_id"))


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


@router.get("/message-templates")
async def message_templates(request: Request):
    tenant_id = await _authenticate(request)
    return order_service.get_message_templates()


@router.post("/{posting_number}/message")
async def send_message(posting_number: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return order_service.send_order_message(
        tenant_id, posting_number,
        str(body.get("message") or ""),
        template_key=str(body.get("template_key") or "custom"),
        credential_id=body.get("credential_id"),
    )


@router.get("/messages")
async def list_messages(request: Request, limit: int = 50, offset: int = 0):
    tenant_id = await _authenticate(request)
    return order_service.list_order_messages(tenant_id, limit=limit, offset=offset)

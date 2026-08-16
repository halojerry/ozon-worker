"""P0-4: 订单路由 — 参数解析 + 鉴权 + 调 order_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /orders?credential_id=&status=&limit=&offset=&since_days=

token 来源：Authorization: Bearer 优先，body token 兜底（与 credentials_routes 一致）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from api.schemas import OrderListResponse
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

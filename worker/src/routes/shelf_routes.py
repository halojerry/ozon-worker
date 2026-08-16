"""M2.1: 在售商品列表路由薄层 — 鉴权 + 参数解析 + 调 shelf_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /products?limit=&offset=   按租户返回在售商品列表（product_task_index + moderation_status）

token 来源：Authorization: Bearer 优先，query param token 兜底（GET 无 body）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas import OzonProductListResponse, ProductListResponse
from services import shelf_service

router = APIRouter(prefix="/products", tags=["products"])


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    token = request.query_params.get("token", "")
    return _authenticate_token(token)


@router.get("", response_model=ProductListResponse)
async def list_products(request: Request):
    tenant_id = await _authenticate(request)
    try:
        limit = int(request.query_params.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    return shelf_service.list_products(tenant_id, limit=limit, offset=offset)


@router.get("/ozon", response_model=OzonProductListResponse)
async def list_ozon_products(request: Request):
    tenant_id = await _authenticate(request)
    try:
        limit = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    credential_id = request.query_params.get("credential_id")
    return shelf_service.list_ozon_products(
        tenant_id, credential_id=credential_id, limit=limit, offset=offset)


@router.post("/bulk-prices")
async def bulk_prices(request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return shelf_service.bulk_update_prices(
        tenant_id, body.get("prices") or [], credential_id=body.get("credential_id"))


@router.post("/bulk-stocks")
async def bulk_stocks(request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return shelf_service.bulk_update_stocks(
        tenant_id, body.get("stocks") or [], credential_id=body.get("credential_id"))


@router.post("/bulk-archive")
async def bulk_archive(request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return shelf_service.bulk_archive(
        tenant_id, body.get("product_ids") or [], bool(body.get("archive", True)),
        credential_id=body.get("credential_id"))

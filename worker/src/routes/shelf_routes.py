"""M2.1: 在售商品列表路由薄层 — 鉴权 + 参数解析 + 调 shelf_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /products?limit=&offset=   按租户返回在售商品列表（product_task_index + moderation_status）

token 来源：Authorization: Bearer 优先，query param token 兜底（GET 无 body）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

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
    """Ozon 店铺在线商品：PG 缓存读取（v0.56）——未同步懒同步，?refresh=1 强制。

    租户隔离：store_sync_service 内 get_decrypted 校验凭证归属（跨租户 404）。
    """
    from services import store_sync_service

    tenant_id = await _authenticate(request)
    limit = 50
    offset = 0
    try:
        limit = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError):
        pass
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        pass
    credential_id = request.query_params.get("credential_id")
    refresh = request.query_params.get("refresh") in ("1", "true")
    status = request.query_params.get("status", "")
    source = request.query_params.get("source", "")
    if not credential_id:
        from services.credential_service import get_default_credential
        default = get_default_credential(tenant_id)
        if default is None:
            raise HTTPException(status_code=400,
                                detail="未配置默认店铺：请传 credential_id 或先在店铺管理设置默认店铺")
        credential_id = str(default["id"])
    return store_sync_service.list_cached_products(
        tenant_id, credential_id, limit=limit, offset=offset,
        lazy_sync=not refresh, status=status, source=source)


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

"""v0.56: 店铺数据手动同步路由（薄层：鉴权 → 调 store_sync_service）。

    POST /api/v1/stores/{credential_id}/sync        手动同步单店（订单+商品）
    GET  /api/v1/stores/{credential_id}/sync-status 同步状态（最后时间/错误）

鉴权：Bearer token → user_id；凭证归属 store_sync_service 内 get_decrypted 校验。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from services import store_sync_service

router = APIRouter(prefix="/stores", tags=["stores"])


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    return _authenticate_token("")


@router.post("/{credential_id}/sync")
async def sync_store(credential_id: str, request: Request):
    """手动同步单店（订单增量 + 商品全量）；归属校验失败 → 404。"""
    tenant_id = await _authenticate(request)
    return store_sync_service.sync_store(tenant_id, credential_id)


@router.get("/{credential_id}/sync-status")
async def sync_status(credential_id: str, request: Request):
    """同步状态：最后同步时间 + 错误（webui 展示「上次同步 xx」）。"""
    from services.credential_service import get_decrypted

    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)  # 归属校验（跨租户 404）
    return store_sync_service.get_sync_status(tenant_id, credential_id)

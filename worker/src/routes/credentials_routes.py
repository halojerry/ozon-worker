"""T5: 凭证路由薄层 — 参数解析 + 鉴权 + 调 credential_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET    /credentials           列表（仅掩码）
    POST   /credentials           创建（加密 + 掩码）
    PATCH  /credentials/{id}      轮换（旧行 revoked + 新行 active）
    DELETE /credentials/{id}      吊销（软删 status=revoked）
    POST   /credentials/{id}/validate  解密 → Ozon probe → {valid, reason}

token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request

from api.schemas import CredentialCreate, CredentialOut, CredentialUpdate, ValidateResponse
from services import credential_service

router = APIRouter(prefix="/credentials", tags=["credentials"])


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


@router.get("", response_model=list[CredentialOut])
async def list_credentials(request: Request):
    tenant_id = await _authenticate(request)
    return credential_service.list_credentials(tenant_id)


@router.post("", response_model=CredentialOut, status_code=201)
async def create_credential(request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    data = CredentialCreate.model_validate(body)
    return credential_service.create_credential(tenant_id, data)


@router.patch("/{credential_id}", response_model=CredentialOut)
async def rotate_credential(credential_id: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    data = CredentialUpdate.model_validate(body)
    return credential_service.rotate_credential(tenant_id, credential_id, data)


@router.delete("/{credential_id}")
async def revoke_credential(credential_id: str, request: Request):
    tenant_id = await _authenticate(request)
    return credential_service.revoke_credential(tenant_id, credential_id)


@router.delete("/{credential_id}/data")
async def hard_delete_credential_data(credential_id: str, request: Request):
    """PRD M5(P2): 硬删除该店缓存/历史数据(管理端授权 + confirm 二次确认,默认关闭)。

    query: confirm=true 必传;环境开关 ADMIN_HARD_DELETE_ENABLED=1 才可用。
    审计:store_operation_log 落 hard_delete 记录(operator=admin user_id)。
    用户草稿/凭证吊销记录/手工 order_notes 不删(见 data_erasure_service 文档)。
    """
    from routes.admin_routes import _authenticate_admin
    from services.data_erasure_service import hard_delete_credential_data as _erase

    admin_user_id = await _authenticate_admin(request)
    if os.environ.get("ADMIN_HARD_DELETE_ENABLED") != "1":
        raise HTTPException(
            status_code=403,
            detail="硬删除入口未启用(需部署侧设置 ADMIN_HARD_DELETE_ENABLED=1)",
        )
    if request.query_params.get("confirm") != "true":
        raise HTTPException(status_code=400, detail="需要 confirm=true 二次确认")
    return _erase(admin_user_id, credential_id)


@router.post("/{credential_id}/validate", response_model=ValidateResponse)
async def validate_credential(credential_id: str, request: Request):
    tenant_id = await _authenticate(request)
    return credential_service.validate_credential(tenant_id, credential_id)

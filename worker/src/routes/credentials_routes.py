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

from fastapi import APIRouter, Request

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


@router.post("/{credential_id}/validate", response_model=ValidateResponse)
async def validate_credential(credential_id: str, request: Request):
    tenant_id = await _authenticate(request)
    return credential_service.validate_credential(tenant_id, credential_id)

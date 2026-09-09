"""T5: 凭证路由薄层 — 参数解析 + 鉴权 + 调 credential_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET    /credentials           列表（仅掩码）
    POST   /credentials           创建（加密 + 掩码）
    PATCH  /credentials/{id}      轮换（旧行 revoked + 新行 active）
    DELETE /credentials/{id}      吊销（软删 status=revoked）
    POST   /credentials/{id}/validate  解密 → Ozon probe → {valid, reason}
    POST   /credentials/{id}/session   会话代管上传（v0.70 批次 C，AES-GCM 加密存储）
    GET    /credentials/{id}/session   会话状态（只回名单与状态，永不回 cookie 值）
    DELETE /credentials/{id}/session   会话撤销（204）

token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from api.schemas import CredentialCreate, CredentialOut, CredentialUpdate, ValidateResponse
from services import credential_service, ozon_session_service
from utils.credential_cipher import CredentialCipherError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _validation_detail(exc: ValidationError) -> str:
    """pydantic.ValidationError → 可读 detail（字段缺失/类型错不再裸 500）。"""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', err.get('type', 'invalid'))}")
    return "请求体校验失败: " + "; ".join(parts)


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
    try:
        data = CredentialCreate.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    return credential_service.create_credential(tenant_id, data)


@router.patch("/{credential_id}", response_model=CredentialOut)
async def rotate_credential(credential_id: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    try:
        data = CredentialUpdate.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
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


# ── 会话代管（v0.70 批次 C，对标 bindShopCookie）：skill session-sync 上传 →
# AES-GCM 加密存储 → 服务端 cookie 直调。安全红线：cookie 值只进密文，
# 响应只回名单与状态。业务在 services/ozon_session_service.py。 ──


class SessionUploadBody(BaseModel):
    """会话上传请求体（openapi_extra 展示用 schema；校验失败 → 422）。"""

    cookies: dict[str, str]


_SESSION_OPENAPI_EXTRA = {
    "requestBody": {"required": True, "content": {
        "application/json": {"schema": {
            "type": "object",
            "required": ["cookies"],
            "properties": {
                "cookies": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "seller.ozon.ru 会话 cookie {名: 值}（核心 sc_company_id）",
                    "example": {"sc_company_id": "5371047"},
                },
            },
        }}
    }}
}


@router.post("/{credential_id}/session", status_code=201, openapi_extra=_SESSION_OPENAPI_EXTRA)
async def upload_session(credential_id: str, request: Request):
    """上传会话（加密存储）。跨租户/不存在 credential → 404；未配主密钥 → 500（同凭证端点文案）。"""
    tenant_id = await _authenticate(request)
    body = await request.json()
    try:
        data = SessionUploadBody.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    if not credential_service.credential_owned_by(tenant_id, credential_id):
        raise HTTPException(status_code=404, detail="店铺凭证不存在或不属于当前用户")
    try:
        ozon_session_service.store_session(tenant_id, credential_id, data.cookies)
    except CredentialCipherError as exc:
        logger.error("会话存储加密失败（CREDENTIAL_MASTER_KEY 未配置或过短）tenant=%s", tenant_id)
        raise HTTPException(
            status_code=500,
            detail="凭证加密失败：CREDENTIAL_MASTER_KEY 未配置或过短，请联系管理员",
        ) from exc
    return {
        "ok": True,
        "credential_id": credential_id,
        "status": "active",
        "cookie_names": sorted(data.cookies.keys()),
    }


@router.get("/{credential_id}/session")
async def get_session(credential_id: str, request: Request):
    """会话状态快照 {status, harvested_at, cookie_names}（永不回 cookie 值）。"""
    tenant_id = await _authenticate(request)
    snap = ozon_session_service.session_status(tenant_id, credential_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail="尚未同步会话，先在 skill 执行 session-sync",
        )
    return snap


@router.delete("/{credential_id}/session", status_code=204)
async def revoke_session(credential_id: str, request: Request):
    """撤销会话（物理删行）。无会话 → 404。"""
    tenant_id = await _authenticate(request)
    if not ozon_session_service.delete_session(tenant_id, credential_id):
        raise HTTPException(
            status_code=404,
            detail="尚未同步会话，先在 skill 执行 session-sync",
        )
    return Response(status_code=204)

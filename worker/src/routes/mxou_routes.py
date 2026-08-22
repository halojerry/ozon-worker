"""T2+T4: MXOU 登录 + 密钥管理代理端点（薄层：鉴权 → 调 service，无业务逻辑）。

    POST   /api/v1/mxou/login                 登录（唯一无 token 鉴权端点）
    GET    /api/v1/mxou/keys                  密钥列表（脱敏）
    POST   /api/v1/mxou/keys                  新建密钥（返回完整 key 仅一次）
    DELETE /api/v1/mxou/keys/{key_id}         吊销密钥（204）
    POST   /api/v1/mxou/keys/{key_id}/select  切换密钥（返回完整 key 仅一次）

密钥管理端点全部走 _authenticate（Bearer/body token，参照 products_routes 模式）。
业务逻辑在 services/mxou_login_service.py；平台解析在 utils/mxou_platform.py（T1）。
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from api.schemas import (
    MxouKeyCreateRequest,
    MxouKeyCreateResponse,
    MxouKeyItem,
    MxouKeySelectResponse,
    MxouLoginResponse,
)
from services import mxou_login_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mxou", tags=["mxou"])


async def _authenticate(request: Request) -> str:
    """token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。"""
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


@router.post("/login", response_model=MxouLoginResponse)
async def mxou_login(request: Request):
    """MXOU 账号密码登录（无 token 鉴权——登录入口本身；限流防爆破）。"""
    from main import rate_limiter  # 延迟导入防循环（main 模块加载后再取单例）

    # 解析 body（缺 username/password → 400）
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username/password 必填")

    # ⚠️ 限流防爆破：登录入口不鉴权，按 username 独立限流键
    allowed, _ = rate_limiter.check(f"mxou_login:{username}")
    if not allowed:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")

    return mxou_login_service.login(username, password)


@router.get("/keys", response_model=list[MxouKeyItem])
async def list_mxou_keys(request: Request):
    """密钥列表（脱敏，走 _authenticate 鉴权）。"""
    tenant_id = await _authenticate(request)
    return mxou_login_service.list_keys(tenant_id)


@router.get("/my-key")
async def get_my_key(request: Request, uid: str = ""):
    """WebUI 登录后自动获取该用户已有的 enabled key（免手动建 key）。

    uid 来自 query/header（可伪造），必须经 verify_session_user 用请求自带的
    New API cookie session 向平台校验 uid 归属（防 IDOR 枚举他人 key），
    未通过 → 401。WebUI 同源 fetch 自动携带 cookie，正常登录链路无感。
    """
    user_id = (uid or request.headers.get("New-Api-User", "") or "").strip()
    if not user_id:
        return {"key": ""}
    cookie = request.headers.get("cookie", "")
    if not mxou_login_service.verify_session_user(cookie, user_id):
        raise HTTPException(status_code=401, detail="session 校验失败，拒绝查询 key")
    return mxou_login_service.get_my_key(user_id)


@router.post("/keys", response_model=MxouKeyCreateResponse)
async def create_mxou_key(request: Request):
    """新建密钥（响应含完整 key 仅一次；同时幂等 upsert 进 tokens 表）。"""
    tenant_id = await _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    req = MxouKeyCreateRequest.model_validate(body)
    return mxou_login_service.create_key(tenant_id, req.name)


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_mxou_key(key_id: str, request: Request):
    """吊销密钥（MXOU 删除成功 → 204）。"""
    tenant_id = await _authenticate(request)
    mxou_login_service.revoke_key(tenant_id, key_id)


@router.post("/keys/{key_id}/select", response_model=MxouKeySelectResponse)
async def select_mxou_key(key_id: str, request: Request):
    """切换密钥：解出明文 key（仅此一次返回）+ 幂等 upsert 进 tokens 表。"""
    tenant_id = await _authenticate(request)
    return mxou_login_service.select_key(tenant_id, key_id)


@router.post("/logout")
async def mxou_logout(request: Request):
    tenant_id = await _authenticate(request)
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    else:
        try:
            raw = await request.body()
            if raw:
                data = json.loads(raw.decode("utf-8"))
                token = str(data.get("token", "") or "")
        except Exception:
            pass
    clean = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    if clean:
        try:
            from main import _revoked_tokens
            _revoked_tokens.add(clean)
            _revoked_tokens.add(token)
        except Exception:
            pass
        try:
            from storage.database.supabase_client import get_supabase_client
            supabase = get_supabase_client()
            if supabase is not None:
                supabase.table("tokens").delete().eq("key", clean).execute()
                supabase.table("tokens").update({"status": 0}).eq("key", clean).execute()
        except Exception:
            pass
        try:
            mxou_login_service.session_store.pop(tenant_id)
        except Exception:
            pass
    return {"ok": True}


@router.get("/me")
async def mxou_me(request: Request):
    tenant_id = await _authenticate(request)
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = tenant_id
    email = ""
    role = "user"
    try:
        from storage.database.supabase_client import get_supabase_client
        from services.admin_service import is_admin_role
        supabase = get_supabase_client()
        if supabase is not None:
            rows = supabase.table("users").select("username, email, role").eq("id", user_id).limit(1).execute()
            if rows.data:
                row = rows.data[0]
                email = str(row.get("email") or row.get("username") or "")
                r = row.get("role")
                role = "admin" if is_admin_role(r) else "user"
    except Exception:
        pass
    return {"user_id": user_id, "email": email, "role": role}

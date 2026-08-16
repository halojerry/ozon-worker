"""T2: MXOU 登录代理端点（薄层：参数解析 → 限流 → 调 service，无业务逻辑）。

    POST /api/v1/mxou/login

唯一不做 token 鉴权的端点（登录入口本身）；防爆破靠 rate_limiter 按 username
限流（`mxou_login:{username}` 独立限流键）。业务逻辑在
services/mxou_login_service.py；平台解析在 utils/mxou_platform.py（T1）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from api.schemas import MxouLoginResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mxou", tags=["mxou"])


@router.post("/login", response_model=MxouLoginResponse)
async def mxou_login(request: Request):
    """MXOU 账号密码登录（无 token 鉴权——登录入口本身；限流防爆破）。"""
    from main import rate_limiter  # 延迟导入防循环（main 模块加载后再取单例）
    from services import mxou_login_service

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

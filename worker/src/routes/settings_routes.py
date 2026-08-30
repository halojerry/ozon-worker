"""用户设置端点(系统设置真实化):GET/PUT /api/v1/settings(租户隔离)。"""

import json
import logging

from fastapi import APIRouter, Request

from services import settings_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


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


@router.get("")
async def get_settings(request: Request):
    """读当前用户设置(合并默认值,返回全量键)。"""
    tenant_id = await _authenticate(request)
    return settings_service.get_settings(tenant_id)


@router.put("")
async def put_settings(request: Request):
    """合并更新用户设置(仅已知键,数值范围校验)。"""
    tenant_id = await _authenticate(request)
    body = await request.json()
    body.pop("token", None)  # token 是鉴权字段,不是设置项
    return settings_service.update_settings(tenant_id, body)

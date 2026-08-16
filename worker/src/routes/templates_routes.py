"""P0-1: 上架配置模板路由 — 参数解析 + 鉴权 + 调 template_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET    /templates           列表（默认模板在前）
    POST   /templates           创建（is_default=true 清旧默认）
    PATCH  /templates/{id}      部分更新
    DELETE /templates/{id}      删除
    POST   /templates/{id}/default  设默认（清旧默认）

token 来源：Authorization: Bearer 优先，body token 兜底（与 credentials_routes 一致）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from api.schemas import ListingTemplateCreate, ListingTemplateOut, ListingTemplateUpdate
from services import template_service

router = APIRouter(prefix="/templates", tags=["templates"])


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


@router.get("", response_model=list[ListingTemplateOut])
async def list_templates(request: Request):
    tenant_id = await _authenticate(request)
    return template_service.list_templates(tenant_id)


@router.post("", response_model=ListingTemplateOut, status_code=201)
async def create_template(request: Request):
    tenant_id = await _authenticate(request)
    data = await request.json()
    return template_service.create_template(tenant_id, data)


@router.patch("/{template_id}", response_model=ListingTemplateOut)
async def update_template(template_id: str, request: Request):
    tenant_id = await _authenticate(request)
    data = await request.json()
    return template_service.update_template(tenant_id, template_id, data)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str, request: Request):
    tenant_id = await _authenticate(request)
    template_service.delete_template(tenant_id, template_id)


@router.post("/{template_id}/default", response_model=ListingTemplateOut)
async def set_default(template_id: str, request: Request):
    tenant_id = await _authenticate(request)
    return template_service.set_default(tenant_id, template_id)

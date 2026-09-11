"""T7a: 生图工作台路由（薄层：鉴权 → 调 image_service，无业务逻辑）。

端点（挂载在 /api/v1/tasks 下）：
    GET  /api/v1/tasks/{task_id}/images              列表（slot/version/url/params）
    POST /api/v1/tasks/{task_id}/images/{slot}/regen 强制重生成（version++）

token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from api.schemas import ImageRegenResponse, TaskImageRegenRequest, TaskImagesResponse
from services import image_service

router = APIRouter(prefix="/tasks", tags=["images"])


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


@router.get("/{task_id}/images", response_model=TaskImagesResponse)
async def list_task_images(task_id: str, request: Request):
    tenant_id = await _authenticate(request)
    return image_service.list_images(task_id, tenant_id)


@router.post("/{task_id}/images/{slot}/regen", response_model=ImageRegenResponse,
             # 函数不消费 body（鉴权只兜底读 token）：请求体声明为可选 TaskImageRegenRequest，
             # 文档如实呈现「Bearer 优先、body 可省略」（v1_submit_task 先例）。
             openapi_extra={"requestBody": {"required": False, "content": {
                 "application/json": {"schema": TaskImageRegenRequest.model_json_schema()}}}})
async def regen_task_image(task_id: str, slot: str, request: Request):
    tenant_id = await _authenticate(request)
    return await image_service.regen_image(task_id, slot, tenant_id)

"""T14: 在线商品更新路由（薄层：鉴权 → 调 image_service，无业务逻辑）。

端点（挂载在 /api/v1 下）：
    POST /api/v1/products/{product_id}/update_images  在线商品改图全量重传
    （索引定位 → URL 存活检查 → /v3/product/import 重传 → 重新审核中标记）

token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from api.schemas import (
    ProductEditResponse,
    UpdateProductImagesRequest,
    UpdateProductImagesResponse,
)
from services import image_service

router = APIRouter(prefix="/products", tags=["products"])


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


@router.post("/{product_id}/update_images", response_model=UpdateProductImagesResponse)
async def update_product_images(product_id: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    req = UpdateProductImagesRequest.model_validate(body)
    return image_service.update_product_images(tenant_id, product_id, req.images)


@router.get("/{product_id}/edit", response_model=ProductEditResponse)
async def get_product_edit(product_id: str, request: Request):
    """T6: 在线商品编辑初值（product_task_index 关联草稿 envelope + 审核状态）。

    404 = 无索引/草稿缺失；409 = 无草稿来源（仅改图可用 update_images）。
    """
    tenant_id = await _authenticate(request)
    return image_service.get_product_edit_data(tenant_id, product_id)

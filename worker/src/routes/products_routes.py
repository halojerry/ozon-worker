"""T14: 在线商品更新路由（薄层：鉴权 → 调 image_service，无业务逻辑）。

端点（挂载在 /api/v1 下）：
    POST /api/v1/products/{product_id}/update_images  在线商品改图全量重传
    （索引定位 → URL 存活检查 → /v3/product/import 重传 → 重新审核中标记）

token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。
"""
from __future__ import annotations

import json

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.schemas import (
    ProductEditResponse,
    UpdateProductImagesRequest,
    UpdateProductImagesResponse,
)
from services import image_service

router = APIRouter(prefix="/products", tags=["products"])


class ProductSourceUpdate(BaseModel):
    """成本/货源手动维护(PATCH /products/{id}/source,manual 最高优先级)。"""
    credential_id: str = Field(..., description="店铺凭证 id(归属校验)")
    purchase_url: str = Field("", description="1688 货源链接")
    purchase_cost: float = Field(..., gt=0, description="到仓成本(CNY,含国内运费)")
    freight_cny: Optional[float] = Field(None, ge=0, description="1688 国内运费(可选)")
    supplier: str = Field("", description="1688 店铺名")


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


@router.patch("/{product_id}/source")
async def update_product_source(product_id: str, data: ProductSourceUpdate, request: Request):
    """手动维护商品成本/货源(manual 优先,写历史 + 重算订单利润);归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    from services.product_cost_service import upsert_manual
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, data.credential_id)
    return upsert_manual(
        tenant_id, data.credential_id, product_id, "",
        purchase_url=data.purchase_url, purchase_cost=data.purchase_cost,
        supplier=data.supplier, freight_cny=data.freight_cny,
    )


@router.get("/{product_id}/cost")
async def get_product_cost(product_id: str, request: Request, credential_id: str):
    """商品成本主数据 + 成本历史;归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    from services.product_cost_service import get_cost
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)
    return get_cost(tenant_id, credential_id, product_id)


@router.get("/{product_id}/source-candidates")
async def get_source_candidates(product_id: str, request: Request, credential_id: str):
    """货源匹配候选列表(skill 上报 / discover 派生 / 手动维护),归属校验失败 → 404。"""
    from fastapi import HTTPException
    from services.credential_service import credential_owned_by
    from services.source_candidate_service import list_by_product
    tenant_id = await _authenticate(request)
    if not credential_owned_by(tenant_id, credential_id):
        raise HTTPException(status_code=404, detail="凭证不存在或已吊销")
    try:
        limit = int(request.query_params.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    return list_by_product(tenant_id, credential_id, product_id, limit=limit)


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

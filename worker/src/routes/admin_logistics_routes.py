"""C2: 物流费率管理路由（/admin/logistics）— 鉴权（管理员）+ 参数解析 + 调 logistics_service。

端点（挂载在 /api/v1 下，由 main.py include_router）：
    GET  /admin/logistics/rates         费率列表（limit/offset 分页）
    PUT  /admin/logistics/rates/{id}    更新单条费率（400 校验失败 / 404 不存在）
    POST /admin/logistics/rates/import  CSV 批量导入（upsert）
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services import admin_service, logistics_service

router = APIRouter(prefix="/admin/logistics", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


# ──────────────────────────────────────────────
# 模块内 Pydantic 模型（不污染 api/schemas.py —— T1/T2i 拥有该文件）
# ──────────────────────────────────────────────


class LogisticsRateRow(BaseModel):
    """单条费率行（服务返回结构，供文档/校验用）。"""
    id: int
    scoring_group: str
    service_level: str
    tpl_provider: str
    delivery_method: Optional[str] = None
    base_cost: float
    per_gram_rate: float
    weight_min: int
    weight_max: int
    sum_limit_cm: int
    longest_limit_cm: int
    charge_type: str
    vol_weight_divisor: int = 0
    created_at: Optional[str] = None


class LogisticsRateUpdateIn(BaseModel):
    """更新费率请求体（服务层再做语义校验）。"""
    scoring_group: str = Field(..., min_length=1)
    service_level: str = Field(..., min_length=1)
    tpl_provider: str = Field(..., min_length=1)
    delivery_method: Optional[str] = None
    base_cost: float
    per_gram_rate: float
    weight_min: int
    weight_max: int
    sum_limit_cm: int
    longest_limit_cm: int
    charge_type: str
    vol_weight_divisor: int = 0


class LogisticsImportIn(BaseModel):
    """CSV 导入请求体。"""
    csv: str


class LogisticsImportResult(BaseModel):
    """导入结果：inserted/updated 计数 + 逐行错误。"""
    imported: int
    updated: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


# ──────────────────────────────────────────────
# 端点
# ──────────────────────────────────────────────


@router.get("/rates", response_model=dict)
async def admin_logistics_list_rates(request: Request, limit: int = 50, offset: int = 0):
    """费率列表（limit ≤ 200，offset ≥ 0）。"""
    await _authenticate_admin(request)
    return logistics_service.list_rates(limit=limit, offset=offset)


@router.put("/rates/{rate_id}", response_model=LogisticsRateRow)
async def admin_logistics_update_rate(rate_id: int, request: Request):
    """更新单条费率：校验失败 → 400，id 不存在 → 404。"""
    await _authenticate_admin(request)
    body = LogisticsRateUpdateIn.model_validate(await request.json())
    try:
        row = logistics_service.update_rate(rate_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"费率不存在: {rate_id}")
    return row


@router.post("/rates/import", response_model=LogisticsImportResult)
async def admin_logistics_import_rates(request: Request):
    """CSV 批量导入（键匹配 upsert；坏行跳过并记录）。"""
    await _authenticate_admin(request)
    body = LogisticsImportIn.model_validate(await request.json())
    return logistics_service.import_rates_csv(body.csv)

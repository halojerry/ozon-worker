"""工作台聚合端点:GET /api/v1/dashboard/overview(租户隔离,只读 PG)。"""

import json
import logging

from fastapi import APIRouter, Request

from services import dashboard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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


# 响应示例（openapi_extra 路由级补；结构 = dashboard_service.get_dashboard 返回值）
_OVERVIEW_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "today": {"orders_count": 12, "sales_amount": 48250.5,
              "commission_amount": 8685.09, "profit_amount": 6200.4},
    "active_products": 132, "pending_tasks": 3, "store_count": 2,
    "trend": [{"date": "2026-09-10", "orders": 9, "sales_amount": 36120.0,
               "profit_amount": 4650.0}],
    "hot_products": [{"product_id": "123456789", "name": "汽车香薰",
                      "quantity": 28}],
    "latest_orders": [{"posting_number": "23456789-0010-3",
                       "product_name": "汽车香薰", "total_amount": 2150.0,
                       "status": "awaiting_packaging",
                       "created_at": "2026-09-11T07:40:00+00:00"}],
    "last_synced_at": "2026-09-11T08:00:00+00:00",
    "trend_days": 14,
}}}}}}


@router.get("/overview", openapi_extra=_OVERVIEW_OK_EXTRA)
async def dashboard_overview(request: Request):
    """工作台聚合:今日订单/销售额/在售/待办/趋势/热销/最近订单。"""
    tenant_id = await _authenticate(request)
    try:
        days = int(request.query_params.get("days", 14))
    except (TypeError, ValueError):
        days = 14
    return dashboard_service.get_dashboard(tenant_id, trend_days=days)

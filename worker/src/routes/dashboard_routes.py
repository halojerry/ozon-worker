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


@router.get("/overview")
async def dashboard_overview(request: Request):
    """工作台聚合:今日订单/销售额/在售/待办/趋势/热销/最近订单。"""
    tenant_id = await _authenticate(request)
    try:
        days = int(request.query_params.get("days", 14))
    except (TypeError, ValueError):
        days = 14
    return dashboard_service.get_dashboard(tenant_id, trend_days=days)

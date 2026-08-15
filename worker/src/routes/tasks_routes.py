"""T8: 任务列表路由薄层 — 鉴权 + 参数解析 + 调 task_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /tasks?limit=&offset=   按租户返回任务列表（status/progress/product_summary）

token 来源：Authorization: Bearer 优先，query param token 兜底（GET 无 body）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas import TaskListResponse
from services import task_service

router = APIRouter(prefix="/tasks", tags=["task"])


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    token = request.query_params.get("token", "")
    return _authenticate_token(token)


@router.get("", response_model=TaskListResponse)
async def list_tasks(request: Request):
    tenant_id = await _authenticate(request)
    try:
        limit = int(request.query_params.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    return task_service.list_tasks(tenant_id, limit=limit, offset=offset)
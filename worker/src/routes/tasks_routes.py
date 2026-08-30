"""T8/M1.1: 任务列表 + 任务草稿解析路由薄层 — 鉴权 + 参数解析 + 调 task_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /tasks?limit=&offset=                 按租户返回任务列表（status/progress/product_summary）
    GET /tasks/{task_id}/draft                失败/被拒任务 → 采集箱草稿 id（M1.1 重上闭环）

token 来源：Authorization: Bearer 优先，query param token 兜底（GET 无 body）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.schemas import TaskDraftResponse, TaskListResponse
from services import task_service

router = APIRouter(prefix="/tasks", tags=["task"])
sse_router = APIRouter(tags=["task"])


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    token = request.query_params.get("token", "")
    return _authenticate_token(token)


def _require_task_owner(tenant_id: str, task_id: str) -> None:
    """任务归属校验(跨租户 404)。"""
    from storage.database.db import get_engine
    from sqlalchemy import text
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM ozon_product_tasks WHERE id::text=:id AND tenant_id=:t"
        ), {"id": task_id, "t": tenant_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")


@router.get("/{task_id}/progress")
async def task_progress_detail(task_id: str, request: Request):
    """任务进度事件列表 + 汇总(PRD M4 时间线数据源)。"""
    from main import get_progress
    from services import task_progress_service
    tenant_id = await _authenticate(request)
    _require_task_owner(tenant_id, task_id)
    progress = get_progress(task_id) or {}
    events = task_progress_service.list_events(task_id)
    return {
        "task_id": task_id,
        "percent": progress.get("percent"),
        "stage": progress.get("stage"),
        "message": progress.get("message", ""),
        "events": events,
    }


@sse_router.get("/progress/{task_id}/stream")
async def task_progress_stream(task_id: str, request: Request):
    """SSE 实时进度:Last-Event-ID 增量回放,断线重连不丢(PRD M4)。"""
    tenant_id = await _authenticate(request)
    _require_task_owner(tenant_id, task_id)
    try:
        last = int(request.headers.get("Last-Event-ID", "0") or 0)
    except (TypeError, ValueError):
        last = 0
    from services import task_progress_service

    async def _gen():
        nonlocal last
        while True:
            events = await asyncio.to_thread(task_progress_service.list_events, task_id, last)
            for ev in events:
                last = ev["seq"]
                yield f"id: {ev['seq']}\nevent: progress\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if await asyncio.to_thread(task_progress_service.is_terminal, task_id):
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


@router.get("/{task_id}/draft", response_model=TaskDraftResponse)
async def get_task_draft(task_id: str, request: Request):
    """M1.1: task → 采集箱草稿（失败/被拒任务回采集箱改 → 重上）。

    租户隔离：task 属于其他租户/不存在 → 404（task_service 内拦截）。
    返回 {"draft_id": uuid | None}：直连任务无草稿 → None（前端提示直接重上）。
    """
    tenant_id = await _authenticate(request)
    return {"draft_id": task_service.resolve_draft_by_task(tenant_id, task_id)}

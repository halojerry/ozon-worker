"""Batch 5 T5.2: Image tasks CRUD — placeholder processing pipeline.

端点（挂载在 /api/v1/image-tasks 下）：
    POST   /            → 创建任务（status=pending, 同步 stub 标记 completed）
    GET    /            → 列表当前用户任务（分页 limit/offset）
    GET    /{id}        → 获取单个任务（404 if missing or not owned）
    POST   /{id}/cancel → 取消待处理任务

鉴权：Bearer token → main._authenticate_token（返回 user_id）。
模型：ImageTask（table image_tasks）。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/image-tasks", tags=["image-tasks"])


class ImageTaskCreateRequest(BaseModel):
    type: str
    input_image_url: str
    params: Optional[dict] = None


class ImageTaskResponse(BaseModel):
    id: str
    type: str
    input_image_url: str
    status: str
    result_image_url: Optional[str] = None
    params: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ImageTaskListResponse(BaseModel):
    items: list[ImageTaskResponse]
    total: int
    limit: int
    offset: int


async def _authenticate_user(request: Request) -> str:
    from main import _authenticate_token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token is required")
    return _authenticate_token(auth[7:].strip())


@router.post("", status_code=201, response_model=ImageTaskResponse)
@router.post("/", status_code=201, response_model=ImageTaskResponse)
async def create_image_task(request: Request, body: ImageTaskCreateRequest):
    """同步 stub：status=completed, result=input_image_url。真实处理留后续批次。"""
    user_id = await _authenticate_user(request)
    valid_types = ("remove_bg", "upscale", "background_change")
    if body.type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid type: {body.type}. Must be one of: {', '.join(valid_types)}")

    task_id = str(uuid.uuid4())
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    status = "completed"
    result_image_url = body.input_image_url

    try:
        from sqlalchemy import text
        from storage.database.db import get_engine
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO image_tasks "
                    "(id, user_id, type, input_image_url, status, result_image_url, "
                    "params, created_at, updated_at) "
                    "VALUES (:id, :user_id, :type, :input_url, :status, :result_url, "
                    ":params, :created_at, :updated_at)"
                ),
                {
                    "id": task_id,
                    "user_id": user_id,
                    "type": body.type,
                    "input_url": body.input_image_url,
                    "status": status,
                    "result_url": result_image_url,
                    "params": body.params,
                    "created_at": now,
                    "updated_at": now,
                },
            )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create image task")

    return ImageTaskResponse(
        id=task_id, type=body.type, input_image_url=body.input_image_url,
        status=status, result_image_url=result_image_url, params=body.params,
        created_at=now.isoformat(), updated_at=now.isoformat(),
    )


@router.get("", response_model=ImageTaskListResponse)
@router.get("/", response_model=ImageTaskListResponse)
async def list_image_tasks(request: Request, limit: int = 50, offset: int = 0):
    user_id = await _authenticate_user(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    from sqlalchemy import text
    from storage.database.db import get_engine
    items = []
    total = 0
    try:
        with get_engine().connect() as conn:
            total = int(conn.execute(text("SELECT COUNT(*) FROM image_tasks WHERE user_id = :uid"), {"uid": user_id}).scalar() or 0)
            rows = conn.execute(text(
                "SELECT id, type, input_image_url, status, result_image_url, params, error_message, created_at, updated_at "
                "FROM image_tasks WHERE user_id = :uid ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
            ), {"uid": user_id, "limit": limit, "offset": offset}).fetchall()
            items = [ImageTaskResponse(
                id=str(r[0]), type=str(r[1]), input_image_url=str(r[2]), status=str(r[3]),
                result_image_url=str(r[4]) if r[4] else None, params=r[5],
                error_message=str(r[6]) if r[6] else None,
                created_at=r[7].isoformat() if hasattr(r[7], "isoformat") else str(r[7]) if r[7] else None,
                updated_at=r[8].isoformat() if hasattr(r[8], "isoformat") else str(r[8]) if r[8] else None,
            ) for r in rows]
    except Exception:
        pass
    return ImageTaskListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=ImageTaskResponse)
async def get_image_task(task_id: str, request: Request):
    user_id = await _authenticate_user(request)
    from sqlalchemy import text
    from storage.database.db import get_engine
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id, type, input_image_url, status, result_image_url, params, error_message, created_at, updated_at "
            "FROM image_tasks WHERE id = :id AND user_id = :uid"
        ), {"id": task_id, "uid": user_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Image task {task_id} not found")
    return ImageTaskResponse(
        id=str(row[0]), type=str(row[1]), input_image_url=str(row[2]), status=str(row[3]),
        result_image_url=str(row[4]) if row[4] else None, params=row[5],
        error_message=str(row[6]) if row[6] else None,
        created_at=row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]) if row[7] else None,
        updated_at=row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]) if row[8] else None,
    )


@router.post("/{task_id}/cancel")
async def cancel_image_task(task_id: str, request: Request):
    user_id = await _authenticate_user(request)
    from sqlalchemy import text
    from storage.database.db import get_engine
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT status FROM image_tasks WHERE id = :id AND user_id = :uid"
        ), {"id": task_id, "uid": user_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Image task {task_id} not found")
    current_status = str(row[0])
    if current_status != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot cancel task in status '{current_status}': only pending tasks can be cancelled")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE image_tasks SET status = 'cancelled', updated_at = :now WHERE id = :id AND user_id = :uid"
        ), {"id": task_id, "uid": user_id, "now": now})
    return {"status": "ok", "task_id": task_id, "message": "Task cancelled"}

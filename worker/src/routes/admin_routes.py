"""v0.51: 管理员面板路由 — 鉴权（管理员）+ 参数解析 + 调 admin_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET /admin/overview      平台概览
    GET /admin/users         用户列表（平台视角）
    GET /admin/users/{id}    用户详情（店铺 + 任务统计）
    GET /admin/stores        店铺列表（跨用户）
    GET /admin/tasks         任务统计（全租户）

鉴权：_authenticate_token 拿 user_id → require_admin（非管理员 403；本地 local_dev 放行）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas import (
    AdminOverviewOut,
    AdminStoreOut,
    AdminUserDetailOut,
    AdminUserOut,
)
from services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


@router.get("/overview", response_model=AdminOverviewOut)
async def admin_overview(request: Request):
    await _authenticate_admin(request)
    return admin_service.get_overview()


@router.get("/users", response_model=list[AdminUserOut])
async def admin_users(request: Request):
    await _authenticate_admin(request)
    return admin_service.list_users()


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
async def admin_user_detail(user_id: str, request: Request):
    await _authenticate_admin(request)
    return admin_service.get_user_detail(user_id)


@router.get("/stores", response_model=list[AdminStoreOut])
async def admin_stores(request: Request):
    await _authenticate_admin(request)
    return admin_service.list_stores()


@router.get("/tasks")
async def admin_tasks(request: Request):
    """任务统计（全租户）——get_task_stats 是 async，必须 await。"""
    await _authenticate_admin(request)
    return await admin_service.get_task_stats()

"""管理员面板服务（v0.51）：平台运营视图（Supabase users + PG 业务数据聚合）。

职责：只读聚合——用户列表（Supabase users + tokens）、店铺列表（PG credentials）、
任务统计（PG ozon_product_tasks）、平台概览。不做任何写操作（用户禁用/充值 P1）。
管理员判定：Supabase users.role='admin' 或本地开发 local_dev 放行。
"""

import logging
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text

from storage.database.db import get_engine
from utils.task_statistics import statistics_payload

logger = logging.getLogger(__name__)


def is_admin_user(user_id: str) -> bool:
    """管理员判定：Supabase users.role='admin'；本地开发（无 Supabase）放行。"""
    if user_id == "local_dev":
        return True
    try:
        from main import get_supabase_client
        supabase = get_supabase_client()
        if supabase is None:
            return True  # 无 Supabase（本地）→ 放行
        rows = supabase.table("users").select("role").eq("id", user_id).limit(1).execute()
        if rows.data:
            return str(rows.data[0].get("role") or "").lower() == "admin"
    except Exception as exc:
        logger.warning("管理员判定失败 user=%s: %s", user_id, str(exc)[:200])
    return False


def require_admin(user_id: str) -> None:
    """非管理员 → 403（admin 路由统一入口）。"""
    if not is_admin_user(user_id):
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _pg_count(sql: str, params: Optional[dict] = None) -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(text(sql), params or {}).scalar() or 0)


def _pg_rows(sql: str, params: Optional[dict] = None) -> list:
    with get_engine().connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


# ──────────────────────────────────────────────
# 平台概览
# ──────────────────────────────────────────────


def get_overview() -> dict:
    """平台概览：用户数/店铺数/任务数/成功率/今日任务。"""
    user_count = len(_list_supabase_users())  # Supabase users 全量长度（无 count API，limit 1000 近似）

    store_count = _pg_count("SELECT COUNT(*) FROM credentials WHERE status='active'")
    task_total = _pg_count("SELECT COUNT(*) FROM ozon_product_tasks")
    task_completed = _pg_count("SELECT COUNT(*) FROM ozon_product_tasks WHERE status='completed'")
    task_failed = _pg_count("SELECT COUNT(*) FROM ozon_product_tasks WHERE status='failed'")
    task_running = _pg_count("SELECT COUNT(*) FROM ozon_product_tasks WHERE status='running'")
    task_pending = _pg_count("SELECT COUNT(*) FROM ozon_product_tasks WHERE status='pending'")
    today_start = None
    try:
        from datetime import datetime, timezone
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass
    task_today = 0
    if today_start:
        task_today = _pg_count(
            "SELECT COUNT(*) FROM ozon_product_tasks WHERE created_at::date = :d",
            {"d": today_start})

    success_rate = round(task_completed / task_total * 100, 1) if task_total > 0 else 0.0
    return {
        "user_count": user_count,
        "store_count": store_count,
        "task_total": task_total,
        "task_today": task_today,
        "success_rate": success_rate,
        "statistics": statistics_payload(
            total=task_total, completed=task_completed, failed=task_failed,
            running=task_running, pending=task_pending,
        ),
    }


# ──────────────────────────────────────────────
# 用户列表 / 详情
# ──────────────────────────────────────────────


def _list_supabase_users() -> list[dict]:
    """Supabase users 表全量（id/username/quota/role/created_at）；无 Supabase → []。"""
    try:
        from main import get_supabase_client
        supabase = get_supabase_client()
        if supabase is None:
            return []
        rows = supabase.table("users").select(
            "id, username, display_name, quota, role, created_at"
        ).limit(1000).execute()
        return [dict(r) for r in (rows.data or [])]
    except Exception as exc:
        logger.warning("Supabase users 拉取失败: %s", str(exc)[:200])
        return []


def list_users() -> list[dict]:
    """用户列表：Supabase users + 每用户店铺数/任务数（PG 聚合）。"""
    users = _list_supabase_users()
    out = []
    for u in users:
        uid = str(u.get("id") or "")
        store_count = _pg_count(
            "SELECT COUNT(*) FROM credentials WHERE tenant_id=:t AND status='active'", {"t": uid}) if uid else 0
        task_count = _pg_count(
            "SELECT COUNT(*) FROM ozon_product_tasks WHERE tenant_id=:t", {"t": uid}) if uid else 0
        out.append({
            "id": uid,
            "username": u.get("username") or u.get("display_name") or "",
            "quota": u.get("quota"),
            "role": u.get("role") or "user",
            "created_at": str(u.get("created_at")) if u.get("created_at") else None,
            "store_count": store_count,
            "task_count": task_count,
        })
    return out


def get_user_detail(user_id: str) -> dict:
    """用户详情：店铺列表 + 任务统计。"""
    stores = []
    for row in _pg_rows(
        "SELECT id, ozon_client_id, shop_name, currency, is_default, status, "
        "last_validated_at FROM credentials WHERE tenant_id=:t ORDER BY created_at DESC",
        {"t": user_id},
    ):
        stores.append({
            "id": str(row[0]),
            "ozon_client_id": str(row[1]),
            "shop_name": str(row[2] or ""),
            "currency": str(row[3] or "CNY"),
            "is_default": bool(row[4]),
            "status": str(row[5] or "active"),
            "last_validated_at": row[6].isoformat() if row[6] else None,
        })
    task_total = _pg_count("SELECT COUNT(*) FROM ozon_product_tasks WHERE tenant_id=:t", {"t": user_id})
    task_completed = _pg_count(
        "SELECT COUNT(*) FROM ozon_product_tasks WHERE tenant_id=:t AND status='completed'", {"t": user_id})
    task_failed = _pg_count(
        "SELECT COUNT(*) FROM ozon_product_tasks WHERE tenant_id=:t AND status='failed'", {"t": user_id})
    return {
        "id": user_id,
        "stores": stores,
        "task_total": task_total,
        "task_completed": task_completed,
        "task_failed": task_failed,
    }


# ──────────────────────────────────────────────
# 店铺列表（跨用户）
# ──────────────────────────────────────────────


def list_stores() -> list[dict]:
    """店铺列表（跨用户）：店铺 + 归属 tenant_id。"""
    out = []
    for row in _pg_rows(
        "SELECT id, tenant_id, ozon_client_id, shop_name, currency, is_default, status, "
        "last_validated_at FROM credentials ORDER BY created_at DESC LIMIT 500",
    ):
        out.append({
            "id": str(row[0]),
            "tenant_id": str(row[1]),
            "ozon_client_id": str(row[2]),
            "shop_name": str(row[3] or ""),
            "currency": str(row[4] or "CNY"),
            "is_default": bool(row[5]),
            "status": str(row[6] or "active"),
            "last_validated_at": row[7].isoformat() if row[7] else None,
        })
    return out


# ──────────────────────────────────────────────
# 任务统计（全租户，复用 task_processor）
# ──────────────────────────────────────────────


def get_task_stats() -> dict:
    """全租户任务统计（复用 task_processor.get_task_statistics）。"""
    try:
        from main import task_processor
        if task_processor is None:
            return {"error": "Task processor not initialized"}
        return await_task_stats(task_processor)
    except Exception as exc:
        logger.warning("任务统计失败: %s", str(exc)[:200])
        return {"error": str(exc)[:200]}


async def await_task_stats(task_processor) -> dict:
    return await task_processor.get_task_statistics(None)

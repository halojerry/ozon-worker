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

# New API 角色体系（common/constants.go:190-193）：
#   RoleRootUser=100（root，最高）、RoleAdminUser=10（admin）、RoleCommonUser=1、RoleGuestUser=0
# 管理员判定 = role >= 10（root 与 admin 都是管理员）。兼容整数与字符串两种存储形态。
ADMIN_ROLE_MIN = 10


def is_admin_role(role) -> bool:
    """统一管理员角色判定：role >= 10（New API RoleAdminUser/RoleRootUser）。

    兼容：
    - 整数（Supabase 实际存储：100=root / 10=admin / 1=user / 0=guest）
    - 字符串 '100'/'10'/'admin'/'root'（历史代码/测试曾用字符串）
    - None/空 → False
    """
    if role is None:
        return False
    if isinstance(role, bool):  # True 不等于管理员数值
        return False
    if isinstance(role, int):
        return role >= ADMIN_ROLE_MIN
    s = str(role).strip().lower()
    if not s:
        return False
    if s in ("admin", "root", "superadmin"):
        return True
    try:
        return int(s) >= ADMIN_ROLE_MIN
    except ValueError:
        return False


def is_admin_user(user_id: str) -> bool:
    """管理员判定：Supabase users.role >= 10（root/admin）。

    fail-closed：Supabase 不可用/未配置时除显式 local_dev 外一律拒绝
    （对齐 auth/verify「DB 不可用不放行」原则——原 supabase is None → True
    是 fail-open，env 误配即全员管理员）。
    """
    if user_id == "local_dev":
        return True
    try:
        from storage.database.supabase_client import get_supabase_client  # ✅ W3b: 直连 storage
        supabase = get_supabase_client()
        if supabase is None:
            return False  # fail-closed：未配置 Supabase 不放行（本地开发走 local_dev）
        rows = supabase.table("users").select("role").eq("id", user_id).limit(1).execute()
        if rows.data:
            return is_admin_role(rows.data[0].get("role"))
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
        from storage.database.supabase_client import get_supabase_client  # ✅ W3b: 直连 storage
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
            # Supabase role 是整数（100=root/10=admin/1=user）——按 AdminUserOut
            # 契约（webui u.role === 'admin'）映射为 admin/user 字符串，防 500
            "role": "admin" if is_admin_role(u.get("role")) else "user",
            "created_at": str(u.get("created_at")) if u.get("created_at") else None,
            "store_count": store_count,
            "task_count": task_count,
        })
    return out


def get_user_detail(user_id: str) -> dict:
    """用户详情：店铺列表 + 任务统计。

    ✅ v0.83.2 修复 ResponseValidationError 500（生产 2026-10-02 实锤，
    Sentry 0b623dff）：stores 出参缺 tenant_id——AdminUserDetailOut.stores
    复用 AdminStoreOut（跨用户店铺列表形状，tenant_id 必填）。SELECT 补列，
    值恒等于入参 user_id（credentials.tenant_id 即归属）。
    """
    stores = []
    for row in _pg_rows(
        "SELECT id, tenant_id, ozon_client_id, shop_name, currency, is_default, status, "
        "last_validated_at FROM credentials WHERE tenant_id=:t ORDER BY created_at DESC",
        {"t": user_id},
    ):
        stores.append({
            "id": str(row[0]),
            "tenant_id": str(row[1] or user_id),
            "ozon_client_id": str(row[2]),
            "shop_name": str(row[3] or ""),
            "currency": str(row[4] or "CNY"),
            "is_default": bool(row[5]),
            "status": str(row[6] or "active"),
            "last_validated_at": row[7].isoformat() if row[7] else None,
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


async def get_task_stats(tenant_id: Optional[str] = None) -> dict:
    """任务统计（缺省全租户；tenant_id 指定 → 单租户过滤）。

    async——内部 await task_processor.get_task_statistics；同步调用会拿到
    coroutine 未 await → 响应序列化 500（QA 实测 /admin/tasks 崩溃根因）。
    ✅ v0.83.2：补 tenant_id 透传（task_processor.get_task_statistics 本就
    支持租户过滤，路由层此前没接——事故排查时 ?tenant_id=28 被静默忽略）。
    """
    try:
        from orchestrator.task_processor import get_task_processor  # ✅ W3b: 不再 from main
        task_processor = get_task_processor()
        if task_processor is None:
            return {"error": "Task processor not initialized"}
        return await task_processor.get_task_statistics(
            str(tenant_id).strip() if tenant_id and str(tenant_id).strip() else None)
    except Exception as exc:
        logger.warning("任务统计失败: %s", str(exc)[:200])
        return {"error": str(exc)[:200]}

"""PRD M1: 店铺同步任务表服务 — 任务可见/去重/认领/完成/僵尸恢复/due 扫描。"""
from __future__ import annotations

import datetime
import logging
import os
from typing import Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

JOB_TIMEOUT_MINUTES = int(os.getenv("STORE_SYNC_JOB_TIMEOUT_MINUTES", "30"))
DEFAULT_ORDERS_INTERVAL_MINUTES = int(os.getenv("STORE_SYNC_INTERVAL_MINUTES", "15"))
DEFAULT_PRODUCTS_INTERVAL_MINUTES = int(os.getenv("STORE_SYNC_PRODUCTS_INTERVAL_MINUTES", "30"))

_JOB_COLS = (
    "id, tenant_id, credential_id::text AS credential_id, kind, status, trigger, "
    "error_code, orders_synced, products_synced, progress, error, "
    "started_at, finished_at, created_at"
)


def _row_to_dict(row) -> dict:
    return {
        "id": int(row.id),
        "tenant_id": str(row.tenant_id),
        "credential_id": str(row.credential_id),
        "kind": row.kind,
        "status": row.status,
        "trigger": row.trigger,
        "error_code": row.error_code,
        "orders_synced": int(row.orders_synced or 0),
        "products_synced": int(row.products_synced or 0),
        "progress": int(row.progress or 0),
        "error": row.error or "",
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def enqueue(tenant_id: str, credential_id: str, kind: str = "incremental",
            trigger: str = "scheduler") -> dict:
    """入队同步 job;同店已有 pending/running 时返回在途 job(去重不重复入队)。"""
    with get_engine().begin() as conn:
        row = conn.execute(text(
            f"""
            INSERT INTO store_sync_jobs (tenant_id, credential_id, kind, trigger)
            VALUES (:t, :c, :kind, :trigger)
            ON CONFLICT (credential_id) WHERE status IN ('pending','running') DO NOTHING
            RETURNING {_JOB_COLS}
            """
        ), {"t": tenant_id, "c": str(credential_id), "kind": kind, "trigger": trigger}).fetchone()
        if row is not None:
            return _row_to_dict(row)
        row = conn.execute(text(
            f"""
            SELECT {_JOB_COLS} FROM store_sync_jobs
            WHERE credential_id=:c AND status IN ('pending','running')
            ORDER BY created_at LIMIT 1
            """
        ), {"c": str(credential_id)}).fetchone()
        if row is None:
            raise RuntimeError(f"enqueue conflict but no active job: {credential_id}")
        return _row_to_dict(row)


def claim_next() -> Optional[dict]:
    """认领一个 pending job(SKIP LOCKED + advisory 兜底),置 running 并返回。"""
    with get_engine().begin() as conn:
        row = conn.execute(text(
            f"""
            SELECT {_JOB_COLS} FROM store_sync_jobs
            WHERE status='pending' ORDER BY created_at, id LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )).fetchone()
        if row is None:
            return None
        locked = conn.execute(text(
            "SELECT pg_try_advisory_xact_lock(hashtext(:t), hashtext(:c))"
        ), {"t": str(row.tenant_id), "c": str(row.credential_id)}).scalar()
        if not locked:
            return None
        conn.execute(text(
            "UPDATE store_sync_jobs SET status='running', started_at=NOW() WHERE id=:id"
        ), {"id": row.id})
        row = conn.execute(text(
            f"SELECT {_JOB_COLS} FROM store_sync_jobs WHERE id=:id"
        ), {"id": row.id}).fetchone()
        return _row_to_dict(row)


def update_progress(job_id: int, *, orders_synced: Optional[int] = None,
                    products_synced: Optional[int] = None, progress: Optional[int] = None) -> None:
    """运行中 job 进度回写(逐页调用,前端可轮询)。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            UPDATE store_sync_jobs SET
                orders_synced = COALESCE(:o, orders_synced),
                products_synced = COALESCE(:p, products_synced),
                progress = COALESCE(:pg, progress)
            WHERE id=:id AND status='running'
            """
        ), {"id": job_id, "o": orders_synced, "p": products_synced, "pg": progress})


def finish(job_id: int, *, status: str = "ok", error: str = "",
           error_code: Optional[str] = None) -> None:
    """结束 job(ok/failed);error_code 供前端中文映射。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            UPDATE store_sync_jobs SET status=:s, error=:e, error_code=:ec, finished_at=NOW()
            WHERE id=:id AND status='running'
            """
        ), {"id": job_id, "s": status, "e": error[:1000], "ec": error_code})


def get_job(tenant_id: str, job_id: int) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            f"SELECT {_JOB_COLS} FROM store_sync_jobs WHERE id=:id AND tenant_id=:t"
        ), {"id": job_id, "t": tenant_id}).fetchone()
    return _row_to_dict(row) if row else None


def current_job(tenant_id: str, credential_id: str) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            f"""
            SELECT {_JOB_COLS} FROM store_sync_jobs
            WHERE tenant_id=:t AND credential_id=:c AND status IN ('pending','running')
            ORDER BY created_at LIMIT 1
            """
        ), {"t": tenant_id, "c": str(credential_id)}).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(tenant_id: str, credential_id: str, limit: int = 20, offset: int = 0) -> dict:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        total = int(conn.execute(text(
            "SELECT COUNT(*) FROM store_sync_jobs WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant_id, "c": str(credential_id)}).scalar() or 0)
        rows = conn.execute(text(
            f"""
            SELECT {_JOB_COLS} FROM store_sync_jobs
            WHERE tenant_id=:t AND credential_id=:c
            ORDER BY created_at DESC, id DESC LIMIT :lim OFFSET :off
            """
        ), {"t": tenant_id, "c": str(credential_id), "lim": limit, "off": offset}).fetchall()
    return {"items": [_row_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def zombie_reset(timeout_minutes: int = JOB_TIMEOUT_MINUTES) -> int:
    """running 超时 → 重置 pending(启动/周期调用);返回重置数。"""
    with get_engine().begin() as conn:
        n = conn.execute(text(
            """
            UPDATE store_sync_jobs SET status='pending', started_at=NULL, error='zombie reset'
            WHERE status='running' AND started_at < NOW() - make_interval(mins => :m)
            """
        ), {"m": timeout_minutes}).rowcount
    return int(n or 0)


def due_credentials(now: Optional[datetime.datetime] = None) -> list[dict]:
    """扫描 active+sync_enabled 凭证,返回需同步项及 due 分节。

    分节调度:orders/products 各自水位判断;orders_sync_incomplete 触发 continuation。
    从未同步(水位 NULL)按两节都 due 处理(initial 已入队时由唯一索引去重)。
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    from services.store_sync_service import (
        _ACTIONS_INTERVAL_MIN, _ANALYTICS_INTERVAL_MIN, _RATING_INTERVAL_MIN,
        _RETURNS_INTERVAL_MIN, _WAREHOUSE_INTERVAL_MIN, _domain_state_row,
    )
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            """
            SELECT c.id::text AS credential_id, c.tenant_id,
                   c.sync_interval_minutes, c.sync_products_interval_minutes,
                   s.orders_last_synced_at, s.products_last_synced_at,
                   s.orders_sync_incomplete
            FROM credentials c
            LEFT JOIN credential_sync_state s
                ON s.credential_id = c.id AND s.tenant_id = c.tenant_id
            WHERE c.status='active' AND c.sync_enabled
            """
        )).fetchall()

    out: list[dict] = []
    for r in rows:
        cid = str(r.credential_id)
        orders_interval = max(5, int(r.sync_interval_minutes or DEFAULT_ORDERS_INTERVAL_MINUTES))
        products_interval = max(5, int(r.sync_products_interval_minutes or DEFAULT_PRODUCTS_INTERVAL_MINUTES))
        orders_due = r.orders_last_synced_at is None or (
            r.orders_last_synced_at + datetime.timedelta(minutes=orders_interval) <= now)
        products_due = r.products_last_synced_at is None or (
            r.products_last_synced_at + datetime.timedelta(minutes=products_interval) <= now)
        sections: list[str] = []
        if orders_due:
            sections.append("orders")
        if products_due:
            sections.append("products")
        incomplete = bool(r.orders_sync_incomplete)
        domains_due: list[str] = []
        ds = _domain_state_row(str(r.tenant_id), cid)
        for domain, interval in (
            ("returns", _RETURNS_INTERVAL_MIN), ("actions", _ACTIONS_INTERVAL_MIN),
            ("warehouse", _WAREHOUSE_INTERVAL_MIN), ("analytics", _ANALYTICS_INTERVAL_MIN),
            ("rating", _RATING_INTERVAL_MIN),
        ):
            last = (ds.get(domain) or {}).get("last_synced_at")
            if not last:
                domains_due.append(domain)
                continue
            try:
                last_dt = datetime.datetime.fromisoformat(str(last))
            except (ValueError, TypeError):
                domains_due.append(domain)
                continue
            if last_dt + datetime.timedelta(minutes=interval) <= now:
                domains_due.append(domain)
        if not sections and not incomplete and not domains_due:
            continue
        out.append({
            "tenant_id": str(r.tenant_id),
            "credential_id": cid,
            "sections": sections,
            "incomplete": incomplete,
            "domains_due": domains_due,
        })
    return out


def mark_sync_success(tenant_id: str, credential_id: str, job_id: int) -> None:
    """job ok 后推进 sync_state:last_success_at + 清连续失败 + 记 last_job_id。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, last_success_at, consecutive_failures, last_job_id,
                 orders_error, products_error, updated_at)
            VALUES (:t, :c, NOW(), 0, :jid, '', '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                last_success_at = NOW(),
                consecutive_failures = 0,
                last_job_id = :jid,
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": str(credential_id), "jid": job_id})


def mark_sync_failure(tenant_id: str, credential_id: str) -> None:
    """job failed 后递增连续失败(退避/stale 判定用)。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, consecutive_failures, orders_error, products_error, updated_at)
            VALUES (:t, :c, 1, '', '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                consecutive_failures = credential_sync_state.consecutive_failures + 1,
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": str(credential_id)})

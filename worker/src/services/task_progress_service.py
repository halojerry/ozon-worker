"""PRD M3/M4: 任务进度事件 — task_progress_events append-only,前端时间线/SSE 数据源。

- emit:每阶段/子步落一行(seq 单调,按 task_id 递增);
- list_events:按 seq 升序读取(SSE Last-Event-ID 增量回放用);
- 事件表不存在(未迁移)时静默降级,不阻断任务管线。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)


def emit(task_id: str, node: str, step: str = "", status: str = "progress",
         message: str = "", detail: Optional[dict] = None) -> Optional[int]:
    """追加一条进度事件,返回 seq;表缺失/异常 → None(静默降级)。"""
    try:
        with get_engine().begin() as conn:
            seq = int(conn.execute(text(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_progress_events WHERE task_id=:t"
            ), {"t": task_id}).scalar() or 1)
            conn.execute(text(
                """
                INSERT INTO task_progress_events (task_id, seq, node, step, status, message, detail)
                VALUES (:t, :s, :n, :st, :stt, :m, CAST(:d AS jsonb))
                """
            ), {
                "t": task_id, "s": seq, "n": node[:64], "st": step[:64],
                "stt": status[:16], "m": message[:500],
                "d": json.dumps(detail, ensure_ascii=False) if detail else None,
            })
        return seq
    except Exception as exc:
        logger.debug("进度事件写入失败(降级) task=%s: %s", task_id, str(exc)[:120])
        return None


def list_events(task_id: str, after_seq: int = 0, limit: int = 500) -> list[dict]:
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                """
                SELECT seq, node, step, status, message, detail, started_at, finished_at
                FROM task_progress_events WHERE task_id=:t AND seq > :after
                ORDER BY seq LIMIT :lim
                """
            ), {"t": task_id, "after": after_seq, "lim": limit}).fetchall()
        return [{
            "seq": int(r.seq), "node": r.node, "step": r.step, "status": r.status,
            "message": r.message or "", "detail": r.detail,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        } for r in rows]
    except Exception:
        return []


def is_terminal(task_id: str) -> bool:
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT status FROM ozon_product_tasks WHERE id::text=:t"
            ), {"t": task_id}).fetchone()
        return bool(row and row[0] in ("completed", "failed", "rejected", "cancelled", "canceled"))
    except Exception:
        return False

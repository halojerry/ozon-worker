"""备份心跳（BL-08，repo-gov B2-β）——备份脚本可观测性。

mark_backup 由备份脚本每次收尾调用（成功/失败都写一行 backup_heartbeat）；
last_backup_at 给监控/健康检查读最近一次备份时间（epoch 秒）。

关键约束：两个入口都整体吞错（logger.warning）——心跳是观测面，
**绝不能让备份脚本因心跳写失败而失败**，也不能让读端点因心跳表
缺失/不可达而抛异常（返回 None = 「无心跳数据」语义）。
表结构见 storage/database/shared/model.py 的 BackupHeartbeat（append-only）。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

_DETAIL_MAX = 500


def mark_backup(ok: bool, detail: str = "") -> None:
    """备份收尾写心跳一行（append-only）。整体吞错，绝不向调用方抛异常。

    Args:
        ok: True=备份成功 / False=失败
        detail: 失败原因或摘要（写入侧截 500）
    """
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO backup_heartbeat (finished_at, ok, detail) "
                    "VALUES (:ts, :ok, :detail)"
                ),
                {
                    "ts": time.time(),
                    "ok": bool(ok),
                    "detail": (detail or "")[:_DETAIL_MAX],
                },
            )
    except Exception as exc:
        logger.warning(
            "备份心跳写入失败（不阻断备份流程）ok=%s: %s", ok, str(exc)[:200]
        )


def last_backup_at() -> float | None:
    """最近一次**成功**备份（ok=true）的 finished_at（epoch 秒）。

    只认成功行——连续失败的备份不刷新新鲜度，/health 的 backup_stale 才能如实
    告警「备份链断了」而不是被失败心跳掩盖。无行 / 查询失败 → None（调用方
    自行决定「从未备份」与「心跳不可读」的呈现）。
    """
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT finished_at FROM backup_heartbeat "
                    "WHERE ok = true "
                    "ORDER BY finished_at DESC LIMIT 1"
                )
            ).fetchone()
    except Exception as exc:
        logger.warning("备份心跳查询失败: %s", str(exc)[:200])
        return None
    if row is None:
        return None
    return float(row[0])

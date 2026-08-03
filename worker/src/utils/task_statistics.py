"""任务统计载荷构造（v0.19）。

独立小模块（仅标准库）便于单测：把 get_task_statistics 的聚合行映射为
TaskStatisticsResponse 字段（total/pending/running/completed/failed/
cancelled/avg_duration_seconds）。此前字段名（total_tasks 等）与响应模型
对不上，Pydantic 全填默认值导致统计接口恒返回 0。
"""
from __future__ import annotations

from typing import Optional


def statistics_payload(
    total: int,
    completed: int,
    failed: int,
    running: int,
    pending: int,
    cancelled: int = 0,
    avg_duration_seconds: Optional[float] = None,
) -> dict:
    """把聚合统计行映射为 TaskStatisticsResponse 字段。"""
    return {
        "total": int(total or 0),
        "pending": int(pending or 0),
        "running": int(running or 0),
        "completed": int(completed or 0),
        "failed": int(failed or 0),
        "cancelled": int(cancelled or 0),
        "avg_duration_seconds": round(float(avg_duration_seconds), 2)
        if avg_duration_seconds is not None else None,
    }

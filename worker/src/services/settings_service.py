"""用户设置(系统设置真实化):tenant 级 JSONB KV。

默认值集中在此(前端不再写死);PUT 只合并传入键,未传键保持原值/默认;
数值做范围校验(防脏数据)。业务参数与通知设置同表存储,便于扩展。
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    # 业务参数
    "fx_buffer_percent": 3.5,        # 默认汇率缓冲 %
    "low_stock_threshold": 10,       # 低库存预警值
    "auto_review_enabled": True,     # 自动上架审核(评分达标自动提交)
    "auto_review_score": 85,         # 自动提交评分阈值
    # 通知设置
    "order_status_notify": True,     # 订单状态提醒
    "task_fail_notify": True,        # 任务失败通知
    "daily_report_enabled": False,   # 每日经营日报
}

_RANGES: dict[str, tuple[Optional[float], Optional[float]]] = {
    "fx_buffer_percent": (0, 50),
    "low_stock_threshold": (0, 100000),
    "auto_review_score": (50, 100),
}


def get_settings(tenant_id: str) -> dict:
    """读用户设置(合并默认值,保证前端永远拿全量键)。"""
    merged = copy.deepcopy(DEFAULTS)
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT settings FROM user_settings WHERE tenant_id=:t"
        ), {"t": tenant_id}).fetchone()
    if row and isinstance(row[0], dict):
        for k, v in row[0].items():
            if k in DEFAULTS:
                merged[k] = v
    return merged


def update_settings(tenant_id: str, patch: dict) -> dict:
    """合并更新(仅接受已知键,数值范围校验)。"""
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="settings 必须是对象")
    unknown = [k for k in patch if k not in DEFAULTS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知设置项: {', '.join(unknown)}",
        )
    current = get_settings(tenant_id)
    for k, v in patch.items():
        if isinstance(v, bool):
            current[k] = v
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{k} 必须是数字或布尔")
        lo, hi = _RANGES.get(k, (None, None))
        if lo is not None and fv < lo:
            raise HTTPException(status_code=400, detail=f"{k} 不能小于 {lo}")
        if hi is not None and fv > hi:
            raise HTTPException(status_code=400, detail=f"{k} 不能大于 {hi}")
        current[k] = fv
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO user_settings (tenant_id, settings, updated_at)
            VALUES (:t, CAST(:s AS jsonb), NOW())
            ON CONFLICT (tenant_id) DO UPDATE SET
                settings = EXCLUDED.settings, updated_at = NOW()
            """
        ), {"t": tenant_id, "s": json.dumps(current, ensure_ascii=False)})
    return current

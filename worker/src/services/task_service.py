"""T8: 任务列表业务层 — 租户隔离 + 分页查询 ozon_product_tasks。

- 只读：SELECT id/status/result/progress/created_at/updated_at，不写任何数据
- 租户隔离：WHERE tenant_id=:tenant_id（A 租户看不到 B 租户的任务）
- 分页：ORDER BY created_at DESC + LIMIT/OFFSET；total 用 COUNT(*) 独立查询
- product_summary 从 result JSONB 的 product_summary 键提取（task_processor 写入）
- M1.1 resolve_draft_by_task：失败/被拒任务 → 找回采集箱草稿（重上闭环 worker 侧）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

_SELECT_COLS = "id::text AS id, status, result, progress, payload, created_at, updated_at"


def _payload_meta(payload: Any) -> dict:
    """从 payload 安全提取非敏感展示字段（T12 前端表格列 + P0-2 上架方式）。

    只取 title/图片/货号/账号/店铺/跟卖标记/编辑更新/重上来源——绝不回显
    token/api_key 等敏感字段。payload 缺失或结构异常时全部兜底为空。
    """
    if not isinstance(payload, dict):
        return {}
    envelope = payload.get("envelope") or {}
    draft = envelope.get("draft") or {}
    images = draft.get("images") or []
    ext = envelope.get("extensions") or {}
    return {
        "title": draft.get("title"),
        "image": images[0] if isinstance(images, list) and images else None,
        "item_id": draft.get("item_id"),
        "ozon_client_id": payload.get("ozon_client_id"),
        "shop_name": payload.get("shop_name"),
        "follow_sell": bool(ext.get("follow_sell")),
        # P0-2 上架方式细分：编辑更新（update_product_id）/ 重上来源（parent_task_id）
        "update_mode": bool(ext.get("update_product_id")),
        "parent_task_id": str(payload.get("parent_task_id") or "") or None,
    }


def _as_dict(value: Any) -> Any:
    """JSONB 列兼容 dict 与 JSON 字符串（psycopg2 通常返回 dict，防御性兜底）。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _row_to_item(row) -> dict:
    result = _as_dict(row.result)
    item = {
        "id": str(row.id),
        "status": row.status,
        "progress": _as_dict(row.progress),
        "product_summary": (result or {}).get("product_summary", []),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    # 单测 FakeRow 无 payload 属性时跳过（getattr 兜底，兼容 mock 行）
    item.update(_payload_meta(getattr(row, "payload", None)))
    return item


def list_tasks(tenant_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """按租户查询任务列表（created_at DESC），返回 {items, total, limit, offset}。"""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM ozon_product_tasks "
            "WHERE tenant_id=:tenant_id ORDER BY created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ), {"tenant_id": tenant_id, "limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM ozon_product_tasks WHERE tenant_id=:tenant_id"
        ), {"tenant_id": tenant_id}).scalar()
    return {
        "items": [_row_to_item(r) for r in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }


def resolve_draft_by_task(tenant_id: str, task_id: str) -> Optional[str]:
    """task → 采集箱草稿解析（M1.1 重上闭环 worker 侧）。

    解析顺序：
        1. draft_submissions.submitted_task_id=:task_id → draft_id（采集任务）
        2. product_task_index.task_id=:task_id → draft_id（直连任务回落）
        3. 都无 → None（直连任务无草稿关联）

    租户隔离：task 必须先确认属于该 tenant（ozon_product_tasks WHERE id AND tenant_id）；
    任务不存在/跨租户 → 404（在解析前拦截，避免泄露其他租户的草稿关联）。
    """
    with get_engine().connect() as conn:
        owner = conn.execute(text(
            "SELECT 1 FROM ozon_product_tasks "
            "WHERE id::text=:task_id AND tenant_id=:tenant_id LIMIT 1"
        ), {"task_id": task_id, "tenant_id": tenant_id}).fetchone()
        if owner is None:
            raise HTTPException(status_code=404, detail="任务不存在或无权访问")

        row = conn.execute(text(
            "SELECT draft_id FROM draft_submissions "
            "WHERE submitted_task_id=:task_id LIMIT 1"
        ), {"task_id": task_id}).fetchone()
        if row is not None and row[0] is not None:
            return str(row[0])

        row = conn.execute(text(
            "SELECT draft_id FROM product_task_index "
            "WHERE task_id::text=:task_id LIMIT 1"
        ), {"task_id": task_id}).fetchone()
        if row is not None and row[0] is not None:
            return str(row[0])

    return None
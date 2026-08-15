"""M2.1: 在售商品列表业务层 — 租户隔离 + 分页查询 product_task_index。

- 只读：SELECT product_task_index LEFT JOIN ozon_product_tasks 提取审核状态，不写任何数据
- 租户隔离：WHERE tenant_id=:tenant_id（A 租户看不到 B 租户的商品）
- 分页：ORDER BY created_at DESC + LIMIT/OFFSET；total 用 COUNT(*) 独立查询
- moderation_status：尽力从任务 result JSONB 提取（LEFT JOIN，无 → null），不实时调 Ozon
  （缓存语义：任务终态即最新，避免速率限制）
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# t.result->>'moderation_status'：LEFT JOIN 提取 JSONB 键（无匹配/键缺失 → NULL）
_SELECT_COLS = (
    "i.product_id, i.offer_id, i.task_id, i.draft_id, i.credential_id, i.created_at, "
    "t.result->>'moderation_status' AS moderation_status"
)


def _row_to_item(row) -> dict:
    return {
        "product_id": str(row[0]),
        "offer_id": str(row[1]),
        "task_id": str(row[2]),
        "draft_id": str(row[3]) if row[3] is not None else None,
        "credential_id": str(row[4]) if row[4] is not None else None,
        "created_at": row[5],
        "moderation_status": row[6],
    }


def list_products(tenant_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """按租户查询在售商品列表（created_at DESC），返回 {items, total, limit, offset}。"""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} "
            "FROM product_task_index i "
            "LEFT JOIN ozon_product_tasks t ON t.id = i.task_id "
            "WHERE i.tenant_id=:tenant_id ORDER BY i.created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ), {"tenant_id": tenant_id, "limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM product_task_index WHERE tenant_id=:tenant_id"
        ), {"tenant_id": tenant_id}).scalar()
    return {
        "items": [_row_to_item(r) for r in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }

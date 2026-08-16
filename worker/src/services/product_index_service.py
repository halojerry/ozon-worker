"""T6: product_task_index 共享访问 — 从 image_service 抽取，供改图/编辑/学习回填复用。

product_task_index 表：product_id → (offer_id, task_id, credential_id, draft_id) 的
商品↔任务↔店铺映射。写入方：T14 改图（update_product_images）/ T9 上传成功回填；
读取方：改图、GET /products/{id}/edit 编辑数据（T6）。

租户隔离纪律：lookup 必须带 tenant_id 过滤（A 租户看不到 B 租户的商品），
upsert 必须带 tenant_id 落库。函数签名与抽取前完全一致（back-compat）。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# C1b: 索引行 UPSERT（回填/刷新商品↔任务↔店铺映射，approved 路径挂钩）
_INDEX_UPSERT_SQL = text(
    "INSERT INTO product_task_index "
    "(product_id, tenant_id, offer_id, task_id, credential_id, draft_id) "
    "VALUES (:product_id, :tenant_id, :offer_id, :task_id, :credential_id, :draft_id) "
    "ON CONFLICT (product_id) DO UPDATE SET "
    "  offer_id = EXCLUDED.offer_id, task_id = EXCLUDED.task_id, "
    "  credential_id = EXCLUDED.credential_id, draft_id = EXCLUDED.draft_id, created_at = NOW()"
)


def lookup_index(tenant_id: str, product_id: str) -> Optional[dict]:
    """product_task_index 定位（租户隔离）；无索引 → None（调用方转 404）。"""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT product_id, offer_id, task_id, credential_id, draft_id "
                "FROM product_task_index WHERE product_id=:pid AND tenant_id=:tenant_id"
            ), {"pid": product_id, "tenant_id": tenant_id}).fetchone()
    except Exception as e:
        logger.warning("product_task_index 查询失败 product_id=%s: %s", product_id, e)
        return None
    if row is None:
        return None
    return {
        "product_id": str(row[0]),
        "offer_id": str(row[1]),
        "task_id": str(row[2]),
        "credential_id": str(row[3]) if row[3] is not None else None,
        "draft_id": str(row[4]) if row[4] is not None else None,
    }


def upsert_index(
    tenant_id: str, product_id: str, offer_id: str, task_id: str, credential_id: str,
    draft_id: Optional[str] = None,
) -> None:
    """写入/刷新索引行（ON CONFLICT (product_id) 覆盖映射 + created_at 刷新）。"""
    with get_engine().begin() as conn:
        conn.execute(_INDEX_UPSERT_SQL, {
            "product_id": product_id, "tenant_id": tenant_id, "offer_id": offer_id,
            "task_id": task_id, "credential_id": credential_id, "draft_id": draft_id,
        })

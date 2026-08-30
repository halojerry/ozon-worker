"""PRD M5(P2): 店铺数据硬删除(管理端授权,默认关闭)。

语义:吊销后清除该店全部缓存/同步历史/成本货源/进度数据,保留用户草稿本体与
平台级审计(store_operation_log 落一条 hard_delete 记录)。

安全门槛:
- 仅管理端(require_admin)可调;
- 路由层要求 confirm=true(二次确认)+ 环境开关 ADMIN_HARD_DELETE_ENABLED=1(默认关闭);
- 删除范围 = 该 tenant + 该 credential 的店级表(逐表校验存在性,防老库缺表);
- 单事务执行,任何表删除失败 → 整体回滚,不产生半删状态;
- 用户级数据(credentials 行/商品草稿/order_notes 手工备注/fx_rates)不删:
  credentials 仍保留吊销记录供审计,草稿是用户资产,order_notes 无店维度。
"""
from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# 店级缓存/历史表(tenant_id + credential_id 双维度删除)
STORE_SCOPED_TABLES = [
    "scheduled_listings",
    "draft_submissions",
    "ozon_orders_cache",
    "ozon_products_cache",
    "ozon_returns_cache",
    "ozon_store_analytics_daily",
    "store_daily_metrics",
    "store_metrics_history",
    "warehouse_cache",
    "store_sync_jobs",
    "credential_sync_state",
    "order_line_costs",
    "product_costs",
    "product_cost_history",
    "source_candidates",
    "product_task_index",
]


def hard_delete_credential_data(admin_user_id: str, credential_id: str) -> dict:
    """硬删除某店全部缓存/历史数据(管理端+confirm 已由路由校验)。"""
    import uuid as _uuid

    try:
        uid = _uuid.UUID(str(credential_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="凭证不存在")

    with get_engine().begin() as conn:
        # 1. 定位凭证(含已吊销行,审计需要 tenant_id/client_id)
        cred = conn.execute(text(
            "SELECT tenant_id, ozon_client_id FROM credentials WHERE id=:id"
        ), {"id": uid}).fetchone()
        if cred is None:
            raise HTTPException(status_code=404, detail="凭证不存在")
        tenant_id, client_id = str(cred[0]), str(cred[1])

        # 2. 逐表删除(校验表+列存在,防老库缺表整事务失败)
        counts: dict[str, int] = {}
        for table in STORE_SCOPED_TABLES:
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:t"
            ), {"t": table}).fetchone()
            if not exists:
                counts[table] = 0
                continue
            has_cols = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t "
                "AND column_name IN ('tenant_id','credential_id')"
            ), {"t": table}).scalar()
            if int(has_cols or 0) < 2:
                counts[table] = 0
                continue
            deleted = conn.execute(text(
                f"DELETE FROM {table} "
                "WHERE tenant_id=:t AND credential_id::text=:c"
            ), {"t": tenant_id, "c": str(uid)})
            counts[table] = int(deleted.rowcount or 0)

        # 3. 审计(store_operation_log,result 与删除结果一致)
        conn.execute(text(
            """
            INSERT INTO store_operation_log
                (tenant_id, credential_id, store_id, operation, target_id, before,
                 after, result, error, operator)
            VALUES (:t, :c, :client, 'hard_delete', :c, '{}', :after,
                    'success', '', :op)
            """
        ), {
            "t": tenant_id, "c": str(uid), "client": client_id,
            "after": json.dumps(counts, ensure_ascii=False), "op": admin_user_id,
        })

    total = sum(counts.values())
    logger.info("硬删除完成 operator=%s credential=%s 删除行=%d %s",
                admin_user_id, credential_id, total, counts)
    return {"ok": True, "credential_id": credential_id,
            "deleted_total": total, "per_table": counts}

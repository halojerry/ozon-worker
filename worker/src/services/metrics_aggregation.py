"""PRD M3: 店铺指标日聚合 + 保留清理。

- store_metrics_history(原始快照) → store_daily_metrics(店×日 upsert);
  profit_amount 来自 ozon_orders_cache.real_profit(有成本才填,无则 NULL)。
- 保留:快照 30 天、sync_jobs 30 天且每店最多 500 条、订单缓存 180 天(终态)。
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

METRICS_RETENTION_DAYS = int(os.getenv("STORE_METRICS_RETENTION_DAYS", "30"))
JOB_RETENTION_DAYS = int(os.getenv("STORE_SYNC_JOB_RETENTION_DAYS", "30"))
JOB_KEEP_PER_STORE = int(os.getenv("STORE_SYNC_JOB_KEEP_PER_STORE", "500"))
ORDER_RETENTION_DAYS = int(os.getenv("ORDER_CACHE_RETENTION_DAYS", "180"))
AGG_INTERVAL_SECONDS = int(os.getenv("METRICS_AGG_INTERVAL_SECONDS", "600"))


def run_aggregation() -> int:
    """近 30 天快照 → store_daily_metrics upsert;profit 来自订单 real_profit。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO store_daily_metrics
                (tenant_id, credential_id, store_id, stat_date, order_count,
                 sales_amount, commission_amount, product_count, low_stock_count,
                 active_discount_count, profit_rate, created_at)
            SELECT tenant_id, credential_id, store_id, snapshot_at::date,
                   SUM(order_count), SUM(sales_amount), SUM(commission_amount),
                   MAX(product_count), MAX(low_stock_count),
                   MAX(active_discount_count), AVG(profit_rate), NOW()
            FROM store_metrics_history
            WHERE snapshot_at >= CURRENT_DATE - make_interval(days => :d)
            GROUP BY tenant_id, credential_id, store_id, snapshot_at::date
            ON CONFLICT (tenant_id, credential_id, stat_date) DO UPDATE SET
                order_count = EXCLUDED.order_count,
                sales_amount = EXCLUDED.sales_amount,
                commission_amount = EXCLUDED.commission_amount,
                product_count = EXCLUDED.product_count,
                low_stock_count = EXCLUDED.low_stock_count,
                active_discount_count = EXCLUDED.active_discount_count,
                profit_rate = EXCLUDED.profit_rate,
                created_at = NOW()
            """
        ), {"d": METRICS_RETENTION_DAYS})
        # profit_amount 来自订单真实利润(有成本才填)
        conn.execute(text(
            """
            UPDATE store_daily_metrics m SET profit_amount = sub.p
            FROM (
                SELECT tenant_id, credential_id, (order_created_at AT TIME ZONE 'UTC')::date AS d,
                       SUM(real_profit) AS p
                FROM ozon_orders_cache
                WHERE order_created_at IS NOT NULL AND real_profit IS NOT NULL
                GROUP BY 1, 2, 3
            ) sub
            WHERE m.tenant_id = sub.tenant_id AND m.credential_id = sub.credential_id
              AND m.stat_date = sub.d
            """
        ))
    return 0


def prune() -> dict:
    """保留清理:快照/job/订单缓存。"""
    with get_engine().begin() as conn:
        snap = conn.execute(text(
            "DELETE FROM store_metrics_history "
            "WHERE snapshot_at < NOW() - make_interval(days => :d)"
        ), {"d": METRICS_RETENTION_DAYS}).rowcount
        jobs = conn.execute(text(
            """
            DELETE FROM store_sync_jobs WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY credential_id ORDER BY created_at DESC, id DESC) rn
                    FROM store_sync_jobs
                ) t WHERE rn <= :keep
            ) OR created_at < NOW() - make_interval(days => :d)
            """
        ), {"keep": JOB_KEEP_PER_STORE, "d": JOB_RETENTION_DAYS}).rowcount
        orders = conn.execute(text(
            "DELETE FROM ozon_orders_cache "
            "WHERE order_created_at < NOW() - make_interval(days => :d)"
        ), {"d": ORDER_RETENTION_DAYS}).rowcount
    return {"metrics_history": int(snap or 0), "sync_jobs": int(jobs or 0),
            "orders_cache": int(orders or 0)}


async def aggregation_loop(stop_event=None) -> None:
    """后台循环:日聚合 + 保留清理(默认 10min;stop_event 置位即退出)。"""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(run_aggregation)
            pruned = await asyncio.to_thread(prune)
            if pruned["metrics_history"] or pruned["sync_jobs"] or pruned["orders_cache"]:
                logger.info("保留清理完成: %s", pruned)
        except Exception as exc:
            logger.warning("日聚合/清理异常(不退出): %s", str(exc)[:200])
        try:
            await asyncio.wait_for(
                (stop_event.wait() if stop_event else asyncio.sleep(AGG_INTERVAL_SECONDS)),
                timeout=AGG_INTERVAL_SECONDS,
            )
            if stop_event and stop_event.is_set():
                return
        except asyncio.TimeoutError:
            continue

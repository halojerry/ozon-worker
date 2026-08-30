"""工作台聚合(上生产前演示清零):GET /api/v1/dashboard/overview。

租户隔离,聚合该用户全部店铺:
- today:今日订单/销售额/佣金/净利(store_daily_metrics 日聚合;空 → 0)
- active_products:在售商品数(ozon_products_cache 未归档且非 error,跨店去重)
- pending_tasks:进行中任务数(ozon_product_tasks pending/running/pending_moderation)
- trend:近 N 天销售趋势(日聚合按 date 汇总)
- hot_products:近 30 天销量 Top5(解析 ozon_orders_cache.products JSONB,防脏兜底)
- latest_orders:最近订单快照(单号/标题/金额/状态)
- last_synced_at:全店最近一次成功同步
全部只读 PG,不触发任何 Ozon 调用。
"""
from __future__ import annotations

import logging
import datetime as _dt

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)


def _num(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_dashboard(tenant_id: str, trend_days: int = 14) -> dict:
    days = max(3, min(int(trend_days), 90))
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    with get_engine().connect() as conn:
        # 今日聚合(全店)
        row = conn.execute(text(
            """
            SELECT COALESCE(SUM(order_count), 0),
                   COALESCE(SUM(sales_amount), 0),
                   COALESCE(SUM(commission_amount), 0),
                   COALESCE(SUM(profit_amount), 0)
            FROM store_daily_metrics WHERE tenant_id=:t AND stat_date=:d
            """
        ), {"t": tenant_id, "d": today}).fetchone()
        today_orders = int(row[0] or 0)
        today_sales = round(_num(row[1]), 2)
        today_commission = round(_num(row[2]), 2)
        today_profit = row[3]
        today_profit = round(float(today_profit), 2) if today_profit is not None else None

        # 在售商品数(跨店去重,未归档且非 error)
        active = conn.execute(text(
            "SELECT COUNT(DISTINCT product_id) FROM ozon_products_cache "
            "WHERE tenant_id=:t AND archived=FALSE AND status <> 'error'"
        ), {"t": tenant_id}).scalar()

        # 进行中任务
        pending = conn.execute(text(
            "SELECT COUNT(*) FROM ozon_product_tasks "
            "WHERE tenant_id=:t AND status IN ('pending','running','pending_moderation')"
        ), {"t": tenant_id}).scalar()

        # 趋势(近 N 天)
        trend_rows = conn.execute(text(
            """
            SELECT stat_date, SUM(order_count), SUM(sales_amount), SUM(profit_amount)
            FROM store_daily_metrics
            WHERE tenant_id=:t AND stat_date >= CURRENT_DATE - (:n || ' days')::interval
            GROUP BY stat_date ORDER BY stat_date
            """
        ), {"t": tenant_id, "n": days}).fetchall()
        trend = [{
            "date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "orders": int(r[1] or 0),
            "sales_amount": round(_num(r[2]), 2),
            "profit_amount": round(float(r[3]), 2) if r[3] is not None else None,
        } for r in trend_rows]

        # 热销 Top5(近 30 天订单行,按数量聚合)
        hot = conn.execute(text(
            """
            SELECT p->>'product_id' AS pid,
                   COALESCE(p->>'name', p->>'sku', '未知商品') AS name,
                   SUM(CASE WHEN p->>'quantity' ~ '^[0-9]+$'
                            THEN (p->>'quantity')::int ELSE 1 END) AS qty
            FROM ozon_orders_cache, jsonb_array_elements(products) AS p
            WHERE tenant_id=:t AND COALESCE(order_created_at, synced_at)
                  >= NOW() - INTERVAL '30 days'
              AND jsonb_typeof(products) = 'array'
            GROUP BY 1, 2 ORDER BY qty DESC LIMIT 5
            """
        ), {"t": tenant_id}).fetchall()
        hot_products = [{
            "product_id": str(r[0] or ""),
            "name": str(r[1]),
            "quantity": int(r[2] or 0),
        } for r in hot]

        # 最近订单
        latest = conn.execute(text(
            """
            SELECT posting_number, COALESCE(products->0->>'name', ''), total_amount,
                   status, COALESCE(order_created_at, synced_at)
            FROM ozon_orders_cache
            WHERE tenant_id=:t ORDER BY 5 DESC LIMIT 8
            """
        ), {"t": tenant_id}).fetchall()
        latest_orders = [{
            "posting_number": str(r[0]),
            "product_name": str(r[1] or "—"),
            "total_amount": round(_num(r[2]), 2) if r[2] is not None else None,
            "status": str(r[3] or ""),
            "created_at": r[4].isoformat() if r[4] else None,
        } for r in latest]

        # 最近一次成功同步(全店)
        last_sync = conn.execute(text(
            "SELECT MAX(last_success_at) FROM credential_sync_state WHERE tenant_id=:t"
        ), {"t": tenant_id}).scalar()

    return {
        "today": {
            "orders_count": today_orders,
            "sales_amount": today_sales,
            "commission_amount": today_commission,
            "profit_amount": today_profit,
        },
        "active_products": int(active or 0),
        "pending_tasks": int(pending or 0),
        "store_count": _store_count(tenant_id),
        "trend": trend,
        "hot_products": hot_products,
        "latest_orders": latest_orders,
        "last_synced_at": last_sync.isoformat() if last_sync else None,
        "trend_days": days,
    }


def _store_count(tenant_id: str) -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(text(
            "SELECT COUNT(*) FROM credentials "
            "WHERE tenant_id=:t AND status='active'"
        ), {"t": tenant_id}).scalar() or 0)

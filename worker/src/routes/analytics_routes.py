"""Batch 5 T5.1: Analytics aggregation endpoints — market overview, categories, hot queries, sales trend.

端点（挂载在 /api/v1/analytics 下）：
    GET /market-overview   → 聚合: total_gmv, total_orders, total_products, total_discovery_runs, bestseller_count
    GET /categories        → 按类目聚合 discovery_runs: items[{category, run_count, total_products}]
    GET /hot-queries      → blue_ocean_queries 按 uniq_queries_wca DESC
    GET /sales-trend       → ozon_orders_cache 按天聚合: items[{date, gmv, orders}]

鉴权：Bearer token → main._verify_analytics_token（Supabase 未配置 → 本地放行），
按 token 复用 main.RateLimiter（与 seo_keywords / commissions lookup 同款）。
错误不回显内部异常（对齐 analytics 端点安全纪律）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _auth_rate_limit(request: Request) -> dict:
    """从 Bearer header 提取 token → 解析 scope(PRD M2:用户租户内/admin 全局)+ 限流。

    Returns: {"tenant_id": str, "is_admin": bool};未配置 Supabase → 本地非 admin。
    """
    from main import (
        RATE_LIMIT_PER_MINUTE,
        rate_limiter,
    )
    from services.tenant_service import resolve_analytics_scope

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    scope = resolve_analytics_scope(clean_token)

    allowed, _remaining = rate_limiter.check(clean_token)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute",
        )
    return scope


@router.get("/market-overview")
async def http_market_overview(request: Request):
    """聚合市场概览:用户看自己店铺,admin 看全平台;热销品数保持全局共享目录。"""
    scope = _auth_rate_limit(request)
    tenant_filter = "" if scope["is_admin"] else " WHERE tenant_id=:t"

    from sqlalchemy import text
    from storage.database.db import get_engine

    result = {
        "total_gmv": 0.0,
        "total_orders": 0,
        "total_products": 0,
        "total_discovery_runs": 0,
        "bestseller_count": 0,
    }

    try:
        with get_engine().connect() as conn:
            # total_gmv: sum of total_amount from ozon_orders_cache
            row = conn.execute(text(
                f"SELECT COALESCE(SUM(total_amount), 0) FROM ozon_orders_cache{tenant_filter}"
            ), {"t": scope["tenant_id"]} if not scope["is_admin"] else {}).scalar()
            result["total_gmv"] = float(row or 0)

            # total_orders: count of rows in ozon_orders_cache
            row = conn.execute(text(
                f"SELECT COUNT(*) FROM ozon_orders_cache{tenant_filter}"
            ), {"t": scope["tenant_id"]} if not scope["is_admin"] else {}).scalar()
            result["total_orders"] = int(row or 0)

            # total_products: count of rows in ozon_products_cache
            row = conn.execute(text(
                f"SELECT COUNT(*) FROM ozon_products_cache{tenant_filter}"
            ), {"t": scope["tenant_id"]} if not scope["is_admin"] else {}).scalar()
            result["total_products"] = int(row or 0)

            # total_discovery_runs: count of rows in discovery_runs
            row = conn.execute(text(
                f"SELECT COUNT(*) FROM discovery_runs{tenant_filter}"
            ), {"t": scope["tenant_id"]} if not scope["is_admin"] else {}).scalar()
            result["total_discovery_runs"] = int(row or 0)

            # bestseller_count: count of rows in ozon_bestsellers
            row = conn.execute(text(
                "SELECT COUNT(*) FROM ozon_bestsellers"
            )).scalar()
            result["bestseller_count"] = int(row or 0)

    except Exception:
        # Empty data → all zeros, NO crash
        pass

    result["scope"] = "global" if scope["is_admin"] else "tenant"
    return result


@router.get("/categories")
async def http_categories(request: Request):
    """按类目聚合 discovery_runs 的选品次数和产品数(用户只看自己,admin 全局)。

    从 candidates_json 中提取 product_count（若存在）聚合。
    """
    scope = _auth_rate_limit(request)
    tenant_filter = "" if scope["is_admin"] else " WHERE tenant_id=:t"

    from sqlalchemy import text
    from storage.database.db import get_engine

    items = []

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                f"SELECT keyword, COUNT(*) as run_count, "
                "COALESCE(SUM(jsonb_array_length(candidates_json)), 0) as total_products "
                "FROM discovery_runs "
                f"{tenant_filter}"
                "GROUP BY keyword "
                "ORDER BY run_count DESC"
            ), {"t": scope["tenant_id"]} if not scope["is_admin"] else {}).fetchall()
            items = [
                {
                    "category": str(r[0]),
                    "run_count": int(r[1]),
                    "total_products": int(r[2]),
                }
                for r in rows
            ]
    except Exception:
        pass

    return {"items": items, "scope": "global" if scope["is_admin"] else "tenant"}


@router.get("/hot-queries")
async def http_hot_queries(request: Request):
    """热门蓝海关键词:仅 admin(PRD:蓝海数据管理端独享)。"""
    scope = _auth_rate_limit(request)
    if not scope["is_admin"]:
        raise HTTPException(status_code=403, detail="admin_only")

    q = request.query_params
    try:
        limit = int(q.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    from sqlalchemy import text
    from storage.database.db import get_engine

    items = []

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT query, count, ca, avg_ca_rub, avg_count_items, "
                "items_views, uniq_queries_wca, uniq_sellers "
                "FROM blue_ocean_queries "
                "ORDER BY uniq_queries_wca DESC NULLS LAST "
                "LIMIT :limit"
            ), {"limit": limit}).fetchall()
            items = [
                {
                    "query": str(r[0]),
                    "count": int(r[1] or 0),
                    "ca": float(r[2]) if r[2] is not None else None,
                    "avg_ca_rub": float(r[3]) if r[3] is not None else None,
                    "avg_count_items": float(r[4]) if r[4] is not None else None,
                    "items_views": float(r[5]) if r[5] is not None else None,
                    "uniq_queries_wca": int(r[6]) if r[6] is not None else None,
                    "uniq_sellers": float(r[7]) if r[7] is not None else None,
                }
                for r in rows
            ]
    except Exception:
        pass

    return {"items": items, "scope": "global" if scope["is_admin"] else "tenant"}


@router.get("/sales-trend")
async def http_sales_trend(request: Request):
    """销售趋势:用户看自己店铺,admin 看全平台。"""
    scope = _auth_rate_limit(request)
    tenant_filter = "" if scope["is_admin"] else " AND tenant_id=:t"

    q = request.query_params
    try:
        days = int(q.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 90))

    from sqlalchemy import text
    from storage.database.db import get_engine

    items = []

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT DATE(order_created_at) as dt, "
                "COALESCE(SUM(total_amount), 0) as gmv, "
                "COUNT(*) as orders "
                "FROM ozon_orders_cache "
                f"WHERE order_created_at >= NOW() - (:days || ' days')::interval{tenant_filter} "
                "GROUP BY dt "
                "ORDER BY dt DESC"
            ), {"days": str(days), **({"t": scope["tenant_id"]} if not scope["is_admin"] else {})}).fetchall()
            items = [
                {
                    "date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                    "gmv": float(r[1]),
                    "orders": int(r[2]),
                }
                for r in rows
            ]
    except Exception:
        pass

    return {"items": items}

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

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from api.schemas import SellerSyncIn
from services.sku_metrics_pool_service import (
    SKU_QUERY_MAX,
    SKU_SYNC_MAX_ITEMS,
    query_sku_metrics,
    upsert_seller_sync_items,
)
from storage.database.db import get_session

logger = logging.getLogger(__name__)

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


# 响应示例（openapi_extra 路由级补——聚合端点均返回裸 dict，见各 handler）
_MARKET_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "total_gmv": 152340.5, "total_orders": 1206, "total_products": 348,
    "total_discovery_runs": 27, "bestseller_count": 5000, "scope": "tenant",
}}}}}}
_CATEGORIES_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{"category": "宠物饮水机", "run_count": 6, "total_products": 240}],
    "scope": "tenant",
}}}}}}
_HOT_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{
        "query": "органайзер для косметики", "count": 1520, "ca": 0.8,
        "avg_ca_rub": 1250.5, "avg_count_items": 310.2, "items_views": 45600.0,
        "uniq_queries_wca": 118, "uniq_sellers": 128.0,
    }],
    "scope": "global",
}}}}}}
_TREND_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{"date": "2026-09-10", "gmv": 21540.0, "orders": 168}],
}}}}}}
_WTS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "found": True, "data": {"items": []},
}}}}}}
_SYNC_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "accepted": 10, "skipped": 2,
}}}}}}
_SKU_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "metrics": [{
        "sku": "123456789", "sales_payload": {"orders": 42}, "variant_payload": None,
        "category_dc": 17027532, "category_tp": 9165596, "category_name_zh": "汽车香薰",
        "needs_sales_sync": False, "needs_variant_sync": True,
        "updated_at": "2026-09-11T06:00:00+00:00",
    }],
}}}}}}


@router.get("/market-overview", openapi_extra=_MARKET_OK_EXTRA)
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


@router.get("/categories", openapi_extra=_CATEGORIES_OK_EXTRA)
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


@router.get("/hot-queries", openapi_extra=_HOT_OK_EXTRA)
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


@router.get("/sales-trend", openapi_extra=_TREND_OK_EXTRA)
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


# ── 会话直调通道（v0.70 批次 C5/C6，对标商品级运营数据入口）──
# 复用 ozon_sessions 加密会话，服务端 cookie 直调 seller.ozon.ru what_to_sell v3。
# 客户端只拼请求与判废（utils/ozon_session_client），业务判定在本端点：
#   无会话/跨租户 → 404；存量 expired → 409 fast-fail；
#   直调 401/403/登录 302 → mark expired + 409 session_expired（失效联动）。
# ⚠️ cookie 明文只进上游请求头，本端点响应绝不回显会话 cookie 值。

from services import ozon_session_service as _ozon_session_service
from utils import ozon_session_client as _ozon_session_client


@router.get("/what-to-sell", openapi_extra=_WTS_OK_EXTRA)
async def http_what_to_sell(request: Request):
    """GET /api/v1/analytics/what-to-sell?credential_id=&sku=&limit= → {found, data}。"""
    scope = _auth_rate_limit(request)
    tenant_id = scope["tenant_id"]

    q = request.query_params
    credential_id = (q.get("credential_id") or "").strip()
    sku = (q.get("sku") or "").strip()
    if not credential_id or not sku:
        raise HTTPException(status_code=400, detail="credential_id 和 sku 必填")
    try:
        limit = int(q.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        offset = max(0, int(q.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0

    # 会话归属与状态（租户隔离：跨租户/未同步同为查无 → 404）
    snap = _ozon_session_service.session_status(tenant_id, credential_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail="尚未同步会话，先在 skill 执行 session-sync",
        )
    if snap.get("status") == "expired":
        # 存量已判废 → fast-fail，不再烧直调（重同步闭环入口在 skill session-sync）
        raise HTTPException(
            status_code=409,
            detail="session_expired: Ozon 会话已失效，请在 skill 重新执行 session-sync",
        )

    cookies = _ozon_session_service.load_cookies(tenant_id, credential_id)
    sc_company_id = str((cookies or {}).get("sc_company_id") or "")
    if cookies is None or not sc_company_id:
        # load_cookies 解密失败已联动标 expired（换 key 后旧会话不可解是预期）
        raise HTTPException(
            status_code=409,
            detail="session_expired: 会话不可用（解密失败或缺 sc_company_id），请重新 session-sync",
        )

    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    payload = _ozon_session_client.build_what_to_sell_payload(sku, limit, offset)
    data, err = _ozon_session_client.what_to_sell(cookie_header, sc_company_id, payload)

    if err == "session_expired":
        # C6 失效联动：401/403/登录 302 → 标 expired，下次 fast-fail
        _ozon_session_service.mark_status(tenant_id, credential_id, "expired")
        raise HTTPException(
            status_code=409,
            detail="session_expired: Ozon 会话已失效，请在 skill 重新执行 session-sync",
        )
    if err is not None:
        raise HTTPException(status_code=502, detail=f"ozon_direct_error: {err}")

    _ozon_session_service.mark_status(tenant_id, credential_id, "active")
    return {"found": True, "data": data}


# ── 数据池 v1：跨店 SKU 指标贡献收包 + 读侧补采指令（对标 goldminer 读-回馈）──
# 服务/依赖已模块顶层导入（勿改函数内局部导入——endpoint 测试 mock.patch
# "routes.analytics_routes.upsert_seller_sync_items" 打的是模块属性）。
# 只存指标不回显内部异常（对齐 analytics 端点安全纪律）。

@router.post("/seller-sync", openapi_extra={
    # 请求体声明（v0.70 批次 C5 先例）+ 响应示例（upsert_seller_sync_items 计数）
    "requestBody": {"required": True, "content": {
        "application/json": {"schema": SellerSyncIn.model_json_schema()}}},
    "responses": {"200": {"content": {
        "application/json": {"example": {"accepted": 10, "skipped": 2}}}}},
})
async def http_seller_sync(request: Request):
    """POST /api/v1/analytics/seller-sync —— 贡献收包（goldminer ≤12/批）。

    手读 raw Request（同 main.v1_submit_task），openapi_extra 补 SellerSyncIn
    契约元数据让 /docs 与 API-REFERENCE 能展示请求体及其 _examples。
    """
    scope = _auth_rate_limit(request)
    try:
        body = SellerSyncIn.model_validate(await request.json())
    except ValidationError as exc:
        # 字段缺失/类型错 → 422 可读 detail（credentials_routes 同款，不裸 500）
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError:
        # 畸形 JSON（JSONDecodeError 是 ValueError 子类但非 ValidationError）→ 422；
        # 原文不回显（analytics 错误纪律）。
        raise HTTPException(status_code=422, detail="malformed JSON body")
    session = get_session()
    try:
        return upsert_seller_sync_items(
            session, body.items[:SKU_SYNC_MAX_ITEMS],
            source_company_id=(body.source_company_id or "").strip() or None,
            contributed_by=scope.get("tenant_id"))
    except (ValueError, KeyError, TypeError):
        # 畸形 item（如非数字 category_dc）→ 422 可读固定文案；
        # 原始异常只进日志不回显（analytics 错误纪律）。
        logger.exception("seller-sync 贡献收包含畸形 item（items=%s）", len(body.items))
        raise HTTPException(status_code=422, detail="invalid seller-sync item")
    except IntegrityError:
        # B2a-3: 写入冲突（并发同 sku 撞唯一键）是瞬态而非畸形 item——
        # 误标 422 会误导调用方改数据；给诚实文案提示重试，原文只进日志。
        logger.exception("seller-sync 写入冲突（items=%s）", len(body.items))
        raise HTTPException(status_code=503, detail="写入冲突，请重试")
    finally:
        session.close()


@router.get("/sku-metrics", openapi_extra=_SKU_OK_EXTRA)
async def http_sku_metrics(request: Request, skus: str = ""):
    """GET /api/v1/analytics/sku-metrics?skus=1,2 → {metrics: [...]}（读侧指标+补采指令，≤50/查）。

    skus 声明为 FastAPI query 参数（OpenAPI 自动可见）。
    """
    _auth_rate_limit(request)  # 鉴权+限流副作用；查询无租户维度（sku 池全局共享，同 category_mapping W11）
    raw = (skus or "").strip()
    parsed = [s for s in raw.split(",") if s.strip()]
    session = get_session()
    try:
        return {"metrics": query_sku_metrics(session, parsed[:SKU_QUERY_MAX])}
    finally:
        session.close()

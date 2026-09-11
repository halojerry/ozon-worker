"""v0.56+PRD M1: 店铺数据同步路由（薄层：鉴权 → 任务化/查询）。

    POST /api/v1/stores/{credential_id}/sync        手动同步单店 → 202 job_id(任务化)
    GET  /api/v1/stores/{credential_id}/sync-jobs   同步任务历史
    GET  /api/v1/sync-jobs/{job_id}                 单个任务状态/进度
    GET  /api/v1/stores/{credential_id}/sync-status 同步状态（最后时间/错误/在途 job/stale）
    GET  /api/v1/stores/{credential_id}/stats       店铺卡统计（今日订单/销售额/利润）
    GET  /api/v1/stores/{credential_id}/analysis    店铺分析（利润率/库存/候选清单）

鉴权：Bearer token → user_id；凭证归属 store_sync_service 内 get_decrypted 校验。
STORE_SYNC_JOBS_ENABLED=0 时 POST /sync 回退旧版同步阻塞行为（临时兜底）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from api.schemas import StoreSyncConfigUpdate
from services import store_analysis_service, store_sync_service

router = APIRouter(prefix="/stores", tags=["stores"])
detail_router = APIRouter(tags=["stores"])

# 一键全店同步冷却(内存,按租户 60s)
_sync_all_cooldown: dict[str, float] = {}

# 响应示例（openapi_extra 路由级补；结构 = store_sync_jobs / store_sync_service /
# store_analysis_service 各返回值，job 行结构见 store_sync_jobs._row_to_dict）
_JOB_EXAMPLE = {
    "id": 88, "tenant_id": "28",
    "credential_id": "7f3a91d2-4c5b-4e8f-9a01-2b3c4d5e6f70",
    "kind": "manual", "status": "running", "trigger": "manual",
    "error_code": "", "orders_synced": 42, "products_synced": 128,
    "progress": 60, "error": "",
    "started_at": "2026-09-11T08:30:00+00:00",
    "finished_at": None, "created_at": "2026-09-11T08:30:00+00:00",
}
_SYNC_ALL_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "enqueued": 2,
    "job_ids": [88, 89],
}}}}}}
_SYNC_CONFIG_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "sync_enabled": True, "sync_interval_minutes": 15,
    "sync_products_interval_minutes": 30,
}}}}}}
_SYNC_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "job_id": 88, "status": "running", "kind": "manual",
}}}}}}
_JOBS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [_JOB_EXAMPLE], "total": 1, "limit": 20, "offset": 0,
}}}}}}
_RETURNS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{
        "return_id": 1209931, "posting_number": "23456789-0010-3",
        "order_id": 44818899, "return_type": "customer", "schema": "FBS",
        "reason": "Товар не подошёл", "compensation_status": "NOT_COMPENSATED",
        "status": "arrived_at_return_place", "product": "汽车香薰",
        "synced_at": "2026-09-11T08:00:00+00:00",
    }],
    "total": 1, "limit": 50, "offset": 0,
}}}}}}
_ANALYTICS_DAILY_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{"stat_date": "2026-09-10", "metric": "hits_view_search",
               "value": 1520.0}],
}}}}}}
_WAREHOUSES_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{"warehouse_id": 23642110530000, "name": "Коледино",
               "is_rfbs": False}],
}}}}}}
_JOB_DETAIL_OK_EXTRA = {"responses": {"200": {"content": {
    "application/json": {"example": _JOB_EXAMPLE}}}}}
_HEALTH_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "summary": {"total": 2, "syncing": 1, "stale": 0},
    "items": [{
        "credential_id": "7f3a91d2-4c5b-4e8f-9a01-2b3c4d5e6f70",
        "ozon_client_id": "5381204", "shop_name": "测试店",
        "sync_enabled": True,
        "last_success_at": "2026-09-11T08:30:00+00:00",
        "consecutive_failures": 0, "is_stale": False, "current_job": None,
        "orders_error": "", "products_error": "",
    }],
}}}}}}
_DAILY_METRICS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "items": [{
        "stat_date": "2026-09-10", "order_count": 9,
        "sales_amount": 36120.0, "commission_amount": 6514.4,
        "profit_amount": 4650.0, "product_count": 120,
        "low_stock_count": 3, "active_discount_count": 2,
        "profit_rate": 0.129,
    }],
}}}}}}
_SYNC_STATUS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "credential_id": "7f3a91d2-4c5b-4e8f-9a01-2b3c4d5e6f70",
    "orders_last_synced_at": "2026-09-11T08:30:00+00:00",
    "products_last_synced_at": "2026-09-11T08:00:00+00:00",
    "orders_error": "", "products_error": "",
    "last_success_at": "2026-09-11T08:30:00+00:00",
    "consecutive_failures": 0,
}}}}}}
_STATS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "credential_id": "7f3a91d2-4c5b-4e8f-9a01-2b3c4d5e6f70",
    "ozon_client_id": "5381204", "stats_date": "2026-09-11",
    "today_orders": 12, "today_sales_amount": 48250.5,
    "today_commission": 8685.09, "today_profit": 6200.4,
    "today_product_count": 132,
    "data_freshness": {"synced_at": "2026-09-11T08:30:00+00:00",
                       "is_stale": False},
}}}}}}
_ANALYSIS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "summary": {"product_count": 120, "low_stock_count": 3,
                "active_discount_count": 2, "avg_profit_rate": 0.18},
    "profit_trend": [{"snapshot_at": "2026-09-10T00:00:00+00:00",
                      "profit_rate": 0.18, "sales_amount": 36120.0}],
    "low_margin_products": [{"product_id": "123456789", "name": "汽车香薰",
                             "price_rub": 690.0, "profit_rate": 0.04,
                             "suggestion": "考虑提价或更换货源"}],
    "out_of_stock_products": [{"product_id": "987654321", "name": "折叠水杯",
                               "stock": 0}],
    "promo_ready_products": [],
}}}}}}


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    return _authenticate_token("")


@router.post("/sync-all", openapi_extra=_SYNC_ALL_OK_EXTRA)
async def sync_all_stores(request: Request):
    """一键全店同步:入队所有 active 店 manual job(60s 冷却,去重)。"""
    tenant_id = await _authenticate(request)
    from services.store_sync_scheduler import jobs_enabled
    if not jobs_enabled():
        raise HTTPException(status_code=400, detail="任务化同步未启用(STORE_SYNC_JOBS_ENABLED=0)")
    now = time.time()
    if now - _sync_all_cooldown.get(tenant_id, 0.0) < 60:
        raise HTTPException(status_code=429, detail="操作过于频繁,请 60 秒后再试")
    _sync_all_cooldown[tenant_id] = now
    from services import store_sync_jobs
    from services.credential_service import list_credentials
    creds = list_credentials(tenant_id)
    job_ids: list[int] = []
    for c in creds:
        job = store_sync_jobs.enqueue(tenant_id, str(c["id"]), kind="manual", trigger="manual")
        job_ids.append(job["id"])
    return {"enqueued": len(creds), "job_ids": job_ids}


@router.patch("/{credential_id}/sync-config", openapi_extra=_SYNC_CONFIG_OK_EXTRA)
async def update_store_sync_config(credential_id: str, data: StoreSyncConfigUpdate, request: Request):
    """更新店铺同步配置(免 api_key;间隔下限 5min);归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)
    return store_sync_service.update_sync_config(
        tenant_id, str(credential_id),
        sync_enabled=data.sync_enabled,
        sync_interval_minutes=data.sync_interval_minutes,
        sync_products_interval_minutes=data.sync_products_interval_minutes,
    )


@router.post("/{credential_id}/sync", openapi_extra=_SYNC_OK_EXTRA)
async def sync_store(credential_id: str, request: Request):
    """手动同步单店:任务化入队 → 202 {job_id}；归属校验失败 → 404。

    旧版(特性开关=0)保留同步阻塞语义返回同步结果。
    """
    tenant_id = await _authenticate(request)
    from services.credential_service import get_decrypted
    get_decrypted(tenant_id, credential_id)  # 归属校验(跨租户 404)
    from services.store_sync_scheduler import jobs_enabled
    if not jobs_enabled():
        return store_sync_service.sync_store(tenant_id, credential_id)
    from services import store_sync_jobs
    job = store_sync_jobs.enqueue(tenant_id, str(credential_id), kind="manual", trigger="manual")
    return JSONResponse(status_code=202, content={
        "job_id": job["id"], "status": job["status"], "kind": job["kind"],
    })


@router.get("/{credential_id}/sync-jobs", openapi_extra=_JOBS_OK_EXTRA)
async def store_sync_jobs_history(credential_id: str, request: Request, limit: int = 20, offset: int = 0):
    """该店同步任务历史(分页);归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    from services import store_sync_jobs
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)  # 归属校验
    return store_sync_jobs.list_jobs(tenant_id, str(credential_id), limit=limit, offset=offset)


@router.get("/{credential_id}/returns", openapi_extra=_RETURNS_OK_EXTRA)
async def store_returns(credential_id: str, request: Request, limit: int = 50, offset: int = 0):
    """该店退货列表(PG 缓存,ozon_returns_cache);归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    from services import store_sync_jobs  # 保持模块加载(函数级副作用导入)
    from storage.database.db import get_engine
    from sqlalchemy import text
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        total = int(conn.execute(text(
            "SELECT COUNT(*) FROM ozon_returns_cache WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant_id, "c": str(credential_id)}).scalar() or 0)
        rows = conn.execute(text(
            "SELECT return_id, posting_number, order_id, return_type, schema, reason, "
            "compensation_status, status, product, synced_at "
            "FROM ozon_returns_cache WHERE tenant_id=:t AND credential_id=:c "
            "ORDER BY return_id DESC LIMIT :lim OFFSET :off"
        ), {"t": tenant_id, "c": str(credential_id), "lim": limit, "off": offset}).fetchall()
    items = [{
        "return_id": r.return_id,
        "posting_number": r.posting_number,
        "order_id": r.order_id,
        "return_type": r.return_type,
        "schema": r.schema,
        "reason": r.reason,
        "compensation_status": r.compensation_status,
        "status": r.status,
        "product": r.product,
        "synced_at": r.synced_at.isoformat() if r.synced_at else None,
    } for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{credential_id}/analytics-daily", openapi_extra=_ANALYTICS_DAILY_OK_EXTRA)
async def store_analytics_daily(credential_id: str, request: Request, days: int = 30):
    """该店店铺分析日表(访问/加购/转化/广告展示);归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    from storage.database.db import get_engine
    from sqlalchemy import text
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)
    days = max(1, min(int(days), 90))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT stat_date, metric, value FROM ozon_store_analytics_daily "
            "WHERE tenant_id=:t AND credential_id=:c "
            "AND stat_date >= CURRENT_DATE - (:d || ' days')::interval "
            "ORDER BY stat_date, metric"
        ), {"t": tenant_id, "c": str(credential_id), "d": str(days)}).fetchall()
    return {"items": [{
        "stat_date": r.stat_date.isoformat(),
        "metric": r.metric,
        "value": float(r.value),
    } for r in rows]}


@router.get("/warehouses", openapi_extra=_WAREHOUSES_OK_EXTRA)
async def list_warehouses(request: Request):
    """默认店铺的仓库字典(上架选仓下拉);未配置默认店 → 空列表。"""
    from services.credential_service import get_default_credential
    from storage.database.db import get_engine
    from sqlalchemy import text
    tenant_id = await _authenticate(request)
    default = get_default_credential(tenant_id)
    if default is None:
        return {"items": []}
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT warehouse_id, name, is_rfbs FROM warehouse_cache "
            "WHERE tenant_id=:t AND credential_id=:c ORDER BY name"
        ), {"t": tenant_id, "c": str(default["id"])}).fetchall()
    return {"items": [{
        "warehouse_id": r.warehouse_id, "name": r.name, "is_rfbs": bool(r.is_rfbs),
    } for r in rows]}


@detail_router.get("/sync-jobs/{job_id}", openapi_extra=_JOB_DETAIL_OK_EXTRA)
async def sync_job_detail(job_id: int, request: Request):
    """单个同步任务状态/进度(前端轮询目标);跨租户 → 404。"""
    from services import store_sync_jobs
    tenant_id = await _authenticate(request)
    job = store_sync_jobs.get_job(tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    return job


@detail_router.get("/admin/sync-health", openapi_extra=_HEALTH_OK_EXTRA)
async def admin_sync_health(request: Request):
    """全部 active 店同步健康总览(仅 admin)。"""
    from services.admin_service import require_admin
    from main import _authenticate_token
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    require_admin(user_id)
    return store_sync_service.sync_health()


@router.get("/{credential_id}/daily-metrics", openapi_extra=_DAILY_METRICS_OK_EXTRA)
async def store_daily_metrics(credential_id: str, request: Request, days: int = 30):
    """该店日聚合指标(趋势图数据源);归属校验失败 → 404。"""
    from services.credential_service import get_decrypted
    from storage.database.db import get_engine
    from sqlalchemy import text
    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)
    days = max(1, min(int(days), 90))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT stat_date, order_count, sales_amount, commission_amount, profit_amount, "
            "product_count, low_stock_count, active_discount_count, profit_rate "
            "FROM store_daily_metrics WHERE tenant_id=:t AND credential_id=:c "
            "AND stat_date >= CURRENT_DATE - (:d || ' days')::interval "
            "ORDER BY stat_date"
        ), {"t": tenant_id, "c": str(credential_id), "d": str(days)}).fetchall()
    return {"items": [{
        "stat_date": r.stat_date.isoformat(),
        "order_count": int(r.order_count or 0),
        "sales_amount": float(r.sales_amount) if r.sales_amount is not None else None,
        "commission_amount": float(r.commission_amount) if r.commission_amount is not None else None,
        "profit_amount": float(r.profit_amount) if r.profit_amount is not None else None,
        "product_count": int(r.product_count or 0),
        "low_stock_count": int(r.low_stock_count or 0),
        "active_discount_count": int(r.active_discount_count or 0),
        "profit_rate": float(r.profit_rate) if r.profit_rate is not None else None,
    } for r in rows]}


@router.get("/{credential_id}/sync-status", openapi_extra=_SYNC_STATUS_OK_EXTRA)
async def sync_status(credential_id: str, request: Request):
    """同步状态：最后同步时间 + 错误（webui 展示「上次同步 xx」）。"""
    from services.credential_service import get_decrypted

    tenant_id = await _authenticate(request)
    get_decrypted(tenant_id, credential_id)  # 归属校验（跨租户 404）
    return store_sync_service.get_sync_status(tenant_id, credential_id)


@router.get("/{credential_id}/stats", openapi_extra=_STATS_OK_EXTRA)
async def store_stats(credential_id: str, request: Request):
    """店铺卡统计（T4.6）：今日订单数/销售额/佣金/利润/件数（ozon_orders_cache 聚合）。

    归属校验失败 → 404（跨租户不可见）；无评分字段——缓存无 rating 数据，卡片不显示评分。
    """
    tenant_id = await _authenticate(request)
    return store_sync_service.get_store_stats(tenant_id, credential_id)


@router.get("/{credential_id}/analysis", openapi_extra=_ANALYSIS_OK_EXTRA)
async def store_analysis(credential_id: str, request: Request):
    """店铺分析（todo 6）：利润率/库存/候选清单（summary + profit_trend + 三组清单）。

    有成本商品（product_task_index→payload.envelope）经唯一入口算精确利润；
    无成本商品只给「当前价 + 库存」，不填 profit_rate（不给无成本商品编造利润）。
    归属校验失败 → 404（跨租户不可见）。
    """
    tenant_id = await _authenticate(request)
    return store_analysis_service.analyze_store(tenant_id, str(credential_id))

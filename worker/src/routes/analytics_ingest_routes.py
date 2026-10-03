"""R3a: analytics 上报/读取 + discover session 路由族（自 main.py 迁出）。

端点（router 由 main ``v1.include_router`` 注册，成品路径 /api/v1/...）：
    POST /analytics/queries                what-to-sell 蓝海关键词上报
    POST /analytics/ozon-bestsellers       Ozon 榜单上报
    POST /analytics/market-bestsellers     全平台榜单上报
    POST /discovery/runs                   discover 选品结果归档（canonical session 幂等）
    GET  /analytics/bestsellers            榜单浏览（全局共享）
    GET  /discovery/runs                   归档历史（全局共享 + 贡献者指纹）
    GET  /discovery/runs/{session_run_id}  canonical session 全文（跨租户 404）

依赖纪律（R3a）：鉴权/限流走权威模块（api.security / runtime.rate_limit），
tenant 解析走 api.deps_tenant，DB 走 storage 源头；禁止 ``from main import``。
⚠️ 本 router 不带 prefix（由 main 的 v1 提供 /api/v1），勿再加 /analytics 前缀，
否则出现 /api/v1/analytics/analytics/... 双前缀。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.errors import WorkerErrorCode, error_response
from api.schemas import (
    AnalyticsReportResponse,
    BlueOceanQueryItem,
    DiscoveryRunDetail,
    DiscoveryRunItem,
    MarketBestsellerItem,
    OzonBestsellerItem,
)
from api.security import _verify_analytics_token
from runtime.rate_limit import RATE_LIMIT_PER_MINUTE, rate_limiter
from storage.database.db import get_engine
from storage.database.shared.model import (
    BlueOceanQuery,
    DiscoveryRun,
    MarketBestseller,
    OzonBestseller,
)
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ==================== /api/v1/analytics/* — skill 选品数据上报（v0.34 C5） ====================
# skill what-to-sell 采集的蓝海/榜单数据集体沉淀到 worker PG（数据来源于用户、服务于用户）。
# 每类数据: (表名, 去重冲突列, 可更新数据列, 条目 Pydantic 模型, body 列表字段名)

# discovery_runs 上报请求体上限（v0.83 批⑤ canonical session_json 自包含；超限 413）。
# 对照 pounding-mcp tasks_server 的 1MB 先例，canonical 文档含候选证据故放宽到 4MB。
_DISCOVERY_REPORT_MAX_BYTES = 4 * 1024 * 1024

_ANALYTICS_KINDS = {
    "queries": (
        BlueOceanQuery,
        ("query", "contributed_by_token_id"),
        # token_fp 进 data_cols：冲突行（重复上报）也刷新指纹——存量行未回填/算法
        # 迁移场景下，用户重复采集一次即自愈（A8 F6）。
        ("query", "count", "ca", "avg_ca_rub", "avg_count_items",
         "items_views", "uniq_queries_wca", "uniq_sellers", "token_fp"),
        BlueOceanQueryItem,
        "queries",
    ),
    "ozon-bestsellers": (
        OzonBestseller,
        ("sku_or_id", "contributed_by_token_id"),
        ("sku_or_id", "brand", "category_id", "category_path",
         "ordering_amount", "ordering_count", "avg_price_rub", "token_fp"),
        OzonBestsellerItem,
        "items",
    ),
    "market-bestsellers": (
        MarketBestseller,
        ("product_name", "contributed_by_token_id"),
        ("product_name", "brand", "category_id", "category_path",
         "ordering_amount", "daily_avg", "other_platform_price", "token_fp"),
        MarketBestsellerItem,
        "items",
    ),
    # discovery_runs 是单条 run 归档（非批量 upsert，无自然冲突键），
    # _handle_analytics_report 入口特判转 _handle_discovery_run_report（见下）。
    "discovery_runs": (
        DiscoveryRun,
        ("keyword", "contributed_by_token_id"),
        ("filters_json", "candidates_json"),
        DiscoveryRunItem,
        "runs",
    ),
}


def _upsert_analytics(
    model,
    conflict_cols: tuple[str, ...],
    data_cols: tuple[str, ...],
    rows: list[dict],
) -> tuple[int, int]:
    """两趟 upsert（单语句多行 VALUES，每趟一次往返）：
    第一趟 ON CONFLICT DO NOTHING → rowcount = 真实新增行数；
    第二趟全量 ON CONFLICT DO UPDATE → rowcount = 批内全部行数（第一趟新增的行此时也冲突）；
    upserted = 第二趟 - 第一趟 = 本次被覆盖更新的行数。
    """
    if not rows:
        return 0, 0
    stmt = pg_insert(model).values(rows)
    stmt_nothing = stmt.on_conflict_do_nothing(index_elements=list(conflict_cols))
    stmt_update = stmt.on_conflict_do_update(
        index_elements=list(conflict_cols),
        set_={c: stmt.excluded[c] for c in data_cols if c not in conflict_cols},
    )
    engine = get_engine()
    with engine.connect() as conn:
        r1 = conn.execute(stmt_nothing)
        inserted = int(r1.rowcount or 0)
        conn.commit()
    with engine.connect() as conn:
        r2 = conn.execute(stmt_update)
        total = int(r2.rowcount or 0)
        conn.commit()
    return inserted, max(total - inserted, 0)


async def _handle_analytics_report(request: Request, kind: str):
    """analytics 上报公共处理：token 鉴权 → Pydantic 校验 → upsert → 计数响应。"""
    if kind == "discovery_runs":
        return await _handle_discovery_run_report(request)
    from services.tenant_service import token_fingerprint
    model, conflict_cols, data_cols, item_model, list_key = _ANALYTICS_KINDS[kind]

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_request: body must be JSON")

    token = str(body.get("token", "") or "")
    if not token:
        raise HTTPException(status_code=401, detail="token is required")

    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean_token)

    # v0.34 security: 按 token 限流（防单 token 批量打爆共享 PG；与 submit_task 同 RateLimiter）
    allowed, remaining = rate_limiter.check(clean_token)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute")

    raw_items = body.get(list_key)
    if not isinstance(raw_items, list) or not raw_items:
        return error_response(
            WorkerErrorCode.INVALID_REQUEST,
            f"{list_key} must be a non-empty list",
        )
    # v0.34 security: 单次上报条数上限（防超大 JSON → 内存/DB 压力）
    if len(raw_items) > 2000:
        return error_response(
            WorkerErrorCode.INVALID_REQUEST,
            f"{list_key} too large: max 2000 items per request",
        )

    parsed = []
    for it in raw_items:
        if not isinstance(it, dict):
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                f"{list_key} items must be JSON objects",
            )
        try:
            parsed.append(item_model(**it))
        except Exception:
            # v0.34 security: 不把 Pydantic 校验异常原文回显给客户端（可能泄露字段/结构细节）
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                f"invalid {list_key} item: check required fields",
            )

    rows = []
    for p in parsed:
        row = p.model_dump(exclude_none=True)
        row["contributed_by_token_id"] = clean_token
        # A8 F6/BL-06：MXOU key 明文落库脱敏双写——指纹唯一算法入口
        # services.tenant_service.token_fingerprint（sha256 前 16）。
        row["token_fp"] = token_fingerprint(clean_token)
        row["source"] = "fetched"
        rows.append(row)

    try:
        inserted, upserted = _upsert_analytics(model, conflict_cols, data_cols, rows)
    except Exception as e:
        logger.error("analytics upsert failed (kind=%s): %s", kind, e)
        return error_response(
            WorkerErrorCode.INTERNAL_ERROR,
            "analytics upsert failed",
        )

    return {"status": "ok", "inserted": inserted, "upserted": upserted}


async def _handle_discovery_run_report(request: Request):
    """discover 选品结果归档（W10 D12）。

    v0.83 批⑤ canonical：请求体可带 ``session_run_id``/``schema_version``/
    ``session_json`` —— 存在 session_run_id 时按它幂等 upsert（重报同 run 不炸、
    只覆盖）；缺省保持旧行为（裸 insert，兼容旧 skill）。请求体上限 4MB（超限 413）。

    鉴权/限流复用 analytics 模式；candidates 白名单裁剪在 skill 端，worker 原样存 JSONB。
    tenant_id = clean token（POST 归属写入；GET 全局共享——见 v1_discovery_list_runs）。
    A8 F6：tenant_id 是 MXOU key 明文（grep 判定见 model.py DiscoveryRun 注释），
    同行双写 token_fp 指纹（唯一入口 tenant_service.token_fingerprint）。
    """
    from services.tenant_service import token_fingerprint

    # v0.83 批⑤：请求体上限 4MB（canonical session_json 自包含，cap 后仍应远小于此；
    # 超限直接 413 防超大 JSON 打爆内存/DB）。对照 pounding-mcp tasks_server 1MB 先例。
    # getattr 防御：既有回归测试夹具 FakeRequest 无 headers 属性。
    _headers = getattr(request, "headers", None)
    try:
        _clen = int((_headers.get("content-length") if _headers else 0) or 0)
    except (TypeError, ValueError):
        _clen = 0
    if _clen > _DISCOVERY_REPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail=(
            f"request_too_large: max {_DISCOVERY_REPORT_MAX_BYTES} bytes"))

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_request: body must be JSON")

    token = str(body.get("token", "") or "")
    if not token:
        raise HTTPException(status_code=401, detail="token is required")

    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean_token)

    allowed, _remaining = rate_limiter.check(clean_token)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute")

    try:
        item = DiscoveryRunItem(**body)
    except Exception:
        return error_response(
            WorkerErrorCode.INVALID_REQUEST,
            "invalid discovery run: check required fields",
        )

    row = {
        "tenant_id": clean_token,
        "token_fp": token_fingerprint(clean_token),
        "keyword": item.keyword,
        "filters_json": item.filters,
        "candidates_json": item.candidates,
    }
    session_run_id = (item.session_run_id or "").strip()
    if session_run_id:
        row["session_run_id"] = session_run_id
        row["schema_version"] = item.schema_version
        row["session_json"] = item.session_json
    try:
        engine = get_engine()
        with engine.connect() as conn:
            if session_run_id:
                # canonical 幂等 upsert：重报同 session_run_id 只覆盖不新增
                stmt = pg_insert(DiscoveryRun).values([row])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["session_run_id"],
                    set_={
                        "tenant_id": stmt.excluded.tenant_id,
                        "token_fp": stmt.excluded.token_fp,
                        "keyword": stmt.excluded.keyword,
                        "filters_json": stmt.excluded.filters_json,
                        "candidates_json": stmt.excluded.candidates_json,
                        "schema_version": stmt.excluded.schema_version,
                        "session_json": stmt.excluded.session_json,
                    },
                )
                r = conn.execute(stmt)
                inserted = 0
                upserted = int(r.rowcount or 0)
            else:
                r = conn.execute(pg_insert(DiscoveryRun).values([row]))
                inserted = int(r.rowcount or 0)
                upserted = 0
            conn.commit()
    except Exception as e:
        logger.error("discovery run insert failed: %s", e)
        return error_response(
            WorkerErrorCode.INTERNAL_ERROR,
            "discovery run insert failed",
        )

    try:
        from services.selection_insight_service import upsert_from_discovery_run
        upsert_from_discovery_run(clean_token, item.keyword, item.candidates)
    except Exception as e:
        logger.warning("selection insight aggregation skipped (keyword=%s): %s", item.keyword, e)

    try:
        from services.source_candidate_service import derive_from_discovery_run
        derive_from_discovery_run(clean_token, item.candidates)
    except Exception as e:
        logger.warning("source_candidates derivation skipped (keyword=%s): %s", item.keyword, e)

    return {"status": "ok", "inserted": inserted, "upserted": upserted}


@router.post("/analytics/queries", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_analytics_queries(request: Request):
    """skill what-to-sell all-queries 关键词蓝海数据上报（去重键 query+token，重复上报 upsert 更新）。"""
    return await _handle_analytics_report(request, "queries")


@router.post("/analytics/ozon-bestsellers", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_analytics_ozon_bestsellers(request: Request):
    """skill ozon-bestsellers 榜单数据上报（去重键 sku_or_id+token）。"""
    return await _handle_analytics_report(request, "ozon-bestsellers")


@router.post("/analytics/market-bestsellers", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_analytics_market_bestsellers(request: Request):
    """skill market-bestsellers 全平台榜单数据上报（去重键 product_name+token）。"""
    return await _handle_analytics_report(request, "market-bestsellers")


@router.post("/discovery/runs", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_discovery_report_run(request: Request):
    """discover 选品结果归档（W10 D12）：单次上报一条 run（keyword+filters+candidates），按 tenant 隔离。"""
    return await _handle_analytics_report(request, "discovery_runs")


@router.get("/analytics/bestsellers", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "items": [{
            "sku_or_id": "1680357214",
            "brand": "Thermos",
            "category_path": "Дом и сад / Термосы",
            "ordering_amount": 1284500.0,
            "ordering_count": 412,
            "avg_price_rub": 3117.7,
            "contributed_by_fp": "a1b2c3d4",
        }],
        "total": 1, "limit": 50, "offset": 0,
    }}}},
})
async def v1_analytics_list_bestsellers(request: Request):
    """T4b.1 榜单浏览：读 skill 上报的 ozon-bestsellers（全局共享，含贡献者列）。

    query: category?（类目筛选）/ order_by?（ordering_amount|ordering_count|avg_price_rub）/ limit/offset
    鉴权与上报一致：token 即身份；token 不再作数据过滤（A 采集 B 可见）。
    """
    from services.analytics_service import list_bestsellers
    # B 租户 guard 二期：Bearer 提取→verify（无限流，对齐原内联序列）收敛单行；
    # 无租户消费 → verify_bearer（不加 resolve_tenant，行为逐字等价）。
    from api.deps_tenant import verify_bearer_from_request
    clean_token = verify_bearer_from_request(request, rate_limited=False)

    q = request.query_params
    try:
        limit = int(q.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(q.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    def _opt_float(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return list_bestsellers(
        clean_token,
        category=q.get("category"),
        order_by=q.get("order_by") or "ordering_amount",
        limit=limit, offset=offset,
        brand=q.get("brand", "").strip(),
        min_sales=_opt_float(q.get("min_sales")),
        max_sales=_opt_float(q.get("max_sales")),
        min_price=_opt_float(q.get("min_price")),
        max_price=_opt_float(q.get("max_price")),
    )


def _fetch_discovery_runs_rows(*, limit: int, offset: int):
    """discovery/runs 行查询封装（v0.76 T1 抽出以便测试 monkeypatch 钉住脱敏行为）。

    SQL 主体从原 v1_discovery_list_runs 内联处平移，零语义变更。返回 (rows, total)。
    SELECT 保留 tenant_id 明文列（r[5]）供 Python 内算指纹用——明文绝不进响应 dict
    （v0.76 api-C1：读侧只发 contributed_by_fp）。v0.83 批⑤追加 session_run_id（r[6]）。
    """
    from sqlalchemy import text
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, keyword, filters_json, candidates_json, created_at, tenant_id, "
            "session_run_id "
            "FROM discovery_runs "
            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        ), {"limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM discovery_runs"
        )).scalar()
    return rows, int(total or 0)


@router.get("/discovery/runs", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "items": [{
            "id": "0192b1f0-9c3f-7f2e-8b1a-3d4e5f6a7b8c",
            "keyword": "宠物饮水机",
            "filters": {"min_margin": 0.25},
            "candidates": 23,
            "created_at": "2026-09-11T10:24:31",
            "contributed_by_fp": "a1b2c3d4",
        }],
        "total": 1, "limit": 50, "offset": 0,
    }}}},
})
async def v1_discovery_list_runs(request: Request):
    """discover 选品结果历史读取（W4b.2）：全局共享（A 可见 B 的归档，含贡献者标注）。

    query: limit/offset（分页，limit 上限 200）；鉴权与上报一致：token 即身份。
    蓝海（/admin/queries admin-only）与榜单（market_bestsellers 无读端点）保持关闭。
    """
    # B 租户 guard 二期：Bearer 提取→verify（无限流，对齐原内联序列）收敛单行；
    # 全局共享无租户过滤 → verify_bearer（不加 resolve_tenant，行为逐字等价）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=False)

    q = request.query_params
    try:
        limit = int(q.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(q.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    rows, total = _fetch_discovery_runs_rows(limit=limit, offset=offset)

    from services.tenant_service import token_fingerprint
    items = [{
        "id": str(r[0]),
        "keyword": str(r[1]),
        "filters": r[2],
        "candidates": r[3],
        "created_at": r[4].isoformat() if r[4] is not None else None,
        # A8 F6 展示脱敏：贡献者只回指纹前 8 位。
        # v0.76 安全修复(api-C1): "contributed_by_token_id" 明文键已删除——读侧只发 fp
        #（读时从 tenant_id 现算与写侧 token_fingerprint 同源等值；DB 明文列保留，defer 退役）。
        "contributed_by_fp": token_fingerprint(str(r[5] or ""))[:8],
        # v0.83 批⑤：canonical session id（老行为 None）
        "session_run_id": (str(r[6]) if len(r) > 6 and r[6] is not None else None),
    } for r in rows]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@router.get("/discovery/runs/{session_run_id}", tags=["analytics"],
        response_model=DiscoveryRunDetail, responses={
    200: {"content": {"application/json": {"example": {
        "session_run_id": "disc_260928_101530_a1b2c3",
        "schema_version": "discover.session.v1",
        "keyword": "宠物饮水机",
        "created_at": "2026-09-28T10:15:30",
        "legacy": False,
        "session_json": {
            "schema_version": "discover.session.v1",
            "session_run_id": "disc_260928_101530_a1b2c3",
            "summary": {"total": 23, "profitable": 5},
            "candidates": [],
        },
    }}}},
    404: {"description": "session 不存在或非本租户（跨租户 404，不泄漏存在性）"},
})
async def v1_discovery_get_run(session_run_id: str, request: Request):
    """读取 canonical discover session（v0.83 批⑤）：session_json 全文 + meta。

    鉴权 Bearer（``resolve_tenant_from_request``：Bearer→verify→限流→租户）+
    跨租户 404（run_id 带 disc_ 前缀 + 随机段不可枚举，见 skill
    discovery_session.new_run_id）。归属判定用写侧同源指纹（token_fp）——
    discovery_runs.tenant_id 存的是 clean token 明文，与 resolve_tenant 的
    user_id 不同域，故用 ``token_fingerprint`` 等值比较（写入侧唯一算法入口）。
    老行（无 session_json）→ ``legacy: true``，``session_json`` null，
    ``candidates`` 回退 candidates_json。
    """
    from api.deps_tenant import _extract_bearer, resolve_tenant_from_request
    from sqlalchemy import text

    rid = (session_run_id or "").strip()
    # 形态校验（与 skill new_run_id 同口径；防路径注入/无意义查询）
    if not _is_valid_session_run_id(rid):
        raise HTTPException(status_code=404, detail="session not found")

    # 四段鉴权（Bearer→verify→限流→resolve_tenant；401/429/503 与全网收口同源）
    resolve_tenant_from_request(request)
    _raw_token, clean_token = _extract_bearer(request)

    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT session_run_id, schema_version, keyword, session_json, "
            "candidates_json, created_at, token_fp "
            "FROM discovery_runs WHERE session_run_id = :rid LIMIT 1"
        ), {"rid": rid}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")

    # 跨租户 404：非本人不暴露存在性。写侧同行双写 token_fp（唯一算法入口
    # tenant_service.token_fingerprint）；老行 token_fp 为空时不拦（兼容）。
    from services.tenant_service import token_fingerprint
    row_fp = str(row[6] or "")
    if row_fp and row_fp != token_fingerprint(clean_token):
        raise HTTPException(status_code=404, detail="session not found")

    return {
        "session_run_id": str(row[0] or ""),
        "schema_version": row[1],
        "keyword": str(row[2] or ""),
        "created_at": row[5].isoformat() if row[5] is not None else None,
        "legacy": row[3] is None,
        "session_json": row[3],
        "candidates": list(row[4] or []) if row[3] is None else [],
    }


def _is_valid_session_run_id(run_id: str) -> bool:
    """``disc_<6d>_<6d>_<6hex>`` 形态校验（与 skill discovery_session.is_valid_run_id 同口径）。"""
    rid = (run_id or "").strip()
    if not rid.startswith("disc_"):
        return False
    parts = rid.split("_")
    if len(parts) != 4:
        return False
    _, d, t, hx = parts
    if len(d) != 6 or len(t) != 6 or not (d + t).isdigit():
        return False
    return len(hx) == 6 and all(c in "0123456789abcdef" for c in hx)

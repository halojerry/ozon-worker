"""R3a: ops 路由族（自 main.py 迁出）——健康检查 / 鉴权校验 / 进度 / 图元数据 / 物流报价。

两个 router：
- ``root_router``（绝对路径，由 main ``app.include_router`` 注册）：
    GET  /health                    compose healthcheck 依赖（200/503 语义零变化）
    GET  /api/v1/store/health       Ozon 店铺配额健康（Bearer 必填）
    POST /auth/verify               + POST /api/v1/auth/verify
    GET  /progress/{run_id}         进度查询（async 图经 runtime.graph_service holder 取）
    GET  /graph_parameter           GraphInput/GraphOutput JSON Schema
    POST /api/v1/logistics/quote    物流运费报价
- ``router``（v1-relative，由 main ``v1.include_router`` 注册）：
    GET  /api/v1/health             含 PG 连通性（自带 DB 探测）

依赖纪律（R3a）：鉴权/限流走权威模块（api.security / runtime.rate_limit），
async 图走 runtime.graph_service.get_async_graph()（lifespan 注入）；
禁止 ``from main import``。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from api.schemas import AuthVerifyResponse, ErrorBody, HealthResponse
from api.security import _auth_verify_sync, _require_bearer
from runtime.graph_service import get_async_graph, service
from runtime.progress import STAGE_ORDER, get_progress
from runtime.rate_limit import rate_limiter
from storage.database.supabase_client import get_supabase_client
from storage.memory.memory_saver import get_memory_saver
from utils.logger import get_logger
from utils.ozon_client import ozon_check_quota

logger = get_logger(__name__)

router = APIRouter()
root_router = APIRouter()


@root_router.get("/health", responses={
    200: {"content": {"application/json": {"example": {
        "status": "ok",
        "message": "Service is running",
        "db": "connected",
        "queue": {"pending": 2, "running": 5, "completed": 120},
        "last_backup_at": 1725996400.0,
        "backup_stale": False,
    }}}}, 503: {"content": {"application/json": {"example": {
        "status": "degraded", "message": "db_error: OperationalError", "db": "disconnected",
    }}}}})
async def health_check():
    try:
        from sqlalchemy import text
        from storage.database.db import get_engine
        _engine = get_engine()
        queue_stats = {}
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            # 获取队列统计
            rows = conn.execute(text(
                "SELECT status, COUNT(*) as cnt FROM ozon_product_tasks GROUP BY status"
            )).fetchall()
            queue_stats = {row[0]: row[1] for row in rows}

        # BL-08 (repo-gov): 备份心跳透出 —— last_backup_at(epoch 秒 | None) 来自
        # services/backup_heartbeat_service（lazy import + 全吞错：服务未部署 /
        # PG 无该表时为 None）。backup_stale 只对「有过心跳但距今 >26h」置 True；
        # 「表存在但从未备份」时 last_backup_at 恒为 None、不置 stale —— 交给运维
        # 判断（埋点刚上线时天然为 None，误报 stale 只会训练人忽略告警）。
        # 以下任何异常都不得破坏 /health 的 200 语义（compose healthcheck 依赖它）。
        last_backup_at: Optional[float] = None
        backup_stale = False
        try:
            from services.backup_heartbeat_service import last_backup_at as _lba
            _hb = _lba()
            if _hb is not None:
                last_backup_at = float(_hb)
                backup_stale = (time.time() - last_backup_at) > 26 * 3600
        except Exception:
            last_backup_at = None
            backup_stale = False

        return {
            "status": "ok",
            "message": "Service is running",
            "db": "connected",
            "queue": queue_stats,
            "last_backup_at": last_backup_at,
            "backup_stale": backup_stale,
        }
    except Exception as e:
        # v0.63.1 D8: /health 公开无鉴权且被 compose healthcheck 使用——异常
        # 只回错误类型不回 str(e)（DB 错误详情对公网扫描者不可见）
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "message": f"db_error: {type(e).__name__}", "db": "disconnected"},
        )


@root_router.get("/api/v1/store/health", responses={
    200: {"content": {"application/json": {"example": {
        # T9(api-M3) 起 status ∈ ok/warning/critical/unknown（缺凭证=unknown）；
        # 上游失败一律 502 固定文案，200 体不再有 error 形态
        "status": "ok",
        "total_usage": 9900,
        "total_limit": 10000,
        "remaining": 100,
        "daily_usage": 40,
        "daily_limit": 200,
        "daily_remaining": 160,
    }}}},
    401: {"model": ErrorBody},
    502: {"content": {"application/json": {"example": {
        # T9(api-M3): 上游失败只回固定文案，Ozon 原文只进日志
        "detail": "upstream store health check failed",
    }}}}})
def store_health(request: Request, client_id: str = None, api_key: str = None):
    """查询 Ozon 店铺配额健康状态。

    T9(api-M3): Bearer 必填（``_require_bearer``，无 Bearer 401 "Token is
    required"）——此前完全无鉴权，匿名可拿任意店铺凭证探测 Ozon 店铺配额/
    存在性。凭证取值：优先 ``X-Ozon-Client-Id`` / ``X-Ozon-Api-Key`` header，
    缺省回落 query（**query 传凭证已弃用**——query 会进反代/访问日志留痕面，
    仅为存量调用方向后兼容保留）。上游失败（意外异常或 Ozon error）→ 502
    固定文案，原文只进 logger——此前 ``message: str(e)`` / Ozon error 原文
    直接进 200 响应体，上游内部细节泄漏给客户端。

    凭证（header 或 query）:
    - client_id: Ozon Client-Id
    - api_key: Ozon Api-Key

    v0.63.1 架构优化 R2: sync def — 内部阻塞调用，FastAPI 自动丢线程池，
    不冻结事件循环（async def 下单个慢调用会停摆全部 worker 心跳）。
    F-F01（2026-09-09 审计）：收敛 ozon_check_quota——与 submit 配额闸同源
    解析/限流/重试，移除裸 requests.post 直连。
    """
    _require_bearer(request)
    client_id = request.headers.get("X-Ozon-Client-Id") or client_id or ""
    api_key = request.headers.get("X-Ozon-Api-Key") or api_key or ""
    if not client_id or not api_key:
        return {"status": "unknown", "message": "需要提供 client_id 和 api_key"}
    try:
        quota = ozon_check_quota(client_id=client_id, api_key=api_key, timeout=10)
        if quota.get("error"):
            # T9(api-M3): Ozon 错误原文不回显（此前 f"Ozon API error: {quota['error']}"
            # 直接进响应体）——固定文案 + 原文截断进日志
            logger.warning("store/health upstream fail: %s",
                           str(quota["error"])[:120])
            raise HTTPException(status_code=502,
                                detail="upstream store health check failed")
        total_used = int(quota.get("total_used", 0) or 0)
        total_limit = int(quota.get("total_limit", 0) or 0)
        daily_used = int(quota.get("daily_used", 0) or 0)
        daily_limit = int(quota.get("daily_limit", 0) or 0)
        remaining = total_limit - total_used
        daily_remaining = daily_limit - daily_used

        if remaining <= 0: status = "critical"
        elif remaining < 10: status = "warning"
        else: status = "ok"

        return {
            "status": status,
            "total_usage": total_used, "total_limit": total_limit, "remaining": remaining,
            "daily_usage": daily_used, "daily_limit": daily_limit, "daily_remaining": daily_remaining,
        }
    except HTTPException:
        raise  # 401/502 原样透传，不被兜底吞掉
    except Exception as e:
        # T9(api-M3): 异常文本绝不进响应（此前 200 体 {"status":"error",
        # "message": str(e)} 会把连接串等内部细节泄漏给客户端）
        logger.warning("store/health upstream fail: %s", str(e)[:120])
        raise HTTPException(status_code=502, detail="upstream store health check failed")


@root_router.post("/auth/verify", response_model=AuthVerifyResponse)
@root_router.post("/api/v1/auth/verify", response_model=AuthVerifyResponse)
async def auth_verify(request: Request):
    """
    Skill 鉴权端点。

    验证（与 submit_task 相同逻辑）:
    1. token 有效性（Supabase tokens 表 key 列，剥离 sk- 前缀）
    2. token 状态 = 1（active；status=4 欠费 → balance_insufficient）
    3. 余额检查：users.quota - used_quota（unlimited_quota=true 放行；
       不再用 remain_quota——它是僵尸字段且无限额度 key 会被误判）
    4. Ozon API 有效性（可选）

    不返回余额数字，只返回 valid + reason。

    v0.63.1 架构优化 R2: 阻塞逻辑在 _auth_verify_sync（to_thread）——
    Supabase/Ozon HTTP 最长 10s，async 内直接执行会冻结事件循环。
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"valid": False, "reason": "invalid_request", "expires_in": 0})

    token = body.get("token", "")
    client_id = body.get("client_id", "")
    api_key = body.get("api_key", "")
    return await asyncio.to_thread(_auth_verify_sync, token, client_id, api_key)


@root_router.get("/progress/{run_id}", responses={
    200: {"content": {"application/json": {"example": {
        # source=checkpointer（实时）或 memory_or_pg（任务完成后/重启后回退）
        "run_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "source": "checkpointer",
        "progress_counter": 5,
        "total_nodes": 13,
        "percentage": 38,
        "stages": {"auth": "done", "ingest": "done", "category_match": "done"},
    }}}}, 404: {"content": {"application/json": {"example": {
        "detail": "No progress found for run_id=3fa85f64-5717-4562-b3fc-2c963f66afa6",
    }}}}, 401: {"model": ErrorBody}})
async def http_progress(run_id: str, request: Request):
    """查询工作流执行进度。

    v0.76 T8(api-M2): Bearer 鉴权（``_require_bearer``）——此前完全无鉴权，
    匿名可探测 run_id 存在性与执行进度（13 阶段逐节点）。无独立应急开关，
    语义见 helper docstring。

    优先从 LangGraph checkpointer 读取实时 state，
    降级到内存 _task_progress → PG progress 列（任务完成后/重启后可用）。
    """
    _ = _require_bearer(request)
    # 1. 尝试 LangGraph checkpointer（实时 running state）
    async_graph = get_async_graph()
    if async_graph is not None:
        checkpointer = get_memory_saver()
        if checkpointer is not None:
            config = {"configurable": {"thread_id": run_id}}
            try:
                state = await async_graph.aget_state(config)
                if state is not None and state.values:
                    values = state.values
                    counter = values.get("progress_counter", 0)
                    stages = values.get("stages", {})
                    total = len(STAGE_ORDER)
                    return {
                        "run_id": run_id,
                        "source": "checkpointer",
                        "progress_counter": counter,
                        "total_nodes": total,
                        "percentage": int((counter / total) * 100) if total > 0 else 0,
                        "stages": stages,
                    }
            except Exception:
                pass  # 降级到内存/PG

    # 2. 降级：内存 _task_progress → PG progress 列
    progress = get_progress(run_id)
    if progress:
        return {
            "run_id": run_id,
            "source": "memory_or_pg",
            **progress,
        }

    raise HTTPException(status_code=404, detail=f"No progress found for run_id={run_id}")


@root_router.get(path="/graph_parameter", responses={
    200: {"content": {"application/json": {"example": {
        # GraphInput/GraphOutput 的 JSON Schema（service.graph_inout_schema，此处节选）
        "input_schema": {"title": "GraphInput", "type": "object", "properties": {}},
        "output_schema": {"title": "GraphOutput", "type": "object", "properties": {}},
        "code": 0,
        "msg": "",
    }}}}})
async def http_graph_inout_parameter(request: Request):
    # v0.81 安全收尾（Mimosa medium 判定「真缺」已修）：消费矩阵标「需鉴权」
    # （webui API-INTEGRATION-GUIDE §任务·运行 🔒 GET /graph_parameter），实现
    # 漏挂——补 ``_require_bearer``（v0.76 后鉴权唯一入口）。纯 GraphInput/
    # GraphOutput schema 元数据只读、无租户数据，但对外按矩阵收口：匿名 401
    # "Token is required"。
    _require_bearer(request)
    return service.graph_inout_schema()


@root_router.post("/api/v1/logistics/quote", responses={
    200: {"content": {"application/json": {"example": {
        # quote_logistics 明细（utils/logistics_quote.py）：RETS/Standard 费率表命中
        "tpl_provider": "RETS",
        "service_level": "Standard",
        "scoring_group": "A",
        "base_cost": 6.0,
        "per_gram_rate": 0.004,
        "billable_weight": 500.0,
        "weight": 480.0,
        "dims_cm": [20.0, 15.0, 10.0],
        "fallback_chain": [],
        "logistics_cost_cny": 8.0,
        "channel": "RETS_Standard_A",
    }}}},  # 200 示例收口（example/json/content/200 四层）
    401: {"model": ErrorBody}, 429: {"model": ErrorBody}})
async def logistics_quote(request: Request):
    """物流运费报价端点（v0.29.x, skill 选品利润估算用）。

    入参: {token?, weight_g, depth_cm, width_cm, height_cm,
           tpl_provider?, service_level?, ozon_client_id?, ozon_api_key?}
    - 未传 tpl_provider/service_level 时, 若有 ozon 凭证自动探测 3PL;
      否则默认 RETS/Standard。
    - T10(api-M4): Authorization Bearer **必填**（``_require_bearer``）——
      此前 token 走 body 可选字段，缺省直接跳过鉴权（匿名可拉费率表、可打满
      带 Ozon 凭证的 3PL 探测），且无 rate limit；现 ``logistics:{clean_token}``
      独立限流键（不与提交限流额度互挤），超限 429。
    - body token 保留向后兼容（有值仍校验，语义同 auth_verify）。

    返回: {logistics_cost_cny, channel, tpl_provider_used, service_level_used,
           base_cost, per_gram_rate, billable_weight, weight, dims_cm, fallback_chain}

    v0.63.1 架构优化 R2: 阻塞 Supabase/Ozon 逻辑在 _logistics_quote_sync（to_thread）。
    """
    clean_token = _require_bearer(request)  # T10(api-M4): 匿名拉费率面收口
    allowed, _ = rate_limiter.check(f"logistics:{clean_token}")
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limited")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_request: body must be JSON")
    return await asyncio.to_thread(_logistics_quote_sync, body)


def _logistics_quote_sync(body: dict) -> dict:
    """logistics_quote 同步实现（纯阻塞 IO，供 asyncio.to_thread 调用）。"""
    token = str(body.get("token", "") or "")
    client_id = str(body.get("ozon_client_id", "") or "")
    api_key = str(body.get("ozon_api_key", "") or "")

    # 鉴权(与 auth_verify 相同: Supabase 未配置 → 本地放行)
    if token:
        clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
        supabase = get_supabase_client()
        if supabase is not None:
            try:
                token_records = supabase.table("tokens").select(
                    "status"
                ).eq("key", clean_token).is_("deleted_at", "null").execute()
                if not token_records.data or int(token_records.data[0].get("status", 0)) != 1:
                    raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=503, detail="service_unavailable")

    try:
        weight = float(body.get("weight_g", 0) or 0)
        depth = float(body.get("depth_cm", 0) or 0)
        width = float(body.get("width_cm", 0) or 0)
        height = float(body.get("height_cm", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="weight/dimensions must be numeric")

    if weight <= 0:
        raise HTTPException(status_code=400, detail="weight_g must be > 0")
    if depth <= 0 or width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="dimensions must be > 0")

    # v0.81 安全收尾（Mimosa High 判定留痕）：tpl/svc 为用户可控自由文本——
    # ①quote_logistics 入口先过白名单归一（canonical_tpl_provider/
    #   canonical_service_level）；②下游 query_logistics_cost 全部 ORM
    #   select().where() 绑定参数（参数化证明见其 docstring），无字符串拼 SQL。
    # weight/dims 上面已 float() 强转。
    tpl = str(body.get("tpl_provider", "") or "") or None
    svc = str(body.get("service_level", "") or "") or None

    from utils.logistics_quote import quote_logistics
    detail = quote_logistics(
        ozon_client_id=client_id or None,
        ozon_api_key=api_key or None,
        weight=weight,
        depth_cm=depth,
        width_cm=width,
        height_cm=height,
        tpl_provider=tpl,
        service_level=svc,
    )
    return detail


# ==================== API v1 路由 ====================
# 新端点统一走 /api/v1/ 前缀，旧路径通过重定向兼容

@router.get("/health", response_model=HealthResponse, tags=["health"])
async def v1_health():
    """健康检查（含 PG 连通性）。"""
    try:
        from sqlalchemy import text
        from storage.database.db import get_engine
        _engine = get_engine()
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return HealthResponse(status="ok", message="Service is running", db="connected")
    except Exception:
        # T2(api-M1 补): str(e) 可能携带连接串等内部信息，只进日志；status/db 语义字段保留
        logger.exception("health check failed")
        raise HTTPException(status_code=503, detail={
            "status": "degraded", "message": "service temporarily unavailable", "db": "disconnected"
        })

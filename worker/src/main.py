import argparse
import asyncio
import contextvars
import copy
import hashlib
import json
import os
import threading
import traceback
import logging
import uuid
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Any, Dict, Iterable, Optional
import uvicorn
import time
from fastapi import FastAPI, HTTPException, Query, Request, APIRouter
from fastapi.responses import JSONResponse, FileResponse
from api.errors import WorkerErrorCode, error_response
from utils.envelope_contract import envelope_strict_enabled, validate_envelope  # ✅ hotfix: 契约模块下沉 utils（graphs 层 ingest 也要用，graphs→api 是立法禁止的 upward 边）
from api.schemas import (
    SubmitTaskRequest, SubmitTaskResponse, TaskStatusResponse,
    CancelTaskResponse, HealthResponse, TaskStatisticsResponse, ErrorBody,
    AuthVerifyResponse, AnalyticsReportResponse,
    BlueOceanQueryItem, OzonBestsellerItem, MarketBestsellerItem, DiscoveryRunItem,
    DiscoveryRunDetail,
)
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from storage.database.db import get_session, get_engine, init_db
from storage.database.supabase_client import get_supabase_client
from services.tenant_service import resolve_analytics_scope  # v0.76 T7(api-H3): task_statistics admin 判定（模块级 import，测试 patch main 命名空间；resolve_tenant 仍按 _task_status_guard 惯例函数内延迟 import）
from storage.memory.memory_saver import get_memory_saver
from storage.database.shared.model import (
    Base, BlueOceanQuery, OzonBestseller, MarketBestseller, DiscoveryRun,
)
from orchestrator.task_processor import SupabaseTaskProcessor, set_task_processor  # ✅ W3a: 编排器归位 orchestrator 包；W3b: lifespan 注入单例 holder
from utils.ozon_client import ozon_check_quota, ozon_post  # 配额检查 + F-F01 auth_verify Ozon 校验
from utils.instance_lock import (  # E-4: 后台循环单实例锁（多副本防重复跑）
    METRICS_AGGREGATION,
    PERIODIC_CLEANUP,
    STORE_SYNC_LEGACY,
    try_acquire_instance_lock,
)
from utils.ozon_errors import OzonError  # F-F01: auth_verify Ozon 校验错误分类
from utils.draft_sanity import validate_draft_sanity  # v0.21 P2 入队防线
from utils.sentry_setup import init_sentry  # v0.23 Sentry 错误监测
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ✅ v0.23: Sentry 错误监测（SENTRY_DSN 为空则 no-op；HTTP 与 CLI 入口共用）
init_sentry()

# ✅ W3b composition root 拆解：四族共享设施归位新模块（唯一权威）：
#   进度/任务上下文/优雅关闭 → runtime/progress.py
#   限流器 → runtime/rate_limit.py
#   GraphService → runtime/graph_service.py
#   鉴权族（token 校验/限流接线/余额判定/Bearer 守卫） → api/security.py
# 此处同名 re-export 是**兼容面**：路由裸名调用与存量测试的 monkeypatch
# main.X 不迁移即继续工作（main 的名字绑定同一函数对象，patch main.X 对
# main 内裸名调用照常生效）。新代码一律直接 from 新模块 import，勿再经
# main 转手；routes→main 懒导入清退已登记 follow-up。
from runtime.progress import (
    STAGE_ORDER,
    SHUTDOWN_FLAG,
    _current_task_id,
    _last_persist_ts,
    _persist_progress,
    _purge_stale_progress,
    _task_progress,
    get_current_task_id,
    get_progress,
    is_shutting_down,
    request_shutdown,
    set_current_task_id,
    update_progress,
)
from runtime.rate_limit import (
    _RATE_LIMITER_MAX_KEYS,
    RATE_LIMIT_PER_MINUTE,
    RateLimiter,
    rate_limiter,
)
from runtime.graph_service import GraphService, service
from api.security import (
    _authenticate_token,
    _auth_verify_sync,
    _balance_source_label,
    _check_mxou_balance,
    _extract_token_from_body,
    _key_user_id,
    _require_bearer,
    _revoked_tokens,
    _task_status_guard,
    _verify_analytics_token,
)

# Local runtime utilities (standalone replacements for platform SDK)
from runtime.context import new_context, Context
from runtime.helpers import graph_helper, ErrorClassifier, classify_error
from runtime.log_utils import (
    LOG_FILE, LOG_LEVEL, setup_logging,
    LangGraphParser, extract_core_stack,
)

from utils.logger import setup_structured_logging, get_logger, set_trace_context, log_task_event

# 结构化日志：生产用 JSON，本地开发用可读格式
setup_structured_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_format=os.getenv("LOG_FORMAT", "json").lower() == "json",
    log_file=os.getenv("LOG_FILE", ""),
)

logger = get_logger(__name__)

async_graph: Optional[CompiledStateGraph] = None
task_processor: Optional[SupabaseTaskProcessor] = None

# ✅ v0.77.3：启动关键配置校验清单——缺失即大声报（ERROR+Sentry）。
# 必需 = 节点硬依赖（缺了任务确定性失败）；可选 = 有内置降级（仅 WARNING）。
_CRITICAL_CONFIG_FILES = (
    "scene_generation_llm_cfg.json",
    "visual_vars_llm_cfg.json",
    "category_match_v2_cfg.json",
    "attributes_llm_cfg.json",
    "translate_russian_cfg.json",
    "error_repair_llm_cfg.json",
    "imagegen.json",
    "image_prompts.json",
)
_OPTIONAL_CONFIG_FILES = (
    "restricted_keywords.json",   # 缺失回退内置默认词表（受限闸降级）
    "attr_synonyms.json",         # 同义词组缺失=匹配质量降级，非失败
    "category_synonyms.json",
)


def _assert_critical_configs(base_dir: str | None = None) -> None:
    """启动即校验 /app/config 关键文件在位（bind mount 挂空秒级暴露）。

    实测事故（2026-09-19 gate 取证）：compose 栈从已删除 worktree 启动 →
    config bind 源路径不存在 → Docker 静默创建空目录 → 镜像内配置被空目录
    遮蔽 → 任务在 scene 节点 FileNotFoundError 被重试 4 轮白烧 40s+。
    校验只报不拦（repair = 修 bind/重新部署，热路径不受影响）。
    base_dir 参数供测试注入（缺省 /app/config）。
    """
    base = base_dir or os.path.join(os.sep, "app", "config")
    if not os.path.isdir(base):
        logger.warning("⚠️ 配置目录 %s 不存在（源码态运行可忽略）", base)
        return
    missing = [f for f in _CRITICAL_CONFIG_FILES if not os.path.isfile(os.path.join(base, f))]
    if missing:
        logger.error(
            "🚨 关键配置文件缺失 %d 个（bind mount 挂空/镜像残缺？任务将确定性失败）: %s",
            len(missing), ", ".join(missing),
        )
        try:
            from utils.sentry_setup import capture_task_event
            capture_task_event(
                "critical_configs_missing",
                f"关键配置缺失: {', '.join(missing)}",
                level="error",
            )
        except Exception:
            pass
    for f in _OPTIONAL_CONFIG_FILES:
        if not os.path.isfile(os.path.join(base, f)):
            logger.warning("⚠️ 可选配置缺失（走内置降级）: %s", f)


def _warn_if_multi_worker() -> None:
    """B4 BL-31（2026-09-11 仓库治理）：多 worker 部署探测告警（只告警不改行为）。

    本进程存在多处**内存态组件**：MXOU 余额缓存（_check_balance_cached，token
    指纹绑定）、任务进度缓存（内存优先 + PG 2s 节流回写）、限流器计数、远程 MCP
    session manager（FastMCP）——均进程局部，无任何开关能使其多副本安全，当前
    部署假设 uvicorn workers=1（start_http_server 硬编码；横向并发靠
    MAX_CONCURRENT 单进程内消化）。uvicorn/gunicorn 均会读 WEB_CONCURRENCY
    （gunicorn 亦读 WORKERS）——env >1 即告警，提醒运维改回单 worker 部署。
    """
    for _var in ("WEB_CONCURRENCY", "WORKERS"):
        _raw = os.getenv(_var, "").strip()
        if _raw.isdigit() and int(_raw) > 1:
            logger.warning(
                "⚠️ 检测到 %s=%s：内存态组件（余额缓存/租户缓存/MCP session/限流计数）"
                "非多副本安全，当前部署假设 workers=1——多 worker 会出现余额误判/进度"
                "丢失/MCP 会话错乱，请改用单 worker + MAX_CONCURRENT 扩并发",
                _var, _raw,
            )


def _revive_failed_enabled() -> bool:
    """T19(race-M4): 部署重启默认不复活 failed 任务（retry_count 归零会烧用户额度重复上架）。
    应急恢复旧行为：SKIP_FAILED_REVIVE=0。"""
    return os.environ.get("SKIP_FAILED_REVIVE", "").strip() == "0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ✅ B4 BL-31: 启动即探测多 worker 误部署（内存态组件非多副本安全，见函数注释）
    _warn_if_multi_worker()
    # ✅ v0.77.3（管线延迟同批运维守卫）：启动即校验关键 config 文件在位。
    # 实测事故（2026-09-19 gate）：栈从已删除的 worktree 启动 → config bind 挂到
    # 死路径 → Docker 静默挂空目录遮住镜像内配置 → scene 节点 FileNotFound、
    # 受限词表静默回退内置、任务 4 轮重试白烧。缺文件大声报（ERROR + Sentry），
    # 不 crash（可热修 bind 后重启）；restricted_keywords 属可选降级仅 WARNING。
    _assert_critical_configs()
    engine = get_engine()
    # 自动建表（幂等，CREATE TABLE IF NOT EXISTS）
    init_db()
    @event.listens_for(engine, "connect")
    def _set_utc(dbapi_conn, _):
        with dbapi_conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
    checkpointer = get_memory_saver()
    if graph_helper.is_agent_proj():
        base = graph_helper.get_agent_instance("agents.agent", None)
        sync_graph = base.builder.compile(checkpointer=checkpointer)
    else:
        base = graph_helper.get_graph_instance("graphs.graph")
        sync_graph = base.builder.compile(checkpointer=checkpointer)
    global async_graph
    async_graph = base.builder.compile(checkpointer=checkpointer)
    service.set_graph(sync_graph)
    
    # 启动Supabase任务处理器（最多30个并发任务 — 4核4G 服务器 I/O 密集安全值，外部 API 由全局限流器兜底）
    # ✅ W3b: 单例归 orchestrator holder（services 层经 get_task_processor 取，
    # 不再 from main）；main.task_processor 保留为同名别名——路由裸名与
    # 测试 monkeypatch main.task_processor 兼容面不变。
    global task_processor
    max_concurrent = int(os.getenv("MAX_CONCURRENT", "30"))
    task_processor = SupabaseTaskProcessor(max_concurrent=max_concurrent)
    set_task_processor(task_processor)

    # v0.63.1 架构优化 R1: 扩容 asyncio 默认线程池——LangGraph 全部节点是 sync 函数,
    # ainvoke 下经 run_in_executor(None, ...) 跑在 asyncio 默认 ThreadPoolExecutor
    # (min(32, cpu_count+4), 4 核 = 8 线程) → 「MAX_CONCURRENT 并发」实际并行度被卡死,
    # Phase2 的 8 个并行生图节点退化为排队。按 并发×节点扇出 扩容, 上限 128 防 4GB OOM;
    # 上游 MXOU/Ozon 由全局限流器兜底, 不会被打爆。
    try:
        import concurrent.futures
        _graph_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(128, max(64, max_concurrent * 8)),
            thread_name_prefix="graph-node",
        )
        _loop = asyncio.get_running_loop()
        _loop.set_default_executor(_graph_executor)
        logger.info("🔄 asyncio 默认线程池扩容: %d workers（sync 图节点并行度）",
                    _graph_executor._max_workers)
    except Exception as _exec_e:
        logger.warning("默认线程池扩容失败（不影响启动）: %s", _exec_e)

    # ✅ 启动时僵尸任务恢复：重置重启前的 running 任务和可重试的 failed 任务
    # ⚠️ v0.30.0:
    #   SKIP_ZOMBIE_RECOVERY=1 — 跳过全部恢复（本地/测试环境必开，防止旧 failed 任务复活真实上架）
    # ⚠️ T19(race-M4) 语义翻转：failed 复活默认**关闭**——部署重启不再把 retry_count<max 的
    #   failed 重置回 pending（retry_count 归零=对用户无人同意的重新上架，烧生图/上传额度）。
    #   应急恢复旧行为：SKIP_FAILED_REVIVE=0 — failed→pending 复活（running 恢复不受影响）。
    _skip_all = os.getenv("SKIP_ZOMBIE_RECOVERY", "0") == "1"
    _skip_failed = not _revive_failed_enabled()
    if _skip_all:
        logger.info("🧹 跳过全部僵尸任务恢复（SKIP_ZOMBIE_RECOVERY=1）")
    else:
        try:
            from sqlalchemy import text
            sess = get_session()
            try:
                # ⚠️ v0.26 FIX: 僵尸重置同样有界化——running 且 retry_count < max_retries → pending+递增；
                # retry_count >= max_retries → failed（避免重启后无限重跑烧生图额度）
                # 1. 重置 running 任务（Worker 重启导致中断）— 永远保留（任务卡 running 会永久阻塞）
                zombie_running = sess.execute(
                text("UPDATE ozon_product_tasks SET status='pending', retry_count=retry_count+1, "
                     "error_message='[ZOMBIE_RESET] 重启前 running 任务重置重试(有界)', "
                     "started_at=NULL, updated_at=NOW() "
                     "WHERE status='running' AND retry_count < max_retries")
                ).rowcount
                zombie_running_failed = sess.execute(
                text("UPDATE ozon_product_tasks SET status='failed', "
                     "error_message='[ZOMBIE_RESET] 重试次数耗尽，终止（不再重跑）', "
                     "completed_at=NOW(), updated_at=NOW() "
                     "WHERE status='running' AND retry_count >= max_retries")
                ).rowcount
                # 2. 重置可重试的 failed 任务（T19 默认跳过——防部署重启复活旧任务重复上架；
                #    仅 SKIP_FAILED_REVIVE=0 显式恢复旧行为）
                zombie_failed = 0
                if not _skip_failed:
                    zombie_failed = sess.execute(
                    text("UPDATE ozon_product_tasks SET status='pending', retry_count=0, error_message=NULL, updated_at=NOW() WHERE status='failed' AND retry_count < max_retries")
                    ).rowcount
                sess.commit()
                if zombie_running or zombie_running_failed or zombie_failed:
                    logger.info(f"🧹 启动清理: {zombie_running} 个僵尸 running → pending, {zombie_running_failed} 个 running → failed(耗尽), {zombie_failed} 个 failed → pending{'（failed 默认不复活；SKIP_FAILED_REVIVE=0 恢复旧行为）' if _skip_failed else ''}")
                    # v0.29.2 监控: 启动时任务重跑/恢复上报 Sentry(带数量)
                    try:
                        from utils.sentry_setup import capture_task_event
                        capture_task_event(
                            "zombie_reset",
                            f"启动僵尸恢复: running→pending {zombie_running}, "
                            f"running→failed(耗尽) {zombie_running_failed}, failed→pending {zombie_failed}",
                            level="warning",
                            zombie_running=zombie_running,
                            zombie_running_failed=zombie_running_failed,
                            zombie_failed=zombie_failed,
                        )
                    except Exception:
                        pass
            finally:
                sess.close()
        except Exception as _cleanup_e:
            logger.warning(f"⚠️ 启动清理失败（非致命）: {_cleanup_e}")

    # 启动定时清理任务（E-4: 多副本时仅持锁副本跑，单副本恒 True 零变化）
    cleanup_task = None
    if try_acquire_instance_lock("periodic_cleanup", PERIODIC_CLEANUP):
        cleanup_task = asyncio.create_task(_periodic_task_cleanup(interval_seconds=60))

    # 店铺数据自动同步（PRD M1 任务化:5s 扫描+worker 池+job 可见;SKIP_STORE_SYNC=1 关闭;
    # STORE_SYNC_JOBS_ENABLED=0 回退旧版 15min 全局轮询）
    global store_sync_task
    if os.getenv("SKIP_STORE_SYNC", "0") == "1":
        logger.info("⏸️ 店铺自动同步已关闭（SKIP_STORE_SYNC=1）")
        store_sync_task = None
    else:
        from services.store_sync_scheduler import (
            jobs_enabled, store_sync_jobs_loop, store_sync_loop,
        )
        if jobs_enabled():
            from services import store_sync_jobs as _sync_jobs_mod
            try:
                _sync_jobs_mod.zombie_reset()
                logger.info("🧹 同步任务僵尸恢复完成")
            except Exception as _zexc:
                logger.warning("同步任务僵尸恢复异常(不阻断): %s", str(_zexc)[:200])
        if jobs_enabled():
            # jobs 版：PG 队列 SKIP LOCKED 认领，天然多副本安全，不加实例锁
            store_sync_task = asyncio.create_task(store_sync_jobs_loop())
        elif try_acquire_instance_lock("store_sync_legacy", STORE_SYNC_LEGACY):
            store_sync_task = asyncio.create_task(store_sync_loop())
        else:
            store_sync_task = None

    # PRD M3: 指标日聚合 + 保留清理(10min)
    from services.metrics_aggregation import aggregation_loop
    metrics_agg_task = None
    if try_acquire_instance_lock("metrics_aggregation", METRICS_AGGREGATION):
        metrics_agg_task = asyncio.create_task(aggregation_loop())

    # 启动Worker后台任务（不阻塞主服务启动）
    # ⚠️ v0.14 E9: num_workers 联动 MAX_CONCURRENT（旧代码硬编码 10，调大 env 实际并发仍封顶 10）
    worker_task = asyncio.create_task(task_processor.start_workers(num_workers=max_concurrent))
    
    yield

    # ✅ v0.29(PRD-cicd-stability): 优雅关闭 — 不再强杀运行中任务
    # 1. 停止接收新任务(worker_loop 检查 SHUTDOWN_FLAG 后 break)
    request_shutdown()
    logger.info("🛑 收到关闭信号, 停止接收新任务, 等待运行中任务排空(最多 5 分钟)...")
    # 2. drain: 轮询 PG 中 running 任务数, 直到为 0 或超时 300s
    try:
        from sqlalchemy import text as _sa_text
        for _drain_i in range(300):
            sess = get_session()
            try:
                _running = sess.execute(
                    _sa_text("SELECT COUNT(*) FROM ozon_product_tasks WHERE status='running'")
                ).scalar() or 0
            finally:
                sess.close()
            if _running == 0:
                logger.info("✅ 运行中任务已全部完成, 优雅关闭")
                break
            if _drain_i % 30 == 0:
                logger.info(f"⏳ 排空中: 仍有 {_running} 个任务运行中 ({_drain_i}s)")
            await asyncio.sleep(1)
        else:
            logger.warning("⚠️ 排空超时(5 分钟), 取消剩余任务(zombie cleanup 兜底)")
            # F-C05（2026-09-09 审计）：被部署中断的 running 任务若只靠 zombie
            # 复活，重启后会被静默重跑（同 SKU 双倍成本）。原地置 failed 并
            # 推满 retry_count（防复活谓词命中），让用户显式重提。
            _sess = get_session()
            try:
                _n = _sess.execute(
                    _sa_text("""
                        UPDATE ozon_product_tasks
                        SET status='failed', retry_count=max_retries, completed_at=NOW(),
                            error_message='[部署重启] 排空超时任务中断，请重新提交'
                        WHERE status='running'
                    """)
                ).rowcount
                _sess.commit()
                if _n:
                    logger.warning("🛑 已将 %d 个运行中任务标记为 failed（部署中断），不自动重跑", _n)
            finally:
                _sess.close()
    except Exception as _drain_e:
        logger.warning(f"⚠️ 排空检查异常(继续关闭): {_drain_e}")

    # 关闭时停止Worker
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            logger.info("Worker任务已取消")
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            logger.info("定时清理任务已取消")

    # v0.64 P2: 关闭 asyncio 默认线程池（扩容的 ThreadPoolExecutor）
    # 防止进程退出时线程池中的 sync 节点泄漏（内存/句柄）
    try:
        if _graph_executor is not None:
            _graph_executor.shutdown(wait=True, timeout=30)
            logger.info("🔄 asyncio 线程池已关闭")
    except Exception as _exec_shutdown_e:
        logger.warning("线程池关闭异常(不影响退出): %s", _exec_shutdown_e)

# ── MCP 远程服务（v0.67.0 批次 1）──
# mcp_server.py 模块级构建 mcp_app（FastMCP streamable-http ASGI + Bearer 鉴权中间件），
# 不反向 import main（工具内延迟导入，routes 同款防循环模式）。
# MCP_ENABLED=0 或 fastmcp 缺失 → 跳过挂载（HTTP 面完全不受影响）。
_MCP_ENABLED = os.getenv("MCP_ENABLED", "1") == "1"
_mcp_asgi_app = None
if _MCP_ENABLED:
    try:
        from mcp_server import mcp_app as _mcp_asgi_app
    except Exception as _mcp_err:  # fastmcp 未安装等：降级为无 MCP，不阻断主服务
        logger.warning(f"⚠️ MCP 服务加载失败，已跳过（HTTP API 不受影响）: {_mcp_err}")
        _mcp_asgi_app = None

_mcp_lifespan = getattr(_mcp_asgi_app, "lifespan", None) if _mcp_asgi_app is not None else None


@asynccontextmanager
async def _root_lifespan(app: FastAPI):
    """合并 worker 主 lifespan 与 FastMCP session manager lifespan（挂载必需）。"""
    if _mcp_lifespan is not None:
        async with AsyncExitStack() as _stack:
            await _stack.enter_async_context(_mcp_lifespan(app))
            async with lifespan(app):
                yield
    else:
        async with lifespan(app):
            yield


app = FastAPI(
    lifespan=_root_lifespan,
    title="Ozon Worker API",
    description="Ozon 产品上架 Worker — 接收信封、执行 LangGraph 管线、上传 Ozon",
    version="1.0.0",
)

# MCP 端点挂载（/mcp；webui 在 /app、newapi 代理在 /api 白名单，路径零冲突）
if _mcp_asgi_app is not None:
    app.mount("/mcp", _mcp_asgi_app)

    class _McpNoSlash:
        """精确 /mcp（无尾斜杠）内部转交：Mount 对裸路径会在鉴权前 307 外部重定向，
        这里复刻 Mount 的 scope 语义（root_path+=/mcp, path=/）直接进鉴权中间件。
        可调用类实例 → Starlette 按 raw ASGI app 消费（函数会被包成 request handler）。"""

        def __init__(self, app_):
            self.app = app_

        async def __call__(self, scope, receive, send):
            scope = dict(scope)
            scope["root_path"] = scope.get("root_path", "") + "/mcp"
            scope["path"] = "/"
            await self.app(scope, receive, send)

    from starlette.routing import Route as _Route
    # 插在 Mount 之前，精确路径优先命中
    app.router.routes.insert(
        len(app.router.routes) - 1,
        _Route("/mcp", _McpNoSlash(_mcp_asgi_app),
               methods=["GET", "POST", "DELETE"], include_in_schema=False),
    )
    logger.info("🔌 MCP 远程服务已挂载: POST/GET /mcp（streamable-http，Bearer=mxou key）")

# ── API v1 路由 ──
v1 = APIRouter(prefix="/api/v1", tags=["v1"])


@app.get("/task/{task_id}", include_in_schema=False)
async def http_get_task(task_id: str) -> dict:
    """[REMOVED] 端点已删除（2026-09-23）：无鉴权且自 async runtime 重构起 100% 500
    （AsyncTaskRuntime 无 get 方法）。真实路由已移除，此处仅占位返回 410 Gone 引导迁移。
    """
    raise HTTPException(status_code=410,
                        detail="endpoint removed; use GET /task_status/{task_id}")


# ============================================================
# 运维：定期清理 + 健康检查
# ============================================================

# B2a-5/6: 两个每日级维护钩子的节流时间戳（epoch 秒；0.0 = 启动后首轮即执行）
_LAST_CACHE_SWEEP: float = 0.0
_LAST_FX_REFRESH: float = 0.0

# 过期缓存清扫覆盖面：只扫两缓存表——category_cache 是类目树快照，另有治理，勿加入
_SWEEP_CACHE_TABLES = ("dictionary_value_cache", "attribute_cache")
_SWEEP_BATCH_LIMIT = 5000  # 每批 ctid 删除上限（防长事务锁表）
_SWEEP_MAX_BATCHES = 20    # 单表迭代批数封顶（防病态大量过期行拖死清理循环）

# ✅ v0.77.2（运维修复）: store_metrics_history 保留策略默认天数——_append_metrics_snapshot
# 每次同步 append 一条快照，是唯一高速增长表（生产 15,557 行 / ~650 行/店/天，无保留策略）。
# env STORE_METRICS_RETENTION_DAYS 可覆盖；非法值回落 90。
STORE_METRICS_RETENTION_DAYS = 90
_STORE_METRICS_RETENTION_ENV = "STORE_METRICS_RETENTION_DAYS"


def _store_metrics_retention_days() -> int:
    """保留天数（运行时读 env，故 ops 改环境变量即生效；非法值回落 90）。"""
    try:
        days = int(os.getenv(_STORE_METRICS_RETENTION_ENV, str(STORE_METRICS_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return STORE_METRICS_RETENTION_DAYS
    return days if days >= 1 else STORE_METRICS_RETENTION_DAYS


def _sweep_store_metrics_history(conn, retention_days: int | None = None) -> int:
    """删 snapshot_at 早于 NOW()-N 天的店铺指标快照，返回删除行数（v0.77.2）。

    实现细节：
    - 天数经 int() 强转后传入 **绑定参数**，SQL 用 ``make_interval(days => :days)``
      组装——不用 f-string 拼字面量（避免 sqlalchemy text() 裸 cast 家族坑，见
      AGENTS 记忆 sqlalchemy-jsonb-cast-trap；也免注入面）。
    - 幂等：删过的行不再命中 → 每轮清理循环调用安全。
    - 失败由调用方（:func:`_maybe_sweep_store_metrics`）处理——本函数把异常如实抛出。
    """
    from sqlalchemy import text

    days = retention_days if retention_days is not None else _store_metrics_retention_days()
    days = max(1, int(days))
    res = conn.execute(
        text(
            "DELETE FROM store_metrics_history "
            "WHERE snapshot_at < NOW() - make_interval(days => :days)"
        ),
        {"days": days},
    )
    return int(res.rowcount or 0)


def _maybe_sweep_store_metrics(conn) -> int:
    """每轮清理 store_metrics_history 超期快照（默认 90 天，v0.77.2）。

    - 每轮（非节流）执行——单语句 DELETE 成本低，且增长最快表需及时回收；
    - **SAVEPOINT（begin_nested）隔离**：sweep 失败只回滚本语句，绝不污染同轮
      r1/r1f/r2/r3 等其它清理写入，也不中止主循环；
    - 失败仅 warning（非致命），返回 0。
    """
    try:
        with conn.begin_nested():
            n = _sweep_store_metrics_history(conn)
        if n:
            logger.info(
                f"🧹 定期清理: {n} 行过期店铺指标快照已删除"
                f"（store_metrics_history > {_store_metrics_retention_days()} 天）"
            )
        return n
    except Exception:
        logger.warning("store_metrics_history 保留清理失败（非致命，下轮重试）", exc_info=True)
        return 0


def _sweep_expired_caches(conn) -> int:
    """物理删除两缓存表的过期行（expires_at 为 int 秒），返回删除总数。

    ctid 批删（LIMIT 5000/批，删空一批再看下一批）；表名来自固定元组非用户
    输入，f-string 拼接安全；绑定参数纯 int 无需 CAST。
    """
    from sqlalchemy import text
    total = 0
    now = int(time.time())
    for table in _SWEEP_CACHE_TABLES:
        for _ in range(_SWEEP_MAX_BATCHES):
            res = conn.execute(text(
                f"DELETE FROM {table} WHERE ctid IN "
                f"(SELECT ctid FROM {table} WHERE expires_at < :now LIMIT {_SWEEP_BATCH_LIMIT})"
            ), {"now": now})
            deleted = int(res.rowcount or 0)
            total += deleted
            if deleted < _SWEEP_BATCH_LIMIT:
                break
    return total


def _maybe_sweep_caches(conn) -> None:
    """每 24h 一轮的过期缓存清扫（B2a-5）。整体非致命：失败吞掉且不推进节流，
    下一轮清理循环自然重试。"""
    global _LAST_CACHE_SWEEP
    if time.time() - _LAST_CACHE_SWEEP <= 86400:
        return
    try:
        deleted = _sweep_expired_caches(conn)
        conn.commit()
        _LAST_CACHE_SWEEP = time.time()
        if deleted:
            logger.info(f"🧹 定期清理: 过期缓存清扫删除 {deleted} 行"
                        f"（dictionary_value_cache/attribute_cache，expires_at 已过期）")
    except Exception:
        logger.warning("过期缓存清扫失败（非致命，下轮重试）", exc_info=True)


def _maybe_refresh_fx() -> None:
    """fx 汇率每日刷新钩子（B2a-6）。lazy import 与并行开发的
    utils.fx_rate_service 解耦（缺失/失败都不影响清理主循环）；
    失败也推进节流——防清理循环每分钟 warning 刷屏。"""
    global _LAST_FX_REFRESH
    if time.time() - _LAST_FX_REFRESH <= 86400:
        return
    try:
        from utils.fx_rate_service import refresh_cny_rub_if_due
        refresh_cny_rub_if_due()
    except Exception:
        logger.warning("fx refresh failed", exc_info=True)
    finally:
        _LAST_FX_REFRESH = time.time()


# ✅ v0.75 C2（BL-25 Phase 1-2 漏项）: langgraph checkpoint 三表清理序——
# thread_id == ozon_product_tasks.id（task_processor.py:970 configurable
# {"thread_id": task_id}），任务行 30 天归档删除后三表行永久孤儿。
# 本地 PG information_schema 探针实证三表无外键；顺序 checkpoints →
# checkpoint_blobs → checkpoint_writes（语义父表先删）。
# ⚠️ memory.checkpoint_migrations 是 langgraph 自有版本表，绝不清理。
_CHECKPOINT_PURGE_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _purge_checkpoints(conn, task_ids) -> int:
    """v0.75 C2: 按 thread_id 清 memory.checkpoints 三表（删任务行**前**调用）。

    Args:
        conn: SQLAlchemy Connection（调用方事务内，随外层 commit 提交）
        task_ids: 将删任务 uuid 文本列表（与任务 DELETE 同 WHERE 收集）

    Returns:
        三表删除总行数；task_ids 空 → 0（零 execute）。

    ANY(:ids) bind 传 Python list[str]——psycopg2 自动数组化（勿手拼 IN 字面量）。
    一次性存量孤儿清理见 worker/scripts/cleanup_checkpoints.py。
    """
    if not task_ids:
        return 0
    from sqlalchemy import text
    total = 0
    for _table in _CHECKPOINT_PURGE_TABLES:
        res = conn.execute(
            text(f"DELETE FROM memory.{_table} WHERE thread_id = ANY(:ids)"),
            {"ids": list(task_ids)},
        )
        total += int(res.rowcount or 0)
    logger.debug(
        f"checkpoint purge: {total} rows across {len(task_ids)} tasks "
        f"({_CHECKPOINT_PURGE_TABLES})"
    )
    return total


async def _periodic_task_cleanup(interval_seconds: int = 60):
    """定期清理僵尸任务：重置卡死的 running 任务，清理过期 completed 任务"""
    await asyncio.sleep(30)  # 启动后等 30 秒再开始
    while True:
        try:
            from sqlalchemy import text
            from storage.database.db import get_engine
            engine = get_engine()
            with engine.connect() as conn:
                # ⚠️ v0.26 FIX: stale running 无条件重置 → 无限重跑（Sentry 超时×100/failed×120 实证）。
                # 改为有界：retry_count < max_retries → pending + retry_count+1 + 记错误（可重试但封顶）；
                # retry_count >= max_retries → failed（终止循环，不再无条件回炉重跑全管线烧生图额度）。
                r1 = conn.execute(text(
                    "UPDATE ozon_product_tasks SET status='pending', retry_count=retry_count+1, "
                    "error_message='[STALE_RUNNING] 任务运行超30分钟未更新，自动重试(有界)', "
                    "started_at=NULL, updated_at=NOW() "
                    "WHERE status='running' AND updated_at < NOW() - INTERVAL '30 minutes' "
                    "AND retry_count < max_retries"
                )).rowcount
                r1f = conn.execute(text(
                    "UPDATE ozon_product_tasks SET status='failed', "
                    "error_message='[STALE_RUNNING] 重试次数耗尽，终止（不再重跑）', "
                    "completed_at=NOW(), updated_at=NOW() "
                    "WHERE status='running' AND updated_at < NOW() - INTERVAL '30 minutes' "
                    "AND retry_count >= max_retries"
                )).rowcount
                # 归档 30 天前的 completed 任务（结果已留存 listing_result_log，见
                # utils/listing_result_log.py——v0.67 起每任务一行事实留存，物理删不再丢数据）
                # ✅ v0.75 C2: 删任务行**前**先收集将删 id → 清 checkpoint 三表
                # （thread_id==task_id，删除谓词与任务 DELETE 同 WHERE；空列表跳过）。
                _due_task_ids = [
                    str(row[0]) for row in conn.execute(text(
                        "SELECT id::text FROM ozon_product_tasks "
                        "WHERE status='completed' AND updated_at < NOW() - INTERVAL '30 days'"
                    )).fetchall()
                ]
                _purge_checkpoints(conn, _due_task_ids)
                r2 = conn.execute(text(
                    "DELETE FROM ozon_product_tasks "
                    "WHERE status='completed' AND updated_at < NOW() - INTERVAL '30 days'"
                )).rowcount
                # F-D04（2026-09-09 审计）对账归位：终态 commit 与 draft_submissions
                # 写回不同事务，崩溃窗口会让 submission 卡 pending/uploading，
                # has_active_submission 恒真 → 该草稿重提永久 409。每轮把
                # 「任务已终态而 submission 仍活跃」的行按任务终态归位（幂等）。
                r3 = conn.execute(text(
                    "UPDATE draft_submissions ds SET status = CASE t.status "
                    "WHEN 'completed' THEN 'published' "
                    "WHEN 'rejected' THEN 'rejected' "
                    "ELSE 'failed' END, updated_at = NOW() "
                    "FROM ozon_product_tasks t "
                    "WHERE ds.submitted_task_id = t.id::text "
                    "AND ds.status IN ('pending','uploading') "
                    "AND t.status IN ('completed','failed','rejected','cancelled')"
                )).rowcount
                # ✅ v0.77.2（运维修复）: store_metrics_history 保留策略——每店每次同步
                # append 快照，唯一高速增长表（生产 15,557 行 / ~650 行/店/天）。每轮清理
                # 超期行（默认 90 天，env 可覆盖），savepoint 隔离 + 失败仅 warning。
                _maybe_sweep_store_metrics(conn)
                conn.commit()
                if r1 or r1f or r2 or r3:
                    logger.info(f"🧹 定期清理: {r1} stale running → pending(重试+1), {r1f} stale running → failed(耗尽), {r2} old completed deleted (结果已留存 listing_result_log), {r3} submission 对账归位")
                    # v0.29.2 监控: 超时任务重跑/终止上报 Sentry
                    try:
                        from utils.sentry_setup import capture_task_event
                        capture_task_event(
                            "stale_running_reset",
                            f"定期清理: stale running→pending(重试+1) {r1}, "
                            f"stale running→failed(耗尽) {r1f}, 清理completed {r2}",
                            level="warning",
                            stale_pending=r1,
                            stale_failed=r1f,
                            old_completed_deleted=r2,
                        )
                    except Exception:
                        pass
                # ✅ v0.26: 清理任务生图缓存（7 天前，防表无限膨胀）
                try:
                    from utils.task_image_cache import cleanup_old
                    _del = cleanup_old(older_than_days=7)
                    if _del:
                        logger.info(f"🧹 定期清理: {_del} 条任务生图缓存（>7天）已删除")
                except Exception:
                    pass
                # ✅ v0.10: 清理 _task_progress 中已完成超过 1 小时的任务条目（防内存泄漏）
                if r2:
                    _purge_stale_progress()
                # ✅ B2a-5/6: 每日级维护钩子（各自 24h 节流，失败非致命）
                _maybe_sweep_caches(conn)
                _maybe_refresh_fx()
        except Exception as _e:
            logger.debug(f"定期清理跳过: {_e}")
        await asyncio.sleep(interval_seconds)


@app.get("/health", responses={
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


@app.get("/api/v1/store/health", responses={
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


@app.post("/auth/verify", response_model=AuthVerifyResponse)
@app.post("/api/v1/auth/verify", response_model=AuthVerifyResponse)
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


@app.get("/progress/{run_id}", responses={
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


def _validate_draft_required_fields(draft: dict, extensions: dict) -> str | None:
    """返回缺失字段错误信息；通过返回 None。
    api 强制跟卖（follow_sell + follow_type=api）走 import-by-sku 复制竞品，
    不需要 1688 货源字段（weight/dimensions/purchase_cost/purchase_url），
    但 ⚠️ v0.26 FIX: 必须带 ozon_product_id（竞品身份）——否则是空壳信封，
    worker 拿不到竞品卡去复制（原漏洞：空壳能通过校验，定价/属性全缺被 Ozon 拒）。"""
    _is_api_follow = bool(
        (extensions or {}).get("follow_sell")
        and str((extensions or {}).get("follow_type") or "hand").lower() == "api"
    )
    base_fields = [("item_id", str), ("title", str), ("currency", str), ("images", list)]
    source_fields = [
        ("weight", (int, float)), ("dimensions", dict),
        ("purchase_cost", (int, float)), ("purchase_url", str),
    ]
    required = base_fields if _is_api_follow else base_fields + source_fields
    missing = []
    for field, expected_type in required:
        val = draft.get(field)
        if val is None or (isinstance(val, (str, list, dict)) and not val):
            missing.append(f"draft.{field}")
        elif not isinstance(val, expected_type):
            if expected_type == (int, float) and isinstance(val, (int, float)):
                continue
            missing.append(f"draft.{field}(类型错误: 期望{expected_type}, 实际{type(val).__name__})")
    # ⚠️ v0.26: api 跟卖必须带竞品身份（ozon_product_id 或 competitor_price 二选一），
    # 否则拦截（防空壳信封——图搜无货源仍提交）
    if _is_api_follow:
        if not str(draft.get("ozon_product_id") or "").strip() and not str(draft.get("competitor_price") or "").strip():
            missing.append("draft.ozon_product_id/competitor_price（api 跟卖必须有竞品身份）")
    return f"envelope.draft 缺少必填字段: {', '.join(missing)}" if missing else None


# ==================== Supabase任务队列API ====================


def _find_existing_task(tenant_id: str, sku_key: str) -> Optional[tuple[str, str]]:
    """查询同租户同 SKU 的活跃任务（pending/running），返回 (task_id, status) 或 None。

    P0-1: 提交层去重防线 — 防同一商品重跑产生重复 Ozon listing。
    去重查询失败时放行（fail-open，不因去重引入新的 500）。
    """
    from sqlalchemy import text
    try:
        _engine = get_engine()
        with _engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status FROM ozon_product_tasks "
                    "WHERE tenant_id = :t AND sku_key = :k "
                    "AND status IN ('pending', 'running') "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": tenant_id, "k": sku_key},
            ).fetchone()
        if row:
            return str(row[0]), str(row[1])
        return None
    except Exception as e:
        logger.warning("SKU 去重查询失败，跳过去重检查: %s", str(e)[:200])
        return None

def _write_direct_submission_row(task_id: str, tenant_id: str, ozon_client_id: str) -> None:
    """直连提交（skill submit_task 端点）写 draft_submissions 行 — 非致命。

    A4 决策：所有任务（skill 直连 + 采集箱提交）都有 draft_submissions 行，
    提交历史/在售货架/生命周期视图对两条路径完全统一。直连任务：
    - draft_id=NULL（无草稿）
    - credential_id=credentials 表按 (tenant_id, ozon_client_id) 反查（标量子查询注入，
      未登记则 NULL）——修复 T9 索引回填因 credential_id=NULL 跳过（W6）
    - store_client_id=payload 的 ozon_client_id、status='pending'（终态由 M0.3 写回）
    - submitted_task_id=task_id、extensions=NULL

    采集路径由 draft_service.submit_draft 自行写行（draft_id 有值）——本辅助只被
    http_submit_task 端点层调用，绝不进 task_processor.submit_task（否则双写）。

    写行失败仅 warning，绝不阻断任务入队（与 M0.2 同纪律）。
    """
    try:
        from sqlalchemy import text
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO draft_submissions "
                    "(draft_id, credential_id, store_client_id, extensions, status, submitted_task_id, tenant_id) "
                    "VALUES (NULL, "
                    "(SELECT id FROM credentials "
                    " WHERE tenant_id = :tenant_id AND ozon_client_id = :store_client_id "
                    "   AND status = 'active' ORDER BY created_at DESC LIMIT 1), "
                    ":store_client_id, NULL, 'pending', :task_id, :tenant_id)"
                ),
                {
                    "tenant_id": tenant_id,
                    "store_client_id": ozon_client_id,
                    "task_id": task_id,
                },
            )
    except Exception as exc:
        logger.warning(
            "直连提交写 draft_submissions 行失败（不阻断）task=%s tenant=%s client=%s: %s",
            task_id, tenant_id, ozon_client_id, str(exc)[:200],
        )


@app.post("/submit_task", responses={
    200: {"content": {"application/json": {"example": {
        "ok": True,
        "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "message": "Task submitted to queue (user: 28, balance: 12.5)",
    }}}}})
async def http_submit_task(request: Request):
    """
    提交任务到Supabase云端队列（方案2：验证token + 提交到队列，不立即执行拓扑）
    
    Args:
        payload: 任务数据（必须包含token、ozon_client_id、ozon_api_key、envelope）
        priority: 任务优先级（0-100，VIP用户使用更高优先级）
        timeout_seconds: 任务超时时间（默认30分钟）
        max_retries: 最大重试次数（默认3次）
    
    Returns:
        task_id: 任务UUID
        user_id: 用户ID（从token中提取）
        balance: 用户余额（可选）
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")
    
    try:
        body = await request.json()

        # ✅ 链路追踪：生成 trace_id
        trace_id = uuid.uuid4().hex[:12]
        set_trace_context(trace_id=trace_id)

        # ✅ Step1: 从payload中提取认证参数（兼容直连格式和包装格式）
        payload = body.get("payload") or body
        token = payload.get("token", "")
        ozon_client_id = payload.get("ozon_client_id", "")
        ozon_api_key = payload.get("ozon_api_key", "")
        envelope = payload.get("envelope", {})
        
        # ✅ v4: 提交层 envelope 结构校验 — 避免无效信封穿透到管线 node 层才报错
        if not isinstance(envelope, dict) or not envelope:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                "envelope 不能为空，必须包含 draft 字段",
                detail={"missing": ["envelope.draft"]},
            )
        draft = envelope.get("draft")
        if not isinstance(draft, dict) or not draft:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                "envelope.draft 不能为空",
                detail={"missing": ["draft"]},
            )
        # 必填字段校验（v0.22 P1: 抽成可测函数；api 强制跟卖跳过 1688 货源字段）
        _missing = _validate_draft_required_fields(draft, envelope.get("extensions") or {})
        if _missing:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                _missing,
                detail={"missing": _missing},
            )
        # weight 和 dimensions 的合理性校验
        weight_g = draft.get("weight", 0)
        dims = draft.get("dimensions", {})
        if isinstance(weight_g, (int, float)) and weight_g < 0:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                f"draft.weight 不能为负数: {weight_g}",
            )
        for dim_key in ("length", "width", "height"):
            dv = dims.get(dim_key, 0) if isinstance(dims, dict) else 0
            if isinstance(dv, (int, float)) and dv < 0:
                return error_response(
                    WorkerErrorCode.INVALID_REQUEST,
                    f"draft.dimensions.{dim_key} 不能为负数: {dv}",
                )

        # v0.21 P2: 物理合理性防线 — 脏重量/脏尺寸直接拒绝，防止打爆定价
        # C2: 传 envelope.extensions（竞品 competitor_weight_g/competitor_dimensions_mm 可放行）
        sanity_err = validate_draft_sanity(draft, envelope.get("extensions") or {})
        if sanity_err:
            logger.warning("❌ 信封数据异常被拒: %s", sanity_err)
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                f"信封数据异常: {sanity_err}",
                detail={"sanity": sanity_err},
            )

        # ✅ W2 信封契约硬化：未知顶层/未知 extensions 键 fail-closed（权威 =
        # utils/envelope_contract.py；ENVELOPE_STRICT=0 降级 warn，存量旧包逃生门）。
        # 只在边界校验——worker 在 ingest 后注入 box_reviewed/update_* 等键不受影响。
        envelope_errors = validate_envelope(envelope)
        if envelope_errors:
            if envelope_strict_enabled():
                return error_response(
                    WorkerErrorCode.INVALID_REQUEST,
                    f"信封契约校验失败（{len(envelope_errors)} 项）",
                    detail={"envelope_errors": envelope_errors},
                )
            logger.warning("⚠️ 信封契约校验失败（ENVELOPE_STRICT=0 降级放行）: %s", envelope_errors)

        # ✅ Step2: 验证token（查询Supabase tokens表）
        if not token:
            raise HTTPException(status_code=401, detail="Token is required")

        raw_token = token  # T21(race-M5): 限流键保持原始 token（含 sk- 前缀）形态不变

        # Step2: 处理sk-前缀
        if token.startswith("sk-"):
            token = token.replace("sk-", "", 1)  # ✅ 剥离第一个sk-前缀

        # Step3: MXOU key 即用户 —— 租户以真实 Supabase 为准（resolve_tenant 已做 Supabase→哈希回退）。
        # v0.62.4 修复：改用 resolve_tenant 而非 _key_user_id（哈希租户），
        # 否则余额降级查 users 表用哈希 id 查不到真实用户 → 误判余额不足，
        # 且任务 tenant_id 与全系统(WebUI/采集箱)不一致造成租户漂移。
        from services.tenant_service import resolve_tenant
        user_id = resolve_tenant(token)

        # ✅ 限流检查（T21(race-M5) 后置：移到 resolve_tenant 凭证校验之后——
        # 未认证洪水不再写限流键（与 logistics/analytics 端点 T6-T10 后的
        # 「鉴权先行」次序对齐）；键仍按原始 token（含 sk- 前缀）。保持在余额
        # 检查之前，超限 token 不会打到 MXOU 外部接口）
        allowed, remaining = rate_limiter.check(raw_token)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute"
            )

        balance, has_quota = _check_mxou_balance({"key": token, "user_id": user_id})
        if not has_quota:
            # v0.64.1 B3: 402 文案带来源标识便于定位误报（订阅 0 哨兵 vs 真欠费 vs
            # Supabase 兜底）。真欠费（MXOU 实查负数）保持原文案与拒绝行为完全不变。
            if isinstance(balance, (int, float)) and balance < 0:
                _msg = f"MXOU 余额不足 (current: {balance}). 请充值"
            else:
                _src = _balance_source_label({"key": token, "user_id": user_id}, balance)
                _msg = (
                    f"MXOU 余额不足 (current: {balance}). 请充值 "
                    f"(source: {_src}, raw_balance: {balance})"
                )
            return error_response(
                WorkerErrorCode.INSUFFICIENT_BALANCE,
                _msg,
            )
        
        # ✅ Step3: 提交任务到队列（使用user_id作为tenant_id）
        # ✅ v0.75 C7: priority 开放（BL-25 Phase 2-5）——读请求体可选 priority
        # （顶层 body，与 timeout_seconds/max_retries 同位同读法）。缺省/非数字 → 0
        # （容错与该端点其余数值字段一致，不 422 不 500）；越界 clamp [0,100]
        # （对齐本端点 docstring 口径）。认领 SQL 零改动（task_processor
        # ORDER BY priority DESC, created_at ASC 已就绪）。
        try:
            priority = min(100, max(0, int(body.get("priority", 0) or 0)))
        except (TypeError, ValueError):
            priority = 0
        timeout_seconds = body.get("timeout_seconds", 1800)
        max_retries = body.get("max_retries", 3)

        # ✅ Step3.5: 检查 Ozon 店铺配额（提前拒绝，避免浪费 MXOU 生图/LLM 额度）
        if ozon_client_id and ozon_api_key:
            try:
                quota = ozon_check_quota(
                    client_id=ozon_client_id,
                    api_key=ozon_api_key,
                    timeout=5,  # submit 阶段只做快速检查
                )
                if not quota["ok"]:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"Ozon 店铺配额不足: "
                            f"日创建 {quota['daily_used']}/{quota['daily_limit']}"
                            f", 总产品 {quota['total_used']}/{quota['total_limit']}"
                            f"。请等待配额重置或归档旧产品。"
                        )
                    )
                if quota["remaining_daily"] <= 3:
                    logger.warning(
                        "店铺 %s 创建配额紧张: 日剩余 %d, 总剩余 %d",
                        ozon_client_id,
                        quota["remaining_daily"],
                        quota["remaining_total"],
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("submit_task 阶段配额检查异常（允许继续）: %s", str(e)[:200])
        
        # ✅ Step4: payload中添加user_id（供下游节点使用）
        payload_with_user_id = {
            **payload,
            "user_id": user_id,  # ✅ 添加user_id字段
            "envelope": envelope
        }

        # ✅ P0-1: SKU 级重复提交防护 — 同一用户同一店铺同一商品已有活跃任务则拒绝二次入队
        #   跟卖走 draft.ozon_product_id，1688 走 draft.item_id，兜底 draft.sku_id；
        #   无任何商品 ID 时跳过去重（sku_key 为空）。
        #   ⚠️ v0.38.1: sku_key 加入店铺维度 {user}:{ozon_client_id}:{product}——
        #   修复同用户两店铺提交同款被误拦（N8 多店铺与 N1 去重冲突）。
        _store_dim = str(ozon_client_id or "").strip()
        sku_key = (
            f"{user_id}:{_store_dim}:{str(draft.get('ozon_product_id') or draft.get('item_id') or draft.get('sku_id') or '').strip()}"
            if _store_dim else
            f"{user_id}:{str(draft.get('ozon_product_id') or draft.get('item_id') or draft.get('sku_id') or '').strip()}"
        )
        if sku_key.endswith(":"):
            sku_key = ""
        if sku_key:
            existing = _find_existing_task(user_id, sku_key)
            if existing:
                existing_id, existing_status = existing
                log_task_event(
                    "duplicate_submit_blocked", task_id=existing_id, user_id=user_id,
                    trace_id=trace_id, sku_key=sku_key, status=existing_status,
                )
                return error_response(
                    WorkerErrorCode.DUPLICATE_SUBMIT,
                    f"该商品已在提交队列 (task_id={existing_id})，请勿重复提交",
                    detail={"task_id": existing_id, "status": existing_status},
                )
        
        # F-C06（2026-09-09 审计）：去重 SELECT 与 INSERT 之间的并发窗口由
        # 部分唯一索引 uq_ozon_product_tasks_tenant_sku 兜底——撞索引映射为
        # 干净的 409（此前 IntegrityError 冒泡成 500，客户端重试放大窗口）。
        try:
            task_id = await task_processor.submit_task(
                tenant_id=user_id,
                payload=payload_with_user_id,
                priority=priority,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                sku_key=sku_key,
            )
        except IntegrityError:
            log_task_event("duplicate_submit_blocked", user_id=user_id,
                           trace_id=trace_id, sku_key=sku_key, status="unique_index")
            return error_response(
                WorkerErrorCode.DUPLICATE_SUBMIT,
                "该商品已在提交队列（并发提交命中唯一约束），请勿重复提交",
            )

        # ✅ 更新 trace context + 生命周期日志
        set_trace_context(task_id=task_id, user_id=user_id)
        log_task_event("submitted", task_id=task_id, user_id=user_id,
                       trace_id=trace_id, priority=priority, timeout_seconds=timeout_seconds)

        # ✅ M0.7: 直连提交写 draft_submissions 行（A4：所有任务都有 submission 行）。
        #   非致命——写行失败不阻断任务入队；采集路径由 draft_service 自行写行不在此处。
        _write_direct_submission_row(task_id, user_id, ozon_client_id)

        return {
            "ok": True,
            "task_id": task_id,
            "message": f"Task submitted to queue (user: {user_id}, balance: {balance})"
        }
        
    except HTTPException:
        raise  # 直接抛出HTTP异常
    except Exception as e:
        # T2(api-M1): 500 detail 固定文案（str(e) 可能携带内部信息），异常细节只进日志
        logger.exception("提交任务失败")
        raise HTTPException(status_code=500, detail="Failed to submit task")


# T3(crypto-H1): payload 内可能出现凭证的键名集合（大小写不敏感匹配）。
_PAYLOAD_SECRET_KEYS = frozenset({"token", "ozon_api_key", "api_key", "secret", "password", "client_secret"})


def _redact_payload(payload):
    """T3(crypto-H1): task_status 出口对 payload 做键名级凭证脱敏（深拷贝，不改原 dict）。
    payload JSONB 存提交时 GraphInput 原文（顶层 token / ozon_api_key 明文），原样回显
    即泄漏。覆盖顶层与任意嵌套 dict/list；非字符串值不动（键名命中但值是 dict/list
    → 不替换、继续下钻）；顶层非 dict（None/list/str）原样透传。REST/MCP 同源生效
    （MCP get_task_status 走本进程 REST 回调）。"""
    def walk(obj):
        if isinstance(obj, dict):
            return {k: ("[REDACTED]" if str(k).lower() in _PAYLOAD_SECRET_KEYS and isinstance(v, str) else walk(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj
    return walk(copy.deepcopy(payload))


@app.get("/task_status/{task_id}", responses={
    200: {"content": {"application/json": {"example": {
        # ozon_product_tasks 行 + 终态归位后的 progress（v0.19）
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "tenant_id": "28",
        "status": "running",
        "priority": 0,
        "result": None,
        "error_message": None,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": "2026-09-11T08:00:00+00:00",
        "updated_at": "2026-09-11T08:02:30+00:00",
        "started_at": "2026-09-11T08:01:00+00:00",
        "completed_at": None,
        "timeout_seconds": 1800,
        "progress": {"stage": "image_generation", "percent": 61,
                     "stages_completed": ["auth", "ingest", "category_match",
                                          "pricing", "attributes", "description", "image_generation"],
                     "stages_remaining": ["prepare_ozon_upload", "ozon_validate",
                                          "check_quota", "ozon_upload", "ozon_status", "learning_record"]},
    }}}}, 401: {"model": ErrorBody}, 404: {"model": ErrorBody}})
async def http_task_status(task_id: str, request: Request):
    """
    查询任务状态（含进度信息）

    v0.73: Bearer 鉴权 + 租户校验（``_task_status_guard``）。

    Returns:
        任务详情（包含status、result、error_message、progress等）
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        task_status = await task_processor.get_task_status(task_id)

        if task_status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # ✅ v0.73: 鉴权 + 租户校验（取到 row 后；HTTPException 由下面的
        # except HTTPException 直通，不再被兜底 except 吞成 500）
        _task_status_guard(request, task_status)

        # ✅ v0.19: 终态优先——completed/failed 时 progress 直接归位，
        # 不再显示内存里残留的中间阶段（如 0%/social_proof_gen）
        if task_status.get("status") == "completed":
            task_status["progress"] = {
                "stage": "completed", "percent": 100,
                "stages_completed": STAGE_ORDER, "stages_remaining": []
            }
        elif task_status.get("status") == "failed":
            task_status["progress"] = {
                "stage": "failed", "percent": 100,
                "message": task_status.get("error_message", "")
            }
        elif task_status.get("status") == "rejected":
            # ✅ P0-2: rejected 是终态 — progress 直接归位，指引用户走重新提交
            task_status["progress"] = {
                "stage": "rejected", "percent": 100,
                "message": "审核被拒，可调用 /resubmit_task/{task_id} 重新提交",
            }
        else:
            progress = get_progress(task_id)
            if progress:
                task_status["progress"] = progress

        # ✅ T3(crypto-H1): payload 存提交时 GraphInput 原文（token/ozon_api_key 明文），
        # 出口必须脱敏。旧路径（无 response_model）与 /api/v1 别名共用此组装点，
        # 单点应用即双路径生效；非 dict payload 原样透传。
        if isinstance(task_status.get("payload"), dict):
            task_status["payload"] = _redact_payload(task_status["payload"])

        return task_status

    except HTTPException:
        raise  # v0.73: 404/401/租户 404 直通（此前被吞成 500，与 v1 docs 的 404 约定不符）
    except Exception as e:
        # T2(api-M1 补): 500 detail 固定文案，异常细节只进日志
        logger.exception("查询任务状态失败")
        raise HTTPException(status_code=500, detail="Failed to get task status")


@app.post("/cancel_task/{task_id}", responses={
    200: {"content": {"application/json": {"example": {
        "status": "success",
        "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "message": "Task cancelled successfully",
    }}}}, 401: {"model": ErrorBody}, 404: {"model": ErrorBody},
    409: {"content": {"application/json": {"example": {
        # 不可取消（非 pending/处理异常）→ TASK_NOT_CANCELLABLE 统一错误信封
        "ok": False,
        "error_code": "TASK_NOT_CANCELLABLE",
        "message": "Task 3fa85f64-5717-4562-b3fc-2c963f66afa6 cannot be cancelled (may not in pending status)",
    }}}}})
async def http_cancel_task(task_id: str, request: Request):
    """
    取消任务（仅pending状态的任务可取消）

    T6(api-H2): 补 Bearer 鉴权 + 租户校验（语义与 task_status v0.73 同源，
    复用 ``_task_status_guard``：TASK_STATUS_AUTH=0 应急关 / 无 Bearer 401 /
    跨租户 404 "task not found" 不泄漏存在性 / 老数据无租户宽容放行）。
    修复前匿名持有 task uuid 即可跨租户取消任意 pending 任务（安全探针实证）。

    Returns:
        取消结果
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        # ✅ T6(api-H2): 先查归属（不存在 → 404，与 task_status 同序），再
        # 鉴权 + 租户校验，放行后才执行取消。HTTPException 由下面的
        # except HTTPException 直通，不被兜底 except 吞成 500。
        task_row = await task_processor.fetch_task_owner(task_id)
        if not task_row:
            raise HTTPException(status_code=404, detail="task not found")
        _task_status_guard(request, task_row)

        success = await task_processor.cancel_task(task_id)

        if success:
            return {
                "status": "success",
                "task_id": task_id,
                "message": "Task cancelled successfully"
            }
        else:
            # B4 (2026-09-11 仓库治理, A5 §2 D-01)：不可取消（非 pending /
            # 处理异常）由 200+{status:failed} 改返 409 + TASK_NOT_CANCELLABLE
            # 统一错误信封——原 200 形态客户端无法编程区分成败（该错误码此前零接线）。
            return error_response(
                WorkerErrorCode.TASK_NOT_CANCELLABLE,
                f"Task {task_id} cannot be cancelled (may not in pending status)",
            )

    except HTTPException:
        raise  # T6: 401/404/租户 404 直通（同 http_task_status v0.73 处理）
    except Exception as e:
        # T2(api-M1 补): 500 detail 固定文案，异常细节只进日志
        logger.exception("取消任务失败")
        raise HTTPException(status_code=500, detail="Failed to cancel task")


@app.post("/resubmit_task/{task_id}", responses={
    200: {"content": {"application/json": {"example": {
        "ok": True,
        "task_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "message": "任务 3fa85f64-5717-4562-b3fc-2c963f66afa6 已重新提交（rejected → pending，parent_task_id=3fa85f64-5717-4562-b3fc-2c963f66afa6）",
    }}}}, 402: {"content": {"application/json": {"example": {
        "ok": False,
        "error_code": "INSUFFICIENT_BALANCE",
        "message": "MXOU 余额不足 (current: -5.0). 请充值",
    }}}}, 409: {"content": {"application/json": {"example": {
        "ok": False,
        "error_code": "TASK_NOT_RESUBMITTABLE",
        "message": "任务状态 completed 不可重新提交，仅 rejected/failed 终态任务可重试",
        "detail": {"task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "status": "completed"},
    }}}}})
async def http_resubmit_task(task_id: str, request: Request):
    """重新提交终态任务（审核被拒/失败自动修复链入口，P0-2）。

    仅 rejected/failed 终态任务可重试：复制原载荷 → 注入 parent_task_id +
    extensions.image_regen=True → 重新入队（pending）。返回新任务 task_id。

    ⚠️ v0.38.1 安全修复：请求体必须携带调用者 token（与 submit_task 一致），
    校验 token 归属租户 == 任务 tenant_id，防跨租户凭证重放（CRITICAL）。

    ⚠️ race-L1（v0.76 Task 26）：与 submit_task 同款两段——入队前
    _check_mxou_balance 余额预检（欠费 → 402，重提交重跑生图/LLM 同样烧额度）；
    入队 IntegrityError（并发撞部分唯一索引）→ 干净 409 DUPLICATE_SUBMIT
    （此前冒泡成 500）。
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        body = await request.json()
        token = (body.get("payload") or body).get("token", "")
        caller_user_id = _authenticate_token(token)

        task_status = await task_processor.get_task_status(task_id)
        if task_status is None:
            return error_response(
                WorkerErrorCode.TASK_NOT_FOUND,
                f"Task {task_id} not found",
            )

        task_tenant = str(task_status.get("tenant_id") or "")
        if task_tenant and caller_user_id != "local_dev" and task_tenant != caller_user_id:
            return error_response(
                WorkerErrorCode.TASK_NOT_FOUND,
                f"Task {task_id} not found",
            )

        status = str(task_status.get("status") or "")
        if status not in ("rejected", "failed"):
            return error_response(
                WorkerErrorCode.TASK_NOT_RESUBMITTABLE,
                f"任务状态 {status} 不可重新提交，仅 rejected/failed 终态任务可重试",
                detail={"task_id": task_id, "status": status},
            )

        # race-L1（v0.76 Task 26）: 余额预检对齐 submit_task——重提交重跑生图/LLM
        # 同样烧 MXOU 额度，欠费 token 不得借重提交绕过 402。在鉴权+归属+状态
        # 校验之后、入队之前执行；key 剥 sk- 前缀 + user_id=caller 租户，与
        # submit_task 同参形态。402 文案逐字同款（含 v0.64.1 B3 来源标识）。
        _balance_key = token[3:] if token.startswith("sk-") else token
        balance, has_quota = _check_mxou_balance({"key": _balance_key, "user_id": caller_user_id})
        if not has_quota:
            if isinstance(balance, (int, float)) and balance < 0:
                _msg = f"MXOU 余额不足 (current: {balance}). 请充值"
            else:
                _src = _balance_source_label({"key": _balance_key, "user_id": caller_user_id}, balance)
                _msg = (
                    f"MXOU 余额不足 (current: {balance}). 请充值 "
                    f"(source: {_src}, raw_balance: {balance})"
                )
            return error_response(
                WorkerErrorCode.INSUFFICIENT_BALANCE,
                _msg,
            )

        # 深拷贝原载荷（避免改到 DB 返回的原始 dict），注入重提交标记
        payload = copy.deepcopy(task_status.get("payload") or {})
        payload["parent_task_id"] = task_id
        envelope = payload.get("envelope")
        if not isinstance(envelope, dict):
            envelope = {}
        extensions = envelope.get("extensions")
        if not isinstance(extensions, dict):
            extensions = {}
        extensions["image_regen"] = True
        envelope["extensions"] = extensions
        payload["envelope"] = envelope

        # sku_key 从原载荷 draft 派生（与 submit_task 去重口径一致，含店铺维度）
        draft = envelope.get("draft") or {}
        product_id = str(
            draft.get("ozon_product_id") or draft.get("item_id") or draft.get("sku_id") or ""
        ).strip()
        tenant_id = str(task_status.get("tenant_id") or "local_dev")
        _store_dim = str((payload or {}).get("ozon_client_id") or "").strip()
        sku_key = (
            f"{tenant_id}:{_store_dim}:{product_id}"
            if _store_dim else
            f"{tenant_id}:{product_id}"
        ) if product_id else ""

        # F-C06 同款（race-L1，v0.76 Task 26）: 去重 SELECT 与 INSERT 之间的并发
        # 窗口由部分唯一索引 uq_ozon_product_tasks_tenant_sku 兜底——重提交撞索引
        # 映射为与 submit_task 同款的干净 409（此前 IntegrityError 冒泡进通用
        # except → 500，客户端重试放大窗口）。
        try:
            new_task_id = await task_processor.submit_task(
                tenant_id=tenant_id,
                payload=payload,
                priority=0,
                timeout_seconds=int(task_status.get("timeout_seconds") or 1800),
                max_retries=int(task_status.get("max_retries") or 3),
                sku_key=sku_key,
            )
        except IntegrityError:
            log_task_event("duplicate_submit_blocked", task_id=task_id, user_id=tenant_id,
                           sku_key=sku_key, status="unique_index")
            return error_response(
                WorkerErrorCode.DUPLICATE_SUBMIT,
                "该商品已在提交队列（并发提交命中唯一约束），请勿重复提交",
            )

        log_task_event("resubmitted", task_id=new_task_id, user_id=tenant_id,
                       parent_task_id=task_id, from_status=status)
        return {
            "ok": True,
            "task_id": new_task_id,
            "message": f"任务 {task_id} 已重新提交（{status} → pending，parent_task_id={task_id}）",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resubmit task error: {e}, traceback: {traceback.format_exc()}")
        # 不向客户端回传 str(e)（防内部路径/DB 细节泄露，v0.38.1）
        return error_response(
            WorkerErrorCode.INTERNAL_ERROR,
            "重提交任务失败，请稍后重试",
        )


@app.get("/task_statistics", responses={
    200: {"content": {"application/json": {"example": {
        "status": "success",
        "statistics": {
            "total": 130, "pending": 2, "running": 5, "completed": 120,
            "failed": 3, "cancelled": 0, "avg_duration_seconds": 210.55,
        },
    }}}}, 401: {"model": ErrorBody}, 403: {"model": ErrorBody}})
async def http_task_statistics(request: Request):
    """
    获取任务统计信息

    T7(api-H3): 补 Bearer 鉴权 + 租户强制。修复前端点完全无鉴权——匿名可枚举
    任意租户任务量，且不传 tenant_id 时 task_processor 层跨全租户聚合。
    规则（保持 MCP get_task_statistics 兼容，其恒传自己租户）：
    - 无 Authorization Bearer → 401 "Token is required"。
    - Bearer 无效 → ``_verify_analytics_token`` 的 401/503 原样透传。
    - query ``tenant_id`` 缺省/为空/等于自己 → 恒查自己租户。
    - ``tenant_id`` 指向他人租户 → 仅 admin（``resolve_analytics_scope`` 放行），
      否则 403 "admin only"。

    Args:
        tenant_id: 租户ID（可选，缺省=自己租户；指定他人租户需 admin）

    Returns:
        任务统计信息（总数、成功率、平均耗时等）
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    # ✅ T8(api-M2): 鉴权收敛到 ``_require_bearer``（T7 内联三行 → 共享 helper；
    # resolve_tenant 内部自剥 sk-，helper 返回的 clean token 直传等价）。仍在
    # try 外——401/403 是 HTTPException，不会被下面的兜底 except 吞成 500
    # （同 http_task_status 口径）。
    own_token = _require_bearer(request)
    from services.tenant_service import resolve_tenant
    own_tenant = resolve_tenant(own_token)
    q_tenant = request.query_params.get("tenant_id") or ""
    tenant = own_tenant
    if q_tenant and q_tenant != own_tenant:
        scope = resolve_analytics_scope(own_token)
        if not scope.get("is_admin"):
            raise HTTPException(status_code=403, detail="admin only")
        tenant = q_tenant

    try:
        statistics = await task_processor.get_task_statistics(tenant)

        return {
            "status": "success",
            "statistics": statistics
        }

    except HTTPException:
        raise  # T7: 401/403/租户语义 HTTPException 直通（同 http_task_status v0.73 处理）
    except Exception as e:
        # T2(api-M1 补): 500 detail 固定文案，异常细节只进日志
        logger.exception("查询任务统计失败")
        raise HTTPException(status_code=500, detail="Failed to get task statistics")


@app.get(path="/graph_parameter", responses={
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


@app.post("/api/v1/logistics/quote", responses={
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

@v1.get("/health", response_model=HealthResponse, tags=["health"])
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


@v1.post("/submit_task", response_model=SubmitTaskResponse, tags=["task"],
         responses={401: {"model": ErrorBody}, 402: {"model": ErrorBody},
                    403: {"model": ErrorBody}, 429: {"model": ErrorBody}},
         # 处理函数手读 raw Request（兼容 body.payload 包装），FastAPI 推不出请求体；
         # 这里只补 OpenAPI 元数据，让 /docs 与 API-REFERENCE 能展示 SubmitTaskRequest。
         openapi_extra={"requestBody": {"required": True, "content": {
             "application/json": {"schema": SubmitTaskRequest.model_json_schema()}}}})
async def v1_submit_task(request: Request):
    """提交任务到队列。鉴权通过 Supabase tokens 表校验。"""
    # 委托给现有实现
    return await http_submit_task(request)


@v1.get("/task_status/{task_id}", response_model=TaskStatusResponse, tags=["task"],
        responses={401: {"model": ErrorBody}, 404: {"model": ErrorBody}})
async def v1_task_status(task_id: str, request: Request):
    """查询任务状态（v0.73: Bearer 鉴权 + 租户校验，TASK_STATUS_AUTH=0 应急关）。"""
    return await http_task_status(task_id, request)


@v1.post("/cancel_task/{task_id}", response_model=CancelTaskResponse, tags=["task"],
         responses={401: {"model": ErrorBody}, 404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def v1_cancel_task(task_id: str, request: Request):
    """取消待处理的任务（v0.76 T6: Bearer 鉴权 + 租户校验，TASK_STATUS_AUTH=0 应急关）。"""
    return await http_cancel_task(task_id, request)


@v1.post("/resubmit_task/{task_id}", response_model=SubmitTaskResponse, tags=["task"],
         responses={402: {"model": ErrorBody}, 404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def v1_resubmit_task(task_id: str, request: Request):
    """重新提交被拒(rejected)/失败(failed)的任务（P0-2 自动修复链入口；race-L1: 补余额预检 402 + 并发 IntegrityError 409）。"""
    return await http_resubmit_task(task_id, request)


@v1.get("/task_statistics", response_model=TaskStatisticsResponse, tags=["task"],
        responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}})
async def v1_task_statistics(request: Request):
    """获取任务统计信息（v0.76 T7: Bearer 必填 + 租户强制，tenant_id 缺省=查自己，
    跨租户仅 admin——语义与旧路径同源）。

    ⚠️ v0.19.2: 旧路径返回 {"status","statistics"} 包裹结构（无 response_model），
    v1 声明了 TaskStatisticsResponse 响应模型，必须解包 statistics 再返回，
    否则字段对不上被 Pydantic 填默认值 → 统计恒 0。
    """
    resp = await http_task_statistics(request)
    return resp.get("statistics", {})


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


@v1.post("/analytics/queries", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_analytics_queries(request: Request):
    """skill what-to-sell all-queries 关键词蓝海数据上报（去重键 query+token，重复上报 upsert 更新）。"""
    return await _handle_analytics_report(request, "queries")


@v1.post("/analytics/ozon-bestsellers", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_analytics_ozon_bestsellers(request: Request):
    """skill ozon-bestsellers 榜单数据上报（去重键 sku_or_id+token）。"""
    return await _handle_analytics_report(request, "ozon-bestsellers")


@v1.post("/analytics/market-bestsellers", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_analytics_market_bestsellers(request: Request):
    """skill market-bestsellers 全平台榜单数据上报（去重键 product_name+token）。"""
    return await _handle_analytics_report(request, "market-bestsellers")


@v1.post("/discovery/runs", response_model=AnalyticsReportResponse, tags=["analytics"])
async def v1_discovery_report_run(request: Request):
    """discover 选品结果归档（W10 D12）：单次上报一条 run（keyword+filters+candidates），按 tenant 隔离。"""
    return await _handle_analytics_report(request, "discovery_runs")


@v1.get("/analytics/bestsellers", tags=["analytics"], responses={
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


@v1.get("/discovery/runs", tags=["analytics"], responses={
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


@v1.get("/discovery/runs/{session_run_id}", tags=["analytics"],
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


@v1.get("/mappings/lookup", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "found": True,
        "mappings": [{"dc": "91936", "tp": "91540", "confidence": 0.85}],
    }}}},
})
async def v1_mappings_lookup(request: Request):
    """类目映射查询（W11）：skill 端按关键词查已学习 Ozon 类目映射。

    query: keyword（1688 中文类目名）→ {found, mappings: [{dc, tp, confidence}]}
    复用 category_mapping_learn.lookup_mapping（精确 leaf/ID，成功数+置信门槛）；
    未命中时按 source_keywords 重叠兜底（ozon_category_query 同表同门槛）。
    category_mapping 表全局共享（无 tenant 隔离——类目映射是平台级知识，PRD §3.3）。
    """
    # B 租户 guard 二期：Bearer 提取→verify（无限流，对齐原内联序列）收敛单行；
    # 全局共享无租户过滤 → verify_bearer（不加 resolve_tenant，行为逐字等价）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=False)

    keyword = (request.query_params.get("keyword") or "").strip()
    if not keyword:
        return {"found": False, "mappings": []}

    from utils.category_mapping_learn import lookup_mapping, MIN_SUCCESS_COUNT, MIN_CONFIDENCE
    result = lookup_mapping(leaf_name=keyword)
    if result:
        return {"found": True, "mappings": [result]}

    from utils.ozon_category_query import OzonCategoryQuery
    rows = OzonCategoryQuery().get_category_mapping_by_keywords([keyword], min_overlap=1, top_k=10)
    mappings = []
    for r in rows:
        if (r.get("success_count") or 0) < MIN_SUCCESS_COUNT or (r.get("confidence") or 0) < MIN_CONFIDENCE:
            continue
        mappings.append({
            "dc": str(r["description_category_id"]),
            "tp": str(r["type_id"]),
            "confidence": float(r.get("confidence") or 0.7),
        })
    return {"found": bool(mappings), "mappings": mappings}


# ==================== 类目/属性只读端点（v0.70 采集箱手工改配） ====================
# GET /api/v1/categories/search?q=  与  GET /api/v1/categories/attributes?dc=&tp=[&attr_id=]
# webui 采集箱「类目选择器 + 属性表单」的数据源：用户在草稿上指定 dc/tp（写
# draft.ozon_category.source=manual）+ 属性值（draft.attributes），worker 侧按
# 权威直通上传（assemble `_is_skill_authoritative` manual 权威，test_manual_category_
# authority_v070 锁定）。两个端点全局只读（类目树/缓存表无租户数据），鉴权与
# analytics 读端点同源。
# ⚠️ v0.71 红线修订（原 v0.70「缓存只读不回源」）：本地实证 7992 类目对 vs
# attribute_cache 12 行——只读缓存使「选类目必出属性表单」在 99.8% 类目上不成立。
# 改为与管线懒加载同语义的交互版（services/category_schema_service.py）：缓存
# 优先 → 未命中用租户默认店铺凭证按需拉一次 Ozon → 回写 30d → 失败降级
# found=False+reason（前端提示，不破坏页面）。字典值按单属性按需（?attr_id=，
# 下拉打开时拉，翻页≤3 页），首屏保持 1 次 API。

@app.get("/categories/search", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "items": [{
            "description_category_id": "91936",
            "type_id": "91938",
            "node_name": "Автомобильный компрессор",
            "category_path": "Авто и мото / Автоинструменты / Автомобильный компрессор",
            "similarity": 0.87,
        }],
    }}}}})
@app.get("/api/v1/categories/search", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "items": [{
            "description_category_id": "91936",
            "type_id": "91938",
            "node_name": "Автомобильный компрессор",
            "category_path": "Авто и мото / Автоинструменты / Автомобильный компрессор",
            "similarity": 0.87,
        }],
    }}}}})
async def v1_categories_search(request: Request):
    """类目树搜索（ZH_HANS）：?q=关键词&limit=20 → 候选 {dc, tp, node_name, category_path}。

    复用 OzonCategoryQuery.search_nodes（jieba 分词 + LIKE，node_type=type 保证
    返回有效 dc/tp 组合）。供 webui 采集箱 manual 类目选择器 / agent 类目确认。
    """
    # B 租户 guard 二期：Bearer 提取→verify→限流 三段内联收敛单行（含限流对齐
    # 原序列）；无租户消费 → verify_bearer（不加 resolve_tenant，行为逐字等价）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=True)

    q_text = (request.query_params.get("q") or "").strip()
    if not q_text:
        return {"items": []}
    try:
        top_k = max(1, min(int(request.query_params.get("limit", 20)), 50))
    except (TypeError, ValueError):
        top_k = 20
    from utils.ozon_category_query import get_category_query
    try:
        rows = get_category_query().search_nodes(
            q_text, top_k=top_k, node_type="type", language="ZH_HANS")
    except Exception as exc:
        # T2(api-M1 补): 503 固定文案，异常细节只进日志
        logger.warning("category tree unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="category tree temporarily unavailable")
    return {"items": [{
        "description_category_id": str(r.get("description_category_id", "") or ""),
        "type_id": str(r.get("type_id", "") or ""),
        "node_name": str(r.get("node_name", "") or ""),
        "category_path": str(r.get("full_path", "") or ""),
        "similarity": float(r.get("similarity", 0) or 0),
    } for r in rows]}


@app.get("/categories/attributes", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        # schema 模式（?dc=&tp=）；?attr_id= 时 values 为顶层键
        "found": True,
        "cached": True,
        "fetched": False,
        "attributes": [{
            "id": 4180,
            "name": "Тип",
            "required": True,
            "type": "String",
            "dictionary_id": 0,
            "is_collection": False,
            "max_value_count": 1,
        }],
    }}}}})
@app.get("/api/v1/categories/attributes", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "found": True,
        "cached": True,
        "fetched": False,
        "attributes": [{
            "id": 4180,
            "name": "Тип",
            "required": True,
            "type": "String",
            "dictionary_id": 0,
            "is_collection": False,
            "max_value_count": 1,
        }],
    }}}}})
async def v1_categories_attributes(request: Request):
    """类目属性 schema + 字典值（缓存优先，未命中按需拉取回写）。

    - ?dc=&tp= → {found, cached, fetched, attributes}；属性键形状与 assemble
      消费一致（id/dictionary_id/name/required/type）+ is_collection/
      max_value_count（值数出口闸同源字段，UI 提示多值/上限）。
    - ?dc=&tp=&attr_id= → 单属性字典值按需拉取 {found, cached, fetched, values}
      （表单下拉打开时调用，避免一个类目几十个字典属性打满首屏）。
    - 未命中且无店铺凭证/拉取失败 → found=False + reason（降级不抛错）。
    """
    # B 租户 guard 二期：Bearer 提取→verify→限流 三段内联收敛单行（原序列位，
    # 鉴权先于参数校验）；无租户列消费但需租户取凭证 → 三段 helper。
    from api.deps_tenant import verify_bearer_from_request
    clean_token = verify_bearer_from_request(request, rate_limited=True)

    dc = (request.query_params.get("dc") or "").strip()
    tp = (request.query_params.get("tp") or "").strip()
    if not dc.isdigit() or not tp.isdigit():
        raise HTTPException(status_code=422, detail="query params dc/tp must be numeric")
    attr_id = (request.query_params.get("attr_id") or "").strip()

    # 按需拉取凭证：租户默认店铺 → 任一 active 店铺回退（schema 与店铺无关，
    # 无默认店铺也应能拉）；两者皆无 → 纯缓存语义，降级提示
    # v0.73 租户统一：凭证按真实 user_id 取（哈希租户 → 懒拉永远无凭证）。
    # 在 try 外调用——resolve_tenant 的 fail-closed（401/503）是鉴权语义，
    # 不得被下方「凭证解析失败降级纯缓存」吞掉。
    # resolve_tenant 保持原位（dc/tp 422 校验之后）；入参 clean token 与原 raw
    # token 等价（resolve_tenant 内部自剥 sk-，缓存键同形）。
    from services.tenant_service import resolve_tenant
    _tenant = resolve_tenant(clean_token)
    _client_id = _api_key = ""
    try:
        from services.credential_service import get_default_credential, list_credentials
        _cred = get_default_credential(_tenant)
        if _cred:
            _client_id = str(_cred.get("ozon_client_id") or "")
            _api_key = str(_cred.get("api_key") or "")
        else:
            for _c in (list_credentials(_tenant) or []):
                if str(_c.get("status") or "") != "active":
                    continue
                try:
                    from services.credential_service import get_decrypted
                    _client_id, _api_key = get_decrypted(_tenant, str(_c["id"]))
                    break
                except Exception:
                    continue
    except Exception as _cred_e:
        logger.debug("categories/attributes 凭证解析失败（降级纯缓存）: %s", _cred_e)

    from services.category_schema_service import (
        get_attribute_values_with_lazy_fetch,
        get_attributes_with_lazy_fetch,
    )

    # ── 单属性字典值按需（?attr_id=）──
    if attr_id:
        if not attr_id.isdigit():
            raise HTTPException(status_code=422, detail="query param attr_id must be numeric")
        try:
            import asyncio as _asyncio
            res = await _asyncio.to_thread(
                get_attribute_values_with_lazy_fetch,
                int(attr_id), int(dc), int(tp), _client_id, _api_key,
            )
        except Exception as exc:
            # T2(api-M1 补): 503 固定文案，异常细节只进日志
            logger.warning("attribute values unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="attribute values temporarily unavailable")
        return {
            "found": bool(res.get("found")),
            "cached": bool(res.get("cached")),
            "fetched": bool(res.get("fetched")),
            "values": res.get("values") or [],
            **({"reason": res["reason"]} if res.get("reason") else {}),
        }

    # ── schema（缓存优先 → 按需拉取回写）──
    try:
        import asyncio as _asyncio
        res = await _asyncio.to_thread(
            get_attributes_with_lazy_fetch, int(dc), int(tp), _client_id, _api_key,
        )
    except Exception as exc:
        # T2(api-M1 补): 503 固定文案，异常细节只进日志
        logger.warning("attribute cache unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="attribute cache temporarily unavailable")
    if not res.get("found"):
        out_fail = {"found": False, "cached": False, "attributes": []}
        if res.get("reason"):
            out_fail["reason"] = res["reason"]
        return out_fail
    out: list[dict] = []
    from utils.ozon_category_query import get_category_query
    for a in (res.get("schema") or [])[:200]:
        if not isinstance(a, dict):
            continue
        attr_id_int = int(a.get("id") or a.get("description_attribute_id") or 0)
        item = {
            "id": attr_id_int,
            "name": str(a.get("name", "") or ""),
            # ⚠️ Ozon 原始字段是 is_required（warm/懒加载存原始响应）——
            # 此前读 a.get("required") 恒 False（webui 必填标记一直没生效）
            "required": bool(a.get("is_required", a.get("required", False))),
            "type": str(a.get("type", "") or ""),
            "dictionary_id": int(a.get("dictionary_id", 0) or 0),
            "is_collection": bool(a.get("is_collection", False)),
            "max_value_count": int(a.get("max_value_count", 0) or 0),
        }
        if item["dictionary_id"] > 0 and attr_id_int > 0:
            try:
                vals = get_category_query().get_dictionary_values(attr_id_int, int(dc), int(tp))
            except Exception:
                vals = None
            if vals:
                src_vals = vals.get("result") if isinstance(vals, dict) else vals
                item["values"] = [{"id": v.get("id"), "value": v.get("value")}
                                  for v in (src_vals or [])[:100]
                                  if isinstance(v, dict) and v.get("id")]
        out.append(item)
    return {"found": True, "cached": bool(res.get("cached")),
            "fetched": bool(res.get("fetched")), "attributes": out}


# ==================== 佣金查询端点（任务 2.1） ====================
# GET /api/v1/commissions/lookup?category_id={description_category_id}（+ legacy /commissions/lookup 双挂）
# 读 category_commission 缓存表（全局共享，无 tenant 隔离——类目佣金是平台级知识）。
# 鉴权与 analytics 读端点一致（Bearer token → _verify_analytics_token + rate_limiter）。

@app.get("/commissions/lookup", tags=["commissions"], responses={
    200: {"content": {"application/json": {"example": {
        # 未命中 → {"found": false}
        "found": True,
        "fbs": {"leq_1500": 0.16, "leq_5000": 0.12, "gt_5000": 0.10},
        "fbo": {"leq_1500": 0.14, "leq_5000": 0.11, "gt_5000": 0.09},
        "source": "prices_api",
    }}}}})
@app.get("/api/v1/commissions/lookup", tags=["commissions"], responses={
    200: {"content": {"application/json": {"example": {
        "found": True,
        "fbs": {"leq_1500": 0.16, "leq_5000": 0.12, "gt_5000": 0.10},
        "fbo": {"leq_1500": 0.14, "leq_5000": 0.11, "gt_5000": 0.09},
        "source": "prices_api",
    }}}}})
async def http_commissions_lookup(request: Request):
    """类目佣金查询：按 description_category_id 查 category_commission 缓存表。

    query: category_id（Ozon 类目 ID，必填整数）
    → 命中  {"found": true, "fbs": {"leq_1500","leq_5000","gt_5000"}, "fbo": {...}, "source"}
    → 未命中 {"found": false}
    鉴权: Authorization: Bearer <token>（剥离 sk- 前缀）；Supabase 未配置 → 本地放行。
    """
    # B 租户 guard 二期：Bearer 提取→verify→限流 三段内联收敛单行（含限流对齐
    # 原序列）；全局共享缓存表无租户消费 → verify_bearer（不加 resolve_tenant）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=True)

    raw = (request.query_params.get("category_id") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="category_id is required")
    try:
        category_id = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="category_id must be an integer")

    from utils.commission_resolver import get_category_commission

    row = get_category_commission(category_id)
    if row is None:
        return {"found": False}
    return {
        "found": True,
        "fbs": {
            "leq_1500": row.get("fbs_leq_1500"),
            "leq_5000": row.get("fbs_leq_5000"),
            "gt_5000": row.get("fbs_gt_5000"),
        },
        "fbo": {
            "leq_1500": row.get("fbo_leq_1500"),
            "leq_5000": row.get("fbo_leq_5000"),
            "gt_5000": row.get("fbo_gt_5000"),
        },
        "source": row.get("source"),
    }


# ── WebUI 凭证端点（T5）：routes/services 分层，业务逻辑在 services/credential_service.py ──
from routes.credentials_routes import router as credentials_router
v1.include_router(credentials_router)

# B4 租户 guard 一期试点（design-b2b-tenant-guard Phase 1）：
# error_reports/forensics 三端点自本文件内联迁至 routes/error_reports_routes.py，
# 鉴权四段内联替换为 Depends(get_tenant)（行为等价，见该文件头说明）。
from routes.error_reports_routes import (
    router as error_reports_router,
    root_router as error_reports_root_router,
)
v1.include_router(error_reports_router)
app.include_router(error_reports_root_router)

# ── 货源匹配上报（M5b）：skill 图搜/跟卖结果 → source_candidates ──
from routes.source_candidates_routes import router as source_candidates_router
v1.include_router(source_candidates_router)

# ── 用户设置 / 工作台聚合(上生产前演示清零)──
from routes.settings_routes import router as settings_router
v1.include_router(settings_router)
from routes.dashboard_routes import router as dashboard_router
v1.include_router(dashboard_router)

# ── WebUI 上架配置模板端点（P0-1）：routes/services 分层，业务逻辑在 services/template_service.py ──
from routes.templates_routes import router as templates_router
v1.include_router(templates_router)

# ── WebUI 订单端点（P0-4）：routes/services 分层，业务逻辑在 services/order_service.py ──
from routes.orders_routes import router as orders_router
v1.include_router(orders_router)
# ── WebUI 管理员面板端点（v0.51）：routes/services 分层，业务逻辑在 services/admin_service.py ──
from routes.admin_routes import router as admin_router
v1.include_router(admin_router)

# ── WebUI 生图工作台端点（T7a）：生图缓存版本化 + 强制重生成 ──
from routes.images_routes import router as images_router
v1.include_router(images_router)


# ── WebUI 任务列表端点（T8）：routes/services 分层，业务逻辑在 services/task_service.py ──
from routes.tasks_routes import router as tasks_router
v1.include_router(tasks_router)

# ── WebUI 草稿端点（T6 采集箱 CRUD/submit + T14b AI 字段）：routes/services 分层 ──
from routes.drafts_routes import router as drafts_router
app.include_router(drafts_router)

# ── WebUI 草稿预估售价端点（M1.2）：routes/services 分层，定价公式在 utils/pricing_estimate.py 单处定义 ──
from routes.estimate_routes import router as estimate_router
app.include_router(estimate_router)
# P2a 独立定价器（无 draft_id）：POST /api/v1/estimate
from routes.estimate_routes import router_estimate
app.include_router(router_estimate)

# ── WebUI MXOU 登录端点（T2）：routes/services 分层，业务逻辑在 services/mxou_login_service.py ──
# 唯一无 token 鉴权端点（登录入口本身），防爆破在端点层按 username 限流
from routes.mxou_routes import router as mxou_router
app.include_router(mxou_router)

# ── WebUI 在线商品更新端点（T14 改图全量重传）：routes/services 分层，业务逻辑在 services/image_service.py ──
from routes.products_routes import router as products_router
v1.include_router(products_router)

# ── WebUI 在售商品列表端点（M2.1）：routes/services 分层，业务逻辑在 services/shelf_service.py ──
from routes.shelf_routes import router as shelf_router
v1.include_router(shelf_router)

# ── 店铺数据同步（v0.56）：手动同步 + 同步状态 ──
from routes.store_sync_routes import detail_router as store_sync_detail_router
from routes.store_sync_routes import router as store_sync_router
from routes.tasks_routes import sse_router as task_sse_router
v1.include_router(store_sync_router)
v1.include_router(store_sync_detail_router)
v1.include_router(task_sse_router)

# ── 店铺执行端点（todo 7）：改价/库存/上下架 + 营销活动（接线 store_operation_log）──
from routes.store_actions_routes import router as store_actions_router
v1.include_router(store_actions_router)

# ── 系统设置：站点运营（v0.55）：站点 Banner/通告 管理（仅管理员）──
from routes.admin_site_routes import router as admin_site_router
v1.include_router(admin_site_router)

# ── 系统设置：站点公开端点（v0.55）：Banner/通告 只读公开 ──
from routes.site_public_routes import router as site_public_router
v1.include_router(site_public_router)

# ── 系统设置：引擎配置（v0.55）：提示词编辑/运费费率/选品库（仅管理员）──
from routes.admin_config_routes import router as admin_config_router
v1.include_router(admin_config_router)
from routes.admin_logistics_routes import router as admin_logistics_router
v1.include_router(admin_logistics_router)
from routes.admin_queries_routes import router as admin_queries_router
v1.include_router(admin_queries_router)

# ── SEO 流量关键词公开读端点（v0.59+）：what-to-sell 流量关键词只读消费（Bearer + RateLimiter）──
from routes.seo_keywords_routes import router as seo_keywords_router
v1.include_router(seo_keywords_router)

from routes.admin_categories_routes import router as admin_categories_router
app.include_router(admin_categories_router)
from routes.admin_data_sources_routes import router as admin_data_sources_router
app.include_router(admin_data_sources_router)
from routes.admin_audit_routes import router as admin_audit_router
app.include_router(admin_audit_router)

# ── Batch 5: Analytics aggregation endpoints ──
from routes.analytics_routes import router as analytics_agg_router
v1.include_router(analytics_agg_router)

# ── Batch 5: Image tasks CRUD ──
from routes.image_tasks_routes import router as image_tasks_router
v1.include_router(image_tasks_router)


# 注册 v1 路由（/api/v1/* 端点）
# 旧路径（/health, /submit_task 等）仍然可用，向后兼容
app.include_router(v1)

# ── New API 通用代理（v0.55.1）：webui 同源 /api/* → api.mxou.cn（登录/订阅/钱包） ──
# catch-all 必须在 v1 具体路由之后注册，否则吞掉 /api/v1
from routes.newapi_proxy_routes import router as newapi_proxy_router
app.include_router(newapi_proxy_router)


# ── WebUI SPA 静态托管（/app，archive/docs/legacy/PLAN-webui-v1.md §1.4 T4） ──
# dist 默认 webui/dist（env WEBUI_DIST 覆盖）；未构建时跳过挂载不阻断 worker。
# SPA fallback：非静态文件路径回 index.html（前端路由直连/刷新不 404），
# 仅允许 dist 目录内的文件（防路径穿越）。
# v0.62.1 P1-5: 用 APP_WORKSPACE_PATH 拼接（容器内外语义一致），
# 不再依赖 __file__ 三次 dirname（宿主/容器路径差异会导致算出错误默认值）。
_WEBUI_DIST_DEFAULT = os.path.normpath(os.path.join(
    os.environ.get("APP_WORKSPACE_PATH") or os.getcwd(),
    "webui", "dist",
))
WEBUI_DIST = os.environ.get("WEBUI_DIST", _WEBUI_DIST_DEFAULT)


def _mount_webui_static(app: FastAPI) -> None:
    dist = os.path.realpath(WEBUI_DIST)
    index_file = os.path.join(dist, "index.html")
    if not os.path.isfile(index_file):
        logger.warning(
            "WebUI dist 未构建（%s），/app 挂载跳过 —— 先 cd webui && npm run build", WEBUI_DIST)
        return

    @app.get("/app", include_in_schema=False)
    @app.get("/app/", include_in_schema=False)
    @app.get("/app/{full_path:path}", include_in_schema=False)
    async def spa_serve(full_path: str = ""):
        if full_path:
            candidate = os.path.realpath(os.path.join(dist, full_path))
            if candidate.startswith(dist + os.sep) and os.path.isfile(candidate):
                return FileResponse(candidate)
        return FileResponse(index_file)


_mount_webui_static(app)


def parse_args():
    parser = argparse.ArgumentParser(description="Start FastAPI server")
    parser.add_argument("-m", type=str, default="http", help="Run mode, support http,flow,node")
    parser.add_argument("-n", type=str, default="", help="Node ID for single node run")
    parser.add_argument("-p", type=int, default=5000, help="HTTP server port")
    parser.add_argument("-i", type=str, default="", help="Input JSON string for flow/node mode")
    return parser.parse_args()


def parse_input(input_str: str) -> Dict[str, Any]:
    """Parse input string, support both JSON string and plain text"""
    if not input_str:
        return {"text": "你好"}

    # Try to parse as JSON first
    try:
        return json.loads(input_str)
    except json.JSONDecodeError:
        # If not valid JSON, treat as plain text
        return {"text": input_str}

def start_http_server(port):
    workers = 1
    reload = False
    if graph_helper.is_dev_env():
        reload = True

    logger.info(f"Start HTTP Server, Port: {port}, Workers: {workers}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload, workers=workers)

if __name__ == "__main__":
    args = parse_args()
    if args.m == "http":
        start_http_server(args.p)
    elif args.m == "flow":
        payload = parse_input(args.i)
        result = asyncio.run(service.run(payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "node" and args.n:
        payload = parse_input(args.i)
        result = asyncio.run(service.run_node(args.n, payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "agent":
        agent_ctx = new_context(method="agent")
        for chunk in service.stream(
                {
                    "type": "query",
                    "session_id": "1",
                    "message": "你好",
                    "content": {
                        "query": {
                            "prompt": [
                                {
                                    "type": "text",
                                    "content": {"text": "现在几点了？请调用工具获取当前时间"},
                                }
                            ]
                        }
                    },
                },
                run_config={"configurable": {"session_id": "1"}},
                ctx=agent_ctx,
        ):
            print(chunk)

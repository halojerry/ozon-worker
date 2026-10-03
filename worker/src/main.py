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
from runtime.graph_service import GraphService, service, set_async_graph
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

# R3a: async 图单例已归 runtime/graph_service holder（唯一权威，见 set_async_graph；
# 唯一消费者 routes/ops_routes.py GET /progress/{run_id} 经 get_async_graph() 取）。
# 本名保留为兼容占位（恒 None）——新代码勿读它，用 get_async_graph()。
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
    # R3a: async 图单例归 runtime/graph_service holder（唯一权威；唯一消费者
    # routes/ops_routes.py GET /progress/{run_id} 经 get_async_graph() 取）。
    set_async_graph(base.builder.compile(checkpointer=checkpointer))
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


# ── R3a 路由族归位（2026-09-23）：四族自本文件迁出，注册在原内联定义等价位置 ──
# task 队列 / analytics 上报 / 类目佣金 / ops 四族 → routes/{task_queue,analytics_ingest,catalog,ops}_routes.py。
# 路径/response_model/responses/示例装饰器逐字随迁（openapi 零漂移）；注册一律在
# newapi catch-all /api/* 之前。禁止新增 routes→main 反向依赖（新模块走权威模块）。
from routes.task_queue_routes import router as task_queue_router
from routes.task_queue_routes import v1_router as task_queue_v1_router
from routes.analytics_ingest_routes import router as analytics_ingest_router
from routes.catalog_routes import root_router as catalog_root_router
from routes.catalog_routes import router as catalog_v1_router
from routes.ops_routes import root_router as ops_root_router
from routes.ops_routes import router as ops_v1_router

app.include_router(task_queue_router)       # /submit_task /task_status /cancel_task /resubmit_task /task_statistics（legacy 绝对路径）
app.include_router(ops_root_router)         # /health /api/v1/store/health /auth/verify(双挂) /progress/{run_id} /graph_parameter /api/v1/logistics/quote
app.include_router(catalog_root_router)     # /categories/* /commissions/lookup（legacy + /api/v1 双挂）
app.include_router(task_queue_v1_router)    # /api/v1/submit_task 等任务队列 v1 别名
v1.include_router(analytics_ingest_router)  # /api/v1/analytics/* /api/v1/discovery/runs*
v1.include_router(catalog_v1_router)        # /api/v1/mappings/lookup
v1.include_router(ops_v1_router)            # /api/v1/health


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

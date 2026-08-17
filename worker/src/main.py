import argparse
import asyncio
import contextvars
import copy
import json
import os
import threading
import traceback
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Iterable, AsyncIterable, AsyncGenerator, Optional
import uvicorn
import time
from fastapi import FastAPI, HTTPException, Query, Request, APIRouter
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from api.errors import WorkerErrorCode, error_response
from api.schemas import (
    SubmitTaskRequest, SubmitTaskResponse, TaskStatusResponse,
    CancelTaskResponse, HealthResponse, TaskStatisticsResponse, ErrorBody,
    AuthVerifyResponse, AnalyticsReportResponse,
    BlueOceanQueryItem, OzonBestsellerItem, MarketBestsellerItem,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from storage.database.db import get_session, get_engine, init_db
from storage.database.supabase_client import get_supabase_client
from storage.memory.memory_saver import get_memory_saver
from storage.database.shared.model import (
    Base, BlueOceanQuery, OzonBestseller, MarketBestseller,
)
from utils.task_processor import SupabaseTaskProcessor
from utils.ozon_client import ozon_check_quota  # 配额检查
from utils.draft_sanity import validate_draft_sanity  # v0.21 P2 入队防线
from utils.sentry_setup import init_sentry  # v0.23 Sentry 错误监测
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ✅ v0.23: Sentry 错误监测（SENTRY_DSN 为空则 no-op；HTTP 与 CLI 入口共用）
init_sentry()

# ── 进度追踪（内存存储，重启清空） ──
# 格式: {task_id: {stage, stage_index, total_stages, percent, message, updated_at}}
_task_progress: Dict[str, Dict[str, Any]] = {}
# ✅ v0.29 P0(PRD-cicd-stability): 模块级全局 → contextvars
# 原实现是模块级 global, 注释谎称 "thread-local" —— asyncio 多任务并发时
# set/get 之间被其他协程 set 覆盖 → 日志/进度/Sentry 串号(PRD 复现路径)。
# ContextVar 按任务协程隔离, 子任务自动继承。
_current_task_id: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "current_task_id", default=None
)

# ✅ v0.29(PRD-cicd-stability): 优雅关闭标志
# 收到 SIGTERM/docker stop 后: worker_loop 不再拉新任务 → drain 运行中任务
# (最多 5 分钟) → 超时才 cancel。避免 update.sh --force-recreate 强杀用户任务。
SHUTDOWN_FLAG = False


def request_shutdown() -> None:
    """请求优雅关闭(停止接收新任务)。"""
    global SHUTDOWN_FLAG
    SHUTDOWN_FLAG = True


def is_shutting_down() -> bool:
    """是否正在优雅关闭。"""
    return SHUTDOWN_FLAG


def set_current_task_id(task_id: str | None):
    """设置当前协程正在处理的 task_id（供 ProgressLogger 等模块使用）"""
    _current_task_id.set(task_id)


def get_current_task_id() -> str | None:
    """获取当前协程正在处理的 task_id"""
    return _current_task_id.get()

# 节点执行顺序（用于计算进度百分比）
STAGE_ORDER = [
    "auth", "ingest", "category_match", "pricing", "attributes",
    "description", "image_generation", "prepare_ozon_upload",
    "ozon_validate", "check_quota", "ozon_upload", "ozon_status", "learning_record"
]

# ✅ v0.9: 合并为单一 update_progress（内存 + PG 持久化），避免重复定义


def get_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务进度（内存优先 → PG 回退）"""
    if task_id in _task_progress:
        return _task_progress[task_id]
    # ✅ P1 修复：内存无数据时回退到 PG（重启后仍可读）
    try:
        from storage.database.db import get_session
        from sqlalchemy import text
        session = get_session()
        try:
            row = session.execute(
                text("SELECT progress FROM ozon_product_tasks WHERE id = :tid"),
                {"tid": task_id}
            ).scalar()
            if row:
                return json.loads(row) if isinstance(row, str) else row
        finally:
            session.close()
    except Exception:
        pass
    return None


async def _persist_progress(task_id: str, data: dict):
    """异步写入 PG progress 列"""
    try:
        from storage.database.db import get_session
        from sqlalchemy import text
        session = get_session()
        try:
            session.execute(
                text("UPDATE ozon_product_tasks SET progress = :p, updated_at = NOW() WHERE id = :tid"),
                {"p": json.dumps(data, ensure_ascii=False), "tid": task_id}
            )
            session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.debug("progress persist failed for %s: %s", task_id, e)


# ⚠️ v0.14 E1: 进度写 PG 节流 — 每任务 2s 合并窗口（旧代码每节点异步写一次 PG）
_last_persist_ts: dict = {}
_PERSIST_THROTTLE = 2.0


def _purge_stale_progress():
    """清理 _task_progress 中已完成超过 1 小时的条目（防内存泄漏）"""
    now = time.time()
    stale = [tid for tid, data in list(_task_progress.items())
              if now - data.get("updated_at", 0) > 3600]
    for tid in stale:
        del _task_progress[tid]


def update_progress(task_id: str, stage: str, message: str = ""):
    """更新任务进度（内存 + 异步 PG）"""
    if not task_id:
        return
    stage_idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
    total = len(STAGE_ORDER)
    percent = int((stage_idx / total) * 100)
    data = {
        "stage": stage,
        "stage_index": stage_idx,
        "total_stages": total,
        "percent": percent,
        "message": message,
        "updated_at": time.time(),
        "stages_completed": STAGE_ORDER[:stage_idx],
        "stages_remaining": STAGE_ORDER[stage_idx+1:],
    }
    _task_progress[task_id] = data
    # ✅ P1 修复：异步持久化到 PG（重启后仍可恢复进度）
    # ⚠️ v0.14 E1: 节流 — 同一任务 2s 窗口内跳过 PG 写（内存进度始终最新，PG 低频落盘）
    try:
        now_ts = time.time()
        if now_ts - _last_persist_ts.get(task_id, 0) >= _PERSIST_THROTTLE:
            _last_persist_ts[task_id] = now_ts
            asyncio.create_task(_persist_progress(task_id, data))
    except RuntimeError:
        pass  # 无 event loop 时跳过（同步模式）

# Local runtime utilities (standalone replacements for platform SDK)
from runtime.context import new_context, Context
from runtime.helpers import (
    graph_helper, ErrorClassifier, classify_error,
    AgentStreamRunner, WorkflowStreamRunner,
    agent_stream_handler, workflow_stream_handler, RunOpt,
    to_stream_input, to_client_message,
)
from runtime.log_utils import (
    LOG_FILE, LOG_LEVEL, setup_logging, request_context,
    LangGraphParser, extract_core_stack,
)
from runtime.async_tasks import (
    AsyncTaskRuntime, AsyncTaskStorageError,
    extract_biz_context, parse_deadline_sec,
    config as async_task_config, HEADER_X_RUN_ID as _ASYNC_HEADER_X_RUN_ID,
)
from runtime.openai_handler import OpenAIChatHandler

from utils.logger import setup_structured_logging, get_logger, set_trace_context, log_task_event

# 结构化日志：生产用 JSON，本地开发用可读格式
setup_structured_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_format=os.getenv("LOG_FORMAT", "json").lower() == "json",
    log_file=os.getenv("LOG_FILE", ""),
)

logger = get_logger(__name__)

# 超时配置常量
TIMEOUT_SECONDS = 900  # 15分钟

# API 限流配置
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))  # 每 token 每分钟最大提交数（与 AGENTS.md/.env.example 一致）


class RateLimiter:
    """滑动窗口限流器：按 token 限制提交频率"""

    def __init__(self, max_per_minute: int = RATE_LIMIT_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self._requests: Dict[str, list] = {}  # token → [timestamp, ...]
        self._lock = threading.Lock()

    def check(self, token: str) -> tuple[bool, int]:
        """检查是否允许请求。返回 (allowed, remaining)。"""
        now = time.time()
        window_start = now - 60
        with self._lock:
            timestamps = self._requests.get(token, [])
            # 清理过期记录
            timestamps = [t for t in timestamps if t > window_start]
            if len(timestamps) >= self.max_per_minute:
                self._requests[token] = timestamps
                return False, 0
            timestamps.append(now)
            self._requests[token] = timestamps
            return True, self.max_per_minute - len(timestamps)


rate_limiter = RateLimiter()

class GraphService:
    def __init__(self):
        # 用于跟踪正在运行的任务（使用asyncio.Task）
        self.running_tasks: Dict[str, asyncio.Task] = {}
        # 错误分类器
        self.error_classifier = ErrorClassifier()
        # stream runner
        self._agent_stream_runner = AgentStreamRunner()
        self._workflow_stream_runner = WorkflowStreamRunner()
        self._graph = None
        self._graph_lock = threading.Lock()

    def set_graph(self, graph) -> None:
        """Inject the compiled graph used by sync endpoints. Called once from
        lifespan with a no-checkpointer build, so /run /stream_run /node_run
        never hit the checkpoint DB."""
        self._graph = graph

    def _get_graph(self, ctx=Context):
        if self._graph is not None:
            return self._graph
        with self._graph_lock:
            if self._graph is not None:
                return self._graph
            if graph_helper.is_agent_proj():
                self._graph = graph_helper.get_agent_instance("agents.agent", ctx)
            else:
                self._graph = graph_helper.get_graph_instance("graphs.graph")
            return self._graph

    @staticmethod
    def _sse_event(data: Any, event_id: Any = None) -> str:
        id_line = f"id: {event_id}\n" if event_id else ""
        return f"{id_line}event: message\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    def _get_stream_runner(self):
        if graph_helper.is_agent_proj():
            return self._agent_stream_runner
        else:
            return self._workflow_stream_runner

    # 流式运行（原始迭代器）：本地调用使用
    def stream(self, payload: Dict[str, Any], run_config: RunnableConfig, ctx=Context) -> Iterable[Any]:
        graph = self._get_graph(ctx)
        stream_runner = self._get_stream_runner()
        for chunk in stream_runner.stream(payload, graph, run_config, ctx):
            yield chunk

    # 同步运行：本地/HTTP 通用
    async def run(self, payload: Dict[str, Any], ctx=None) -> Dict[str, Any]:
        if ctx is None:
            ctx = new_context("run")

        run_id = ctx.run_id
        logger.info(f"Starting run with run_id: {run_id}")

        try:
            graph = self._get_graph(ctx)
            run_config: RunnableConfig = {"configurable": {"thread_id": ctx.run_id}}

            # 直接调用，LangGraph会在当前任务上下文中执行
            # 如果当前任务被取消，LangGraph的执行也会被取消
            return await graph.ainvoke(payload, config=run_config, context=ctx)

        except asyncio.CancelledError:
            logger.info(f"Run {run_id} was cancelled")
            return {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        except Exception as e:
            # 使用错误分类器分类错误
            err = self.error_classifier.classify(e, {"node_name": "run", "run_id": run_id})
            # 记录详细的错误信息和堆栈跟踪
            logger.error(
                f"Error in GraphService.run: [{err.code}] {err.message}\n"
                f"Category: {err.category.name}\n"
                f"Traceback:\n{extract_core_stack()}"
            )
            # 保留原始异常堆栈，便于上层返回真正的报错位置
            raise
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)

    # 流式运行（SSE 格式化）：HTTP 路由使用
    async def stream_sse(self, payload: Dict[str, Any], ctx=None, run_opt: Optional[RunOpt] = None) -> AsyncGenerator[str, None]:
        if ctx is None:
            ctx = new_context(method="stream_sse")
        if run_opt is None:
            run_opt = RunOpt()

        run_id = ctx.run_id
        logger.info(f"Starting stream with run_id: {run_id}")
        graph = self._get_graph(ctx)
        run_config: RunnableConfig = {"configurable": {"thread_id": ctx.run_id}}

        is_workflow = not graph_helper.is_agent_proj()

        try:
            async for chunk in self.astream(payload, graph, run_config=run_config, ctx=ctx, run_opt=run_opt):
                if is_workflow and isinstance(chunk, tuple):
                    event_id, data = chunk
                    yield self._sse_event(data, event_id)
                else:
                    yield self._sse_event(chunk)
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)

    # 取消执行 - 使用asyncio的标准方式
    def cancel_run(self, run_id: str, ctx: Optional[Context] = None) -> Dict[str, Any]:
        """
        取消指定run_id的执行

        使用asyncio.Task.cancel()来取消任务,这是标准的Python异步取消机制。
        LangGraph会在节点之间检查CancelledError,实现优雅的取消。
        """
        logger.info(f"Attempting to cancel run_id: {run_id}")

        # 查找对应的任务
        if run_id in self.running_tasks:
            task = self.running_tasks[run_id]
            if not task.done():
                # 使用asyncio的标准取消机制
                # 这会在下一个await点抛出CancelledError
                task.cancel()
                logger.info(f"Cancellation requested for run_id: {run_id}")
                return {
                    "status": "success",
                    "run_id": run_id,
                    "message": "Cancellation signal sent, task will be cancelled at next await point"
                }
            else:
                logger.info(f"Task already completed for run_id: {run_id}")
                return {
                    "status": "already_completed",
                    "run_id": run_id,
                    "message": "Task has already completed"
                }
        else:
            logger.warning(f"No active task found for run_id: {run_id}")
            return {
                "status": "not_found",
                "run_id": run_id,
                "message": "No active task found with this run_id. Task may have already completed or run_id is invalid."
            }

    # 运行指定节点：本地/HTTP 通用
    async def run_node(self, node_id: str, payload: Dict[str, Any], ctx=None, extra_config=None) -> Any:
        if ctx is None or Context.run_id == "":
            ctx = new_context(method="node_run")

        _graph = self._get_graph()
        node_func, input_cls, output_cls = graph_helper.get_graph_node_func_with_inout(_graph.get_graph(), node_id)
        if node_func is None or input_cls is None:
            raise KeyError(f"node_id '{node_id}' not found")

        parser = LangGraphParser(_graph)
        metadata = parser.get_node_metadata(node_id) or {}

        _g = StateGraph(input_cls, input_schema=input_cls, output_schema=output_cls)
        _g.add_node("sn", node_func, metadata=metadata)
        _g.set_entry_point("sn")
        _g.add_edge("sn", END)
        _graph = _g.compile()

        run_config: RunnableConfig = {"configurable": {"thread_id": ctx.run_id}}
        if extra_config:  # v0.41 T7a: regen 端点注入 force_regen/regen_version（合并到 configurable）
            run_config["configurable"].update(extra_config or {})
        return await _graph.ainvoke(payload, config=run_config)

    def graph_inout_schema(self) -> Any:
        if graph_helper.is_agent_proj():
            return {"input_schema": {}, "output_schema": {}}
        builder = getattr(self._get_graph(), 'builder', None)
        if builder is not None:
            input_cls = getattr(builder, 'input_schema', None) or self._get_graph().get_input_schema()
            output_cls = getattr(builder, 'output_schema', None) or self._get_graph().get_output_schema()
        else:
            logger.warning(f"No builder input schema found for graph_inout_schema, using graph input schema instead")
            input_cls = self._get_graph().get_input_schema()
            output_cls = self._get_graph().get_output_schema()

        return {
            "input_schema": input_cls.model_json_schema(), 
            "output_schema": output_cls.model_json_schema(),
            "code":0,
            "msg":""
        }

    async def astream(self, payload: Dict[str, Any], graph: CompiledStateGraph, run_config: RunnableConfig, ctx=Context, run_opt: Optional[RunOpt] = None) -> AsyncIterable[Any]:
        stream_runner = self._get_stream_runner()
        async for chunk in stream_runner.astream(payload, graph, run_config, ctx, run_opt):
            yield chunk


service = GraphService()

async_runtime: Optional[AsyncTaskRuntime] = None
async_graph: Optional[CompiledStateGraph] = None
task_processor: Optional[SupabaseTaskProcessor] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
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
    global async_graph, async_runtime
    async_graph = base.builder.compile(checkpointer=checkpointer)
    service.set_graph(sync_graph)
    async_runtime = AsyncTaskRuntime(
        session_factory=get_session, engine=engine,
        graph=async_graph, checkpointer=checkpointer,
    )
    
    # 启动Supabase任务处理器（最多30个并发任务 — 4核4G 服务器 I/O 密集安全值，外部 API 由全局限流器兜底）
    global task_processor
    max_concurrent = int(os.getenv("MAX_CONCURRENT", "30"))
    task_processor = SupabaseTaskProcessor(max_concurrent=max_concurrent)
    # ✅ 启动时僵尸任务恢复：重置重启前的 running 任务和可重试的 failed 任务
    # ⚠️ v0.30.0:
    #   SKIP_ZOMBIE_RECOVERY=1 — 跳过全部恢复（本地/测试环境必开，防止旧 failed 任务复活真实上架）
    #   SKIP_FAILED_REVIVE=1   — 只跳过 failed→pending 复活，保留 running→pending 恢复（云端推荐：
    #                            部署重启时已失败任务不再重复上架，但中断任务仍可恢复）
    _skip_all = os.getenv("SKIP_ZOMBIE_RECOVERY", "0") == "1"
    _skip_failed = os.getenv("SKIP_FAILED_REVIVE", "0") == "1"
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
                # 2. 重置可重试的 failed 任务（SKIP_FAILED_REVIVE=1 时跳过——防止部署重启复活旧任务重复上架）
                zombie_failed = 0
                if not _skip_failed:
                    zombie_failed = sess.execute(
                    text("UPDATE ozon_product_tasks SET status='pending', retry_count=0, error_message=NULL, updated_at=NOW() WHERE status='failed' AND retry_count < max_retries")
                    ).rowcount
                sess.commit()
                if zombie_running or zombie_running_failed or zombie_failed:
                    logger.info(f"🧹 启动清理: {zombie_running} 个僵尸 running → pending, {zombie_running_failed} 个 running → failed(耗尽), {zombie_failed} 个 failed → pending{'（SKIP_FAILED_REVIVE 跳过复活）' if _skip_failed and zombie_failed == 0 else ''}")
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

    # 启动定时清理任务
    cleanup_task = asyncio.create_task(_periodic_task_cleanup(interval_seconds=60))

    # v0.56 店铺数据自动同步（15min 遍历全部租户 active 凭证；SKIP_STORE_SYNC=1 关闭）
    global store_sync_task
    if os.getenv("SKIP_STORE_SYNC", "0") == "1":
        logger.info("⏸️ 店铺自动同步已关闭（SKIP_STORE_SYNC=1）")
        store_sync_task = None
    else:
        from services.store_sync_scheduler import store_sync_loop
        store_sync_task = asyncio.create_task(store_sync_loop())

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
    
    if async_runtime is not None:
        try:
            await async_runtime.shutdown()
        except AttributeError:
            pass  # shutdown method not available in this version

app = FastAPI(
    lifespan=lifespan,
    title="Ozon Worker API",
    description="Ozon 产品上架 Worker — 接收信封、执行 LangGraph 管线、上传 Ozon",
    version="1.0.0",
)

# ── API v1 路由 ──
v1 = APIRouter(prefix="/api/v1", tags=["v1"])


# OpenAI 兼容接口处理器
openai_handler = OpenAIChatHandler(service)


@app.post("/async_run")
async def http_async_run(request: Request) -> dict:
    """[DEPRECATED] 使用 POST /submit_task 代替。此端点将在未来版本移除。"""
    logger.warning("⚠️ /async_run 已弃用，请使用 POST /submit_task")
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_async_run: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {extract_core_stack()}")
    try:
        deadline_sec = parse_deadline_sec(request.headers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 一个 ID 走到底：task_id == run_id == thread_id == ctx.run_id。
    # 优先用上游 x-run-id；没传就生成 UUID。
    run_id = request.headers.get(_ASYNC_HEADER_X_RUN_ID) or uuid.uuid4().hex

    # ctx 在 handler scope 构造，与同步 /run 路径一致；后面 new_context 默认会
    # 给 run_id 一个新 UUID，同步路径也是显式覆盖（main.py /run 处），这里同理。
    ctx = new_context(method="async_run")
    ctx.run_id = run_id
    request_context.set(ctx)  # 与其他 HTTP endpoint 一致：让日志组件拿到 run_id 等信息
    run_config: RunnableConfig = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": async_task_config.RECURSION_LIMIT,
    }

    biz_context = extract_biz_context(request.headers) or {}
    if graph_helper.is_agent_proj() and not (isinstance(payload, dict) and payload.get("messages")):
        try:
            client_msg, _ = to_client_message(payload)
            payload = to_stream_input(client_msg)
        except Exception as e:
            error_response = service.error_classifier.get_error_response(
                e, {"node_name": "http_async_run", "run_id": run_id})
            logger.error(
                f"failed to convert agent payload in http_async_run: "
                f"[{error_response['error_code']}] {error_response['error_message']}, "
                f"traceback: {traceback.format_exc()}", exc_info=True
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": error_response["error_code"],
                    "error_message": error_response["error_message"],
                    "stack_trace": extract_core_stack(),
                },
            )

    try:
        return await async_runtime.submit(
            task_id=run_id,
            payload=payload,
            biz_context=biz_context,
            deadline_sec=deadline_sec,
            run_config=run_config,
            ctx=ctx,
        )
    except AsyncTaskStorageError as e:
        raise HTTPException(status_code=503,
                            detail=f"async-task storage unavailable: {e}")


@app.get("/task/{task_id}")
async def http_get_task(task_id: str) -> dict:
    """[DEPRECATED] 使用 GET /task_status/{task_id} 代替。此端点将在未来版本移除。"""
    logger.warning("⚠️ /task/{task_id} 已弃用，请使用 GET /task_status/{task_id}")
    try:
        row = await async_runtime.get(task_id)
    except AsyncTaskStorageError as e:
        raise HTTPException(status_code=503,
                            detail=f"async-task storage unavailable: {e}")
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return row


HEADER_X_RUN_ID = "x-run-id"
@app.post("/run")
async def http_run(request: Request) -> Dict[str, Any]:
    global result
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {traceback.format_exc()}, error: {e}")

    # T3 鉴权门：无/空/无效 token → 401，限流超限 → 429
    _authenticate_token(_extract_token_from_body(body_text))

    ctx = new_context(method="run", headers=request.headers)
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    run_id = ctx.run_id
    request_context.set(ctx)

    logger.info(
        f"Received request for /run: "
        f"run_id={run_id}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )

    try:
        payload = await request.json()

        # ✅ P0 修复：/run 同步端点也做 Ozon 配额预检（与 /submit_task 一致）
        try:
            ozon_cid = payload.get("ozon_client_id", "")
            ozon_key = payload.get("ozon_api_key", "")
            if ozon_cid and ozon_key:
                from utils.ozon_client import ozon_check_quota
                quota = ozon_check_quota(client_id=ozon_cid, api_key=ozon_key, timeout=5)
                if not quota.get("ok"):
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "error": "OZON_QUOTA_EXHAUSTED",
                            "message": quota.get("message", "店铺配额已满"),
                            "quota": quota,
                        }
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("配额预检异常，放行继续: %s", e)

        # 创建任务并记录 - 这是关键，让我们可以通过run_id取消任务
        task = asyncio.create_task(service.run(payload, ctx))
        service.running_tasks[run_id] = task

        try:
            result = await asyncio.wait_for(task, timeout=float(TIMEOUT_SECONDS))
        except TimeoutError:
            logger.error(f"Run execution timeout after {TIMEOUT_SECONDS}s for run_id: {run_id}")
            task.cancel()
            try:
                result = await task
            except asyncio.CancelledError:
                return {
                    "status": "timeout",
                    "run_id": run_id,
                    "message": f"Execution timeout: exceeded {TIMEOUT_SECONDS} seconds"
                }

        if not result:
            result = {}
        if isinstance(result, dict):
            result["run_id"] = run_id
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format, {extract_core_stack()}")

    except asyncio.CancelledError:
        logger.info(f"Request cancelled for run_id: {run_id}")
        result = {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        return result

    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": "http_run", "run_id": run_id})
        logger.error(
            f"Unexpected error in http_run: [{error_response['error_code']}] {error_response['error_message']}, "
            f"traceback: {traceback.format_exc()}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": extract_core_stack(),
            }
        )
    finally:
        pass


HEADER_X_WORKFLOW_STREAM_MODE = "x-workflow-stream-mode"


def _register_task(run_id: str, task: asyncio.Task):
    service.running_tasks[run_id] = task


@app.post("/stream_run")
async def http_stream_run(request: Request):
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {extract_core_stack()}, error: {e}")

    # T3 鉴权门：无/空/无效 token → 401，限流超限 → 429
    _authenticate_token(_extract_token_from_body(body_text))

    ctx = new_context(method="stream_run", headers=request.headers)
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    workflow_stream_mode = request.headers.get(HEADER_X_WORKFLOW_STREAM_MODE, "").lower()
    workflow_debug = workflow_stream_mode == "debug"
    request_context.set(ctx)
    run_id = ctx.run_id
    is_agent = graph_helper.is_agent_proj()
    logger.info(
        f"Received request for /stream_run: "
        f"run_id={run_id}, "
        f"is_agent_project={is_agent}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_stream_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")

    if is_agent:
        stream_generator = agent_stream_handler(
            payload=payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
        )
    else:
        stream_generator = workflow_stream_handler(
            payload=payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
            run_opt=RunOpt(workflow_debug=workflow_debug),
        )

    response = StreamingResponse(stream_generator, media_type="text/event-stream")
    return response

@app.post("/cancel/{run_id}")
async def http_cancel(run_id: str, request: Request):
    """
    取消指定run_id的执行

    使用asyncio.Task.cancel()实现取消,这是Python标准的异步任务取消机制。
    LangGraph会在节点之间的await点检查CancelledError,实现优雅取消。
    """
    ctx = new_context(method="cancel", headers=request.headers)
    request_context.set(ctx)
    logger.info(f"Received cancel request for run_id: {run_id}")
    result = service.cancel_run(run_id, ctx)
    return result


@app.post(path="/node_run/{node_id}")
async def http_node_run(node_id: str, request: Request):
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        body_text = str(raw_body)
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {body_text}")

    # T3 鉴权门：无/空/无效 token → 401，限流超限 → 429
    _authenticate_token(_extract_token_from_body(body_text))

    ctx = new_context(method="node_run", headers=request.headers)
    request_context.set(ctx)
    logger.info(
        f"Received request for /node_run/{node_id}: "
        f"query={dict(request.query_params)}, "
        f"body={body_text}",
    )

    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_node_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")
    try:
        return await service.run_node(node_id, payload, ctx)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"node_id '{node_id}' not found or input miss required fields, traceback: {extract_core_stack()}")
    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": node_id})
        logger.error(
            f"Unexpected error in http_node_run: [{error_response['error_code']}] {error_response['error_message']}, "
            f"traceback: {traceback.format_exc()}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": extract_core_stack(),
            }
        )
    finally:
        pass


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI Chat Completions API 兼容接口"""
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception:
        body_text = ""

    # T3 鉴权门：无/空/无效 token → 401，限流超限 → 429
    _authenticate_token(_extract_token_from_body(body_text))

    ctx = new_context(method="openai_chat", headers=request.headers)
    request_context.set(ctx)

    logger.info(f"Received request for /v1/chat/completions: run_id={ctx.run_id}")

    try:
        payload = await request.json()
        return await openai_handler.handle(payload, ctx)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in openai_chat_completions: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    finally:
        pass


# ============================================================
# 运维：定期清理 + 健康检查
# ============================================================

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
                # 归档 7 天前的 completed 任务（如果有 archive 表的话，先删除）
                r2 = conn.execute(text(
                    "DELETE FROM ozon_product_tasks "
                    "WHERE status='completed' AND updated_at < NOW() - INTERVAL '7 days'"
                )).rowcount
                conn.commit()
                if r1 or r1f or r2:
                    logger.info(f"🧹 定期清理: {r1} stale running → pending(重试+1), {r1f} stale running → failed(耗尽), {r2} old completed deleted")
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
        except Exception as _e:
            logger.debug(f"定期清理跳过: {_e}")
        await asyncio.sleep(interval_seconds)


@app.get("/health")
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
        return {
            "status": "ok",
            "message": "Service is running",
            "db": "connected",
            "queue": queue_stats,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "message": str(e), "db": "disconnected"},
        )


@app.get("/api/v1/store/health")
async def store_health(client_id: str = None, api_key: str = None):
    """查询 Ozon 店铺配额健康状态。
    
    Query params (可选):
    - client_id: Ozon Client-Id
    - api_key: Ozon Api-Key
    """
    if not client_id or not api_key:
        return {"status": "unknown", "message": "需要提供 client_id 和 api_key"}
    try:
        import requests as req
        resp = req.post(
            "https://api-seller.ozon.ru/v4/product/info/limit",
            headers={"Client-Id": client_id, "Api-Key": api_key},
            json={}, timeout=10,
        )
        if resp.status_code != 200:
            return {"status": "error", "message": f"Ozon API error: {resp.status_code}"}
        data = resp.json()
        total = data.get("total", {})
        daily = data.get("daily_create", {})
        total_used = total.get("usage", 0)
        total_limit = total.get("limit", 1000)
        daily_used = daily.get("usage", 0)
        daily_limit = daily.get("limit", 100)
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
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _extract_token_from_body(body_text: str) -> str:
    """从请求体 JSON 提取 token（解析失败/非 dict → 视为无 token → 鉴权 401）。"""
    try:
        data = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("token", "") or "")


def _authenticate_token(token: str) -> str:
    """鉴权 token → user_id（Supabase tokens 表，剥离 sk- 前缀）。

    submit_task / resubmit_task 共用。失败抛 HTTPException(401/403)。
    Supabase 未配置（本地开发）→ 返回 "local_dev"。
    """
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    allowed, _remaining = rate_limiter.check(token)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute",
        )
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    supabase = get_supabase_client()
    if supabase is None:
        logger.warning("Supabase未配置，跳过token鉴权（本地开发模式）")
        return "local_dev"
    try:
        token_records = supabase.table("tokens").select(
            "user_id, key, remain_quota, status, expired_time, unlimited_quota"
        ).eq("key", clean_token).is_("deleted_at", "null").execute()
    except Exception as exc:
        # fail-closed：Supabase 瞬断（SSL/超时）绝不放行，也绝不 500 白屏——
        # 503 让客户端可重试（401 会触发 webui 拦截器误清 token）
        logger.warning("token 鉴权查询失败（Supabase 不可达）: %s", str(exc)[:200])
        raise HTTPException(status_code=503, detail="鉴权服务暂不可用，请稍后重试")
    if not token_records.data or len(token_records.data) == 0:
        raise HTTPException(status_code=401, detail="Invalid token")
    token_record = token_records.data[0]
    status = int(token_record.get("status", 0))
    if status != 1:
        status_desc = {2: "disabled", 3: "expired", 4: "quota exhausted"}
        raise HTTPException(
            status_code=403,
            detail=f"Token is {status_desc.get(status, 'unknown')}: status={status}",
        )
    return str(token_record.get("user_id", ""))


def _check_mxou_balance(token_record: dict) -> tuple[float, bool]:
    """检查用户余额（v0.29.3 统一：优先查 MXOU 平台真实余额）。

    背景（2026-08-07 Sentry 实证）：此前查 Supabase users.quota + unlimited_quota
    放行 —— unlimited_quota=true 时永远放行, 但 MXOU 平台按真实余额扣费,
    平台欠费(¥-0.068)仍放行 → 任务入队后 LLM/生图全 403 失败(253 次错误)。

    修复原则（统一余额来源 = MXOU 平台）：
    - 优先调 MXOU /v1/dashboard/billing/subscription 拿真实 balance
      （balance > 0 放行; <= 0 拒绝"MXOU 余额不足, 请充值"）
    - MXOU 查询失败(网络/接口) → 降级 Supabase users.quota（现有逻辑兜底）
    - unlimited_quota 仅作 Supabase 兜底分支的放行标记, 不再跳过 MXOU 实查

    Returns: (balance, ok) — ok=True 表示有额度
    """
    try:
        # ⚠️ 1. MXOU 平台真实余额优先（统一来源）
        raw_key = str(token_record.get("key", "") or "")
        mxou_balance = None
        if raw_key:
            from utils.mxou_api import get_mxou_balance
            mxou_balance = get_mxou_balance(raw_key)
        if mxou_balance is not None:
            return mxou_balance, mxou_balance > 0

        # ⚠️ 2. MXOU 查询失败 → 降级 Supabase users.quota（原逻辑兜底）
        user_id = token_record.get("user_id", "")
        supabase = get_supabase_client()
        if supabase is None or not user_id:
            # 本地开发模式：无 Supabase，不阻断
            return 0.0, True

        # 查 users 表剩余额度 quota（充值直接加 quota，调用扣 quota）
        try:
            user_rows = supabase.table("users").select(
                "quota"
            ).eq("id", user_id).limit(1).execute()
        except Exception as exc:
            # v0.22: 查询失败不再降级 key 级 remain_quota（僵尸字段会负数误判）。
            # unlimited 放行；非 unlimited 拒绝（数据异常应暴露，宁缺毋滥）
            logger.warning("余额查询失败（user=%s）: %s", user_id, exc)
            return 0.0, bool(token_record.get("unlimited_quota"))

        if user_rows.data:
            u = user_rows.data[0]
            balance = float(u.get("quota", 0) or 0)
            if token_record.get("unlimited_quota"):
                return balance, True
            return balance, balance > 0

        # users 表无记录：unlimited 放行；非 unlimited 拒绝（不降级僵尸字段）
        return 0.0, bool(token_record.get("unlimited_quota"))
    except Exception as e:
        logger.warning(f"余额检查异常（不阻断）: {e}")
        return 0.0, True


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
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"valid": False, "reason": "invalid_request", "expires_in": 0})

    token = body.get("token", "")
    client_id = body.get("client_id", "")
    api_key = body.get("api_key", "")

    if not token:
        return {"valid": False, "reason": "token_invalid", "expires_in": 0}

    # 去掉 sk- 前缀（tokens 表 key 列存储的是不带前缀的值）
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token

    import requests as _req

    # 1. 验证 token（查 Supabase tokens 表，和 submit_task 相同逻辑）
    supabase = get_supabase_client()
    if supabase is None:
        logger.warning("auth_verify: Supabase未配置，跳过token鉴权（本地开发模式）")
    else:
        try:
            token_records = supabase.table("tokens").select(
                "user_id, key, remain_quota, status, expired_time, unlimited_quota"
            ).eq("key", clean_token).is_("deleted_at", "null").execute()

            if not token_records.data or len(token_records.data) == 0:
                return {"valid": False, "reason": "token_invalid", "expires_in": 0}

            token_record = token_records.data[0]
            status = int(token_record.get("status", 0))

            # 2. 检查 token 状态（1=active, 2=disabled, 3=expired, 4=quota exhausted/欠费）
            #    status=4 明确映射 balance_insufficient（与 n8n AUTH_EXHAUSTED 一致）
            if status == 4:
                return {"valid": False, "reason": "balance_insufficient", "expires_in": 0}
            if status != 1:
                return {"valid": False, "reason": "account_inactive", "expires_in": 0}

            # 3. 检查余额（查 users 表 quota-used_quota，无限额度放行；
            #    原实现只查 remain_quota 会把无限额度 token 误判余额不足）
            balance, has_quota = _check_mxou_balance(token_record)
            if not has_quota:
                return {"valid": False, "reason": "balance_insufficient", "expires_in": 0}

        except Exception as e:
            logger.warning(f"auth_verify DB error: {e}")
            return {"valid": False, "reason": "service_unavailable", "expires_in": 0}

    # 4. 可选：验证 Ozon API
    ozon_valid = None
    if client_id and api_key:
        try:
            resp = _req.post(
                "https://api-seller.ozon.ru/v1/seller/info",
                headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
                json={},
                timeout=10
            )
            ozon_valid = resp.status_code == 200
        except Exception:
            ozon_valid = None

    return {
        "valid": True,
        "reason": "ok",
        "expires_in": 86400,
        "ozon_valid": ozon_valid,
    }


@app.get("/progress/{run_id}")
async def http_progress(run_id: str):
    """查询工作流执行进度。

    优先从 LangGraph checkpointer 读取实时 state，
    降级到内存 _task_progress → PG progress 列（任务完成后/重启后可用）。
    """
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
                    "(draft_id, credential_id, store_client_id, extensions, status, submitted_task_id) "
                    "VALUES (NULL, "
                    "(SELECT id::text FROM credentials "
                    " WHERE tenant_id = :tenant_id AND ozon_client_id = :store_client_id "
                    "   AND status = 'active' ORDER BY created_at DESC LIMIT 1), "
                    ":store_client_id, NULL, 'pending', :task_id)"
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


@app.post("/submit_task")
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
        
        # ✅ Step2: 验证token（查询Supabase tokens表）
        if not token:
            raise HTTPException(status_code=401, detail="Token is required")

        # ✅ 限流检查（按原始 token，含 sk- 前缀）
        allowed, remaining = rate_limiter.check(token)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute"
            )

        # Step2: 处理sk-前缀
        if token.startswith("sk-"):
            token = token.replace("sk-", "", 1)  # ✅ 剥离第一个sk-前缀

        # Step3: 查询tokens表（Supabase未配置时跳过鉴权，本地开发模式）
        supabase = get_supabase_client()
        balance = 0.0  # 本地开发模式无余额概念，设默认值
        if supabase is None:
            logger.warning("Supabase未配置，跳过token鉴权（本地开发模式）")
            user_id = "local_dev"
        else:
            try:
                token_records = supabase.table("tokens").select(
                    "user_id, key, remain_quota, status, expired_time, unlimited_quota"
                ).eq("key", token).is_("deleted_at", "null").execute()

                if not token_records.data or len(token_records.data) == 0:
                    raise HTTPException(status_code=401, detail=f"Invalid token: token '{token}' not found")

                token_record = token_records.data[0]
                user_id = str(token_record.get("user_id", ""))
                status = int(token_record.get("status", 0))

                # Step4: 检查token状态
                if status != 1:
                    status_desc = {2: "disabled", 3: "expired", 4: "quota exhausted"}
                    raise HTTPException(
                        status_code=403,
                        detail=f"Token is {status_desc.get(status, 'unknown')}: status={status}"
                    )

                # Step5: 检查余额（查 users 表 quota-used_quota，无限额度放行；
                #    原实现只查 remain_quota 会把无限额度 token 误判余额不足）
                balance, has_quota = _check_mxou_balance(token_record)
                if not has_quota:
                    return error_response(
                        WorkerErrorCode.INSUFFICIENT_BALANCE,
                        f"MXOU 余额不足 (current: {balance}). 请充值",
                    )

            except Exception as e:
                if isinstance(e, HTTPException):
                    raise e
                raise HTTPException(status_code=500, detail=f"Token validation failed: {str(e)}")
        
        # ✅ Step3: 提交任务到队列（使用user_id作为tenant_id）
        priority = 0  # ✅ 固定为0（所有用户平等优先级，直到建立VIP体系）
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
        
        task_id = await task_processor.submit_task(
            tenant_id=user_id,
            payload=payload_with_user_id,
            priority=priority,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            sku_key=sku_key,
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
        logger.error(f"Submit task error: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to submit task: {str(e)}")


@app.get("/task_status/{task_id}")
async def http_task_status(task_id: str):
    """
    查询任务状态（含进度信息）

    Returns:
        任务详情（包含status、result、error_message、progress等）
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        task_status = await task_processor.get_task_status(task_id)

        if task_status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

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

        return task_status

    except Exception as e:
        logger.error(f"Get task status error: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get task status: {str(e)}")


@app.post("/cancel_task/{task_id}")
async def http_cancel_task(task_id: str):
    """
    取消任务（仅pending状态的任务可取消）
    
    Returns:
        取消结果
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")
    
    try:
        success = await task_processor.cancel_task(task_id)
        
        if success:
            return {
                "status": "success",
                "task_id": task_id,
                "message": "Task cancelled successfully"
            }
        else:
            return {
                "status": "failed",
                "task_id": task_id,
                "message": "Task cannot be cancelled (may not in pending status)"
            }
            
    except Exception as e:
        logger.error(f"Cancel task error: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel task: {str(e)}")


@app.post("/resubmit_task/{task_id}")
async def http_resubmit_task(task_id: str, request: Request):
    """重新提交终态任务（审核被拒/失败自动修复链入口，P0-2）。

    仅 rejected/failed 终态任务可重试：复制原载荷 → 注入 parent_task_id +
    extensions.image_regen=True → 重新入队（pending）。返回新任务 task_id。

    ⚠️ v0.38.1 安全修复：请求体必须携带调用者 token（与 submit_task 一致），
    校验 token 归属租户 == 任务 tenant_id，防跨租户凭证重放（CRITICAL）。
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

        new_task_id = await task_processor.submit_task(
            tenant_id=tenant_id,
            payload=payload,
            priority=0,
            timeout_seconds=int(task_status.get("timeout_seconds") or 1800),
            max_retries=int(task_status.get("max_retries") or 3),
            sku_key=sku_key,
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


@app.get("/task_statistics")
async def http_task_statistics(request: Request):
    """
    获取任务统计信息
    
    Args:
        tenant_id: 租户ID（可选，不传则查询所有租户）
    
    Returns:
        任务统计信息（总数、成功率、平均耗时等）
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")
    
    try:
        tenant_id = request.query_params.get("tenant_id")
        
        statistics = await task_processor.get_task_statistics(tenant_id)
        
        return {
            "status": "success",
            "statistics": statistics
        }
        
    except Exception as e:
        logger.error(f"Get task statistics error: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get task statistics: {str(e)}")


@app.get(path="/graph_parameter")
async def http_graph_inout_parameter(request: Request):
    return service.graph_inout_schema()


@app.post("/api/v1/logistics/quote")
async def logistics_quote(request: Request):
    """物流运费报价端点（v0.29.x, skill 选品利润估算用）。

    入参: {token?, weight_g, depth_cm, width_cm, height_cm,
           tpl_provider?, service_level?, ozon_client_id?, ozon_api_key?}
    - 未传 tpl_provider/service_level 时, 若有 ozon 凭证自动探测 3PL;
      否则默认 RETS/Standard。
    - token 校验与 auth_verify 一致(Supabase 未配置时本地放行)。

    返回: {logistics_cost_cny, channel, tpl_provider_used, service_level_used,
           base_cost, per_gram_rate, billable_weight, weight, dims_cm, fallback_chain}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_request: body must be JSON")

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
    except Exception as e:
        raise HTTPException(status_code=503, detail={
            "status": "degraded", "message": str(e), "db": "disconnected"
        })


@v1.post("/submit_task", response_model=SubmitTaskResponse, tags=["task"],
         responses={401: {"model": ErrorBody}, 402: {"model": ErrorBody},
                    403: {"model": ErrorBody}, 429: {"model": ErrorBody}})
async def v1_submit_task(request: Request):
    """提交任务到队列。鉴权通过 Supabase tokens 表校验。"""
    # 委托给现有实现
    return await http_submit_task(request)


@v1.get("/task_status/{task_id}", response_model=TaskStatusResponse, tags=["task"],
        responses={404: {"model": ErrorBody}})
async def v1_task_status(task_id: str):
    """查询任务状态。"""
    return await http_task_status(task_id)


@v1.post("/cancel_task/{task_id}", response_model=CancelTaskResponse, tags=["task"],
         responses={404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def v1_cancel_task(task_id: str):
    """取消待处理的任务。"""
    return await http_cancel_task(task_id)


@v1.post("/resubmit_task/{task_id}", response_model=SubmitTaskResponse, tags=["task"],
         responses={404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def v1_resubmit_task(task_id: str, request: Request):
    """重新提交被拒(rejected)/失败(failed)的任务（P0-2 自动修复链入口）。"""
    return await http_resubmit_task(task_id, request)


@v1.get("/task_statistics", response_model=TaskStatisticsResponse, tags=["task"])
async def v1_task_statistics(request: Request):
    """获取任务统计信息。

    ⚠️ v0.19.2: 旧路径返回 {"status","statistics"} 包裹结构（无 response_model），
    v1 声明了 TaskStatisticsResponse 响应模型，必须解包 statistics 再返回，
    否则字段对不上被 Pydantic 填默认值 → 统计恒 0。
    """
    resp = await http_task_statistics(request)
    return resp.get("statistics", {})


# ==================== /api/v1/analytics/* — skill 选品数据上报（v0.34 C5） ====================
# skill what-to-sell 采集的蓝海/榜单数据集体沉淀到 worker PG（数据来源于用户、服务于用户）。
# 每类数据: (表名, 去重冲突列, 可更新数据列, 条目 Pydantic 模型, body 列表字段名)

_ANALYTICS_KINDS = {
    "queries": (
        BlueOceanQuery,
        ("query", "contributed_by_token_id"),
        ("query", "count", "ca", "avg_ca_rub", "avg_count_items",
         "items_views", "uniq_queries_wca", "uniq_sellers"),
        BlueOceanQueryItem,
        "queries",
    ),
    "ozon-bestsellers": (
        OzonBestseller,
        ("sku_or_id", "contributed_by_token_id"),
        ("sku_or_id", "brand", "category_id", "category_path",
         "ordering_amount", "ordering_count", "avg_price_rub"),
        OzonBestsellerItem,
        "items",
    ),
    "market-bestsellers": (
        MarketBestseller,
        ("product_name", "contributed_by_token_id"),
        ("product_name", "brand", "category_id", "category_path",
         "ordering_amount", "daily_avg", "other_platform_price"),
        MarketBestsellerItem,
        "items",
    ),
}


def _verify_analytics_token(clean_token: str) -> None:
    """analytics 上报鉴权（与 logistics_quote 一致：Supabase 未配置 → 本地放行）。"""
    supabase = get_supabase_client()
    if supabase is None:
        return
    try:
        token_records = supabase.table("tokens").select(
            "status"
        ).eq("key", clean_token).is_("deleted_at", "null").execute()
        if not token_records.data or len(token_records.data) == 0 or int(token_records.data[0].get("status", 0)) != 1:
            raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="service_unavailable")


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


@v1.get("/analytics/bestsellers", tags=["analytics"])
async def v1_analytics_list_bestsellers(request: Request):
    """P2b 榜单浏览：读 skill 上报的 ozon-bestsellers（按上报 token 隔离）。

    query: category?（类目筛选）/ order_by?（ordering_amount|ordering_count|avg_price_rub）/ limit/offset
    鉴权与上报一致：token 即 contributed_by_token_id。
    """
    from services.analytics_service import list_bestsellers
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean_token)

    q = request.query_params
    try:
        limit = int(q.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(q.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    return list_bestsellers(
        clean_token,
        category=q.get("category"),
        order_by=q.get("order_by") or "ordering_amount",
        limit=limit, offset=offset,
    )


@v1.get("/mappings/lookup", tags=["analytics"])
async def v1_mappings_lookup(request: Request):
    """类目映射查询（W11）：skill 端按关键词查已学习 Ozon 类目映射。

    query: keyword（1688 中文类目名）→ {found, mappings: [{dc, tp, confidence}]}
    复用 category_mapping_learn.lookup_mapping（精确 leaf/ID，成功数+置信门槛）；
    未命中时按 source_keywords 重叠兜底（ozon_category_query 同表同门槛）。
    category_mapping 表全局共享（无 tenant 隔离——类目映射是平台级知识，PRD §3.3）。
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean_token)

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


# ── WebUI 凭证端点（T5）：routes/services 分层，业务逻辑在 services/credential_service.py ──
from routes.credentials_routes import router as credentials_router
v1.include_router(credentials_router)

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
from routes.store_sync_routes import router as store_sync_router
v1.include_router(store_sync_router)

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


# 注册 v1 路由（/api/v1/* 端点）
# 旧路径（/health, /submit_task 等）仍然可用，向后兼容
app.include_router(v1)

# ── New API 通用代理（v0.55.1）：webui 同源 /api/* → api.mxou.cn（登录/订阅/钱包） ──
# catch-all 必须在 v1 具体路由之后注册，否则吞掉 /api/v1
from routes.newapi_proxy_routes import router as newapi_proxy_router
app.include_router(newapi_proxy_router)


# ── WebUI SPA 静态托管（/app，docs/PLAN-webui-v1.md §1.4 T4） ──
# dist 默认 webui/dist（env WEBUI_DIST 覆盖）；未构建时跳过挂载不阻断 worker。
# SPA fallback：非静态文件路径回 index.html（前端路由直连/刷新不 404），
# 仅允许 dist 目录内的文件（防路径穿越）。
_WEBUI_DIST_DEFAULT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
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

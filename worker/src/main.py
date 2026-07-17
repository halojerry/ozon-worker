import argparse
import asyncio
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
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from storage.database.db import get_session, get_engine, init_db
from storage.database.supabase_client import get_supabase_client
from storage.memory.memory_saver import get_memory_saver
from storage.database.shared.model import Base
from utils.task_processor import SupabaseTaskProcessor
from sqlalchemy import event

# Local runtime replacements (previously Coze-dependent)
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

setup_logging(
    log_file=LOG_FILE,
    max_bytes=100 * 1024 * 1024,  # 100MB
    backup_count=5,
    log_level=LOG_LEVEL,
    use_json_format=False,  # Simple format for local
    console_output=True,
)

logger = logging.getLogger(__name__)

# 超时配置常量
TIMEOUT_SECONDS = 900  # 15分钟

# API 限流配置
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))  # 每 token 每分钟最大提交数


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
    async def run_node(self, node_id: str, payload: Dict[str, Any], ctx=None) -> Any:
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
        return await _graph.ainvoke(payload, config=run_config)

    def graph_inout_schema(self) -> Any:
        if graph_helper.is_agent_proj():
            return {"input_schema": {}, "output_schema": {}}
        builder = getattr(self._get_graph(), 'builder', None)
        if builder is not None:
            input_cls = getattr(builder, 'input_schema', None) or self.graph.get_input_schema()
            output_cls = getattr(builder, 'output_schema', None) or self.graph.get_output_schema()
        else:
            logger.warning(f"No builder input schema found for graph_inout_schema, using graph input schema instead")
            input_cls = self.graph.get_input_schema()
            output_cls = self.graph.get_output_schema()

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
    
    # 启动Supabase任务处理器（最多10个并发任务）
    global task_processor
    task_processor = SupabaseTaskProcessor(max_concurrent=10)
    
    # 启动Worker后台任务（不阻塞主服务启动）
    worker_task = asyncio.create_task(task_processor.start_workers(num_workers=10))
    
    yield
    
    # 关闭时停止Worker
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            logger.info("Worker任务已取消")
    
    if async_runtime is not None:
        await async_runtime.shutdown()

app = FastAPI(lifespan=lifespan)

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

        # 创建任务并记录 - 这是关键，让我们可以通过run_id取消任务
        task = asyncio.create_task(service.run(payload, ctx))
        service.running_tasks[run_id] = task

        try:
            result = await asyncio.wait_for(task, timeout=float(TIMEOUT_SECONDS))
        except asyncio.TimeoutError:
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
    ctx = new_context(method="stream_run", headers=request.headers)
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    workflow_stream_mode = request.headers.get(HEADER_X_WORKFLOW_STREAM_MODE, "").lower()
    workflow_debug = workflow_stream_mode == "debug"
    request_context.set(ctx)
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {extract_core_stack()}, error: {e}")
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


@app.get("/health")
async def health_check():
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "message": "Service is running",
            "db": "connected",
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "message": str(e), "db": "disconnected"},
        )


@app.get("/progress/{run_id}")
async def http_progress(run_id: str):
    """查询工作流执行进度。

    从 LangGraph checkpointer 读取当前 state，返回 progress_counter / total / stages。
    仅对 async_graph（有 checkpointer 的图）有效；/run 同步路径无 checkpointer。
    """
    if async_graph is None:
        raise HTTPException(status_code=503, detail="Async graph not initialized")
    checkpointer = get_memory_saver()
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="Checkpointer not available")
    config = {"configurable": {"thread_id": run_id}}
    try:
        state = await async_graph.aget_state(config)
        if state is None or not state.values:
            raise HTTPException(status_code=404, detail=f"No state found for run_id={run_id}")
        values = state.values
        counter = values.get("progress_counter", 0)
        stages = values.get("stages", {})
        total = 24  # 与 workflow_progress.json 节点数一致
        return {
            "run_id": run_id,
            "progress_counter": counter,
            "total_nodes": total,
            "percentage": int((counter / total) * 100) if total > 0 else 0,
            "stages": stages,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询进度失败 run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Supabase任务队列API ====================

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
        
        # ✅ Step1: 从payload中提取认证参数（兼容直连格式和包装格式）
        payload = body.get("payload") or body
        token = payload.get("token", "")
        ozon_client_id = payload.get("ozon_client_id", "")
        ozon_api_key = payload.get("ozon_api_key", "")
        envelope = payload.get("envelope", {})
        
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
        if supabase is None:
            logger.warning("Supabase未配置，跳过token鉴权（本地开发模式）")
            user_id = "local_dev"
        else:
            try:
                token_records = supabase.table("tokens").select(
                    "user_id, remain_quota, status, expired_time"
                ).eq("key", token).is_("deleted_at", "null").execute()

                if not token_records.data or len(token_records.data) == 0:
                    raise HTTPException(status_code=401, detail=f"Invalid token: token '{token}' not found")

                token_record = token_records.data[0]
                user_id = str(token_record.get("user_id", ""))
                balance = float(token_record.get("remain_quota", 0.0))
                status = int(token_record.get("status", 0))

                # Step4: 检查token状态
                if status != 1:
                    status_desc = {2: "disabled", 3: "expired", 4: "quota exhausted"}
                    raise HTTPException(
                        status_code=403,
                        detail=f"Token is {status_desc.get(status, 'unknown')}: status={status}"
                    )

                # Step5: 检查余额（MXOU 生图/LLM 需要额度，余额不足会中途失败）
                if balance < 5.0:
                    raise HTTPException(
                        status_code=402,
                        detail=f"Insufficient balance: remain_quota must be >= 5.0 (current: {balance}). Please top up your MXOU account."
                    )

            except Exception as e:
                if isinstance(e, HTTPException):
                    raise e
                raise HTTPException(status_code=500, detail=f"Token validation failed: {str(e)}")
        
        # ✅ Step3: 提交任务到队列（使用user_id作为tenant_id）
        priority = 0  # ✅ 固定为0（所有用户平等优先级，直到建立VIP体系）
        timeout_seconds = body.get("timeout_seconds", 1800)
        max_retries = body.get("max_retries", 3)
        
        # ✅ Step4: payload中添加user_id（供下游节点使用）
        payload_with_user_id = {
            **payload,
            "user_id": user_id,  # ✅ 添加user_id字段
            "envelope": envelope
        }
        
        task_id = await task_processor.submit_task(
            tenant_id=user_id,  # ✅ 使用user_id作为tenant_id
            payload=payload_with_user_id,
            priority=priority,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries
        )
        
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
    查询任务状态
    
    Returns:
        任务详情（包含status、result、error_message等）
    """
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")
    
    try:
        task_status = await task_processor.get_task_status(task_id)
        
        if task_status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
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

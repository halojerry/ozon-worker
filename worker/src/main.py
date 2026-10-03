"""worker 服务入口（composition root，W3c 收口）。

本文件只做四件事（全部保持原时序）：
1. **引导**：``init_sentry`` + ``setup_structured_logging``（模块级副作用，
   时序最早——必须先于 app_factory 导入全部 routes/services）；
2. **兼容面 re-export**：四族共享设施（runtime.progress / runtime.rate_limit /
   runtime.graph_service / api.security）与 runtime.context|helpers|log_utils、
   get_supabase_client、HTTPException 等同名转发——名字绑定同一函数/单例对象，
   路由裸名调用与存量测试 monkeypatch main.X 照常生效。新代码一律直接
   ``from <权威模块> import``，勿再经 main 转手；
3. **装配**：``from app_factory import app``（FastAPI 构造 + MCP/路由/WebUI 装配
   全在 app_factory；lifespan 在 runtime.lifespan）；
4. **CLI 尾部**：``python -m src.main -m http|flow|node|agent``（Dockerfile 启动
   契约 ``CMD ["python","-m","src.main","-m","http","-p","5000"]``；uvicorn 字符串
   契约 ``uvicorn.run("main:app")`` 依赖 main:app 可导入）。

迁移记录（W3c，零行为变化）：
- lifespan/清扫器/启动校验 → runtime.lifespan / runtime.maintenance /
  runtime.startup_checks；FastAPI 装配 → app_factory.py。
- main.task_processor / main.async_graph 降级为恒 None 兼容占位（已无消费者；
  真单例走 orchestrator / runtime.graph_service holder，由 runtime.lifespan 注入）。
- 原 lifespan 内 write-only 全局 store_sync_task 随迁 runtime.lifespan（全仓无读取方）。
"""

import argparse
import asyncio
import json
import os
from typing import Any, Dict, Optional

import uvicorn
from fastapi import HTTPException
from langgraph.graph.state import CompiledStateGraph

from storage.database.supabase_client import get_supabase_client
from orchestrator.task_processor import SupabaseTaskProcessor

# ✅ v0.23: Sentry 错误监测（SENTRY_DSN 为空则 no-op；HTTP 与 CLI 入口共用）
from utils.sentry_setup import init_sentry
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

# 结构化日志：生产用 JSON，本地开发用可读格式（时序最早——app_factory 导入
# 全部 routes/services 之前完成，保证其模块级日志走已配置的 handler）
setup_structured_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_format=os.getenv("LOG_FORMAT", "json").lower() == "json",
    log_file=os.getenv("LOG_FILE", ""),
)

logger = get_logger(__name__)

# ── 应用装配（FastAPI 构造 + MCP 挂载 + 全部路由 + WebUI SPA）──
# app_factory 是装配层唯一权威；main 保留 app 同名 re-export（测试/gen_api_docs
# 的 from main import app 与 uvicorn "main:app" 字符串契约不变）。
from app_factory import app

# R3a: async 图单例已归 runtime/graph_service holder（唯一权威，见 set_async_graph；
# 唯一消费者 routes/ops_routes.py GET /progress/{run_id} 经 get_async_graph() 取）。
# 本名保留为兼容占位（恒 None）——新代码勿读它，用 get_async_graph()。
async_graph: Optional[CompiledStateGraph] = None
task_processor: Optional[SupabaseTaskProcessor] = None


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

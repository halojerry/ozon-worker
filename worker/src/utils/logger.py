"""结构化日志模块 — JSON 格式 + trace_id 链路追踪 + 节点审计。

用法:
    from utils.logger import get_logger, set_trace_context, audit_node

    # 在请求入口设置 trace_id
    set_trace_context(trace_id="abc123", task_id="uuid", user_id="user1")

    # 获取 logger（自动携带 trace_id）
    logger = get_logger(__name__)
    logger.info("处理开始")  # 输出: {"ts":"...","level":"INFO","msg":"处理开始","trace_id":"abc123",...}

    # 节点执行审计
    @audit_node("pricing")
    async def pricing_node(state):
        ...
"""

from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone, UTC
from functools import wraps
from typing import Any, Callable, Optional

# ──────────────────────────────────────────────
# Trace Context（请求级上下文，自动注入到每条日志）
# ──────────────────────────────────────────────

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_task_id: ContextVar[str] = ContextVar("task_id", default="")
_user_id: ContextVar[str] = ContextVar("user_id", default="")
_node_name: ContextVar[str] = ContextVar("node_name", default="")


def set_trace_context(
    trace_id: str = "",
    task_id: str = "",
    user_id: str = "",
    node_name: str = "",
):
    """设置当前请求的链路上下文。所有后续日志自动携带这些字段。"""
    if trace_id:
        _trace_id.set(trace_id)
    if task_id:
        _task_id.set(task_id)
    if user_id:
        _user_id.set(user_id)
    if node_name:
        _node_name.set(node_name)


def get_trace_context() -> dict[str, str]:
    """获取当前链路上下文。"""
    return {
        "trace_id": _trace_id.get(),
        "task_id": _task_id.get(),
        "user_id": _user_id.get(),
        "node_name": _node_name.get(),
    }


def clear_trace_context():
    """清理链路上下文（任务完成后调用）。"""
    _trace_id.set("")
    _task_id.set("")
    _user_id.set("")
    _node_name.set("")


# ──────────────────────────────────────────────
# JSON Formatter
# ──────────────────────────────────────────────


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式。

    输出示例:
    {"ts":"2026-07-18T10:00:00Z","level":"INFO","logger":"main","msg":"任务提交","trace_id":"abc","task_id":"uuid"}
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # 注入 trace context
        trace_id = _trace_id.get()
        task_id = _task_id.get()
        user_id = _user_id.get()
        node_name = _node_name.get()

        if trace_id:
            log_entry["trace_id"] = trace_id
        if task_id:
            log_entry["task_id"] = task_id
        if user_id:
            log_entry["user_id"] = user_id
        if node_name:
            log_entry["node_name"] = node_name

        # 附加额外字段
        if hasattr(record, "extra_data") and record.extra_data:
            log_entry["data"] = record.extra_data

        # 异常信息
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "msg": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ReadableFormatter(logging.Formatter):
    """人类可读的日志格式（本地开发用）。

    输出示例:
    10:00:00 [INFO] main: [abc123/uuid] 任务提交
    """

    def format(self, record: logging.LogRecord) -> str:
        trace_id = _trace_id.get()
        task_id = _task_id.get()

        prefix = ""
        if trace_id:
            prefix = f"[{trace_id[:8]}"
            if task_id:
                prefix += f"/{task_id[:8]}"
            prefix += "] "

        record.msg = f"{prefix}{record.msg}"
        return super().format(record)


# ──────────────────────────────────────────────
# Logger 工厂
# ──────────────────────────────────────────────

_root_configured = False


def setup_structured_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: str = "",
):
    """初始化全局结构化日志。

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        json_format: True=JSON 格式（生产），False=可读格式（开发）
        log_file: 日志文件路径（空=只输出 stdout）
    """
    global _root_configured
    if _root_configured:
        return
    _root_configured = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有 handler
    root.handlers.clear()

    formatter: logging.Formatter
    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = ReadableFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    # stdout handler（Docker 最佳实践）
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    # 文件 handler（可选）
    if log_file:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())  # 文件始终用 JSON
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """获取 logger（自动配置根 logger）。"""
    if not _root_configured:
        setup_structured_logging(json_format=False)
    return logging.getLogger(name)


# ──────────────────────────────────────────────
# 节点执行审计
# ──────────────────────────────────────────────


def audit_node(node_name: str):
    """装饰器：自动记录节点执行的输入、输出、耗时、错误。

    用法:
        @audit_node("pricing")
        async def pricing_node(state):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            logger = get_logger(f"node.{node_name}")
            set_trace_context(node_name=node_name)

            start_time = time.monotonic()
            logger.info(f"▶ 节点开始: {node_name}")

            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.monotonic() - start_time) * 1000

                # 提取关键输出字段（避免日志过大）
                output_summary = _extract_output_summary(result, node_name)

                logger.info(
                    f"✅ 节点完成: {node_name}",
                    extra={"extra_data": {
                        "duration_ms": round(duration_ms, 1),
                        "output": output_summary,
                    }},
                )
                return result

            except Exception as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                logger.error(
                    f"❌ 节点失败: {node_name}: {e}",
                    exc_info=True,
                    extra={"extra_data": {
                        "duration_ms": round(duration_ms, 1),
                        "error_type": type(e).__name__,
                    }},
                )
                raise

        return wrapper
    return decorator


def _extract_output_summary(result: Any, node_name: str) -> dict:
    """从节点输出中提取关键字段（避免日志过大）。"""
    if result is None:
        return {}

    if isinstance(result, dict):
        # 只取关键字段
        keys_to_log = [
            "description_category_id", "type_id", "product_id",
            "is_valid", "upload_status", "error_message",
            "pricing_info", "name",
        ]
        return {k: result.get(k) for k in keys_to_log if k in result}

    return {"type": type(result).__name__}


# ──────────────────────────────────────────────
# Ozon API 调用日志
# ──────────────────────────────────────────────


def log_ozon_api_call(
    method: str,
    endpoint: str,
    status_code: int,
    duration_ms: float,
    request_summary: Optional[dict] = None,
    response_summary: Optional[dict] = None,
    error: Optional[str] = None,
):
    """记录 Ozon API 调用。

    用法:
        log_ozon_api_call(
            method="POST",
            endpoint="/v3/product/import",
            status_code=200,
            duration_ms=1234,
            request_summary={"items_count": 3},
            response_summary={"task_id": 12345},
        )
    """
    logger = get_logger("ozon.api")

    data: dict[str, Any] = {
        "method": method,
        "endpoint": endpoint,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 1),
    }
    if request_summary:
        data["request"] = request_summary
    if response_summary:
        data["response"] = response_summary
    if error:
        data["error"] = error

    if status_code >= 400:
        logger.warning(f"Ozon API {method} {endpoint} → {status_code}", extra={"extra_data": data})
    else:
        logger.info(f"Ozon API {method} {endpoint} → {status_code}", extra={"extra_data": data})


# ──────────────────────────────────────────────
# 任务生命周期审计
# ──────────────────────────────────────────────


def log_task_event(
    event: str,
    task_id: str,
    user_id: str = "",
    **extra_fields,
):
    """记录任务生命周期事件。

    Args:
        event: 事件名 (submitted/started/completed/failed/retried/cancelled)
        task_id: 任务 UUID
        user_id: 用户 ID
        **extra_fields: 附加字段（如 error_message, retry_count, duration_s）

    用法:
        log_task_event("submitted", task_id=task_id, user_id="user1", priority=0)
        log_task_event("completed", task_id=task_id, duration_s=120.5)
        log_task_event("failed", task_id=task_id, error_message="timeout", retry_count=2)
    """
    logger = get_logger("task.lifecycle")

    data: dict[str, Any] = {"event": event, "task_id": task_id}
    if user_id:
        data["user_id"] = user_id
    data.update(extra_fields)

    level = logging.INFO
    if event == "failed":
        level = logging.ERROR
    elif event == "retried":
        level = logging.WARNING

    msg = f"📋 任务 {event}: {task_id}"
    logger.log(level, msg, extra={"extra_data": data})

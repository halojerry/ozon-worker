"""Pydantic 请求/响应 schemas — API 契约的单一事实来源。

FastAPI 自动从这些 model 生成 OpenAPI 文档（/docs 和 /openapi.json）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 通用
# ──────────────────────────────────────────────


class ApiVersion(str, Enum):
    V1 = "v1"


class ErrorBody(BaseModel):
    """统一错误响应体。"""
    ok: bool = False
    error_code: str = Field(..., description="错误码，如 TOKEN_INVALID、RATE_LIMITED")
    message: str = Field(..., description="人类可读的错误描述")
    detail: Optional[Any] = Field(None, description="附加详情（调试用）")


# ──────────────────────────────────────────────
# /api/v1/submit_task
# ──────────────────────────────────────────────


class SubmitTaskRequest(BaseModel):
    """提交任务请求。

    token/ozon_client_id/ozon_api_key/envelope 可以直接放在 body 顶层，
    也可以包在 payload 字段里（向后兼容）。
    """
    token: str = Field(..., description="MXOU API Key（带或不带 sk- 前缀）")
    ozon_client_id: str = Field(..., description="Ozon 卖家 Client-Id")
    ozon_api_key: str = Field(..., description="Ozon 卖家 Api-Key")
    envelope: dict[str, Any] = Field(..., description="产品数据信封 {draft, source, extensions}")
    timeout_seconds: int = Field(1800, description="任务超时时间（秒），默认 30 分钟")
    max_retries: int = Field(3, description="最大重试次数，默认 3")

    # 向后兼容：支持 body.payload 包装格式
    payload: Optional[SubmitTaskRequest] = Field(None, description="向后兼容字段，优先使用顶层字段")


class SubmitTaskResponse(BaseModel):
    """提交任务成功响应。"""
    ok: bool = True
    task_id: str = Field(..., description="任务 UUID，用于轮询状态")
    message: str = Field(..., description="提交成功消息")


# ──────────────────────────────────────────────
# /api/v1/task_status/{task_id}
# ──────────────────────────────────────────────


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatusResponse(BaseModel):
    """任务状态响应。"""
    id: str = Field(..., description="任务 UUID")
    status: TaskStatus = Field(..., description="任务状态")
    tenant_id: str = Field(..., description="用户 ID")
    priority: int = Field(0, description="任务优先级")
    result: Optional[dict[str, Any]] = Field(None, description="任务执行结果（completed 时有值）")
    error_message: Optional[str] = Field(None, description="错误信息（failed 时有值）")
    retry_count: int = Field(0, description="已重试次数")
    max_retries: int = Field(3, description="最大重试次数")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")
    started_at: Optional[datetime] = Field(None, description="开始执行时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    timeout_seconds: int = Field(1800, description="超时时间（秒）")


# ──────────────────────────────────────────────
# /api/v1/cancel_task/{task_id}
# ──────────────────────────────────────────────


class CancelTaskResponse(BaseModel):
    """取消任务响应。"""
    ok: bool = True
    task_id: str = Field(..., description="任务 UUID")
    message: str = Field(..., description="取消结果消息")


# ──────────────────────────────────────────────
# /api/v1/health
# ──────────────────────────────────────────────


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str = Field(..., description="服务状态: ok / degraded")
    message: str = Field(..., description="状态描述")
    db: str = Field(..., description="数据库连接状态: connected / disconnected")


# ──────────────────────────────────────────────
# /api/v1/task_statistics
# ──────────────────────────────────────────────


class TaskStatisticsResponse(BaseModel):
    """任务统计响应。"""
    total: int = Field(0, description="总任务数")
    pending: int = Field(0, description="待处理")
    running: int = Field(0, description="执行中")
    completed: int = Field(0, description="已完成")
    failed: int = Field(0, description="已失败")
    cancelled: int = Field(0, description="已取消")
    avg_duration_seconds: Optional[float] = Field(None, description="平均执行时长（秒）")

"""统一错误码体系。

所有 Worker API 错误响应遵循统一格式：
    {"ok": false, "error_code": "ERROR_CODE", "message": "人类可读描述"}

错误码分组：
    AUTH_*    — 鉴权相关
    TASK_*    — 任务相关
    RATE_*    — 限流相关
    SYSTEM_*  — 系统内部错误
"""

from enum import Enum
from typing import Any, Optional

from fastapi.responses import JSONResponse


class WorkerErrorCode(str, Enum):
    """Worker API 统一错误码。"""

    # 鉴权
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_INVALID = "TOKEN_INVALID"
    TOKEN_DISABLED = "TOKEN_DISABLED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"

    # 限流
    RATE_LIMITED = "RATE_LIMITED"

    # 任务
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_NOT_CANCELLABLE = "TASK_NOT_CANCELLABLE"
    TASK_SUBMIT_FAILED = "TASK_SUBMIT_FAILED"

    # 系统
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"


# 错误码 → HTTP 状态码映射
ERROR_STATUS_MAP: dict[WorkerErrorCode, int] = {
    WorkerErrorCode.TOKEN_MISSING: 401,
    WorkerErrorCode.TOKEN_INVALID: 401,
    WorkerErrorCode.TOKEN_DISABLED: 403,
    WorkerErrorCode.TOKEN_EXPIRED: 403,
    WorkerErrorCode.INSUFFICIENT_BALANCE: 402,
    WorkerErrorCode.RATE_LIMITED: 429,
    WorkerErrorCode.TASK_NOT_FOUND: 404,
    WorkerErrorCode.TASK_NOT_CANCELLABLE: 409,
    WorkerErrorCode.TASK_SUBMIT_FAILED: 500,
    WorkerErrorCode.INTERNAL_ERROR: 500,
    WorkerErrorCode.SERVICE_UNAVAILABLE: 503,
    WorkerErrorCode.INVALID_REQUEST: 400,
}


def error_response(
    error_code: WorkerErrorCode,
    message: str,
    detail: Optional[Any] = None,
) -> JSONResponse:
    """构建统一错误响应。"""
    body: dict[str, Any] = {
        "ok": False,
        "error_code": error_code.value,
        "message": message,
    }
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(
        status_code=ERROR_STATUS_MAP.get(error_code, 500),
        content=body,
    )

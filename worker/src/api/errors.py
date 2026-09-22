"""统一错误码体系。

所有 Worker API 错误响应遵循统一格式：
    {"ok": false, "error_code": "ERROR_CODE", "message": "人类可读描述"}

错误码分组：
    AUTH_*    — 鉴权相关
    TASK_*    — 任务相关
    RATE_*    — 限流相关
    SYSTEM_*  — 系统内部错误
    其余       — 上架管线内部错误码（见各成员注释；PIPELINE_ERROR_MESSAGES 带中文文案）
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
    # B4 (2026-09-11 仓库治理, A5 §2 D-01)：已接线——main.py http_cancel_task
    # 对非 pending 任务返 409 + 本码（原 200+{status:failed} 不可编程处理）。
    TASK_NOT_CANCELLABLE = "TASK_NOT_CANCELLABLE"
    TASK_NOT_RESUBMITTABLE = "TASK_NOT_RESUBMITTABLE"
    TASK_SUBMIT_FAILED = "TASK_SUBMIT_FAILED"
    DUPLICATE_SUBMIT = "DUPLICATE_SUBMIT"

    # 系统
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"

    # 上架管线内部错误码（v0.78 批A fix/image-source-hardgate-v1）
    # 生图全败硬闸：E1 原图兜底默认停用，生图全败 → 任务级失败（绝不出 1688 原图卡）。
    # ⚠️ 非永久错误——task_processor._is_permanent_task_error 对其判 False，
    # 整任务自动重试一轮，重试仍全败才终态 failed（承载异常 ImageGenAllFailedError，
    # 见 utils/image_source.py；禁止加进永久错误清单）。
    IMAGE_GEN_ALL_FAILED = "IMAGE_GEN_ALL_FAILED"


# v0.78 批A：管线内部错误码默认中文文案（随异常消息/任务 error_message 透出，
# 非 HTTP 错误信封——image_source.ImageGenAllFailedError 的 raise 文案唯一来源）
PIPELINE_ERROR_MESSAGES: dict = {
    WorkerErrorCode.IMAGE_GEN_ALL_FAILED.value: "生图全部失败，任务将重试后终态失败；不出原始图卡片",
}


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
    WorkerErrorCode.TASK_NOT_RESUBMITTABLE: 409,
    WorkerErrorCode.TASK_SUBMIT_FAILED: 500,
    WorkerErrorCode.DUPLICATE_SUBMIT: 409,
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

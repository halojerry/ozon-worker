"""Ozon API 调用包装器 — 统一 HTTP 调用 + 结构化日志。

用法:
    from utils.ozon_client import ozon_post

    result = ozon_post(
        client_id="123",
        api_key="abc",
        endpoint="/v3/product/import",
        body={"items": [...]},
    )
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from utils.logger import get_logger, log_ozon_api_call
from utils.ozon_errors import (
    OzonAuthError,
    OzonError,
    OzonRateLimitError,
    OzonServerError,
    _raise_for_status,
)
from utils.ozon_rate_limiter import _rate_limiter

logger = get_logger("ozon.api")

BASE_URL = "https://api-seller.ozon.ru"

_MAX_RETRIES = 3

_exp_wait = wait_exponential_jitter(initial=1, max=20)


def _wait_strategy(retry_state):
    """tenacity wait callable — honor Ozon's Retry-After on 429, else exponential jitter."""
    outcome = getattr(retry_state, "outcome", None)
    exc = outcome.exception() if outcome is not None else None
    if isinstance(exc, OzonRateLimitError) and exc.retry_after is not None:
        return float(exc.retry_after)
    return _exp_wait(retry_state)


def ozon_post(
    client_id: str,
    api_key: str,
    endpoint: str,
    body: dict[str, Any],
    timeout: int = 60,
    language: str = "ZH_HANS",
) -> dict[str, Any]:
    """调用 Ozon Seller API（POST），自动记录调用日志。

    Args:
        client_id: Ozon 卖家 Client-Id
        api_key: Ozon 卖家 Api-Key
        endpoint: API 路径，如 "/v3/product/import"
        body: 请求体
        timeout: 超时秒数
        language: 语言（ZH_HANS/EN/RU）

    Returns:
        响应 JSON dict

    Raises:
        OzonError (及子类): 非 2xx 响应 — OzonAuthError(401)/OzonValidationError(400)/
            OzonRateLimitError(429, 可重试)/OzonServerError(5xx, 可重试) 等；
        requests.exceptions.Timeout / ConnectionError: 网络异常（不重试）。
    """
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    if language:
        headers["Accept-Language"] = language

    # 速率限制：每次调用 acquire 一次（在重试循环之外，避免重复计 token）
    _rate_limiter.acquire(endpoint)

    start = time.monotonic()
    try:
        # ⚠️ v0.14 D2: 用共享 session（连接池复用），旧代码裸 requests.post 每次新建 TCP 连接
        from utils.http_session import session as _shared_session
        # tenacity 重试：仅对 OzonRateLimitError(429) 和 OzonServerError(5xx) 重试；
        # OzonAuthError(401) / OzonValidationError(400) 等立即抛出（不重试）
        for attempt in Retrying(
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=_wait_strategy,
            retry=retry_if_exception_type((OzonRateLimitError, OzonServerError)),
            reraise=True,
        ):
            with attempt:
                resp = _shared_session.post(url, json=body, headers=headers, timeout=timeout)
                duration_ms = (time.monotonic() - start) * 1000

                # 构建请求摘要（避免日志过大）
                req_summary = _summarize_request(endpoint, body)
                resp_summary = _summarize_response(endpoint, resp) if resp.ok else None

                log_ozon_api_call(
                    method="POST",
                    endpoint=endpoint,
                    status_code=resp.status_code,
                    duration_ms=duration_ms,
                    request_summary=req_summary,
                    response_summary=resp_summary,
                    error=None if resp.ok else resp.text[:500],
                )

                # typed error mapping（2xx → None；429/5xx 被 tenacity 捕获重试）
                _raise_for_status(resp, endpoint)
                # Defense-in-depth fallback: _raise_for_status 应覆盖所有 >=400，
                # 保留 requests.raise_for_status 兜底任何未映射的边缘状态码
                resp.raise_for_status()
                return resp.json()

    except requests.exceptions.Timeout:
        duration_ms = (time.monotonic() - start) * 1000
        log_ozon_api_call(
            method="POST", endpoint=endpoint, status_code=0,
            duration_ms=duration_ms, error="timeout",
        )
        raise
    except requests.exceptions.ConnectionError as e:
        duration_ms = (time.monotonic() - start) * 1000
        log_ozon_api_call(
            method="POST", endpoint=endpoint, status_code=0,
            duration_ms=duration_ms, error=str(e)[:200],
        )
        raise


def ozon_check_quota(
    client_id: str,
    api_key: str,
    timeout: int = 10,
) -> dict[str, Any]:
    """查询 Ozon 店铺每日创建配额和总产品上限。

    调用 /v4/product/info/limit 获取配额信息。

    Args:
        client_id: Ozon 卖家 Client-Id
        api_key: Ozon 卖家 Api-Key
        timeout: 超时秒数（默认 10s，配额查询应快速返回）

    Returns:
        {
            "ok": bool,               # 是否有可用配额
            "daily_used": int,        # 今日已用创建数
            "daily_limit": int,       # 每日创建上限
            "total_used": int,        # 当前总产品数
            "total_limit": int,       # 总产品上限
            "daily_update_used": int, # 今日已用更新数
            "daily_update_limit": int,# 每日更新上限
            "remaining_daily": int,   # 每日剩余可创建数
            "remaining_total": int,   # 总剩余可创建数
            "error": str | None,      # 错误信息（如果有）
        }
    """
    result = {
        "ok": True,
        "daily_used": 0,
        "daily_limit": 100,
        "total_used": 0,
        "total_limit": 1000,
        "daily_update_used": 0,
        "daily_update_limit": 2000,
        "remaining_daily": 100,
        "remaining_total": 1000,
        "error": None,
    }

    try:
        resp = ozon_post(
            client_id=client_id,
            api_key=api_key,
            endpoint="/v4/product/info/limit",
            body={},
            timeout=timeout,
        )
        data = resp.get("result", {})
        daily = data.get("daily_create", {})
        total = data.get("total", {})
        daily_update = data.get("daily_update", {})

        daily_used = daily.get("usage", 0)
        daily_limit = daily.get("limit", 100)
        total_used = total.get("usage", 0)
        total_limit = total.get("limit", 1000)
        du_used = daily_update.get("usage", 0)
        du_limit = daily_update.get("limit", 2000)

        result.update({
            "daily_used": daily_used,
            "daily_limit": daily_limit,
            "total_used": total_used,
            "total_limit": total_limit,
            "daily_update_used": du_used,
            "daily_update_limit": du_limit,
            "remaining_daily": max(0, daily_limit - daily_used),
            "remaining_total": max(0, total_limit - total_used),
            "ok": (daily_used < daily_limit and total_used < total_limit),
        })

    except Exception as e:
        logger.warning("配额查询失败（将允许继续上传）: %s", str(e))
        result["error"] = str(e)[:200]
        result["ok"] = True  # 查询失败不阻塞上传

    return result


def _summarize_request(endpoint: str, body: dict) -> dict:
    """提取请求关键字段（避免日志过大）。"""
    summary: dict[str, Any] = {}

    # /v3/product/import — 记录 items 数量
    if "/product/import" in endpoint:
        items = body.get("items", [])
        summary["items_count"] = len(items)
        if items:
            summary["offer_ids"] = [i.get("offer_id", "") for i in items[:5]]

    # /description-category/attribute — 记录 category/type
    elif "/description-category" in endpoint:
        summary["description_category_id"] = body.get("description_category_id")
        summary["type_id"] = body.get("type_id")

    # /description-category/attribute/values/search — 记录搜索值
    elif "/values/search" in endpoint:
        summary["attribute_id"] = body.get("attribute_id")
        summary["search_value"] = body.get("value", "")[:50]

    # /product/import/info — 记录 task_id
    elif "/import/info" in endpoint:
        summary["task_id"] = body.get("task_id")

    # /product/info/list — 记录 product_id 列表
    elif "/info/list" in endpoint:
        summary["product_ids"] = body.get("product_id", [])[:5]

    return summary


def _summarize_response(endpoint: str, resp: requests.Response) -> dict:
    """提取响应关键字段。"""
    try:
        data = resp.json()
    except Exception:
        return {}

    summary: dict[str, Any] = {}

    # /v3/product/import — 记录 task_id
    if "/product/import" in endpoint and "result" in data:
        summary["task_id"] = data["result"].get("task_id")

    # /product/import/info — 记录状态
    elif "/import/info" in endpoint and "result" in data:
        items = data["result"].get("items", [])
        summary["items_count"] = len(items)
        if items:
            summary["statuses"] = [i.get("status", "") for i in items[:5]]

    # /description-category/tree — 记录节点数
    elif "/tree" in endpoint and "result" in data:
        summary["root_categories"] = len(data["result"])

    # /description-category/attribute — 记录属性数
    elif "/attribute" in endpoint and "result" in data:
        summary["attributes_count"] = len(data["result"])

    return summary

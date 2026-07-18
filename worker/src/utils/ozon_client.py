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

from utils.logger import get_logger, log_ozon_api_call

logger = get_logger("ozon.api")

BASE_URL = "https://api-seller.ozon.ru"


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
        requests.HTTPError: 非 2xx 响应
    """
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    if language:
        headers["Accept-Language"] = language

    start = time.monotonic()
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
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

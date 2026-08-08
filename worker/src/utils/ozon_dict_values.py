"""Ozon 字典值查询封装（v0.25 T2）— /values/search 语言链。

从 validation_retry_loop 抽取为公共工具，prepare 与 retry 共用。
"""
from __future__ import annotations

import logging
from typing import Optional

from utils.http_session import session

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api-seller.ozon.ru/v1/description-category/attribute/values/search"
LIST_URL = "https://api-seller.ozon.ru/v1/description-category/attribute/values"


def search_dictionary_values(
    client_id: str,
    api_key: str,
    attribute_id: int,
    description_category_id: int,
    type_id: int,
    value: str,
    language: str = "RU",
) -> list[dict]:
    """按关键词搜索字典值（RU 优先；ZH_HANS 兜底）。返回 values/search 数组。"""
    # ⚠️ PR-1: 官方 /values/search value 最少 2 字符，短词直接返回（避免无效 API 调用）
    if not value or len(str(value).strip()) < 2:
        return []
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    payload = {
        "attribute_id": int(attribute_id),
        "description_category_id": int(description_category_id),
        "type_id": int(type_id),
        "value": value,
        # ⚠️ PR-2: 官方 /values/search 无 language 参数（语言无关），不再塞 body；
        # language 参数仅控制下方 RU→ZH_HANS fallback 链
        "limit": 50,
    }
    try:
        resp = session.post(SEARCH_URL, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            result = resp.json().get("result") or []
            if result:
                return result
    except Exception as e:
        logger.warning("字典值 search 失败(attr=%s, lang=%s): %s", attribute_id, language, e)
    if language != "ZH_HANS":
        return search_dictionary_values(
            client_id, api_key, attribute_id, description_category_id, type_id,
            value, language="ZH_HANS",
        )
    return []


def list_dictionary_values(
    client_id: str,
    api_key: str,
    attribute_id: int,
    description_category_id: int,
    type_id: int,
    language: str = "RU",
    limit: int = 200,
) -> list[dict]:
    """列表模式拉取字典值（/values，分页），用于 search 搜不到时的兜底（如 8292 不合并）。"""
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    all_values: list[dict] = []
    last_id = 0
    try:
        for _ in range(5):
            payload = {
                "attribute_id": int(attribute_id),
                "description_category_id": int(description_category_id),
                "type_id": int(type_id),
                "language": language,
                "limit": limit,
                "last_value_id": last_id,
            }
            resp = session.post(LIST_URL, headers=headers, json=payload, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            result = data.get("result") or []
            all_values.extend(result)
            if not data.get("has_next", False) or not result:
                break
            last_id = result[-1].get("id", 0)
            if not last_id:
                break
    except Exception as e:
        logger.warning("字典值 list 失败(attr=%s): %s", attribute_id, e)
    return all_values

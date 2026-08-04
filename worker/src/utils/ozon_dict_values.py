"""Ozon 字典值查询封装（v0.25 T2）— /values/search 语言链。

从 validation_retry_loop 抽取为公共工具，prepare 与 retry 共用。
"""
from __future__ import annotations

import logging
from typing import Optional

from utils.http_session import session

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api-seller.ozon.ru/v1/description-category/attribute/values/search"


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
    if not value:
        return []
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    payload = {
        "attribute_id": int(attribute_id),
        "description_category_id": int(description_category_id),
        "type_id": int(type_id),
        "value": value,
        "language": language,
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

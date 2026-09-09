"""Ozon 字典值查询封装（v0.25 T2）— /values/search 语言链。

从 validation_retry_loop 抽取为公共工具，prepare 与 retry 共用。
"""
from __future__ import annotations

import logging
from typing import Optional

# F-F01（2026-09-09 审计）：HTTP 层收敛 ozon_post——全局限流 + 429/5xx 有界重试
# + 类型化错误；本模块对外语义不变（search/list 失败返回 []，RU 补查失败返回 fallback）
from utils.ozon_client import ozon_post

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
        result = ozon_post(client_id, api_key,
                           "/v1/description-category/attribute/values/search",
                           payload, timeout=15).get("result") or []
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
            data = ozon_post(client_id, api_key,
                             "/v1/description-category/attribute/values",
                             payload, timeout=15)
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


def fetch_ru_dict_value(
    client_id: str,
    api_key: str,
    description_category_id: int,
    type_id: int,
    attribute_id: int,
    dict_id: int,
    fallback: str = "",
) -> str:
    """按 dictionary_value_id 精确查同值的俄语文本（v0.40.1）。

    背景: dict_lookup / search 可能命中 ZH_HANS 中文缓存，8229(类型) value 中文
    置空后上传 value="" → Ozon 报「照片与类型不符」DESCRIPTION_DECLINE。
    用 /values(RU) 列表模式按 dict_id 精确匹配拿俄语文本（search 按数字 id 搜不到）。
    失败 → 返回 fallback，再失败 → 空串（调用方容忍）。

    v0.62 R2: 分页修复 — 此前单次 POST（limit=5000, last_value_id=0）无分页循环，
    当字典值 >5000 条（8229 类型等大字典）目标 id 不在首页 → 返回 fallback → 空值
    → 「无法获取字典值，跳过」→ 必填属性缺失。现与 list_dictionary_values 同款
    分页（最多 5 页，has_next 驱动），命中即返回，翻完返回 fallback。
    """
    if not dict_id:
        return fallback
    try:
        last_id = 0
        for _ in range(5):
            payload = {
                "attribute_id": int(attribute_id),
                "description_category_id": int(description_category_id),
                "type_id": int(type_id),
                "language": "RU",
                "limit": 5000,
                "last_value_id": last_id,
            }
            data = ozon_post(client_id, api_key,
                             "/v1/description-category/attribute/values",
                             payload, timeout=15)
            result = data.get("result") or []
            for r in result:
                if int(r.get("id") or 0) == int(dict_id) and r.get("value"):
                    return str(r["value"])
            if not data.get("has_next", False) or not result:
                break
            last_id = result[-1].get("id", 0)
            if not last_id:
                break
        return fallback
    except Exception as e:
        logger.debug("RU 字典值补查异常: %s", e)
        return fallback


def fetch_dictionary_values(
    client_id: str,
    api_key: str,
    attribute_id: int,
    description_category_id: int,
    type_id: int,
    language: str = "ZH_HANS",
    limit: int = 2000,
) -> Optional[list[dict]]:
    """分页拉取属性字典值（/values）。assemble 的 `_fetch_dict_values_from_ozon`
    收敛委托至此（F-F01：字典 HTTP 唯一入口）。

    ✅ v0.72 三桶纪律（语义与 assemble 原实现逐字对齐）：
    - limit 契约上限 2000（5000+ 会被 Ozon 静默钳，违约调用）；
    - 首页即 has_next（>2000 值的巨型字典如品牌 85 无底洞）→ 取首页即止不再翻页
      （ephemeral：物化拦截由调用方缓存层负责，首页值仍可供本次匹配，
       value→id 精确查走 /values/search）；
    - 失败返回 None（区别于空字典 []）——调用方以 None 判断「不写缓存」。
    """
    values: list[dict] = []
    last_id = 0
    try:
        while True:
            payload = {
                "attribute_id": int(attribute_id),
                "description_category_id": int(description_category_id),
                "type_id": int(type_id),
                "language": language,
                "limit": min(int(limit), 2000),
                "last_value_id": last_id,
            }
            data = ozon_post(client_id, api_key,
                             "/v1/description-category/attribute/values",
                             payload, timeout=30)
            page = data.get("result") or []
            if not page:
                break
            values.extend(page)
            if not data.get("has_next", False):
                break
            if not last_id:
                # 首页即 has_next → 巨型字典，取首页即止（ephemeral）
                logger.info("attr=%s 字典 >%d 值（巨型），取首页即止", attribute_id, limit)
                break
            nxt = int(page[-1].get("id") or 0)
            if not nxt or nxt == last_id:  # 防死循环
                break
            last_id = nxt
        return values
    except Exception as e:
        logger.warning("字典值拉取失败(attr=%s): %s", attribute_id, e)
        return None

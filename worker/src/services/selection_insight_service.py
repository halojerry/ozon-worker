"""选品洞察聚合服务 — discovery_runs.candidates_json → selection_insights。

数据源是 discovery_runs（NOT blue_ocean_queries）：后者是蓝海关键词指标
（count/ca/uniq_queries_wca，字段形状完全不同），选品洞察需要的是候选产品的
售价/利润/1688 匹配数/销量，只能从 candidates_json 逐候选聚合抽出。

- 全局共享（不按 tenant 隔离）：用 contributed_by_token_id 标记来源，同一用户
  重复上报同一 keyword 走 upsert 覆盖（表级唯一键 uq_selection_insight_keyword_token）。
- 白名单裁剪：只读 REPORT_FIELDS 里的标量字段（skill 端已裁掉
  match_1688_images/competing_seller_list/ozon_images 等大字段），本服务也绝不
  重写冗余大字段——matches 只统计边界（match_1688_url/match_1688_price 是否存在）。
- 非致命：异常只 log warning，绝不阻断 discovery run 上报主链路。

与 queries_service / analytics_service 同风格：raw SQL text() + 绑定参数，get_engine() 连接池。
"""

from __future__ import annotations

import logging
from typing import Any, Final

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# 聚合字段（浮点均值/整数求和）。candidates_json 是 skill 白名单裁剪后的列表，
# 无大字段（match_1688_images/competing_seller_list 不在此列——仅作匹配计数哨兵遍历）。
_PRICE_KEY: Final[str] = "ozon_price"
_MARGIN_KEY: Final[str] = "profit_margin"
_SOLD_KEY: Final[str] = "monthly_sales"
_MATCH_URL_KEY: Final[str] = "match_1688_url"
_MATCH_PRICE_KEY: Final[str] = "match_1688_price"
_CATEGORY_KEY: Final[str] = "category"


def _to_float(value: Any) -> float | None:
    """数值强转；None/''/坏值 → None（不参与均值）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    """整数值强转；None/''/坏值 → 0。"""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _has_1688_match(candidate: dict[str, Any]) -> bool:
    """判定候选是否命中 1688 货源：match_1688_url 或 match_1688_price 任一存在即算。

    只用白名单标量字段，绝不触碰 match_1688_images/competing_seller_list。
    """
    return bool(
        candidate.get(_MATCH_URL_KEY)
        or candidate.get(_MATCH_PRICE_KEY) is not None
        or _to_float(candidate.get(_MATCH_PRICE_KEY)) is not None
    )


def aggregate_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从 candidates_json 逐候选聚合选品洞察标量；空列表 → None（调用方跳过写入）。

    返回固定结构（只含表内可写的标量字段，不含任何大字段）：
      {category_path, avg_price_rub, avg_profit_margin, match_1688_count, sold_count}

    avg_price_rub：候选 ozon_price 的均值（缺值候选不参与）；
    avg_profit_margin：候选 profit_margin 的均值（缺值候选不参与）；
    match_1688_count：命中 1688 货源的候选数；
    sold_count：候选 monthly_sales 之和。
    """
    if not candidates:
        return None

    prices: list[float] = []
    margins: list[float] = []
    match_count = 0
    sold_total = 0
    category_path: str = ""

    for c in candidates:
        if not isinstance(c, dict):
            continue
        price = _to_float(c.get(_PRICE_KEY))
        if price is not None:
            prices.append(price)
        margin = _to_float(c.get(_MARGIN_KEY))
        if margin is not None:
            margins.append(margin)
        if _has_1688_match(c):
            match_count += 1
        sold_total += _to_int(c.get(_SOLD_KEY))
        if not category_path and c.get(_CATEGORY_KEY):
            category_path = str(c[_CATEGORY_KEY])

    return {
        "category_path": category_path or None,
        "avg_price_rub": round(sum(prices) / len(prices), 2) if prices else None,
        "avg_profit_margin": round(sum(margins) / len(margins), 4) if margins else None,
        "match_1688_count": match_count,
        "sold_count": sold_total,
    }


def upsert_from_discovery_run(tenant_token: str, keyword: str, candidates: list[dict[str, Any]]) -> bool:
    """从 discovery run 聚合后 upsert 一行 selection_insights。

    - candidates_json 为空 → 跳过不写（返回 False）。
    - 唯一键 (keyword, contributed_by_token_id) ON CONFLICT DO UPDATE。
    - 非致命：任何异常 log warning 返回 False，绝不 reraise。

    :param tenant_token: 上报用户 clean token（去 sk- 前缀后的 key，即 contributed_by_token_id）。
    :param keyword: 选品关键词。
    :param candidates: discovery run 的 candidates_json（白名单裁剪后的候选列表）。
    :return: 写库成功返回 True，跳过/失败返回 False。
    """
    if not candidates:
        logger.info("selection insight 跳过写入：candidates_json 为空 (keyword=%s)", keyword)
        return False

    agg = aggregate_candidates(candidates)
    if agg is None:
        return False

    params: dict[str, Any] = {
        "keyword": keyword or "",
        "category_path": agg["category_path"],
        "avg_price_rub": agg["avg_price_rub"],
        "avg_profit_margin": agg["avg_profit_margin"],
        "match_1688_count": agg["match_1688_count"],
        "sold_count": agg["sold_count"],
        "contributed_by_token_id": tenant_token,
        "source": "fetched",
    }

    _UPSERT_SQL = text("""
        INSERT INTO selection_insights
            (keyword, category_path, avg_price_rub, avg_profit_margin,
             match_1688_count, sold_count, source, contributed_by_token_id)
        VALUES
            (:keyword, :category_path, :avg_price_rub, :avg_profit_margin,
             :match_1688_count, :sold_count, :source, :contributed_by_token_id)
        ON CONFLICT (keyword, contributed_by_token_id) DO UPDATE SET
            category_path = EXCLUDED.category_path,
            avg_price_rub = EXCLUDED.avg_price_rub,
            avg_profit_margin = EXCLUDED.avg_profit_margin,
            match_1688_count = EXCLUDED.match_1688_count,
            sold_count = EXCLUDED.sold_count,
            source = EXCLUDED.source
    """)

    try:
        with get_engine().begin() as conn:
            conn.execute(_UPSERT_SQL, params)
    except Exception as exc:
        logger.warning("selection insight upsert 失败（不阻断上报）(keyword=%s): %s", keyword, exc)
        return False
    return True

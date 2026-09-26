"""物流费率公共模块（v0.29.x 抽取自 pricing_node）。

职责：
- 探测店铺 3PL/服务等级（Ozon /v2/delivery-method/list）
- 按 3PL+服务等级+重量+尺寸 查 PG logistics_rates 表（Q1→Q2→Q3→RETS fallback→默认）
- 体积重计费（billable = max(实际重, 体积/vol_divisor)）

供两处复用：
1. pricing_node（上架定价主链路，行为与抽取前逐字一致）
2. POST /api/v1/logistics/quote（skill 查询端点，选品利润估算用）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

# F-F01（2026-09-09 审计）：Ozon 直连收敛 ozon_post（全局限流 + 429/5xx 重试），
# 移除私有 requests.Session（每进程独享连接池绕过全局配额）
from utils.ozon_client import ozon_post

logger = logging.getLogger(__name__)

KNOWN_TPLS = ["RETS", "ATC", "ZTO", "Ural", "GUOO", "CEL", "GBS", "OYX", "ABT", "Xingyuan", "Tanais"]
KNOWN_SERVICE_LEVELS = ["Standard", "Economy", "Express"]


def canonical_tpl_provider(value: Optional[str]) -> Optional[str]:
    """tpl_provider 大小写不敏感归一到 KNOWN_TPLS 权威拼写（v0.81 安全收尾）。

    用户可控自由文本（POST /api/v1/logistics/quote 的 body.tpl_provider）在进
    任何 DB 查询前先归一：命中 KNOWN_TPLS（忽略大小写/首尾空白）→ 权威拼写；
    未命中剥空白后**原样透传**——下游 SQLAlchemy ORM ``==`` 比较生成绑定参数
    （参数化证明见 query_logistics_cost docstring），未知值只会查不到行走既有
    fallback 链，无注入面，也无需在此硬拒（保持 Q3 跨 3PL 兜底语义）。
    """
    if not isinstance(value, str) or not value.strip():
        return value
    stripped = value.strip()
    for known in KNOWN_TPLS:
        if known.lower() == stripped.lower():
            return known
    return stripped


def canonical_service_level(value: Optional[str]) -> Optional[str]:
    """service_level 归一到 KNOWN_SERVICE_LEVELS 权威拼写（口径同 canonical_tpl_provider）。"""
    if not isinstance(value, str) or not value.strip():
        return value
    stripped = value.strip()
    for known in KNOWN_SERVICE_LEVELS:
        if known.lower() == stripped.lower():
            return known
    return stripped


def get_store_logistics_config(ozon_client_id: str, ozon_api_key: str) -> tuple[str, str]:
    """查询 Ozon API 获取店铺第三方物流(3PL)和服务等级。

    失败/异常回退 ("RETS", "Standard")，绝不抛异常。
    """
    try:
        data: Any = ozon_post(
            ozon_client_id, ozon_api_key,
            "/v2/delivery-method/list", {"limit": 100}, timeout=30,
        )
        methods = data.get("delivery_methods", [])
        if not methods:
            return ("RETS", "Standard")

        first_method_name = methods[0].get("name", "")
        tpl_provider = "RETS"
        service_level = "Standard"

        for tpl in KNOWN_TPLS:
            if tpl.lower() in first_method_name.lower():
                tpl_provider = tpl
                break

        name_upper = first_method_name.upper()
        if "ECONOMY" in name_upper:
            service_level = "Economy"
        elif "EXPRESS" in name_upper:
            service_level = "Express"
        elif "STANDARD" in name_upper:
            service_level = "Standard"

        logger.info(f"店铺物流配置: 3PL={tpl_provider}, 服务等级={service_level}, 配送方式名={first_method_name}")
        return (tpl_provider, service_level)

    except Exception as e:
        logger.error(f"查询店铺物流配置失败: {str(e)}")
        return ("RETS", "Standard")


def query_logistics_cost(
    weight: float,
    depth_cm: float,
    width_cm: float,
    height_cm: float,
    tpl_provider: str = "RETS",
    service_level: str = "Standard",
) -> tuple[float, str, dict]:
    """从 PG 物流费率表查询运费（Q1→Q2→Q3→RETS fallback→默认）。

    Returns: (logistics_cost_cny, channel_name, detail_dict)
    - detail_dict: {tpl_provider, service_level, scoring_group, base_cost,
      per_gram_rate, billable_weight, weight, dims_cm, fallback_chain}

    参数化证明（v0.81 安全收尾，Mimosa High「_logistics_quote_sync SQL 注入」
    判定为 ORM 误报的反驳留痕）：本函数全部 4 处 ``session.execute`` 均为
    SQLAlchemy ORM ``select(...).where(...)``——tpl_provider/service_level（用户
    可控自由文本）经 ``LogisticsRate.tpl_provider == :x`` 等比较符生成**绑定
    参数**（compiled SQL 形如 ``WHERE tpl_provider = %(tpl_provider_1)s``，值在
    compiled.params 里，从不进 SQL 文本）；weight/dims 在入口
    ``main._logistics_quote_sync`` 已 ``float()`` 强转。全链无 f-string/percent/
    %s 拼 SQL（项目纪律同 utils/like_escape.py）。入口白名单归一见
    ``canonical_tpl_provider``/``canonical_service_level``（quote_logistics 接线）。
    tests/test_sec_closeout_v081.py 以 ``stmt.compile().params`` 断言恶意串只
    出现在绑定参数、不出现在 SQL 文本。
    """
    from storage.database.db import get_session
    from storage.database.shared.model import LogisticsRate
    from sqlalchemy import and_, select

    fallback_chain: list[str] = []
    session = get_session()
    try:
        dims = sorted([float(depth_cm), float(width_cm), float(height_cm)], reverse=True)
        longest_cm = dims[0] if dims else 0.0
        sum_cm = sum(dims)
        w_int = int(weight)

        # 查询1: 3PL + 服务等级 + 重量 + 尺寸全匹配
        rows = session.execute(
            select(LogisticsRate).where(
                and_(
                    LogisticsRate.tpl_provider == tpl_provider,
                    LogisticsRate.service_level == service_level,
                    LogisticsRate.weight_min <= w_int,
                    LogisticsRate.weight_max >= w_int,
                    LogisticsRate.sum_limit_cm >= int(sum_cm),
                    LogisticsRate.longest_limit_cm >= int(longest_cm),
                )
            ).order_by(LogisticsRate.base_cost.asc()).limit(1)
        ).scalars().all()
        if not rows:
            fallback_chain.append("Q1_size_miss")

        # 查询2: 尺寸不满足，仅按重量 + 3PL匹配
        if not rows:
            rows = session.execute(
                select(LogisticsRate).where(
                    and_(
                        LogisticsRate.tpl_provider == tpl_provider,
                        LogisticsRate.service_level == service_level,
                        LogisticsRate.weight_min <= w_int,
                        LogisticsRate.weight_max >= w_int,
                    )
                ).order_by(LogisticsRate.base_cost.asc()).limit(1)
            ).scalars().all()
            if not rows:
                fallback_chain.append("Q2_tpl_miss")

        # 查询3: 该3PL无匹配，同服务等级其他3PL
        if not rows:
            rows = session.execute(
                select(LogisticsRate).where(
                    and_(
                        LogisticsRate.service_level == service_level,
                        LogisticsRate.weight_min <= w_int,
                        LogisticsRate.weight_max >= w_int,
                        LogisticsRate.sum_limit_cm >= int(sum_cm),
                        LogisticsRate.longest_limit_cm >= int(longest_cm),
                    )
                ).order_by(LogisticsRate.base_cost.asc()).limit(1)
            ).scalars().all()
            if not rows:
                fallback_chain.append("Q3_cross_tpl_miss")

        if rows:
            row = rows[0]
            base_cost = float(row.base_cost)
            per_gram_rate = float(row.per_gram_rate)
            vol_divisor = int(row.vol_weight_divisor)

            billable_weight = weight
            if vol_divisor > 1:
                vol_weight = (float(depth_cm) * float(width_cm) * float(height_cm)) / vol_divisor
                billable_weight = max(weight, vol_weight)

            logistics_cost = base_cost + per_gram_rate * billable_weight
            channel_name = f"{row.tpl_provider}_{row.service_level}_{row.scoring_group}"

            logger.info(
                f"PG 物流费率匹配: 3PL={row.tpl_provider}, 等级={row.service_level}, 评分组={row.scoring_group}, "
                f"weight={weight}g, billable={billable_weight:.1f}g, "
                f"base={base_cost}, rate={per_gram_rate}/g, cost={logistics_cost:.2f} CNY"
            )
            return (logistics_cost, channel_name, {
                "tpl_provider": row.tpl_provider,
                "service_level": row.service_level,
                "scoring_group": row.scoring_group,
                "base_cost": base_cost,
                "per_gram_rate": per_gram_rate,
                "billable_weight": round(billable_weight, 1),
                "weight": weight,
                "dims_cm": [depth_cm, width_cm, height_cm],
                "fallback_chain": fallback_chain,
            })

        # 最终 fallback：RETS Standard
        fallback_chain.append("RETS_standard")
        fb_row = session.execute(
            select(LogisticsRate).where(
                and_(
                    LogisticsRate.tpl_provider == "RETS",
                    LogisticsRate.service_level == "Standard",
                    LogisticsRate.weight_min <= w_int,
                    LogisticsRate.weight_max >= w_int,
                )
            ).order_by(LogisticsRate.weight_min.asc()).limit(1)
        ).scalar_one_or_none()

        if fb_row:
            base_cost = float(fb_row.base_cost)
            per_gram_rate = float(fb_row.per_gram_rate)
            logistics_cost = base_cost + per_gram_rate * weight
            logger.warning(f"物流费率最终fallback到RETS Standard: cost={logistics_cost:.2f}")
            return (logistics_cost, "RETS_Standard_fallback", {
                "tpl_provider": "RETS",
                "service_level": "Standard",
                "scoring_group": fb_row.scoring_group,
                "base_cost": base_cost,
                "per_gram_rate": per_gram_rate,
                "billable_weight": weight,
                "weight": weight,
                "dims_cm": [depth_cm, width_cm, height_cm],
                "fallback_chain": fallback_chain,
            })

        # 绝对最后 fallback
        fallback_chain.append("default_weight_rate")
        logger.warning(f"PG 物流费率表无数据，使用默认费率")
        return (max(5.0, weight * 0.05), "default_fallback", {
            "tpl_provider": tpl_provider,
            "service_level": service_level,
            "scoring_group": "",
            "base_cost": 0.0,
            "per_gram_rate": 0.05,
            "billable_weight": weight,
            "weight": weight,
            "dims_cm": [depth_cm, width_cm, height_cm],
            "fallback_chain": fallback_chain,
        })

    except Exception as e:
        logger.error(f"PG 物流费率查询失败: {str(e)}")
        return (max(5.0, weight * 0.05), "error_fallback", {
            "tpl_provider": tpl_provider,
            "service_level": service_level,
            "scoring_group": "",
            "base_cost": 0.0,
            "per_gram_rate": 0.05,
            "billable_weight": weight,
            "weight": weight,
            "dims_cm": [depth_cm, width_cm, height_cm],
            "fallback_chain": fallback_chain + ["error"],
        })
    finally:
        session.close()


def quote_logistics(
    ozon_client_id: Optional[str],
    ozon_api_key: Optional[str],
    weight: float,
    depth_cm: float,
    width_cm: float,
    height_cm: float,
    tpl_provider: Optional[str] = None,
    service_level: Optional[str] = None,
) -> dict:
    """一键报价: 探测 3PL(可覆盖) → 查费率表 → 返回完整明细。

    端点 /api/v1/logistics/quote 与 skill 端共用。tpl_provider/service_level
    先过白名单归一（canonical_tpl_provider/canonical_service_level，v0.81
    安全收尾）再进查询链。
    """
    tpl = canonical_tpl_provider(tpl_provider)
    svc = canonical_service_level(service_level)
    if not tpl or not svc:
        if ozon_client_id and ozon_api_key:
            tpl, svc = get_store_logistics_config(ozon_client_id, ozon_api_key)
        else:
            tpl, svc = tpl or "RETS", svc or "Standard"

    cost, channel, detail = query_logistics_cost(
        weight, depth_cm, width_cm, height_cm, tpl, svc,
    )
    detail["logistics_cost_cny"] = round(cost, 2)
    detail["channel"] = channel
    detail["tpl_provider_used"] = tpl
    detail["service_level_used"] = svc
    return detail

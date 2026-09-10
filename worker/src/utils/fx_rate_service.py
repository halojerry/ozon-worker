"""CNY→RUB 汇率服务（BL-01：exchange_rates 死缓存事故根治）。

背景（2026-09-11 仓库治理审计 BL-01）：exchange_rates 表的写侧
LocalDBManager.set_exchange_rate 全仓零调用（死缓存）——表里只有手工/初始化
种过的旧值，24h 新鲜度一过 get_exchange_rate 恒 None，pricing_node 兜底恒
12.0 且无任何告警/留痕。本模块把「查汇率」升级为三级源链并让写侧真正工作：

- fetch_cny_rub_live()      拉取 open.er-api.com 实时 CNY→RUB；任何异常/缺键/
                            非正数一律返回 None（绝不 raise）。
- resolve_cny_rub_rate()    ① PG 缓存（get_exchange_rate 读侧自带 24h 新鲜度）
                            命中 → (rate, "pg_cache")；
                            ② 未命中/过期 → live 拉取成功 → set_exchange_rate
                            回写（写失败仅 warning 不中断）→ (rate, "live_fetch")；
                            ③ 都失败 → (12.0, "fallback_12")。
- refresh_cny_rub_if_due()  24h 节流的周期刷新入口（main.py 周期循环 lazy
                            import 调用），整体吞错只 warning。

source 值由 pricing_node 透传进 pricing_info marks（exchange_rate_source /
exchange_rate_fallback），供审计「价格离谱是否源于兜底汇率」。
"""

import logging
import time
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_FX_API_URL = "https://open.er-api.com/v6/latest/CNY"
_FX_HTTP_TIMEOUT = 10          # 秒；er-api 免 key 免费档，超时即放弃走兜底
_FALLBACK_RATE = 12.0          # 与 pricing_node 历史兜底值逐字一致
_REFRESH_INTERVAL = 86400      # 24h 节流（与 get_exchange_rate 新鲜度窗口一致）

_LAST_AT: float = 0.0          # 模块级节流游标（进程内共享即可）


def fetch_cny_rub_live() -> Optional[float]:
    """拉取实时 CNY→RUB 汇率；任何异常/缺键/非正数返回 None（绝不 raise）。"""
    try:
        resp = requests.get(_FX_API_URL, timeout=_FX_HTTP_TIMEOUT)
        payload = resp.json() or {}
        rate = float(payload["rates"]["RUB"])
    except Exception as exc:
        logger.warning("CNY→RUB 实时汇率拉取失败（降级兜底）: %s", exc)
        return None
    if not rate > 0:  # 0/负数/NaN 一律不可信（NaN > 0 为 False）
        logger.warning("CNY→RUB 实时汇率非正数（%r），视为无效", rate)
        return None
    return rate


def resolve_cny_rub_rate() -> Tuple[float, str]:
    """三级源解析 CNY→RUB：PG 缓存 → live 拉取并回写 → 12.0 兜底。

    Returns:
        (rate, source)；source ∈ {"pg_cache", "live_fetch", "fallback_12"}。
    """
    # ① PG 缓存（读侧自带 24h 新鲜度；读失败按未命中降级，不让 PG 故障放大成定价失败）
    try:
        from utils.local_db_manager import LocalDBManager
        rate = LocalDBManager().get_exchange_rate("CNY", "RUB")
        if rate and float(rate) > 0:
            return float(rate), "pg_cache"
    except Exception as exc:
        logger.warning("PG 汇率缓存读取失败（降级 live 拉取）: %s", exc)

    # ② live 拉取 + 回写死缓存（写失败仅 warning——回写是增益不是依赖）
    rate = fetch_cny_rub_live()
    if rate:
        try:
            from utils.local_db_manager import LocalDBManager
            LocalDBManager().set_exchange_rate("CNY", "RUB", rate)
        except Exception as exc:
            logger.warning("PG 汇率缓存回写失败（不中断，本次仍用 live 值）: %s", exc)
        return rate, "live_fetch"

    # ③ 双失败 → 历史兜底值（与 pricing_node 旧行为逐字一致）
    logger.warning(
        "CNY→RUB 汇率 PG 未命中且 live 拉取失败，使用默认汇率 %s", _FALLBACK_RATE
    )
    return _FALLBACK_RATE, "fallback_12"


def refresh_cny_rub_if_due() -> None:
    """24h 节流的周期刷新（main.py 周期循环 lazy import）；整体吞错只 warning。

    预热 PG 缓存让定价主路径多数时候走 pg_cache（live 拉取的 10s 超时不进定价关键路径）。
    """
    global _LAST_AT
    now = time.time()
    if now - _LAST_AT < _REFRESH_INTERVAL:
        return
    _LAST_AT = now  # 先占坑再执行：失败也等下个 24h 周期，不打满节流
    try:
        rate, source = resolve_cny_rub_rate()
        logger.info("CNY→RUB 汇率周期刷新完成: rate=%s source=%s", rate, source)
    except Exception as exc:
        logger.warning("CNY→RUB 汇率周期刷新失败（非致命）: %s", exc)

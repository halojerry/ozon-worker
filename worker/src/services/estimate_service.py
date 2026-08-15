"""M1.2: 草稿预估售价/利润服务 — 纯读派生数据（不落库、不调 Ozon 上架）。

输入 envelope（三层结构），输出与 pricing_node 同源的预估价格：
- 采购成本：draft.purchase_cost / draft.cost_cny（与 pricing_node Step 1 一致）
- 尺寸/重量：utils.weight_dimension_normalizer（与 pricing_node 同源归一化）
- 物流费：utils.logistics_quote.query_logistics_cost（同源 /api/v1/logistics/quote）
- 定价公式：utils.pricing_estimate.compute_price（唯一定义处）

⚠️ 铁律：前端/skill 一律不写定价公式；本服务也只做「取数 + 调共享公式」。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from utils.logistics_quote import query_logistics_cost
from utils.pricing_estimate import compute_price
from utils.weight_dimension_normalizer import normalize_weight_dimensions

logger = logging.getLogger(__name__)

# 与 pricing_node 逐字一致（Step 3 包装成本固定值 + Step 1 成本兜底）
PACKAGING_COST_CNY = 2.0
DEFAULT_COST_CNY = 10.0


def estimate_from_envelope(
    envelope: dict,
    *,
    exchange_rate: Optional[float] = None,
    currency_code: Optional[str] = None,
    margin_rate: Optional[float] = None,
    commission_rate: Optional[float] = None,
    fx_buffer: Optional[float] = None,
) -> dict:
    """根据信封草稿计算预估售价/利润（与 pricing_node 同源公式）。

    - 纯读：不落库、不调 Ozon 上架 API。
    - exchange_rate 传 None → 按 CNY 处理（与 skill 估算一致，端点不接汇率实时源）。
    - 物流费无 Ozon 凭证 → 默认 RETS/Standard（query_logistics_cost 默认参数）。
    - currency_code 解析顺序：请求覆盖 → extensions.currency_code → exchange_rate
      有值则 RUB → 否则 CNY。
    """
    draft = (envelope.get("draft") or {}) if isinstance(envelope, dict) else {}
    extensions = (envelope.get("extensions") or {}) if isinstance(envelope, dict) else {}

    # Step 1: 采购成本（兼容 purchase_cost/cost_cny，与 pricing_node 逐字一致）
    cost_cny: float = float(draft.get("cost_cny", 0) or draft.get("purchase_cost", 0) or 0)
    if cost_cny <= 0:
        cost_cny = DEFAULT_COST_CNY
        logger.warning("⚠️ 预估：purchase_cost 为 0 或空，使用默认值: 10 CNY")

    # Step 2: 重量/尺寸归一化（与 pricing_node 同源 weight_dimension_normalizer）
    dims_obj = draft.get("dimensions") or {}
    if not (isinstance(dims_obj, dict) and dims_obj):
        dims_obj = {
            "length": draft.get("depth", 0) or draft.get("length", 0),
            "width": draft.get("width", 0),
            "height": draft.get("height", 0),
        }
    weight, dims_mm, _marks = normalize_weight_dimensions(
        draft.get("weight", 0), dims_obj, extensions
    )
    # mm → cm（物流费率表按 cm 匹配；逐维度补默认值，与 pricing_node 一致）
    depth: float = dims_mm["length"] / 10.0
    width: float = dims_mm["width"] / 10.0
    height: float = dims_mm["height"] / 10.0
    if depth <= 0:
        depth = 3.0
    if width <= 0:
        width = 2.0
    if height <= 0:
        height = 0.5

    # Step 3: 物流费（同源 query_logistics_cost；无凭证 → RETS/Standard 默认）
    logistics_cost, _channel, _detail = query_logistics_cost(weight, depth, width, height)

    # Step 4: 配置（请求覆盖优先，其次 extensions 默认值，与 pricing_node 一致）
    eff_margin = float(margin_rate if margin_rate is not None else extensions.get("margin_rate", 0.25))
    eff_commission = float(
        commission_rate if commission_rate is not None else extensions.get("commission_rate", 0.10)
    )
    eff_fx = float(fx_buffer if fx_buffer is not None else extensions.get("fx_buffer", 0.05))

    # Step 5: 货币判定（请求覆盖 → extensions → exchange_rate 有值则 RUB → CNY）
    eff_currency = (currency_code or str(extensions.get("currency_code", "") or "")).upper()
    if eff_currency not in ("CNY", "RUB"):
        eff_currency = "RUB" if exchange_rate is not None else "CNY"

    total_cost_cny: float = cost_cny + logistics_cost + PACKAGING_COST_CNY
    result = compute_price(
        total_cost_cny=total_cost_cny,
        margin_rate=eff_margin,
        commission_rate=eff_commission,
        fx_buffer=eff_fx,
        currency_code=eff_currency,
        exchange_rate=exchange_rate,
    )
    return {
        "price": result["price"],
        "old_price": result["old_price"],
        "profit_cny": result["profit_cny"],
        "profit_rate": result["profit_rate"],
        "logistics_cost_cny": round(logistics_cost, 2),
        "currency": "CNY" if (exchange_rate is None or eff_currency == "CNY") else "RUB",
    }

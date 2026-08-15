"""共享定价公式（M1.2）— 唯一定义处，pricing_node 与 estimate 端点同源。

公式逐字复制自 pricing_node.py 单 SKU 主定价路径（Step 5-6，v0.40 实测验证）：
- 售价 = 总成本 × (1 + margin) / (1 - commission)     [CNY 店铺，无汇率风险，不用 fx_buffer]
- RUB 店铺再 × (1 + fx_buffer) × exchange_rate
- old_price：Ozon 折扣规则 ≥20%（price≤25 时至少加 5）
- profit_cny：CNY = price - total_cost；RUB = price/exchange_rate - total_cost
- profit_rate = profit_cny / total_cost

⚠️ 修改本文件公式必须同步更新 pricing_node 行为锁定测试（test_estimate_endpoint.py），
   前端/skill 一律禁止自行实现定价公式（v0.40 统一纪律）。
"""
from __future__ import annotations

import math
from typing import Any


def compute_price(
    total_cost_cny: float,
    margin_rate: float,
    commission_rate: float,
    fx_buffer: float,
    currency_code: str,
    exchange_rate: float | None = None,
) -> dict[str, Any]:
    """计算预估售价/利润（与 pricing_node 公式逐字一致）。

    Args:
        total_cost_cny: 总成本 = 采购 + 物流 + 包装（CNY）。
        margin_rate: 目标利润率（如 0.25）。
        commission_rate: Ozon 佣金率（如 0.10）。
        fx_buffer: 汇率缓冲（仅 RUB 店铺生效，如 0.05）。
        currency_code: "CNY" 或 "RUB"。
        exchange_rate: CNY→RUB 汇率；None → 按 CNY 处理（估计端点不接汇率实时源，
            与 skill 估算一致——此时即使 currency_code=RUB 也走 CNY 路径）。

    Returns:
        {"price": int, "old_price": int, "profit_cny": float, "profit_rate": float}
    """
    # pricing_node Step 5：防止除零
    commission_divisor: float = (1.0 - commission_rate)
    if commission_divisor <= 0:
        commission_divisor = 0.9

    # 无汇率 → 按 CNY 处理（与 skill 估算一致；CNY 路径不使用 exchange_rate）
    if exchange_rate is None:
        exchange_rate = 1.0
        currency_code = "CNY"

    if currency_code == "CNY":
        # CNY 店铺无汇率风险，不使用 fx_buffer
        base_price: float = total_cost_cny * (1 + margin_rate) / commission_divisor
        price: int = math.ceil(base_price)
    else:
        base_price = (
            total_cost_cny
            * (1 + margin_rate)
            * (1 + fx_buffer)
            / commission_divisor
            * exchange_rate
        )
        price = math.ceil(base_price)

    # Ozon 规则：折扣至少 20%（price≤25 时 old_price-price≥5；否则 20% 加价）
    if price <= 25:
        old_price: int = max(price + 5, math.ceil(price * 1.2))
    else:
        old_price = math.ceil(price * 1.2)

    # pricing_node Step 6：利润（RUB 店铺换算回 CNY 计算）
    if currency_code == "CNY":
        profit_cny: float = price - total_cost_cny
    else:
        profit_cny = (price / exchange_rate) - total_cost_cny

    profit_rate: float = profit_cny / total_cost_cny if total_cost_cny > 0 else 0.0

    # CNY 等价基准价（pricing_node price_breakdown 展示用；RUB 路径不含汇率）
    if currency_code == "CNY":
        base_price_for_profit: float = total_cost_cny * (1 + margin_rate) / commission_divisor
    else:
        base_price_for_profit = total_cost_cny * (1 + margin_rate) * (1 + fx_buffer) / commission_divisor

    return {
        "price": price,
        "old_price": old_price,
        "profit_cny": round(profit_cny, 2),
        "profit_rate": round(profit_rate, 4),
        "base_price": round(base_price_for_profit, 2),
    }

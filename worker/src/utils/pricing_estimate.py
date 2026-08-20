"""共享定价公式（M1.2 + v0.60 双价格）— 唯一定义处，pricing_node 与 estimate 端点同源。

单档（旧行为，缺省新参时保持）：
- 售价 = 总成本 × (1 + margin) / (1 - commission)     [CNY 店铺，无汇率风险，不用 fx_buffer]
- RUB 店铺再 × (1 + fx_buffer) × exchange_rate
- old_price：Ozon 折扣规则 ≥20%（price≤25 时至少加 5）
- profit_cny：CNY = price - total_cost；RUB = price/exchange_rate - total_cost
- profit_rate = profit_cny / total_cost

三档（v0.60 双价格体系，传入 margin_anchor/margin_floor 时启用）：
- 分母含变动成本率：售价 = 总成本 × (1 + margin) / (1 - commission - variable_cost_rate)
  —— 变动成本（推广/退货/提现/汇损/附加）与佣金同属性都从售价按比例扣，缺省 0.155（日常）。
- 三档：price = 日常价(margin_rate) / old_price = 划线原价(margin_anchor) /
  promo_price = 促销底线(margin_floor，用促销变动成本率 0.245)
- 利润口径 = 销售净利率：profit = 售价×(1-commission-variable_cost_rate) - 总成本
  （不再是成本利润率，避免「毛利当净利」虚高——用户 2026-08-21 拍板）
- promo_price 供 Ozon min_seller_price / 促销活动使用（自动调价不跌破成本线）

⚠️ 修改本文件公式必须同步更新 pricing_node 行为锁定测试（test_estimate_endpoint.py、
   test_pricing_dual_margin.py），前端/skill 一律禁止自行实现定价公式（v0.40 统一纪律）。
"""
from __future__ import annotations

import math
from typing import Any

# v0.60：变动成本率默认值（用户 2026-08-21 拍板）
# 日常 15.5% = 推广6 + 退货5 + 提现1.5 + 汇损2 + 附加1（%）
# 促销 24.5% = 推广12 + 退货8 + 提现1.5 + 汇损2 + 附加1（%）
DEFAULT_VARIABLE_COST_RATE = 0.155
DEFAULT_PROMO_VARIABLE_COST_RATE = 0.245


def compute_price(
    total_cost_cny: float,
    margin_rate: float,
    commission_rate: float,
    fx_buffer: float,
    currency_code: str,
    exchange_rate: float | None = None,
    *,
    margin_anchor: float | None = None,
    margin_floor: float | None = None,
    variable_cost_rate: float | None = None,
    promo_variable_cost_rate: float | None = None,
) -> dict[str, Any]:
    """计算预估售价/利润（与 pricing_node 公式逐字一致）。

    Args:
        total_cost_cny: 总成本 = 采购 + 物流 + 包装（CNY）。
        margin_rate: 目标利润率（如 0.25 单档 / 1.5 三档日常价）。
        commission_rate: Ozon 佣金率（如 0.10）。
        fx_buffer: 汇率缓冲（仅 RUB 店铺生效，如 0.05）。
        currency_code: "CNY" 或 "RUB"。
        exchange_rate: CNY→RUB 汇率；None → 按 CNY 处理（估计端点不接汇率实时源，
            与 skill 估算一致——此时即使 currency_code=RUB 也走 CNY 路径）。
        margin_anchor: 三档划线原价利润率（如 2.0）。None → 旧单档行为（old=price×1.2）。
        margin_floor: 三档促销底线利润率（如 0.6）。None → 不产生 promo_price。
        variable_cost_rate: 日常变动成本率（推广/退货/提现/汇损/附加，0-0.5）。
            None → 0.155（v0.60 默认，用户拍板）。
        promo_variable_cost_rate: 促销变动成本率。None → 0.245（v0.60 默认）。

    Returns:
        {"price": int, "old_price": int, "promo_price": int|None,
         "profit_cny": float, "profit_rate": float, "base_price": float}
        - 单档（margin_anchor/margin_floor 均 None）：与旧行为完全一致，无 promo_price。
        - 三档：price=日常价、old_price=划线原价、promo_price=促销底线；
          profit_rate = 销售净利率（净利/售价）。
    """
    legacy = margin_anchor is None and margin_floor is None
    vcr = variable_cost_rate if variable_cost_rate is not None else DEFAULT_VARIABLE_COST_RATE
    pvcr = promo_variable_cost_rate if promo_variable_cost_rate is not None else DEFAULT_PROMO_VARIABLE_COST_RATE

    # pricing_node Step 5：防止除零（分母 = 1 - commission - 变动成本率）
    commission_divisor: float = 1.0 - commission_rate - (0.0 if legacy else vcr)
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

    if legacy:
        # ── 单档（旧行为，逐字保持）──
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

    # ── 三档（v0.60 双价格体系）──
    # 划线原价：用日常变动成本率（与日常价同分母），anchor 缺省 = margin_rate×1.2 保底
    anchor_eff = margin_anchor if margin_anchor is not None else margin_rate * 1.2
    if currency_code == "CNY":
        old_base = total_cost_cny * (1 + anchor_eff) / commission_divisor
        old_price = math.ceil(old_base)
    else:
        old_base = (
            total_cost_cny
            * (1 + anchor_eff)
            * (1 + fx_buffer)
            / commission_divisor
            * exchange_rate
        )
        old_price = math.ceil(old_base)
    # Ozon 规则：划线价 ≥ 日常价×1.2（anchor 偏低时强制）
    old_price = max(old_price, price + 5 if price <= 25 else math.ceil(price * 1.2))

    # 促销底线价：用促销变动成本率（大促推广/退货更高）
    promo_divisor: float = 1.0 - commission_rate - pvcr
    if promo_divisor <= 0:
        promo_divisor = 0.9
    if margin_floor is not None:
        if currency_code == "CNY":
            promo_base = total_cost_cny * (1 + margin_floor) / promo_divisor
        else:
            promo_base = (
                total_cost_cny
                * (1 + margin_floor)
                * (1 + fx_buffer)
                / promo_divisor
                * exchange_rate
            )
        promo_price: int | None = math.ceil(promo_base)
    else:
        promo_price = None

    # 销售净利率口径：净利 = 售价×(1-佣金-变动成本率) - 总成本
    if currency_code == "CNY":
        profit_cny = price * (1.0 - commission_rate - vcr) - total_cost_cny
    else:
        profit_cny = (price / exchange_rate) * (1.0 - commission_rate - vcr) - total_cost_cny

    profit_rate: float = profit_cny / price if price > 0 else 0.0

    # CNY 等价基准价（三档下 = 日常价口径，展示用）
    if currency_code == "CNY":
        base_price_for_profit = total_cost_cny * (1 + margin_rate) / commission_divisor
    else:
        base_price_for_profit = total_cost_cny * (1 + margin_rate) * (1 + fx_buffer) / commission_divisor

    return {
        "price": price,
        "old_price": old_price,
        "promo_price": promo_price,
        "profit_cny": round(profit_cny, 2),
        "profit_rate": round(profit_rate, 4),
        "base_price": round(base_price_for_profit, 2),
    }

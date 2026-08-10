"""Shared utility functions."""
from __future__ import annotations

import re

# Ozon 价格货币归一化（唯一真源）：CNY → RUB 固定参考汇率。
# ✅ v0.36: Ozon 价格统一归一化为 RUB。1688 采购价仍是 CNY，parse_price 语义不变。
CNY_TO_RUB = 13.33


def normalize_ozon_price(price_str: str, currency_code: str, to_rub: bool = True) -> float:
    """解析 Ozon 价格并归一化为 RUB（唯一真源）。

    - ``currency_code == "CNY"`` → 按 ``CNY_TO_RUB`` 汇率转 RUB
    - ``currency_code == "RUB"`` 或未知 → 原样 ``parse_price``（不换算）

    ``parse_price`` 语义不变（1688 CNY 采购价仍直接用它，不做汇率换算）。
    """
    value = parse_price(price_str)
    if to_rub and str(currency_code or "").strip().upper() == "CNY":
        return round(value * CNY_TO_RUB, 2)
    return value


def parse_price(price_str: str) -> float:
    """Parse price string like '327 ₽', '¥119.00', '1 234,56' to float.

    Handles:
    - Currency symbols (₽, ¥, $, €)
    - Thousands separators (spaces)
    - Comma as decimal separator (European format)
    - Multiple prices (e.g., '¥12.70 ¥22.00') — takes the first
    """
    if not price_str:
        return 0.0
    # Remove currency symbols and whitespace
    cleaned = re.sub(r'[₽¥$€\s]', '', str(price_str))
    # Replace comma with dot for decimal
    cleaned = cleaned.replace(',', '.')
    # Extract all numeric values, take the first one (current price)
    numbers = re.findall(r'\d+(?:\.\d+)?', cleaned)
    return float(numbers[0]) if numbers else 0.0

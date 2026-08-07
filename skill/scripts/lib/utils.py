"""Shared utility functions."""
from __future__ import annotations

import re


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

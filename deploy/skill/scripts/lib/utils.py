"""Shared utility functions."""
from __future__ import annotations
import re


def parse_price(price_str: str) -> float:
    """Parse price string like '327 ₽', '¥119.00', '1 234,56' to float.

    Handles:
    - Currency symbols (₽, ¥, $, €)
    - Thousands separators (spaces)
    - Comma as decimal separator (European format)
    """
    if not price_str:
        return 0.0
    # Remove currency symbols and whitespace
    cleaned = re.sub(r'[₽¥$€\s]', '', str(price_str))
    # Replace comma with dot for decimal
    cleaned = cleaned.replace(',', '.')
    # Extract numeric value
    m = re.search(r'[\d.]+', cleaned)
    return float(m.group()) if m else 0.0

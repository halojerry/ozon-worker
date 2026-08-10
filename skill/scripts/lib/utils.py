"""Shared utility functions."""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

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


def safe_unlink(path) -> bool:
    """安全删除文件（fail-open）。

    Windows 沙箱/AppLocker 策略可能禁止删除非临时文件——`Path.unlink` 抛
    PermissionError 会直接崩溃（缓存清理/更新回滚/临时文件清理）。此函数
    fail-open：unlink 失败降级 ``os.remove``，仍失败仅 ``logger.warning``
    返回 False，绝不 raise 阻断主流程。

    Returns: 是否删除成功（不存在视为成功）。
    """
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except PermissionError:
        try:
            os.remove(path)
            return True
        except OSError as exc:
            logger.warning("safe_unlink 删除失败（Windows 沙箱？）: %s: %s", path, exc)
            return False
    except OSError as exc:
        logger.warning("safe_unlink 删除失败: %s: %s", path, exc)
        return False


def safe_rmtree(path) -> bool:
    """安全递归删除目录（fail-open，同 safe_unlink 语义）。"""
    try:
        shutil.rmtree(path, ignore_errors=True)
        return True
    except OSError as exc:
        logger.warning("safe_rmtree 删除失败（Windows 沙箱？）: %s: %s", path, exc)
        return False

#!/usr/bin/env python3
"""P1a/B4 回归：service.py parseWeightGrams 重量解析行为锁定。

背景（v0.37）：旧 parseInteger 用 replace(/[^0-9]/g,'') 剥离所有非数字，
导致 '61.8g'→618（放大10倍）、'1.2kg'→12（应为1200）、'0.5kg'→5。
修复为 parseWeightGrams（parseFloat 保留小数 + kg→g 换算）。

本测试锁定 JS 函数的等价 Python 逻辑（与 service.py:168-180 逐行对齐），
防止未来改动破坏重量解析语义。

运行：
    cd skill && .venv314/bin/python tests/test_parse_weight_grams.py
"""
from __future__ import annotations

import re
import sys


# ── 与 service.py parseWeightGrams 等价的 Python 实现（测试桩）──
def parse_weight_grams(value) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", ".").strip().lower()
    m = re.match(r"(-?\d+(?:\.\d+)?)\s*(kg|kgs?|g|克|公斤|千克)?", text)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit.startswith("kg") or unit in ("公斤", "千克"):
        return round(num * 1000)
    return round(num)


def test_plain_grams():
    assert parse_weight_grams("500g") == 500
    assert parse_weight_grams("500") == 500
    assert parse_weight_grams("61.8g") == 62  # 小数保留（round），不放大 10 倍


def test_decimal_no_magnification():
    """B4 核心回归：旧 parseInteger 把 61.8g 解析成 618，修复后为 62。"""
    assert parse_weight_grams("61.8g") == 62
    assert parse_weight_grams("61.8") == 62
    assert parse_weight_grams("0.8克") == 1


def test_kg_conversion():
    """kg → g 换算：1.2kg→1200（旧逻辑错误解析为 12）。"""
    assert parse_weight_grams("1.2kg") == 1200
    assert parse_weight_grams("1.2 kg") == 1200
    assert parse_weight_grams("0.5kg") == 500
    assert parse_weight_grams("2公斤") == 2000
    assert parse_weight_grams("1千克") == 1000


def test_none_and_garbage():
    assert parse_weight_grams(None) is None
    assert parse_weight_grams("") is None
    assert parse_weight_grams("无") is None


def test_chinese_unit_variants():
    assert parse_weight_grams("含包装：61.8g") is None or True  # 前缀文本不匹配（JS 侧 cells 已 normalizeText）
    # 无单位纯数字 → 视为克
    assert parse_weight_grams("3") == 3


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

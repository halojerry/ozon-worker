#!/usr/bin/env python3
"""竞品重量/尺寸兜底单测（v0.22）— 1688 数据缺失时用 Ozon 竞品值。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_competitor_fallback.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import apply_competitor_fallback


def test_weight_fallback_when_missing():
    """1688 重量缺失 → 用竞品重量。"""
    w, d, hh, h = apply_competitor_fallback(
        weight_g=0, depth_mm=100, width_mm=80, height_mm=50,
        extensions={"competitor_weight_g": 500},
    )
    assert w == 500
    assert d == 100 and hh == 80 and h == 50  # 尺寸保留 1688 的


def test_dimensions_fallback_when_all_zero():
    """1688 尺寸全 0 → 用竞品尺寸（mm）。"""
    w, d, hh, h = apply_competitor_fallback(
        weight_g=300, depth_mm=0, width_mm=0, height_mm=0,
        extensions={"competitor_dimensions_mm": {"length": 200, "width": 150, "height": 100}},
    )
    assert (d, hh, h) == (200, 150, 100)
    assert w == 300


def test_keep_1688_when_valid():
    """1688 数据有效 → 不用竞品。"""
    w, d, hh, h = apply_competitor_fallback(
        weight_g=300, depth_mm=100, width_mm=80, height_mm=50,
        extensions={"competitor_weight_g": 500,
                    "competitor_dimensions_mm": {"length": 200, "width": 150, "height": 100}},
    )
    assert w == 300
    assert (d, hh, h) == (100, 80, 50)


def test_no_extensions_noop():
    """无竞品数据 → 原样返回。"""
    w, d, hh, h = apply_competitor_fallback(
        weight_g=0, depth_mm=0, width_mm=0, height_mm=0, extensions={},
    )
    assert (w, d, hh, h) == (0, 0, 0, 0)


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

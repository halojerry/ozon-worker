#!/usr/bin/env python3
"""P2a/A2 回归：weight_dimension_normalizer 公共模块行为锁定。

核心断言（v0.37）：
1. 真实轻物 3g+200×200×10 → 保持 3g（旧启发式 ×1000 已废除）
2. 真实正常值 → 原样 + 无 marks
3. 缺失重量 → 竞品兜底 → 默认 100g（weight_source 标记）
4. 缺失尺寸 → 竞品/默认（300×200×50）
5. 密度异常 → 标记 suspected 但不改写
6. 字符串带小数点（'3.0'）→ kg→g 转换 3000（唯一允许的单位级改写）

运行：
    cd worker && PYTHONPATH=src python3 tests/test_weight_dimension_normalizer.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.weight_dimension_normalizer import (
    DEFAULT_DIMS_MM,
    DEFAULT_WEIGHT_G,
    normalize_weight_dimensions,
)


def test_real_light_item_not_magnified():
    """v0.37 A2 核心：真实 3g 轻物（200×200×10mm）→ 保持 3g，绝不 ×1000。"""
    w, d, marks = normalize_weight_dimensions(
        3, {"length": 200, "width": 200, "height": 10}, None
    )
    assert w == 3, f"真实 3g 必须保持，实际 {w}g"
    assert d == {"length": 200, "width": 200, "height": 10}
    assert marks["weight_estimated"] is False
    assert any("light_weight_suspected" in r for r in marks["reasons"])


def test_real_light_item_5g():
    """真实 5g（80×50×30mm）→ 保持 5g + 标记轻物疑点。"""
    w, d, marks = normalize_weight_dimensions(
        5, {"length": 80, "width": 50, "height": 30}, None
    )
    assert w == 5
    assert marks["weight_estimated"] is False
    assert any("light_weight_suspected" in r for r in marks["reasons"])


def test_normal_values_untouched():
    """真实 300g（200×100×80mm）→ 原样，无任何标记。"""
    w, d, marks = normalize_weight_dimensions(
        300, {"length": 200, "width": 100, "height": 80}, None
    )
    assert w == 300
    assert d == {"length": 200, "width": 100, "height": 80}
    assert marks["weight_estimated"] is False
    assert marks["dimensions_suspected"] is False
    assert marks["reasons"] == []


def test_competitor_weight_fallback():
    """缺失重量 + 竞品 500g → 用竞品，weight_source=competitor。"""
    w, d, marks = normalize_weight_dimensions(
        0, {"length": 100, "width": 50, "height": 30},
        {"competitor_weight_g": 500},
    )
    assert w == 500
    assert marks["weight_source"] == "competitor"
    assert marks["weight_estimated"] is True


def test_default_weight_when_all_missing():
    """无竞品 + 缺失 → 100g 默认 + weight_source=default。"""
    w, d, marks = normalize_weight_dimensions(
        0, {"length": 100, "width": 50, "height": 30}, None
    )
    assert w == DEFAULT_WEIGHT_G
    assert marks["weight_source"] == "default"


def test_default_dims_when_all_zero():
    """尺寸全 0 → 300×200×50 默认。"""
    w, d, marks = normalize_weight_dimensions(
        300, {"length": 0, "width": 0, "height": 0}, None
    )
    assert (d["length"], d["width"], d["height"]) == DEFAULT_DIMS_MM
    assert any("dims_missing_used_default" in r for r in marks["reasons"])


def test_competitor_dims_fallback():
    """尺寸全 0 + 竞品 120×80×60 → 用竞品尺寸。"""
    w, d, marks = normalize_weight_dimensions(
        1000, {"length": 0, "width": 0, "height": 0},
        {"competitor_dimensions_mm": {"length": 120, "width": 80, "height": 60}},
    )
    assert d == {"length": 120, "width": 80, "height": 60}


def test_partial_dim_fill():
    """部分维缺失（length=0）→ 仅补缺失维默认值，不覆盖已有。"""
    w, d, marks = normalize_weight_dimensions(
        500, {"length": 0, "width": 80, "height": 30}, None
    )
    assert d == {"length": 100, "width": 80, "height": 30}  # length→默认100


def test_density_high_marks_not_rewrites():
    """密度过高（3000g/100×100×20mm=15000 kg/m³）→ 标记 suspected 不改写。"""
    w, d, marks = normalize_weight_dimensions(
        3000, {"length": 100, "width": 100, "height": 20}, None
    )
    assert w == 3000
    assert marks["dimensions_suspected"] is True
    assert any("density_too_high" in r for r in marks["reasons"])


def test_density_in_range_no_flag():
    """密度恰在合理区间（3000g/200×200×10mm=7500）→ 不标疑（旧启发式的盲区）。"""
    w, d, marks = normalize_weight_dimensions(
        3000, {"length": 200, "width": 200, "height": 10}, None
    )
    assert marks["dimensions_suspected"] is False


def test_string_decimal_is_kg():
    """字符串 '3.0'（带小数点）→ kg→g 转换 3000（唯一允许的单位级改写）。"""
    w, d, marks = normalize_weight_dimensions(
        "3.0", {"length": 800, "width": 500, "height": 300}, None
    )
    assert w == 3000
    assert any("weight_str_kg_parsed" in r for r in marks["reasons"])


def test_non_numeric_no_crash():
    """非数字重量 → 兜底 100g 不崩溃。"""
    w, d, marks = normalize_weight_dimensions(
        "abc", {"length": 100, "width": 50, "height": 30}, None
    )
    assert w == DEFAULT_WEIGHT_G
    assert marks["weight_source"] == "default"


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

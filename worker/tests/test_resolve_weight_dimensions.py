#!/usr/bin/env python3
"""C2: _resolve_weight_dimensions 纯函数单测（sentry-attribute-fixes）。

覆盖：
1. draft.weight=0 + extensions.competitor_weight_g → 竞品重量兜底
2. draft.dimensions 全 0 + extensions.competitor_dimensions_mm → 竞品尺寸兜底
3. draft 有合法值 + extensions 也有 → 用 draft（竞品不覆盖）
4. 无 extensions + weight=0 → 100g 终极兜底
5. 密度校验逻辑仍生效（>13546 kg/m³ 时重量 ÷1000 修正）
6. 部分维缺失 → 仅缺失维用竞品兜底

运行：
    cd worker && PYTHONPATH=src python3 tests/test_resolve_weight_dimensions.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions


def test_uses_competitor_weight_when_draft_weight_zero():
    """draft.weight=0 + 竞品重量 500g → 返回 500g（不用 100g 兜底）。"""
    result = _resolve_weight_dimensions(
        {"weight": 0, "dimensions": {"length": 100, "width": 50, "height": 30}},
        {"competitor_weight_g": 500},
    )
    assert result == (500, 100, 50, 30)


def test_uses_competitor_dimensions_when_draft_dims_zero():
    """dimensions 全 0 + 竞品 120×80×60mm → 用之（不用 300×200×50 兜底）。"""
    result = _resolve_weight_dimensions(
        {"weight": 1000, "dimensions": {"length": 0, "width": 0, "height": 0}},
        {"competitor_dimensions_mm": {"length": 120, "width": 80, "height": 60}},
    )
    assert result == (1000, 120, 80, 60)


def test_draft_wins_over_extensions():
    """draft 有合法值 + extensions 也有 → 用 draft（竞品数据不覆盖真实值）。"""
    result = _resolve_weight_dimensions(
        {"weight": 500, "dimensions": {"length": 100, "width": 50, "height": 30}},
        {
            "competitor_weight_g": 9999,
            "competitor_dimensions_mm": {"length": 999, "width": 999, "height": 999},
        },
    )
    assert result == (500, 100, 50, 30)


def test_default_100g_when_no_extensions():
    """无 extensions + weight=0 → 100g 终极兜底（行为不变）。"""
    result = _resolve_weight_dimensions(
        {"weight": 0, "dimensions": {"length": 100, "width": 50, "height": 30}},
        None,
    )
    assert result == (100, 100, 50, 30)


def test_density_validation_marks_not_rewrites():
    """v0.37 A8 修复：密度 20000 > 13546 → 保留商家重量 20000g（不再 ÷1000 改写），
    由公共模块打 density_too_high 标记。"""
    result = _resolve_weight_dimensions(
        {"weight": 20000, "dimensions": {"length": 100, "width": 100, "height": 100}},
        None,
    )
    assert result == (20000, 100, 100, 100)
    marks = _resolve_weight_dimensions._wd_marks
    assert any("density_too_high" in r for r in marks["reasons"])


def test_partial_dim_fill_from_competitor():
    """仅缺失维用竞品兜底：length 缺失 → 120，width/height 保留 draft 值。"""
    result = _resolve_weight_dimensions(
        {"weight": 500, "dimensions": {"length": 0, "width": 80, "height": 30}},
        {"competitor_dimensions_mm": {"length": 120, "width": 999, "height": 999}},
    )
    assert result == (500, 120, 80, 30)


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

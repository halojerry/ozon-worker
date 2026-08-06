#!/usr/bin/env python3
"""信封合理性防线单测（v0.21 P2）— 防止脏尺寸/脏重量再次打爆定价。

覆盖：
1. validate_draft_sanity：weight>50kg 或任一边>5m 拒绝
2. check_weight_suspect：pricing 打标逻辑

运行：
    cd worker && PYTHONPATH=src python3 tests/test_draft_sanity.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.draft_sanity import check_weight_suspect, validate_draft_sanity


def test_rejects_absurd_weight():
    """364kg（工具套装错误值）→ 拒绝。"""
    err = validate_draft_sanity({"weight": 364000, "dimensions": {"length": 2600, "width": 800, "height": 350}})
    assert err is not None
    assert "weight" in err


def test_rejects_absurd_dimension():
    """2600mm 单边本身合理，但 26000mm（26m）→ 拒绝。"""
    err = validate_draft_sanity({"weight": 5000, "dimensions": {"length": 26000, "width": 800, "height": 350}})
    assert err is not None
    assert "dimensions" in err


def test_accepts_normal_big_item():
    """大件但合理（跑步机 45kg / 1.8m）→ 通过。"""
    err = validate_draft_sanity({"weight": 45000, "dimensions": {"length": 1800, "width": 700, "height": 400}})
    assert err is None


def test_suspect_flag_triggered_over_50kg():
    out = check_weight_suspect(52000, {"length": 1100, "width": 500, "height": 300})
    assert out["suspect"] is True
    assert "weight" in out["reason"]


def test_suspect_flag_ok_for_normal_weight():
    out = check_weight_suspect(5200, {"length": 1100, "width": 500, "height": 300})
    assert out["suspect"] is False


# ── v0.28.5 D1: 缺失/零值拦截 ──

def test_rejects_zero_weight():
    """weight=0(缺失) → 拦截(定价无意义)。"""
    err = validate_draft_sanity({"weight": 0, "dimensions": {"length": 100, "width": 50, "height": 30}})
    assert err is not None
    assert "weight" in err


def test_rejects_missing_weight():
    """weight 键缺失 → 拦截。"""
    err = validate_draft_sanity({"dimensions": {"length": 100, "width": 50, "height": 30}})
    assert err is not None
    assert "weight" in err


def test_rejects_zero_dimension():
    """dimensions 含 0 → 拦截。"""
    err = validate_draft_sanity({"weight": 500, "dimensions": {"length": 100, "width": 0, "height": 30}})
    assert err is not None
    assert "dimensions" in err


def test_rejects_missing_dimensions():
    """dimensions 缺失 → 拦截。"""
    err = validate_draft_sanity({"weight": 500})
    assert err is not None
    assert "dimensions" in err


def test_d1_does_not_break_normal():
    """正常值不受 D1 影响(回归)。"""
    err = validate_draft_sanity({"weight": 500, "dimensions": {"length": 100, "width": 50, "height": 30}})
    assert err is None


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

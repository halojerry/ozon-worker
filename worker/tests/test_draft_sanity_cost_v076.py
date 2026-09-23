#!/usr/bin/env python3
"""purchase_cost 非正数提交闸单测（Task 27 / race-L2）。

行为：非跟卖信封 draft.purchase_cost 存在且 <= 0 → 拦截
（「采购成本必须大于 0」，走既有 sanity 错误通道）；跟卖
（extensions.follow_sell truthy）与 purchase_cost 缺失豁免；
字符串形态数值不在本闸生效（数值型才拦——非跟卖信封的
purchase_cost 类型由 submit 入口 required-fields 层强制 int/float）。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_draft_sanity_cost_v076.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.draft_sanity import validate_draft_sanity

_VALID_DIMS = {"length": 100, "width": 50, "height": 30}


def _draft(cost):
    """构造 weight/dims 合法的 draft；cost 为 None 表示 purchase_cost 缺失。"""
    d = {"weight": 500, "dimensions": dict(_VALID_DIMS)}
    if cost is not None:
        d["purchase_cost"] = cost
    return d


def test_negative_cost_rejected():
    """purchase_cost=-12.5 → 拦截（compute_price 无下限 clamp，负成本会算出负价）。"""
    err = validate_draft_sanity(_draft(-12.5), {})
    assert err is not None
    assert "采购成本必须大于 0" in err


def test_zero_cost_rejected():
    """purchase_cost=0 → 拦截（定价无意义）。"""
    err = validate_draft_sanity(_draft(0), {})
    assert err is not None
    assert "采购成本必须大于 0" in err


def test_follow_sell_exempt():
    """follow_sell=True + 负成本 → 豁免（跟卖定价跟随竞品，无 1688 采购成本语义）。"""
    err = validate_draft_sanity(_draft(-5), {"follow_sell": True})
    assert err is None


def test_missing_cost_exempt():
    """purchase_cost 缺失 → 豁免（api 跟卖等合法缺省场景）。"""
    err = validate_draft_sanity(_draft(None), {})
    assert err is None


def test_string_cost_passthrough():
    """str 形态 "12.5" 不在本闸生效（数值型才拦）——非数值由入口类型闸拦截。"""
    err = validate_draft_sanity(_draft("12.5"), {})
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

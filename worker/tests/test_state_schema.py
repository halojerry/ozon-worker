#!/usr/bin/env python3
"""节点返回键 vs GlobalState schema 一致性（v0.22 防回归）。

LangGraph 节点返回 dict 的键必须在 GlobalState schema，否则运行时
InvalidUpdateError/ValidationError 卡死管线。本测试静态校验。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_state_schema.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import GlobalState, GraphOutput


def _global_state_keys() -> set[str]:
    return set(GlobalState.model_fields.keys())


def _follow_import_return_keys() -> set[str]:
    """follow_sell_import_node 最终 return dict 的键。"""
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "src",
                     "graphs", "nodes", "follow_sell_import_node.py")
    ).read()
    m = re.search(r"return \{\n(.*?)\n    \}", src, re.S)
    assert m, "未找到 follow_sell_import return dict"
    return set(re.findall(r'^\s*"(\w+)":', m.group(1), re.M))


def test_follow_import_keys_in_global_state():
    """follow_sell_import 返回的所有键（含 v0.22 新增 category_missing）必须在 GlobalState。"""
    gs = _global_state_keys()
    ret = _follow_import_return_keys()
    missing = ret - gs
    assert not missing, f"返回键不在 GlobalState: {sorted(missing)}"


def test_category_missing_field_exists():
    assert "category_missing" in _global_state_keys()


def test_product_summary_field_exists():
    """product_summary 是 task_processor 完成后的合并字段，契约在 GraphOutput。"""
    assert "product_summary" in GraphOutput.model_fields


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

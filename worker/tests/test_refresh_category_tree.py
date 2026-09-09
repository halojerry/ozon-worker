"""refresh_category_tree 单测（PLAN-category-tree-refresh-v1）。

纯函数：flatten_tree_keys（叶子继承父 dc / type 键集）/ compute_diff / keys_to_disable；
SQL 薄层用 fake engine 锁行为（软失效只 UPDATE 消失键、分批 500）。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.refresh_category_tree import (
    compute_diff,
    flatten_tree_keys,
    keys_to_disable,
    soft_disable_missing,
)


def _tree():
    """双层样例树： dc=10 下 type 100/101，dc=20 下 type 200（其中 101 无显式 dc 继承）。"""
    return [
        {"description_category_id": 10, "category_name": "A", "children": [
            {"description_category_id": 11, "category_name": "A1", "children": [
                {"type_id": 100, "type_name": "t100"},
                {"type_id": 101, "type_name": "t101"},
            ]},
        ]},
        {"description_category_id": 20, "category_name": "B", "children": [
            {"type_id": 200, "type_name": "t200"},
        ]},
    ]


def test_flatten_collects_type_keys_with_dc_inheritance():
    keys = flatten_tree_keys(_tree())
    assert keys == {(11, 100), (11, 101), (20, 200)}, "叶子继承最近父级 dc"


def test_flatten_empty_and_malformed():
    assert flatten_tree_keys([]) == set()
    assert flatten_tree_keys([{"category_name": "x", "children": []}]) == set()


def test_compute_diff_added_and_removed():
    old = {(1, 10), (2, 20)}
    new = {(2, 20), (3, 30)}
    added, removed = compute_diff(old, new)
    assert added == {(3, 30)}
    assert removed == {(1, 10)}
    assert keys_to_disable(old, new) == [(1, 10)]


class _FakeConn:
    def __init__(self, capture):
        self._capture = capture

    def execute(self, stmt, params=None):
        self._capture.append((str(stmt), params))
        class _R:
            rowcount = 3
        return _R()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_soft_disable_batches_and_uses_begin():
    capture: list = []
    engine = mock.Mock()
    engine.begin.return_value = _FakeConn(capture)
    keys = [(i, i * 10) for i in range(1200)]  # 触发 500 分批 → 3 批
    n = soft_disable_missing(engine, "RU", keys)
    assert n == 9  # 3 批 × rowcount 3
    assert len(capture) == 3
    assert all("disabled = true" in s for s, _ in capture)
    assert all("node_type = 'type'" in s for s, _ in capture)
    # 每批 ≤500 键
    first_params = capture[0][1]
    assert len([k for k in first_params if k.startswith("dc")]) == 500


def test_soft_disable_empty_noop():
    engine = mock.Mock()
    assert soft_disable_missing(engine, "RU", []) == 0
    engine.begin.assert_not_called()

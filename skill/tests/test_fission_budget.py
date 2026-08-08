"""ozon_fission 裂变引擎单测（v0.31 P3）— 预算/双 visited/seller_id 归一化/共识排名/checkpoint。"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ozon_fission
from ozon_fission import (
    FissionBudget,
    FissionState,
    normalize_seller_id,
)


def _mk_state() -> FissionState:
    return FissionState(session_id="test-1", max_depth=2, max_total_products=100,
                        time_budget=600)


def test_normalize_seller_id_extracts_from_url():
    assert normalize_seller_id("/seller/12345") == "12345"
    assert normalize_seller_id("/seller/12345/") == "12345"
    assert normalize_seller_id("/seller/12345?from=modal") == "12345"
    assert normalize_seller_id("/seller/abc-123") == "abc-123"
    assert normalize_seller_id("seller/10001") == "10001"
    assert normalize_seller_id("/seller/") is None


def test_normalize_seller_id_rejects_invalid():
    assert normalize_seller_id("") is None
    assert normalize_seller_id("123") is None  # 太短（启发式：长度 <5）
    assert normalize_seller_id(None) is None
    assert normalize_seller_id("/seller/") is None


def test_budget_max_total_products_truncates():
    """max_total_products 触顶即终止——候选不再增长。"""
    state = _mk_state()
    state.max_total_products = 3
    seen: list[str] = []
    for i in range(10):
        if not state.can_add_product():
            break
        state.add_product(f"p{i}")
        seen.append(f"p{i}")
    assert len(seen) == 3, f"应只接受 3 个候选（预算触顶），got {len(seen)}"


def test_dual_visited_truncates_loop():
    """商品↔卖家环路（A→X→A）被双 visited 截断，不重复处理。"""
    state = _mk_state()
    state.mark_product_seen("pA")
    state.mark_seller_seen("X")
    assert not state.should_visit_product("pA"), "商品已访问，应跳过"
    assert not state.should_visit_seller("X"), "卖家已访问，应跳过"
    assert state.should_visit_product("pB"), "新商品应访问"
    assert state.should_visit_seller("Y"), "新卖家应访问"


def test_seller_products_same_seller_dedup():
    """同一卖家从多个商品出现，visited_sellers 保证只处理一次。"""
    state = _mk_state()
    state.mark_seller_seen("X")
    state.mark_seller_seen("X")
    assert len(state.visited_sellers) == 1


def test_consensus_ranking_counts_distinct_sellers():
    """共识排名 = 被不同一跳卖家售卖的数量，top-K 排序。"""
    candidates = [
        {"product_id": "p1", "sellers": {"X", "Y", "Z"}},
        {"product_id": "p2", "sellers": {"X"}},
        {"product_id": "p3", "sellers": {"X", "Y"}},
    ]
    ranked = ozon_fission.rank_by_consensus(candidates, top_k=2)
    assert ranked[0]["product_id"] == "p1", "p1 有 3 卖家，应排第一"
    assert ranked[1]["product_id"] == "p3", "p3 有 2 卖家，应排第二"
    assert len(ranked) == 2, "top_k=2"


def test_checkpoint_roundtrip():
    """FissionState 序列化/恢复（断点续跑）。"""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "fission_state_test-1.json")
        state = _mk_state()
        state.mark_product_seen("pA")
        state.mark_seller_seen("X")
        state.frontier = [["seller", "X", 1, []]]
        state.save(path)
        restored = FissionState.load(path)
        assert restored.visited_products == {"pA"}
        assert restored.visited_sellers == {"X"}
        assert restored.frontier == [["seller", "X", 1, []]]


def test_chain_builds_path():
    """证据链：种子 → 卖家 → 产品逐跳累积。"""
    state = _mk_state()
    chain = state.extend_chain([], "seller", "X", "卖家X", 0)
    assert chain == [{"type": "seller", "id": "X", "name": "卖家X", "depth": 0}]
    chain2 = state.extend_chain(chain, "product", "pB", "产品B", 1)
    assert chain2[1] == {"type": "product", "id": "pB", "name": "产品B", "depth": 1}


def test_depth_budget_blocks_deeper():
    """max_depth 触顶：depth=2 的 seller 不再展开。"""
    state = _mk_state()
    state.max_depth = 2
    assert state.depth_allowed(2), "depth=2 应允许（== max_depth）"
    assert not state.depth_allowed(3), "depth=3 应被 max_depth=2 阻断"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

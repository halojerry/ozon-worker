"""ozon_fission 并行分析测试（P1a）— 每 pid 独立线程 + 独立 CDP 连接。

核心验证点：
1. _analyze_product 真正并发（Barrier 同步证明两个线程同时进入）。
2. 每个 worker 任务创建独立 CdpConnection（不共享主连接）→ 连接数 = 任务数。
3. visited 集合正确、无重复 pid、max_total_products 预算在主线程预过滤生效。
4. _parallel_workers()==1 时沿用共享 cdp 串行路径（零回归）。
"""
from __future__ import annotations

import os
import sys
import threading
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as ozon_discovery
from scripts.lib import ozon_fission as ozon_fission
from scripts.lib.ozon_discovery import ProductCandidate


def _mk_seed(pid: str, sellers: list) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"种子{pid}", ozon_price=1500)
    c.competing_sellers = len(sellers)
    c.competing_seller_list = sellers
    return c


def test_parallel_workers_true_concurrency():
    """2 workers 真并发：Barrier 同步证明两个线程同时进入 _analyze_product。

    - 每个 worker 任务创建独立 CdpConnection + 独立 tab（连接数 = 主 + 2 任务）。
    - 若实现退化回串行：第一个 pid 在 Barrier 处超时 → 候选 error 或抛异常 → 断言失败。
    """
    seeds = [_mk_seed("A", [{"seller_id": "10001", "seller_name": "卖家A"}])]
    barrier = threading.Barrier(2)
    entered = []

    def analyze(cdp_url, cdp, pid):
        entered.append(pid)
        barrier.wait(timeout=5)  # 两个 worker 同时到达 → 证明真并发
        return _mk_seed(pid, [])

    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch.object(ozon_fission, "_parallel_workers", return_value=2), \
         mock.patch.object(ozon_fission, "fetch_seller_products_shallow", return_value=[]), \
         mock.patch.object(ozon_discovery, "fetch_seller_products",
                           side_effect=lambda **kw: ["P1", "P2"]), \
         mock.patch.object(ozon_discovery, "_analyze_product", side_effect=analyze):
        result = ozon_fission.run_fission(
            seed_products=seeds, max_depth=1, max_total_products=20,
            max_sellers_per_product=10, max_products_per_seller=5, time_budget=30,
        )
    assert len(result) == 1 + 2, f"种子 + 2 裂变候选, got {len(result)}"
    assert all(c.status != "error" for c in result), \
        f"并发正常时不应有 error 候选, got {[c.status for c in result]}"
    assert entered == ["P1", "P2"] or entered == ["P2", "P1"], \
        f"两个 pid 都应被分析, got {entered}"
    # 独立连接：run_fission 主连接 + 每个 pid 一个 worker 连接（不共享）
    assert conn_cls.call_count == 1 + 2, \
        f"连接数应 = 主 + 2 worker, got {conn_cls.call_count}"
    # 每个 worker 任务开独立 tab
    assert conn_cls.return_value.new_tab.call_count == 2, \
        f"每个 worker 任务应开独立 tab, got {conn_cls.return_value.new_tab.call_count}"


def test_single_worker_reuses_shared_connection():
    """_parallel_workers()==1 → 沿用共享 cdp 串行路径，不新建 worker 连接。"""
    seeds = [_mk_seed("A", [{"seller_id": "10001", "seller_name": "卖家A"}])]
    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch.object(ozon_fission, "_parallel_workers", return_value=1), \
         mock.patch.object(ozon_fission, "fetch_seller_products_shallow", return_value=[]), \
         mock.patch.object(ozon_discovery, "fetch_seller_products",
                           side_effect=lambda **kw: ["P1", "P2", "P3"]), \
         mock.patch.object(ozon_discovery, "_analyze_product",
                           side_effect=lambda cdp_url, cdp, pid: _mk_seed(pid, [])):
        result = ozon_fission.run_fission(
            seed_products=seeds, max_depth=1, max_total_products=20,
            max_sellers_per_product=10, max_products_per_seller=5, time_budget=30,
        )
    assert len(result) == 1 + 3, f"got {len(result)}"
    assert sorted(c.ozon_product_id for c in result if c.chain_depth > 0) == \
        ["P1", "P2", "P3"]
    assert conn_cls.call_count == 1, \
        f"单 worker 应只建主连接（共享 cdp）, got {conn_cls.call_count}"


def test_parallel_respects_product_budget_and_visited():
    """max_total_products 预算在主线程预过滤生效；候选 pid 无重复。"""
    seeds = [_mk_seed("A", [{"seller_id": "10001", "seller_name": "卖家A"}])]
    pids = [f"P{i}" for i in range(10)]
    with mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch.object(ozon_fission, "_parallel_workers", return_value=4), \
         mock.patch.object(ozon_fission, "fetch_seller_products_shallow", return_value=[]), \
         mock.patch.object(ozon_discovery, "fetch_seller_products",
                           side_effect=lambda **kw: list(pids)), \
         mock.patch.object(ozon_discovery, "_analyze_product",
                           side_effect=lambda cdp_url, cdp, pid: _mk_seed(pid, [])):
        result = ozon_fission.run_fission(
            seed_products=seeds, max_depth=1, max_total_products=5,
            max_sellers_per_product=10, max_products_per_seller=20, time_budget=30,
        )
    # 种子 1 已在 run_fission 标记 → 预算内最多再派发 4 个商品
    assert len(result) == 5, f"1 种子 + 4 商品 = 5, got {len(result)}"
    pids_out = [c.ozon_product_id for c in result]
    assert len(set(pids_out)) == len(pids_out), "不应有重复 pid"


def test_parallel_workers_formula_lower_bound_one():
    """v0.38.1：_parallel_workers() 下限为 1（单核走串行），上限为 4。"""
    import os as _os
    with mock.patch.object(_os, "cpu_count", return_value=1):
        assert ozon_fission._parallel_workers() == 1, \
            "cpu_count=1 时应返回 1（串行路径真实可达，消除死代码）"
    with mock.patch.object(_os, "cpu_count", return_value=8):
        assert ozon_fission._parallel_workers() == 4, "上限 4（实测安全值）"
    with mock.patch.object(_os, "cpu_count", return_value=None):
        assert ozon_fission._parallel_workers() in (1, 2, 3, 4), "cpu_count=None 时回退默认"


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

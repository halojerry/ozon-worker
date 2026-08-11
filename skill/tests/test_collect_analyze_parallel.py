"""P1a/P2: collect_and_analyze per-pid 循环并行 + 每线程独立 CdpConnection。

验证点:
1. 多 pid 时 _analyze_product 真正并发（Barrier 同步证明 ≥2 线程同时进入）。
2. 每个 worker 任务创建独立 CdpConnection（不共享主连接）→ 连接数 = 主 + N pid。
3. 候选按 pid 原始顺序返回（顺序保持，progress_callback 次序稳定）。
4. force_new_tab=True 透传给 _analyze_product（防 find_tab 抢占用户 tab）。
"""
from __future__ import annotations

import os
import sys
import threading
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.lib.cdp_client as cdp_client
from scripts.lib import ozon_discovery as od
from scripts.lib.ozon_discovery import ProductCandidate


def _mk(pid):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Title {pid}", ozon_price=100.0)
    c.status = "ok"
    return c


def test_parallel_collect_true_concurrency_and_order():
    """2 workers × 2 pids: Barrier(2) 证明真并发; 连接数 = 主 + 2; 顺序保持。"""
    pids = ["p1", "p2"]
    barrier = threading.Barrier(2)
    search_threads: list[int] = []
    force_flags: list[bool] = []
    callbacks: list[str] = []

    def analyze(cdp_url, cdp, pid, force_new_tab=False):
        search_threads.append(threading.get_ident())
        force_flags.append(force_new_tab)
        barrier.wait(timeout=10)  # 两个 worker 同时进入 → 证明并发（串行会在此超时）
        return _mk(pid)

    def cb(i, n, cand):
        callbacks.append(cand.ozon_product_id)

    with mock.patch.object(od, "_discover_workers", return_value=2), \
         mock.patch.object(cdp_client, "CdpConnection") as conn_cls, \
         mock.patch.object(od, "_analyze_product", side_effect=analyze), \
         mock.patch.object(od, "_lazy_collect_urls", return_value=pids), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("time.sleep"):
        result = od.collect_and_analyze(
            "http://127.0.0.1:9222", use_analytics=False, progress_callback=cb)

    # 全部候选完成且顺序保持
    assert [c.ozon_product_id for c in result] == pids, \
        f"候选顺序必须按 pid 原始顺序, got {[c.ozon_product_id for c in result]}"
    assert all(c.status == "ok" for c in result)
    assert callbacks == pids, f"回调次序应保持, got {callbacks}"
    # 真并发
    assert len(set(search_threads)) >= 2, \
        f"应 ≥2 线程并发分析, got {len(set(search_threads))} threads"
    # 每 worker 独立 CdpConnection：主连接 + 每 pid 一个（不共享主连接）
    assert conn_cls.call_count == 1 + len(pids), \
        f"连接数应 = 主 + {len(pids)} worker, got {conn_cls.call_count}"
    # 并行分析必须开独立 tab（force_new_tab=True 透传）
    assert all(force_flags), f"并行分析应 force_new_tab=True, got {force_flags}"


def test_single_worker_reuses_shared_connection():
    """_discover_workers()==1 → 串行路径: 不新建 worker 连接, force_new_tab 默认 False。"""
    pids = ["p1", "p2", "p3"]
    flags: list[bool] = []

    def analyze(cdp_url, cdp, pid, force_new_tab=False):
        flags.append(force_new_tab)
        return _mk(pid)

    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch.object(od, "_analyze_product", side_effect=analyze), \
         mock.patch.object(od, "_lazy_collect_urls", return_value=pids), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("time.sleep"):
        result = od.collect_and_analyze("http://127.0.0.1:9222", use_analytics=False)

    assert [c.ozon_product_id for c in result] == pids
    assert conn_cls.call_count == 1, f"单 worker 只应建主连接, got {conn_cls.call_count}"
    assert all(not f for f in flags), "串行路径 force_new_tab 应保持默认 False（零回归）"


if __name__ == "__main__":
    import traceback

    failed = total = 0
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

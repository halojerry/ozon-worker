"""P2: match_selected 1688 识图并行 + 主线程处理回调。

验证点:
1. _search_1688_source 真正并发（Barrier 证明 ≥2 线程同时进入）。
2. 候选处理顺序保持（匹配结果写回正确的候选，回调按原顺序）。
3. progress_callback 全部在主线程（利润/蓝海评分/状态分配留在主线程）。
4. 串行路径（workers==1）零回归。
"""
from __future__ import annotations

import os
import sys
import threading
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od
from scripts.lib.ozon_discovery import ProductCandidate


def _mk(pid, status="ok"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0)
    c.status = status
    c.ozon_images = [f"https://img.ozone.ru/{pid}.jpg"]
    return c


def _fake_search_return(pid):
    return {"url": f"https://detail.1688.com/offer/{pid}.html",
            "title": f"Товар {pid}", "price": 50.0, "images": []}


def test_match_selected_parallel_concurrency_and_main_thread_callbacks():
    """2 workers × 2 候选: Barrier 证明并发; 结果写回正确候选; 回调在主线程。"""
    cands = [_mk("p1"), _mk("p2")]
    barrier = threading.Barrier(2)
    search_threads: list[int] = []
    main_tid = threading.get_ident()
    callbacks: list[tuple] = []

    def fake_search(cdp_url, images, title, max_retries=1, conn=None, mxou_token=""):
        # ⚠️ 锁定位参映射: 并行路径必须关键字传 conn/mxou_token,
        # 否则 None 会误传给 max_retries（曾致 range(None+1) TypeError）
        assert max_retries == 1, f"max_retries 不得被 conn 位置污染, got {max_retries!r}"
        assert conn is None, f"并行路径应 conn=None（每线程自建连接）, got {conn!r}"
        assert mxou_token == "", f"mxou_token 不得被 conn 位置污染, got {mxou_token!r}"
        search_threads.append(threading.get_ident())
        barrier.wait(timeout=10)  # 两个识图任务并发进入（串行在此超时）
        pid = title.split()[-1]
        return _fake_search_return(pid)

    def cb(i, n, cand):
        callbacks.append((threading.get_ident(), i, n, cand.ozon_product_id))

    with mock.patch.object(od, "_discover_workers", return_value=2), \
         mock.patch.object(od, "_search_1688_source", side_effect=fake_search), \
         mock.patch.object(od, "_query_logistics_from_worker", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch.object(od, "_log_review_record"), \
         mock.patch("time.sleep"):
        result = od.match_selected(cands, "http://127.0.0.1:9222",
                                   min_margin_pct=1, progress_callback=cb)

    # 顺序保持 + 全部处理 + 结果映射回正确候选
    assert [c.ozon_product_id for c in result] == ["p1", "p2"]
    assert all(c.match_1688_url.endswith(f"{c.ozon_product_id}.html")
               for c in result), "1688 匹配结果必须写回正确的候选"
    assert all(c.status == "profitable" for c in result), \
        f"全部候选应 profitable, got {[c.status for c in result]}"
    # 并发
    assert len(set(search_threads)) >= 2, \
        f"应 ≥2 线程并发识图, got {len(set(search_threads))}"
    # 回调全在主线程，顺序保持
    assert callbacks, "应有进度回调"
    assert all(tid == main_tid for tid, *_ in callbacks), \
        "利润/蓝海评分/状态分配/回调必须留在主线程"
    assert [cid for _, _, _, cid in callbacks] == ["p1", "p2"], \
        f"回调顺序应保持, got {callbacks}"


def test_match_selected_serial_no_match_preserves_semantics():
    """串行路径（workers==1）: no_match 语义 + 过滤语义保持（零回归）。"""
    cands = [_mk("p1"), _mk("p2", status="error"), _mk("p3")]
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_search_1688_source", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch.object(od, "_log_review_record"), \
         mock.patch("time.sleep"):
        result = od.match_selected(cands, "http://127.0.0.1:9222")
    # error 候选不被处理（保持 error）；ok 候选 → no_match
    by_id = {c.ozon_product_id: c for c in result}
    assert by_id["p1"].status == "no_match"
    assert by_id["p2"].status == "error", "error 候选应保持原状态（被排除）"
    assert by_id["p3"].status == "no_match"


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

"""P1b: discover --auto-submit 并行提交 — ThreadPoolExecutor(2) 信封构建+提交。

- N 候选全部提交、全部 task_id 收集（stdout 可观测）
- 提交运行在 worker 线程（threading.Barrier(2) 证明并行）
- 单候选失败（build 抛异常）→ 记录后继续，不中断批次
- build 返回 None（无 1688 URL）→ 跳过语义保留
- 确认门（input y/N）保留：非 y 不提交
"""
from __future__ import annotations

import io
import os
import sys
import threading
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate


def _profitable(pid="p1", title="Автопоилка для кошек", url="https://detail.1688.com/offer/1001.html"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = url
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _discover_args(**overrides):
    import argparse
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="monthly_sales>=1", export="", output="", auto_submit=True,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
        china=False, local=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_discover(args, candidates, selected, extra_patches=(), confirm="y"):
    """跑完整 cmd_discover（采集/匹配 mock），返回 (rc, stdout)。"""
    from scripts import cli
    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=candidates),
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   return_value=selected),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.config_store.get_store", return_value={}),
        mock.patch("builtins.input", return_value=confirm),
    ] + list(extra_patches)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    return rc, out.getvalue()


def test_auto_submit_parallel_submits_all_on_worker_threads():
    """2 候选：build+submit 在 worker 线程并行执行（Barrier 证明），全部提交。"""
    c1 = _profitable("p1", "Товар один")
    c2 = _profitable("p2", "Товар два")

    barrier = threading.Barrier(2, timeout=10)
    worker_idents: list[int] = []
    submitted: list[str] = []

    def _build(c, store_config, store_id=""):
        worker_idents.append(threading.get_ident())
        barrier.wait()  # 两个 worker 同时到达 → 并行；串行会超时 BrokenBarrierError
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": f"T-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True)
    rc, out = _run_discover(
        args, [c1, c2], [c1, c2],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert barrier.broken is False, "提交应并行执行（两个 worker 同时到达 barrier）"
    assert len(worker_idents) == 2 and len(set(worker_idents)) == 2, \
        f"两个候选应各自跑在 worker 线程, idents={worker_idents}"
    assert threading.get_ident() not in worker_idents, "提交不应跑在主线程"
    assert sorted(submitted) == ["p1", "p2"], f"全部提交, got {submitted}"
    assert "→ task_id=T-p1" in out and "→ task_id=T-p2" in out, \
        "stdout 应展示两个 task_id"


def test_auto_submit_parallel_failure_continues_batch():
    """单候选 build 抛异常 → 记录失败，其余候选继续提交。"""
    c1 = _profitable("p1", "Товар один")
    c2 = _profitable("p2", "Товар два")
    submitted: list[str] = []

    def _build(c, store_config, store_id=""):
        if c.ozon_product_id == "p1":
            raise RuntimeError("1688 限流")
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": f"T-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True)
    rc, out = _run_discover(
        args, [c1, c2], [c1, c2],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0, "批次不应中断"
    assert "提交失败: Товар один" in out, "失败候选应被记录"
    assert submitted == ["p2"], f"其余候选继续提交, got {submitted}"
    assert "→ task_id=T-p2" in out


def test_auto_submit_parallel_skip_no_envelope():
    """build 返回 None（无 1688 URL）→ 跳过语义保留，不提交。"""
    c1 = _profitable("p1", "Товар один")
    c2 = _profitable("p2", "Товар два")
    submitted: list[str] = []

    def _build(c, store_config, store_id=""):
        if c.ozon_product_id == "p1":
            return None
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": f"T-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True)
    rc, out = _run_discover(
        args, [c1, c2], [c1, c2],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert "跳过（无 1688 URL）: Товар один" in out
    assert submitted == ["p2"], f"无信封候选不应提交, got {submitted}"


def test_auto_submit_confirm_gate_preserved():
    """确认门：input 非 y → 不提交、rc 0。"""
    c1 = _profitable("p1", "Товар один")
    args = _discover_args(auto_submit=True)
    rc, out = _run_discover(
        args, [c1], [c1], confirm="n",
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       return_value={"draft": {}}),
            mock.patch("scripts.cloud_probe.submit_envelope",
                       return_value={"ok": True, "task_id": "T-p1"}),
        ],
    )
    assert rc == 0
    assert "已取消" in out
    assert "task_id=" not in out, "未确认时不应提交"


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

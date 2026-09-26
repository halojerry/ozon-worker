#!/usr/bin/env python3
"""批B3: --wait 一次性命令（graph/follow/discover/discover-task 提交腿）纯 mock 回归。

- 带 --wait：提交成功后调用 poll_task_status，终态打印一行人话
  （completed: task_id+product_id / failed: 原因首行）
- 不带 --wait：poll_task_status 零调用（fire-and-forget 行为回归锁定）
- poll_task_status 自身：状态变化 INFO、连续 3 次不可达 WARNING、每次 poll DEBUG
- 移交批（09-findings）：--to-box×--wait 组合——入箱出口 draft_id 无任务句柄，
  warning 一行 + 零轮询 + 出口码不变（graph/follow/discover/discover-task 四腿）

⚠️ 语义拍板：--wait 在 v0.76 已是重采集串行闸的「排队等锁」标志——本批合并语义：
闸被占排队（原语义）+ 提交后轮询到终态（新语义），缺省（不带 --wait）逐字不变。
终态 failed 时命令 exit 3（打印 ❌ 但 exit 0 = 假成功反模式，不允许）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_wait_flag_v078.py -q
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402
from scripts.cloud_probe import poll_task_status  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


def _fake_poll(calls: list, terminal: dict):
    def _poll(task_id, timeout=900, on_status=None, token=""):
        calls.append(str(task_id))
        return dict(terminal)
    return _poll


def _completed(task_id="T1", product_id="P9"):
    return {"task_id": task_id, "status": "completed", "ok": True, "terminal": True,
            "result_json": {"product_id": product_id}}


def _failed(task_id="T1", err="ошибка размера\nвторая строка"):
    return {"task_id": task_id, "status": "failed", "ok": False, "terminal": True,
            "error_message": err}


# ── graph 腿 ────────────────────────────────────────────────────────────────

_GRAPH_ENV = {
    "token": "t", "ozon_client_id": "c", "ozon_api_key": "k",
    "envelope": {"draft": {
        "item_id": "123", "title": "x",
        # purchase_cost=0 → _estimate_and_print 跳过（无网络），--min-margin 不触发
        "purchase_cost": 0, "weight": 0,
        "dimensions": {"length": 0, "width": 0, "height": 0},
    }, "source": {}, "extensions": {}},
}


def _run_graph(extra_args: list, calls: list, terminal: dict,
               submit_res: dict | None = None):
    """跑 cmd_graph（构建/提交全 mock），返回 (rc, stdout)。"""
    submit_res = submit_res or {"ok": True, "task_id": "T1"}
    poll = _fake_poll(calls, terminal)
    submitted = []

    def _fake_submit(graph):
        submitted.append(graph)
        return dict(submit_res)

    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.preflight_check", return_value=[]))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_graph_envelope_with_retry",
            return_value=_GRAPH_ENV))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._source_preflight", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._check_min_density", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope", side_effect=_fake_submit))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status", side_effect=poll))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        args = cli.build_arg_parser().parse_args(
            ["graph", "--item-id", "123", *extra_args])
        rc = cli.cmd_graph(args)
    return rc, out.getvalue(), submitted


def test_graph_wait_polls_and_prints_completed():
    """①graph --wait：poll 被调 + completed 终态行含 task_id/product_id，exit 0。"""
    calls: list = []
    rc, out, submitted = _run_graph(["--wait"], calls, _completed())
    assert rc == 0
    assert calls == ["T1"]
    assert "✅ 任务完成 task_id=T1 product_id=P9" in out
    assert len(submitted) == 1


def test_graph_wait_failed_prints_reason_and_exit3():
    """②graph --wait：failed 终态行含原因首行，exit 3（不允许 ❌ + exit 0）。"""
    calls: list = []
    rc, out, _ = _run_graph(["--wait"], calls, _failed())
    assert rc == 3
    assert "❌ 任务失败 task_id=T1" in out
    assert "ошибка размера" in out          # 原因首行
    assert "вторая строка" not in out       # 只取首行


def test_graph_no_wait_zero_polls():
    """③graph 不带 --wait：fire-and-forget 回归——poll 零调用、无终态行。"""
    calls: list = []
    rc, out, _ = _run_graph([], calls, _completed())
    assert rc == 0
    assert calls == []
    assert "任务完成" not in out


# ── follow 腿 ───────────────────────────────────────────────────────────────

def _run_follow(extra_args: list, calls: list, terminal: dict):
    poll = _fake_poll(calls, terminal)
    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.preflight_check", return_value=[]))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.follow_sell_cloud",
            return_value={"success": True, "task_id": "T2"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status", side_effect=poll))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        args = cli.build_arg_parser().parse_args(
            ["follow", "--ozon-url", "https://www.ozon.ru/product/x-1/",
             "--auto-submit", *extra_args])
        rc = cli.cmd_follow(args)
    return rc, out.getvalue()


def test_follow_wait_polls_and_prints_completed():
    """④follow --wait：直提 task_id 轮询到终态（此前只埋 _out JSON 不打行）。"""
    calls: list = []
    rc, out = _run_follow(["--wait"], calls, _completed("T2", "P8"))
    assert rc == 0
    assert calls == ["T2"]
    assert "✅ 任务完成 task_id=T2 product_id=P8" in out


def test_follow_no_wait_zero_polls():
    """⑤follow 不带 --wait：零轮询。"""
    calls: list = []
    rc, out = _run_follow([], calls, _completed("T2"))
    assert rc == 0
    assert calls == []


# ── discover 腿 ─────────────────────────────────────────────────────────────

def _profitable(pid="p1", title="Автопоилка для кошек"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = f"https://detail.1688.com/offer/100{pid}.html"
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _discover_args(**overrides):
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        # 规则筛选跳过交互挑选（input）；与 test_cli_auto_submit_parallel 同口径
        rules="monthly_sales>=1", export="", output="", auto_submit=True,
        to_box=False, note="",
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=True, blue_ocean_source="", blue_ocean_csv="",
        china=False, local=False, filter_profile=None, base_filter="",
        review=False, compare_sources=None, notify=False, wait=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_discover(args, candidates, calls: list, terminal: dict,
                  submit_res: dict | None = None, draft_res: dict | None = None):
    submit_res = submit_res or {"ok": True, "task_id": "T-D1"}
    poll = _fake_poll(calls, terminal)

    def _fake_submit(envelope):
        return dict(submit_res)

    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.collect_and_analyze", return_value=candidates))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.apply_selection_rules",
            side_effect=lambda cands, *a, **k: list(cands)))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.match_selected"))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_mxou_token", return_value=""))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_store_profile", return_value={}))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_store", return_value={}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope", side_effect=_fake_submit))
        # 移交批：--to-box 腿走 submit_draft（draft_id 出口）
        if draft_res is not None:
            stack.enter_context(mock.patch(
                "scripts.cloud_probe.submit_draft", return_value=dict(draft_res)))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status", side_effect=poll))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover(args)
    return rc, out.getvalue()


def test_discover_wait_polls_each_submitted_task():
    """⑥discover --auto-submit --wait：每个提交的 task_id 轮询到终态。"""
    cands = [_profitable("p1"), _profitable("p2")]
    calls: list = []

    seq = {"n": 0}

    def _submit(envelope):
        seq["n"] += 1
        return {"ok": True, "task_id": f"T-D{seq['n']}"}

    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.collect_and_analyze", return_value=cands))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.apply_selection_rules",
            side_effect=lambda cands, *a, **k: list(cands)))
        stack.enter_context(mock.patch("scripts.lib.ozon_discovery.match_selected"))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_mxou_token", return_value=""))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_store_profile", return_value={}))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_store", return_value={}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope", side_effect=_submit))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover(_discover_args())
    assert rc == 0
    assert sorted(calls) == ["T-D1", "T-D2"]
    assert out.getvalue().count("✅ 任务完成 task_id=") == 2


def test_discover_no_wait_zero_polls():
    """⑦discover 不带 --wait：零轮询（fire-and-forget 回归）。"""
    cands = [_profitable("p1")]
    calls: list = []
    rc, out = _run_discover(_discover_args(wait=False), cands, calls, _completed())
    assert rc == 0
    assert calls == []
    assert "任务完成" not in out


# ── discover-task 腿 ────────────────────────────────────────────────────────

def _dt_args(**kw):
    base = dict(url="https://www.ozon.ru/highlight/t-935133/", keyword="",
                target_count=10, max_scan=300, filter_profile=None, base_filter="",
                min_margin=15.0, fx_rate=0.075, match_limit=None,
                match_concurrency=1, no_match_streak_stop=5, store="",
                to_box=False, auto_submit=True, dry_run=False, resume=False,
                no_analytics=False, export="", wait=True,
                max_depth=None, allow_depth_3=False, max_total_products=None,
                time_budget=None, expend_shop=0, filters="")
    base.update(kw)
    return argparse.Namespace(**base)


def _dt_cand(pid: str) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
    c.status = "ok"
    return c


def test_discover_task_wait_polls_submitted(tmp_path, monkeypatch):
    """⑧discover-task --auto-submit --wait：提交腿 task_id 逐个轮询到终态。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    calls: list = []
    submits: list = []
    cands = [_dt_cand("401"), _dt_cand("402")]

    def _fake_submit(envelope, source_batch=None):
        submits.append(envelope)
        return {"ok": True, "task_id": f"T-DT-{len(submits)}"}

    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(
            od, "DISCOVERY_CACHE_DIR", tmp_path))
        stack.enter_context(mock.patch.object(
            chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(
            od, "collect_and_analyze", return_value=cands))
        stack.enter_context(mock.patch.object(
            od, "match_selected",
            side_effect=lambda pool, cdp, **kw: [
                (setattr(c, "status", "profitable"),
                 setattr(c, "match_1688_url",
                         f"https://detail.1688.com/offer/{c.ozon_product_id}.html"),
                 setattr(c, "profit_margin", 25.0))
                for c in pool] and pool))
        stack.enter_context(mock.patch.object(
            config_store, "get_store_profile", return_value={}))
        stack.enter_context(mock.patch.object(
            config_store, "get_setting", return_value=None))
        stack.enter_context(mock.patch.object(
            config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope", side_effect=_fake_submit))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover_task(_dt_args())
    assert rc == 0
    assert sorted(calls) == ["T-DT-1", "T-DT-2"]
    assert out.getvalue().count("✅ 任务完成 task_id=") == 2


def test_discover_task_no_wait_zero_polls(tmp_path, monkeypatch):
    """⑨discover-task 不带 --wait：零轮询。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    calls: list = []
    cands = [_dt_cand("501")]
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_path))
        stack.enter_context(mock.patch.object(
            chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(
            od, "collect_and_analyze", return_value=cands))
        stack.enter_context(mock.patch.object(
            od, "match_selected",
            side_effect=lambda pool, cdp, **kw: [
                setattr(c, "status", "profitable") or
                setattr(c, "match_1688_url",
                        f"https://detail.1688.com/offer/{c.ozon_product_id}.html") or
                setattr(c, "profit_margin", 25.0)
                for c in pool] and pool))
        stack.enter_context(mock.patch.object(
            config_store, "get_store_profile", return_value={}))
        stack.enter_context(mock.patch.object(
            config_store, "get_setting", return_value=None))
        stack.enter_context(mock.patch.object(
            config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope",
            return_value={"ok": True, "task_id": "T-X"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover_task(_dt_args(wait=False))
    assert rc == 0
    assert calls == []


# ── arch-findings #4: 批量 --wait 终态 failed → exit 3（对齐 graph/follow 腿）──

def test_discover_wait_failed_exit3():
    """⑫discover --auto-submit --wait：终态 failed → 命令 exit 3（原实现只打印回 0）。"""
    cands = [_profitable("p1")]
    calls: list = []
    rc, out = _run_discover(_discover_args(), cands, calls, _failed("T-D1"))
    assert rc == 3, f"--wait 终态 failed 应 exit 3, got {rc}"
    assert "❌ 任务失败 task_id=T-D1" in out
    assert "1/1 个任务终态 failed" in out


def test_discover_wait_all_completed_exit0():
    """⑬discover --wait 全部 completed：保持 exit 0（成功路径零变化）。"""
    cands = [_profitable("p1"), _profitable("p2")]
    calls: list = []
    rc, out = _run_discover(_discover_args(), cands, calls, _completed())
    assert rc == 0
    assert out.count("✅ 任务完成 task_id=") == 2


def test_discover_wait_partial_failed_exit3():
    """⑭批量 --wait 一成一败：任一 failed 即 exit 3（逐 task_id 判定与顺序无关）。"""
    cands = [_profitable("p1"), _profitable("p2")]
    calls: list = []

    seq = {"n": 0}

    def _submit(envelope):
        seq["n"] += 1
        return {"ok": True, "task_id": f"T-D{seq['n']}"}

    def _poll_by_id(task_id, timeout=900, on_status=None, token=""):
        calls.append(str(task_id))
        return _failed(task_id) if task_id == "T-D1" else _completed()

    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.collect_and_analyze",
            return_value=cands))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.apply_selection_rules",
            side_effect=lambda cands, *a, **k: list(cands)))
        stack.enter_context(mock.patch("scripts.lib.ozon_discovery.match_selected"))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_mxou_token", return_value=""))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_store_profile", return_value={}))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_store", return_value={}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope", side_effect=_submit))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status", side_effect=_poll_by_id))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover(_discover_args())
    assert rc == 3
    assert sorted(calls) == ["T-D1", "T-D2"]
    assert "1/2 个任务终态 failed" in out.getvalue()


def test_discover_task_wait_failed_exit3(tmp_path, monkeypatch):
    """⑮discover-task --auto-submit --wait：终态 failed → exit 3（原实现只打印回 0）。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    calls: list = []
    cands = [_dt_cand("601")]
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_path))
        stack.enter_context(mock.patch.object(
            chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(
            od, "collect_and_analyze", return_value=cands))
        stack.enter_context(mock.patch.object(
            od, "match_selected",
            side_effect=lambda pool, cdp, **kw: [
                setattr(c, "status", "profitable") or
                setattr(c, "match_1688_url",
                        f"https://detail.1688.com/offer/{c.ozon_product_id}.html") or
                setattr(c, "profit_margin", 25.0)
                for c in pool] and pool))
        stack.enter_context(mock.patch.object(
            config_store, "get_store_profile", return_value={}))
        stack.enter_context(mock.patch.object(
            config_store, "get_setting", return_value=None))
        stack.enter_context(mock.patch.object(
            config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope",
            return_value={"ok": True, "task_id": "T-DT-1"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _failed("T-DT-1"))))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover_task(_dt_args())
    assert rc == 3, f"--wait 终态 failed 应 exit 3, got {rc}"
    assert "❌ 任务失败 task_id=T-DT-1" in out.getvalue()
    assert "1/1 个任务终态 failed" in out.getvalue()


# ── 移交批（09-findings）：--to-box × --wait 组合 ──────────────────────────
# 入箱出口是 draft_id 非 worker 任务句柄，此前 --wait 被静默跳过；现在四命令
# （graph/follow/discover/discover-task）warning 一行 + 零轮询 + 出口码不变。

_BOX_WARN = "⚠️ --wait 与 --to-box 组合：入箱出口无任务句柄，--wait 轮询跳过"


def test_graph_to_box_wait_warns_zero_poll_exit0():
    """⑯graph --to-box --wait：warning 一行 + 零轮询 + exit 0（入箱出口码不变）。"""
    calls: list = []
    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.preflight_check", return_value=[]))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_graph_envelope_with_retry",
            return_value=_GRAPH_ENV))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._source_preflight", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._check_min_density", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_draft",
            return_value={"ok": True, "draft_id": "D-G1"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        args = cli.build_arg_parser().parse_args(
            ["graph", "--item-id", "123", "--to-box", "--wait"])
        rc = cli.cmd_graph(args)
    assert rc == 0
    assert calls == [], "入箱出口 draft_id 无任务句柄，不得触发轮询"
    assert _BOX_WARN in out.getvalue()
    assert "📥 已入采集箱" in out.getvalue()


def test_graph_to_box_no_wait_no_warning():
    """⑰graph --to-box 不带 --wait：无 warning（提示只在显式 --wait 时出现）。"""
    calls: list = []
    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.preflight_check", return_value=[]))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_graph_envelope_with_retry",
            return_value=_GRAPH_ENV))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._source_preflight", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._check_min_density", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_draft",
            return_value={"ok": True, "draft_id": "D-G1"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        args = cli.build_arg_parser().parse_args(
            ["graph", "--item-id", "123", "--to-box"])
        rc = cli.cmd_graph(args)
    assert rc == 0
    assert calls == []
    assert _BOX_WARN not in out.getvalue()


def test_follow_to_box_wait_warns_zero_poll_exit0():
    """⑱follow --to-box --wait：warning 一行 + 零轮询 + exit 0。"""
    calls: list = []
    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.preflight_check", return_value=[]))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.follow_sell_cloud",
            return_value={"success": True, "draft_id": "D-F1"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        args = cli.build_arg_parser().parse_args(
            ["follow", "--ozon-url", "https://www.ozon.ru/product/x-1/",
             "--to-box", "--wait"])
        rc = cli.cmd_follow(args)
    assert rc == 0
    assert calls == []
    assert _BOX_WARN in out.getvalue()


def test_discover_to_box_wait_warns_zero_poll_exit0():
    """⑲discover --to-box --auto-submit --wait：warning + 零轮询 + exit 0
    （discover-multi 共用 _finish_discover_flow，同覆盖）。"""
    cands = [_profitable("p1")]
    calls: list = []
    rc, out = _run_discover(_discover_args(to_box=True), cands, calls,
                            _completed(), draft_res={"ok": True, "draft_id": "D-D1"})
    assert rc == 0
    assert calls == []
    assert _BOX_WARN in out
    assert "📥 已入采集箱" in out


def test_discover_to_box_no_wait_no_warning():
    """⑳discover --to-box --auto-submit 不带 --wait：无 warning、零轮询。"""
    cands = [_profitable("p1")]
    calls: list = []
    rc, out = _run_discover(_discover_args(to_box=True, wait=False), cands, calls,
                            _completed(), draft_res={"ok": True, "draft_id": "D-D1"})
    assert rc == 0
    assert calls == []
    assert _BOX_WARN not in out


def test_discover_task_to_box_wait_warns_zero_poll_exit0(tmp_path, monkeypatch):
    """㉑discover-task --to-box --wait：warning + 零轮询 + exit 0（draft_id 无句柄）。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    calls: list = []
    cands = [_dt_cand("701")]
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_path))
        stack.enter_context(mock.patch.object(
            chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(
            od, "collect_and_analyze", return_value=cands))
        stack.enter_context(mock.patch.object(
            od, "match_selected",
            side_effect=lambda pool, cdp, **kw: [
                setattr(c, "status", "profitable") or
                setattr(c, "match_1688_url",
                        f"https://detail.1688.com/offer/{c.ozon_product_id}.html") or
                setattr(c, "profit_margin", 25.0)
                for c in pool] and pool))
        stack.enter_context(mock.patch.object(
            config_store, "get_store_profile", return_value={}))
        stack.enter_context(mock.patch.object(
            config_store, "get_setting", return_value=None))
        stack.enter_context(mock.patch.object(
            config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_draft",
            return_value={"ok": True, "draft_id": "D-DT-1"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.poll_task_status",
            side_effect=_fake_poll(calls, _completed())))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover_task(_dt_args(to_box=True, auto_submit=False))
    assert rc == 0
    assert calls == []
    assert _BOX_WARN in out.getvalue()
    assert "📥 已入采集箱" in out.getvalue()


# ── poll_task_status 可观测性增强（B3.1，行为逐字保持）────────────────────

def test_poll_task_status_status_change_info_and_poll_debug(caplog, monkeypatch):
    """⑩每次 poll DEBUG；状态变化 INFO 一行；终态即返。"""
    seq = [{"status": "running", "terminal": False, "ok": False},
           {"status": "running", "terminal": False, "ok": False},
           {"status": "completed", "terminal": True, "ok": True,
            "result_json": {"product_id": "P"}}]
    monkeypatch.setattr("scripts.cloud_probe.check_task_status",
                        lambda tid, token="": dict(seq.pop(0)))
    sleeps: list = []
    monkeypatch.setattr("scripts.cloud_probe.time.sleep", lambda s: sleeps.append(s))
    with caplog.at_level(logging.DEBUG, logger="scripts.cloud_probe"):
        r = poll_task_status("T-P")
    assert r["status"] == "completed"
    assert len(sleeps) == 2, "终态前每 10s 一次（间隔语义保持）"
    debug_msgs = [x for x in caplog.records
                  if x.name == "scripts.cloud_probe" and x.levelno == logging.DEBUG]
    info_msgs = [x for x in caplog.records
                 if x.name == "scripts.cloud_probe" and x.levelno == logging.INFO]
    assert len(debug_msgs) == 3, "每次 poll 一条 DEBUG"
    assert sum(1 for m in info_msgs if "running" in m.getMessage()) == 1, \
        "同状态重复 poll 不重复 INFO（状态变化才一行）"
    assert any("completed" in m.getMessage() for m in info_msgs)


def test_poll_task_status_warns_after_3_consecutive_unreachable(caplog, monkeypatch):
    """⑪连续 3 次 worker_unreachable → 一行 WARNING（每轮只一次），不改变轮询行为。"""
    seq = ([{"status": "worker_unreachable", "terminal": False, "ok": False}] * 3
           + [{"status": "running", "terminal": False, "ok": False}]
           + [{"status": "failed", "terminal": True, "ok": False,
               "error_message": "e"}])
    monkeypatch.setattr("scripts.cloud_probe.check_task_status",
                        lambda tid, token="": dict(seq.pop(0)))
    monkeypatch.setattr("scripts.cloud_probe.time.sleep", lambda s: None)
    with caplog.at_level(logging.WARNING, logger="scripts.cloud_probe"):
        r = poll_task_status("T-U")
    assert r["status"] == "failed"
    warns = [x for x in caplog.records
             if x.name == "scripts.cloud_probe" and x.levelno == logging.WARNING
             and "连续" in x.getMessage()]
    assert len(warns) == 1, "同一不可达段只 WARNING 一行"


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
    sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""批B4: discover/discover-task 终局 run 报告（data/logs/report_{ts}_{cmd}.json）纯 mock。

- 报告 JSON 落盘：逐条 {item_id/title/status/task_id/draft_id/error_first_line} + 汇总计数
- 路径 print 一行（📄 运行报告: <path>）
- 已有 analysis report（export_analysis_report）不受影响（仍生成）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_run_report_v078.py -q
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


def _cand(pid: str, status: str = "profitable", error: str = "") -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1500.0)
    c.status = status
    c.match_1688_url = (f"https://detail.1688.com/offer/88{pid}.html"
                        if status == "profitable" else "")
    c.error = error
    c.has_analytics = False
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
        review=False, compare_sources=None, notify=False, wait=False,
        command="discover",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_discover(args, candidates, submit_res=None, tmp_logs=None):
    submit_res = submit_res or {"ok": True, "task_id": "T-R1"}
    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=candidates),
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   side_effect=lambda cands, *a, **k: list(cands)),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.config_store.get_store", return_value={}),
        mock.patch("scripts.cloud_probe.submit_envelope", return_value=dict(submit_res)),
        mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                   side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}),
        mock.patch("scripts.lib.ozon_discovery.export_analysis_report",
                   return_value=None),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover(args)
    return rc, out.getvalue()


def test_discover_run_report_written(tmp_path, monkeypatch):
    """①discover 结束写 report_discover_*.json：逐条字段 + 汇总 + 路径 print。"""
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    cands = [_cand("61001", "profitable"), _cand("61002", "rejected", error="利润率不足\n明细"),
             _cand("61003", "no_match")]
    rc, out = _run_discover(_discover_args(), cands)
    assert rc == 0
    reports = list(tmp_path.glob("report_*_discover.json"))
    assert len(reports) == 1, f"应落一份报告: {list(tmp_path.iterdir())}"
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    items = data["items"]
    assert len(items) == 3
    by_pid = {i["item_id"]: i for i in items}
    # item_id 来自 1688 货源 URL（offer/8861001.html → 数字 item）；task_id 只给已提交条
    assert by_pid["8861001"]["status"] == "profitable"
    assert by_pid["8861001"]["task_id"] == "T-R1"
    assert by_pid["8861001"]["draft_id"] == ""
    assert by_pid["8861001"]["title"] == "Товар 61001"
    # 无货源（rejected/no_match 无 1688 URL）→ item_id 回退 ozon_product_id
    assert by_pid["61002"]["status"] == "rejected"
    assert by_pid["61002"]["error_first_line"] == "利润率不足", "error 只取首行"
    assert by_pid["61003"]["task_id"] == ""
    assert data["summary"]["total"] == 3
    assert data["summary"]["profitable"] == 1
    assert data["summary"]["rejected"] == 1
    assert "📄 运行报告:" in out and str(reports[0]) in out


def test_discover_to_box_report_records_draft_id(tmp_path, monkeypatch):
    """②--to-box：报告 draft_id 出口（task_id 空），提交走 submit_draft。"""
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    cands = [_cand("p1", "profitable")]
    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=cands),
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   side_effect=lambda cands, *a, **k: list(cands)),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.config_store.get_store", return_value={}),
        mock.patch("scripts.cloud_probe.submit_draft",
                   return_value={"ok": True, "draft_id": "DR-1"}),
        mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                   side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}),
        mock.patch("scripts.lib.ozon_discovery.export_analysis_report",
                   return_value=None),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover(_discover_args(to_box=True))
    assert rc == 0
    reports = list(tmp_path.glob("report_*_discover.json"))
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert data["items"][0]["draft_id"] == "DR-1"
    assert data["items"][0]["task_id"] == ""


def test_discover_task_run_report_written(tmp_path, monkeypatch):
    """③discover-task 结束写 report_discover-task_*.json（auto_submit 出口）。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od

    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    c = ProductCandidate(ozon_product_id="601", ozon_title="Товар 601",
                         ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
    c.status = "ok"
    c2 = ProductCandidate(ozon_product_id="602", ozon_title="Товар 602",
                          ozon_price=900.0)
    c2.status = "filtered"

    def _match(pool, cdp, **kw):
        for x in pool:
            x.status = "profitable"
            x.match_1688_url = f"https://detail.1688.com/offer/{x.ozon_product_id}.html"
            x.profit_margin = 25.0
        return pool

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_path))
        stack.enter_context(mock.patch.object(
            chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(od, "collect_and_analyze",
                                              return_value=[c, c2]))
        stack.enter_context(mock.patch.object(od, "match_selected", side_effect=_match))
        stack.enter_context(mock.patch.object(config_store, "get_store_profile",
                                              return_value={}))
        stack.enter_context(mock.patch.object(config_store, "get_setting",
                                              return_value=None))
        stack.enter_context(mock.patch.object(config_store, "get_store",
                                              return_value={"client_id": "1",
                                                            "api_key": "k"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope",
            return_value={"ok": True, "task_id": "T-DT-1"}))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_envelope_from_discovery",
            side_effect=lambda x, sc, store_id="": {"token": "t", "envelope": {}}))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery.export_analysis_report", return_value=None))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_discover_task(argparse.Namespace(
            url="https://www.ozon.ru/highlight/t-1/", keyword="", target_count=10,
            max_scan=300, filter_profile=None, base_filter="", min_margin=15.0,
            fx_rate=0.075, match_limit=None, match_concurrency=1,
            no_match_streak_stop=5, store="", to_box=False, auto_submit=True,
            dry_run=False, resume=False, no_analytics=False, export="", wait=False,
            max_depth=None, allow_depth_3=False, max_total_products=None,
            time_budget=None, expend_shop=0, filters=""))
    assert rc == 0
    reports = list(tmp_path.glob("report_*_discover-task.json"))
    assert len(reports) == 1
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    by_pid = {i["item_id"]: i for i in data["items"]}
    assert by_pid["601"]["status"] == "profitable"
    assert by_pid["601"]["task_id"] == "T-DT-1"
    assert by_pid["602"]["status"] == "filtered"
    assert "📄 运行报告:" in out.getvalue()


def test_analysis_report_untouched(tmp_path, monkeypatch):
    """④既有 analysis report 通道不受影响：export_analysis_report 仍被调用。"""
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    cands = [_cand("p1", "profitable")]
    called = []
    with ExitStack() as stack:
        for p in [
            mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                       return_value=(True, "ok")),
            mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                       return_value=cands),
            mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                       side_effect=lambda cands, *a, **k: list(cands)),
            mock.patch("scripts.lib.ozon_discovery.match_selected"),
            mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
            mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
            mock.patch("scripts.lib.config_store.get_store", return_value={}),
            mock.patch("scripts.cloud_probe.submit_envelope",
                       return_value={"ok": True, "task_id": "T-Z"}),
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=lambda c, sc, store_id="": {"token": "t",
                                                               "envelope": {}}),
            mock.patch("scripts.lib.ozon_discovery.export_analysis_report",
                       side_effect=lambda sel: called.append(len(sel)) or None),
        ]:
            stack.enter_context(p)
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        cli.cmd_discover(_discover_args(), )
    assert called == [1], "export_analysis_report 仍被调用（不动）"


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

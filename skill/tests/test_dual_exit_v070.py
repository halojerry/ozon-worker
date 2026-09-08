#!/usr/bin/env python3
"""选品双出口回归（v0.70）：所有选品管线 入采集箱 / 直接上架 按用户需求二选一。

出口矩阵（本批补齐后全量成立）：
| 命令               | --to-box（采集箱） | --auto-submit（直上管线） |
| search（1688 词搜） | ✅ v0.70 新增      | ✅ 原有                   |
| discover（Ozon）    | ✅                 | ✅                        |
| discover-multi      | ✅                 | ✅                        |
| discover-task       | ✅                 | ✅ v0.70 新增             |
| follow / graph      | ✅                 | ✅                        |
两 flag 互斥（argparse mutually exclusive group）；信封构建链同源，
仅提交端点不同（submit_draft=POST /drafts vs submit_envelope=submit_task）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_dual_exit_v070.py -q
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


# ── discover-task --auto-submit ───────────────────────────────────────

def _cand(pid: str, status: str = "ok") -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
    c.status = status
    c.match_1688_url = f"https://detail.1688.com/offer/{pid}.html"
    return c


def _dt_args(**kw) -> argparse.Namespace:
    base = dict(url="", keyword="k", target_count=10, max_scan=300,
                filter_profile=None, base_filter="", min_margin=15.0,
                fx_rate=0.075, match_limit=None, match_concurrency=1,
                no_match_streak_stop=5, store="", to_box=False,
                auto_submit=False, dry_run=True, resume=False,
                no_analytics=False, export="")
    base.update(kw)
    return argparse.Namespace(**base)


def _run_dt(args, cands, tmp_cache, calls, envelope=None):
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od

    def _match(candidates, cdp, **kw):
        for c in candidates:
            if c.status in ("ok", "uncertain"):
                c.status = "profitable"
                c.profit_margin = 25.0
        return candidates

    patches = [
        mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_cache),
        mock.patch.object(chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")),
        mock.patch.object(od, "collect_and_analyze", return_value=cands),
        mock.patch.object(od, "match_selected", side_effect=_match),
        mock.patch.object(config_store, "get_store_profile", return_value={}),
        mock.patch.object(config_store, "get_setting", return_value=None),
        mock.patch.object(config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}),
        mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                   side_effect=lambda c, sc, store_id="": (envelope or {"token": "t"})),
        mock.patch("scripts.cloud_probe.submit_draft",
                   side_effect=lambda env: (calls.setdefault("draft", []).append(env)
                                            or {"draft_id": f"d{len(calls['draft'])}"})),
        mock.patch("scripts.cloud_probe.submit_envelope",
                   side_effect=lambda env: (calls.setdefault("worker", []).append(env)
                                            or {"ok": True, "task_id": f"T{len(calls['worker'])}"})),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover_task(args)
    return rc, out.getvalue()


def test_discover_task_auto_submit_goes_to_worker_pipeline(tmp_path):
    """--auto-submit：profitable 逐条 submit_envelope（管线），不走 submit_draft。"""
    calls: dict = {}
    cands = [_cand("301"), _cand("302")]
    rc, out = _run_dt(_dt_args(auto_submit=True, dry_run=False), cands, tmp_path, calls)
    assert rc == 0
    assert len(calls.get("worker", [])) == 2
    assert calls.get("draft") is None, "auto-submit 不应入采集箱"
    assert "已提交上架" in out and "task_id=T" in out
    state = json.loads(next((tmp_path / "tasks").glob("task_*.json")).read_text(encoding="utf-8"))
    assert state["summary"]["submitted"] == 2
    assert state["processed"]["301"] == {"status": "ok", "task_id": "T1"}


def test_discover_task_to_box_untouched(tmp_path):
    """--to-box 语义不变：走 submit_draft，不入 worker 队列。"""
    calls: dict = {}
    rc, out = _run_dt(_dt_args(to_box=True, dry_run=False),
                      [_cand("401")], tmp_path, calls)
    assert rc == 0
    assert len(calls.get("draft", [])) == 1
    assert calls.get("worker") is None
    assert "已入采集箱" in out


def test_discover_task_exit_flags_mutually_exclusive():
    """--to-box 与 --auto-submit 同给 → argparse 直接报错（SystemExit）。"""
    parser = cli.build_arg_parser()
    try:
        parser.parse_args(["discover-task", "--keyword", "k", "--to-box", "--auto-submit"])
        raise AssertionError("应触发 mutually exclusive 报错")
    except SystemExit:
        pass


# ── search --to-box ───────────────────────────────────────────────────

def _run_search(args, calls, products):
    from scripts.lib import ak_1688_client, config_store
    from scripts.lib import ozon_discovery as od

    patches = [
        mock.patch.object(ak_1688_client, "search_products", return_value=products),
        mock.patch.object(config_store, "get_ozon_credentials",
                          return_value={"margin_rate": 0.25, "commission_rate": 0.10}),
        mock.patch.object(od, "_query_logistics_from_worker", return_value=6.0),
        mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry",
                   side_effect=lambda item_id, detail_url, store_id="":
                       (calls.setdefault("build", []).append(item_id)
                        or {"token": "t", "envelope": {}})),
        mock.patch("scripts.cloud_probe.submit_draft",
                   side_effect=lambda env: (calls.setdefault("draft", []).append(env)
                                            or {"draft_id": f"d{len(calls['draft'])}"})),
        mock.patch("scripts.cloud_probe.submit_envelope",
                   side_effect=lambda env: (calls.setdefault("worker", []).append(env)
                                            or {"ok": True, "task_id": f"T{len(calls['worker'])}"})),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_search(args)
    return rc, out.getvalue()


def _search_args(**kw) -> argparse.Namespace:
    base = dict(query="手套", page_size=5, sort="", export="", rules="",
                store="", auto_submit=False, to_box=False, threads=2)
    base.update(kw)
    return argparse.Namespace(**base)


_PRODUCTS = [{"product_id": "1001", "title": "Перчатки A", "price": 5.0},
             {"product_id": "1002", "title": "Перчатки B", "price": 8.0}]


def test_search_to_box_uses_draft_endpoint(tmp_path):
    """search --to-box：同一信封构建链 → submit_draft（采集箱），不进 worker 队列。"""
    calls: dict = {}
    rc, out = _run_search(_search_args(to_box=True), calls, list(_PRODUCTS))
    assert rc == 0
    assert calls.get("build") == ["1001", "1002"]
    assert len(calls.get("draft", [])) == 2
    assert calls.get("worker") is None
    assert "已入采集箱" in out and "draft_id=d" in out


def test_search_auto_submit_pipeline_unchanged(tmp_path):
    """search --auto-submit：原有 submit_envelope 语义逐字保持。"""
    calls: dict = {}
    rc, out = _run_search(_search_args(auto_submit=True), calls, list(_PRODUCTS))
    assert rc == 0
    assert len(calls.get("worker", [])) == 2
    assert calls.get("draft") is None
    assert "已提交" in out and "task_id=T" in out


def test_search_exit_flags_mutually_exclusive():
    """search 的 --to-box 与 --auto-submit 同给 → argparse 报错。"""
    parser = cli.build_arg_parser()
    try:
        parser.parse_args(["search", "词", "--to-box", "--auto-submit"])
        raise AssertionError("应触发 mutually exclusive 报错")
    except SystemExit:
        pass


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

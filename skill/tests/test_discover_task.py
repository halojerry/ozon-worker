#!/usr/bin/env python3
"""discover-task 任务式全自动命令回归（discover 漏斗 v2 · Task 8b）。

覆盖：①干跑全链（不出信封不入箱，任务状态落盘）；②--to-box 真实入箱链
（mock submit_draft，逐条 envelope → drafts）；③--resume 跳过已处理 pid；
④缺省档位 = ai（resolve_filter_profile(None, True) 语义）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_task.py -q
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


def _args(**kw) -> argparse.Namespace:
    base = dict(url="https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/",
                keyword="", target_count=10, max_scan=300, filter_profile=None,
                base_filter="",
                min_margin=15.0, fx_rate=0.075, match_limit=None,
                match_concurrency=1, no_match_streak_stop=5, store="",
                to_box=False, dry_run=True, resume=False, no_analytics=False,
                export="")
    base.update(kw)
    return argparse.Namespace(**base)


def _cand(pid: str, status: str, margin: float = 20.0) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
    c.status = status
    c.profit_margin = margin
    c.match_1688_url = f"https://detail.1688.com/offer/{pid}.html" if status == "profitable" else ""
    return c


def _run_cli(args, cands, tmp_cache, submit_mock=None):
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    calls = {"submit": []}

    def _fake_submit(envelope):
        calls["submit"].append(envelope)
        return {"draft_id": f"draft-{len(calls['submit'])}"}

    with mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_cache), \
         mock.patch.object(chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch.object(od, "collect_and_analyze", return_value=cands), \
         mock.patch.object(od, "match_selected",
                           side_effect=lambda candidates, cdp, **kw: [
                               setattr(c, "status", "profitable") or
                               setattr(c, "match_1688_url",
                                       f"https://detail.1688.com/offer/{c.ozon_product_id}.html") or
                               setattr(c, "profit_margin", 25.0)
                               for c in candidates if c.status in ("ok", "uncertain")] and candidates), \
         mock.patch.object(config_store, "get_store_profile", return_value={}), \
         mock.patch.object(config_store, "get_setting", return_value=None), \
         mock.patch.object(config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.cloud_probe.submit_draft",
                    side_effect=submit_mock or _fake_submit), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}):
        rc = cli.cmd_discover_task(args)
    return rc, calls


def test_dry_run_full_chain(tmp_path):
    """干跑：不出信封不提交；任务状态落盘；summary 带 candidates 计数。"""
    cands = [_cand("101", "ok"), _cand("102", "ok"), _cand("103", "uncertain"),
             _cand("104", "filtered")]
    rc, calls = _run_cli(_args(dry_run=True), cands, tmp_path)
    assert rc == 0
    assert calls["submit"] == []                     # 干跑不提交
    # 任务状态已落盘（tmp 缓存目录 tasks/）
    files = list((tmp_path / "tasks").glob("task_*.json"))
    assert len(files) == 1
    state = json.loads(files[0].read_text(encoding="utf-8"))
    assert state["params"]["filter_profile"] == "ai"  # 任务式缺省 ai 档
    assert state["summary"]["candidates"]["profitable"] == 3


def test_to_box_submits_each_profitable(tmp_path):
    """--to-box：每个 profitable 候选出信封 → submit_draft → 状态 ok 记录。"""
    cands = [_cand("201", "ok"), _cand("202", "ok")]
    rc, calls = _run_cli(_args(to_box=True, dry_run=False), cands, tmp_path)
    assert rc == 0
    assert len(calls["submit"]) == 2
    files = list((tmp_path / "tasks").glob("task_*.json"))
    state = json.loads(files[0].read_text(encoding="utf-8"))
    assert state["summary"]["submitted"] == 2
    assert set(state["processed"]) == {"201", "202"}


def test_resume_skips_processed_pids(tmp_path):
    """--resume：预置任务状态含 pid=301 已处理 → 该候选跳过（只提交 302）。"""
    cands = [_cand("301", "ok"), _cand("302", "ok")]
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    prev = {"task_id": "20260906_000000", "entry": {"url": _args().url, "keyword": ""},
            "processed": {"301": {"status": "ok", "draft_id": "draft-old"}},
            "summary": {}}
    (tasks_dir / "task_20260906_000000.json").write_text(
        json.dumps(prev, ensure_ascii=False), encoding="utf-8")
    rc, calls = _run_cli(_args(to_box=True, resume=True, dry_run=False), cands, tmp_path)
    assert rc == 0
    assert len(calls["submit"]) == 1                 # 301 被跳过，只入箱 302


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

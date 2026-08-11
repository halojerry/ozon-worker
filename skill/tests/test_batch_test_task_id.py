#!/usr/bin/env python3
"""P4: batch_test process_ozon_url 提交模式下 success 必须要求真实 Worker task_id。

背景：follow_sell_cloud 曾因 envelope 构建失败仍 success=True（图搜命中即置位），
follow_result = {"success": True, "task_id": ""} → process_ozon_url 报成功但无 task_id。

修复：auto_submit（dry_run=False）时 success = bool(task_id)。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_batch_test_task_id.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import batch_test  # noqa: E402


def _run_process(follow_result: dict, dry_run: bool = False) -> dict:
    """mock follow_sell_cloud 后调 process_ozon_url。"""
    with mock.patch("scripts.cloud_probe.follow_sell_cloud", return_value=follow_result):
        return batch_test.process_ozon_url(
            "https://www.ozon.ru/product/slug-4767514314/",
            "4767514314",
            "cid",
            "akey",
            "http://localhost:8080",
            dry_run=dry_run,
            store_id="",
        )


def test_submit_mode_success_false_when_task_id_empty():
    """auto_submit: follow_result success=True 但 task_id 空 → 最终 success=False。"""
    r = _run_process({
        "success": True,
        "task_id": "",
        "1688_matches": [{"id": "1", "badge_score": 3}],
        "error": "",
        "submit_result": {"ok": True},
    })
    assert r.get("success") is False, f"无 task_id 不应 success: {r}"
    assert r.get("task_id") == ""


def test_submit_mode_success_true_when_task_id_present():
    """auto_submit: follow_result success=True + task_id → 最终 success=True。"""
    r = _run_process({
        "success": True,
        "task_id": "T-123",
        "1688_matches": [{"id": "1", "badge_score": 3}],
        "error": "",
        "submit_result": {"ok": True, "task_id": "T-123"},
    })
    assert r.get("success") is True
    assert r.get("task_id") == "T-123"


def test_submit_mode_success_false_when_follow_failed():
    """auto_submit: follow_result success=False → 最终 success=False。"""
    r = _run_process({
        "success": False,
        "task_id": "",
        "1688_matches": [],
        "error": "no_relevant_match",
    })
    assert r.get("success") is False
    assert r.get("error") == "no_relevant_match"


def test_dry_run_success_follows_follow_result():
    """dry-run: success 保持跟随 follow_result（不要求 task_id，dry-run 不提交）。"""
    r = _run_process({
        "success": True,
        "task_id": "",
        "1688_matches": [{"id": "1", "badge_score": 3}],
        "error": "",
    }, dry_run=True)
    assert r.get("success") is True, "dry-run 不提交，不应要求 task_id"
    assert r.get("dry_run") is True


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

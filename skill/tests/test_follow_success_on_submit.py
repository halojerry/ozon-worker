#!/usr/bin/env python3
"""P4: follow_sell_cloud 的 success 必须来自提交结果（auto_submit）而非图搜命中。

- auto_submit + submit ok + task_id → success=True
- auto_submit + submit ok=False → success=False
- auto_submit + submit ok=True 但无 task_id → success=False
- dry-run（auto_submit=False）envelope 构建成功 → success=True

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_follow_success_on_submit.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402

URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"
CDP_DATA = {"success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка"}
CDP_RESULTS = [{
    "id": "980815374096", "title": "宠物饮水器", "price": "5.5",
    "image": "http://img/1688/1.jpg", "badge": "符合 3/3 个条件",
}]
BEST = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器"}
ENVELOPE = {
    "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
    "envelope": {"draft": {"item_id": "980815374096"}, "extensions": {}},
}


def _run_follow(auto_submit: bool, submit_res: dict) -> dict:
    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"), \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cloud_probe, "_cached_ozon_scrape", return_value=CDP_DATA), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics", return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp", return_value=CDP_RESULTS), \
         mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=BEST), \
         mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=ENVELOPE), \
         mock.patch.object(cloud_probe, "submit_envelope", return_value=submit_res), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        return cloud_probe.follow_sell_cloud(URL, auto_submit=auto_submit, store_id="s1")


def test_success_true_when_submit_ok_and_task_id():
    """auto_submit + submit ok + task_id → success=True（success 在提交后置位）。"""
    r = _run_follow(auto_submit=True, submit_res={"ok": True, "task_id": "T-123"})
    assert r.get("success") is True
    assert r.get("task_id") == "T-123"
    assert r.get("envelope_built") is True
    assert r.get("submit_result") == {"ok": True, "task_id": "T-123"}


def test_success_false_when_submit_not_ok():
    """auto_submit + submit ok=False → success=False（图搜命中不能代表成功）。"""
    r = _run_follow(auto_submit=True, submit_res={"ok": False, "error": "余额不足"})
    assert r.get("success") is False
    assert r.get("task_id") == ""
    assert r.get("envelope_built") is True, "envelope 已构建但提交失败"


def test_success_false_when_task_id_missing():
    """auto_submit + submit ok=True 但无 task_id → success=False（无任务可查）。"""
    r = _run_follow(auto_submit=True, submit_res={"ok": True})
    assert r.get("success") is False
    assert r.get("task_id") == ""


def test_dry_run_success_true_when_envelope_built():
    """dry-run（auto_submit=False）envelope 构建成功 → success=True（不提交）。"""
    r = _run_follow(auto_submit=False, submit_res={"ok": True, "task_id": "T-123"})
    assert r.get("success") is True
    assert "submit_result" not in r
    assert r.get("envelope_built") is True


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

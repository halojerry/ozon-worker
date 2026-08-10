"""batch_test --resume 断点续传（Q8, TDD）。

功能点:
1. find_latest_batch_file: 找最新 batch_*.json（排除 *_summary.json）
2. load_resume_results: 容错读历史结果（损坏/非 list → []）
3. successful_ids: 只收集 success=true 的 ID（失败项续传时重试，不跳过）
4. filter_unprocessed: 过滤已成功项 → (剩余列表, 跳过数量)
5. main() 集成: --resume-from 跳过成功项 + 只重试失败项 + 结果合并写回原文件
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import batch_test


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="batch_resume_"))


def _result(uid: str, success: bool) -> dict:
    return {
        "type": "1688",
        "url": f"https://detail.1688.com/offer/{uid}.html",
        "offer_id": uid,
        "success": success,
    }


# ── 1. find_latest_batch_file ──

def test_find_latest_batch_file_skips_summary():
    """最新 batch_*.json 被选中，*_summary.json 排除。"""
    d = _tmp_dir()
    (d / "batch_20260811_100001.json").write_text("[]", encoding="utf-8")
    latest = d / "batch_20260811_100002.json"
    latest.write_text("[]", encoding="utf-8")
    (d / "batch_20260811_100002_summary.json").write_text("{}", encoding="utf-8")
    got = batch_test.find_latest_batch_file(d)
    assert got is not None and got.name == latest.name, f"应选中 {latest.name}，实际 {got}"


def test_find_latest_batch_file_none():
    """空目录 → None。"""
    assert batch_test.find_latest_batch_file(_tmp_dir()) is None


# ── 2. load_resume_results ──

def test_load_resume_results_valid():
    """合法结果文件 → 原样读出 list。"""
    d = _tmp_dir()
    p = d / "batch_x.json"
    p.write_text(json.dumps([{"offer_id": "1", "success": True}]), encoding="utf-8")
    assert batch_test.load_resume_results(p) == [{"offer_id": "1", "success": True}]


def test_load_resume_results_corrupt_returns_empty():
    """损坏 JSON → []（不阻断续传）。"""
    d = _tmp_dir()
    p = d / "batch_x.json"
    p.write_text("{not json", encoding="utf-8")
    assert batch_test.load_resume_results(p) == []


def test_load_resume_results_non_list_returns_empty():
    """JSON 是 dict（如误传 summary 文件）→ []。"""
    d = _tmp_dir()
    p = d / "batch_x.json"
    p.write_text('{"stats": {}}', encoding="utf-8")
    assert batch_test.load_resume_results(p) == []


# ── 3. successful_ids ──

def test_successful_ids_only_success():
    """仅 success=true 的 ID 被收集；失败项不跳过（续传时重试）。"""
    results = [_result("1", True), _result("2", False), _result("3", True)]
    assert batch_test.successful_ids(results) == {"1", "3"}


def test_successful_ids_ozon_uses_product_id():
    """Ozon 结果取 product_id 字段。"""
    results = [{"type": "ozon", "product_id": "4767514314", "success": True}]
    assert batch_test.successful_ids(results) == {"4767514314"}


def test_successful_ids_empty():
    """空结果 → 空集合。"""
    assert batch_test.successful_ids([]) == set()


# ── 4. filter_unprocessed ──

def test_filter_unprocessed():
    """已成功 ID 被过滤，返回 (剩余, 跳过数)。"""
    urls = [
        {"type": "1688", "url": "u1", "id": "1"},
        {"type": "1688", "url": "u2", "id": "2"},
        {"type": "1688", "url": "u3", "id": "3"},
    ]
    remaining, skipped = batch_test.filter_unprocessed(urls, {"1", "3"})
    assert [u["id"] for u in remaining] == ["2"], remaining
    assert skipped == 2


def test_filter_unprocessed_none_done():
    """done_ids 为空 → 全保留。"""
    urls = [{"type": "1688", "url": "u1", "id": "1"}]
    remaining, skipped = batch_test.filter_unprocessed(urls, set())
    assert len(remaining) == 1 and skipped == 0


# ── 5. main() 集成：--resume 断点续传 ──

def test_main_resume_skips_done_retries_failed_same_file():
    """--resume-from: 跳过已成功项、只重试失败项，结果合并写回原文件。"""
    out_dir = _tmp_dir()
    urls_file = out_dir / "urls.txt"
    urls_file.write_text(
        "https://detail.1688.com/offer/111.html\n"
        "https://detail.1688.com/offer/222.html\n",
        encoding="utf-8",
    )
    old_log = out_dir / "batch_20260811_090000.json"
    old_log.write_text(
        json.dumps([_result("111", True), _result("222", False)]), encoding="utf-8"
    )

    processed: list[str] = []

    def fake_process_1688(url, offer_id, client_id, api_key, worker_url,
                          dry_run, store_id=""):
        processed.append(offer_id)
        return _result(offer_id, True)

    with mock.patch.object(batch_test, "OUTPUT_DIR", out_dir), \
         mock.patch.object(sys, "argv", [
             "batch_test.py", "--urls-file", str(urls_file), "--dry-run", "--resume",
             "--resume-from", str(old_log)]), \
         mock.patch.object(batch_test.time, "sleep"), \
         mock.patch.object(requests, "get", side_effect=ConnectionError("no cdp")), \
         mock.patch("scripts.lib.config_store.check_config",
                    return_value={"missing": [], "cdp": {"browser_available": True}}), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch.object(batch_test, "process_1688_url", side_effect=fake_process_1688):
        rc = batch_test.main()

    assert rc == 0, f"main 返回 {rc}"
    assert processed == ["222"], f"应只重试失败项 222，实际 {processed}"
    data = json.loads(old_log.read_text(encoding="utf-8"))
    assert [r["offer_id"] for r in data] == ["111", "222", "222"], \
        "旧 2 条 + 新 1 条应合并写回原文件"
    summary = json.loads(
        (out_dir / "batch_20260811_090000_summary.json").read_text(encoding="utf-8")
    )
    assert summary["stats"]["success"] == 1, summary["stats"]


def test_main_resume_all_done_exits_zero():
    """--resume-from: 全部 URL 已成功 → 不处理任何 URL，返回 0。"""
    out_dir = _tmp_dir()
    urls_file = out_dir / "urls.txt"
    urls_file.write_text("https://detail.1688.com/offer/111.html\n", encoding="utf-8")
    old_log = out_dir / "batch_20260811_090000.json"
    old_log.write_text(json.dumps([_result("111", True)]), encoding="utf-8")

    processed: list[str] = []

    def fake_process_1688(url, offer_id, client_id, api_key, worker_url,
                          dry_run, store_id=""):
        processed.append(offer_id)
        return _result(offer_id, True)

    with mock.patch.object(batch_test, "OUTPUT_DIR", out_dir), \
         mock.patch.object(sys, "argv", [
             "batch_test.py", "--urls-file", str(urls_file), "--dry-run", "--resume",
             "--resume-from", str(old_log)]), \
         mock.patch.object(batch_test.time, "sleep"), \
         mock.patch.object(requests, "get", side_effect=ConnectionError("no cdp")), \
         mock.patch("scripts.lib.config_store.check_config",
                    return_value={"missing": [], "cdp": {"browser_available": True}}), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch.object(batch_test, "process_1688_url", side_effect=fake_process_1688):
        rc = batch_test.main()

    assert rc == 0, f"main 返回 {rc}"
    assert processed == [], "全部完成时不应再处理任何 URL"


def test_main_resume_missing_file_returns_1():
    """--resume 找不到历史文件 → 返回 1（不静默当全新批处理跑，防重复上架）。"""
    out_dir = _tmp_dir()
    urls_file = out_dir / "urls.txt"
    urls_file.write_text("https://detail.1688.com/offer/111.html\n", encoding="utf-8")

    with mock.patch.object(batch_test, "OUTPUT_DIR", out_dir), \
         mock.patch.object(sys, "argv", [
             "batch_test.py", "--urls-file", str(urls_file), "--dry-run", "--resume"]), \
         mock.patch.object(batch_test.time, "sleep"), \
         mock.patch.object(requests, "get", side_effect=ConnectionError("no cdp")), \
         mock.patch("scripts.lib.config_store.check_config",
                    return_value={"missing": [], "cdp": {"browser_available": True}}), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")):
        rc = batch_test.main()

    assert rc == 1, f"无历史文件时应返回 1，实际 {rc}"


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

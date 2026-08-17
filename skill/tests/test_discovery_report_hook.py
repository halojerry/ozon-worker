"""discover 结果上报 worker 归档（D12）单测 — _report_discovery_run 白名单裁剪。

覆盖:
1. payload 只含 REPORT_FIELDS 白名单字段（无 competing_seller_list / match_1688_images
   / ozon_images / source_chain 等大字段），单次 POST /api/v1/discovery/runs。
2. 仅 ok/matched/profitable 状态候选上报（filtered/error/rejected/no_match 跳过）。
3. 无 token → 跳过上报不报错（fail-open）。
4. 上报异常 fail-open 不阻断本地 JSON 落盘。
5. _save_discovery_log 带 keyword/filters → 触发非阻塞上报；不带 → 不上报。
纯 mock requests/config_store，不依赖真实网络。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od
from scripts.lib.ozon_discovery import ProductCandidate

_BIG_FIELDS = ("competing_seller_list", "match_1688_images", "ozon_images",
               "source_chain", "ozon_url", "match_1688_title")


def _mk(product_id="p1", status="ok", big=True):
    c = ProductCandidate(ozon_product_id=product_id, ozon_title=f"Title {product_id}",
                         ozon_price=1500.0)
    c.status = status
    if big:
        # 大字段：competing_seller_list 4KB 级 / match_1688_images 1KB 级
        c.competing_seller_list = [{"seller": f"s{i}", "price": i} for i in range(50)]
        c.match_1688_images = [f"https://img.example.com/{i}.jpg" for i in range(20)]
        c.ozon_images = [f"https://img.ozon.ru/{i}.jpg" for i in range(10)]
        c.source_chain = [{"type": "s", "id": "x", "name": "y", "depth": 1}]
    return c


def _report(candidates, keyword="поилка", filters=None):
    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch("requests.post") as mock_post:
        od._report_discovery_run(keyword, filters, candidates)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        return args[0], kwargs["json"]


def test_report_payload_whitelist_only_and_single_post():
    """payload 只含白名单字段；单次 POST；去掉大字段。"""
    url, payload = _report([_mk(product_id="p1", status="ok")],
                           filters={"min_price": 100})
    assert url == "https://worker.mxou.cn/api/v1/discovery/runs"
    assert payload["token"] == "sk-test"
    assert payload["keyword"] == "поилка"
    assert payload["filters"] == {"min_price": 100}
    rows = payload["candidates"]
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == set(od.REPORT_FIELDS), \
        f"payload 应只含白名单字段, got {set(row.keys())}"
    for big in _BIG_FIELDS:
        assert big not in row, f"大字段不应上报: {big}"
    assert row["ozon_product_id"] == "p1"
    assert row["status"] == "ok"
    assert row["dimensions_mm"] == {}
    assert row["ozon_title"] == "Title p1"


def test_report_filters_by_status():
    """仅 ok/matched/profitable 上报，filtered/error/rejected/no_match 跳过。"""
    cands = [_mk("p1", "ok"), _mk("p2", "matched"), _mk("p3", "profitable"),
             _mk("p4", "filtered"), _mk("p5", "error"), _mk("p6", "rejected"),
             _mk("p7", "no_match")]
    _, payload = _report(cands)
    ids = [r["ozon_product_id"] for r in payload["candidates"]]
    assert ids == ["p1", "p2", "p3"], f"只应上报 ok/matched/profitable, got {ids}"


def test_report_skips_without_token():
    """无 token → 不 POST，直接返回（fail-open）。"""
    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""), \
         mock.patch("requests.post") as mock_post:
        od._report_discovery_run("kw", {}, [_mk(status="ok")])
        mock_post.assert_not_called()


def test_report_failure_does_not_block_local_save():
    """上报异常（requests 抛错）→ warning 吞掉，本地 JSON 照常落盘。"""
    cands = [_mk(product_id="p1", status="ok")]
    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch("requests.post", side_effect=RuntimeError("boom")), \
         mock.patch.object(od, "DISCOVERY_CACHE_DIR", Path(tempfile.mkdtemp())):
        result = od._save_discovery_log(cands, keyword="kw", filters={"min_price": 100})
    assert result is not None and result.exists(), "本地 JSON 应照常落盘"
    saved = json.loads(result.read_text(encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["ozon_product_id"] == "p1"


def test_save_discovery_log_triggers_report_only_with_keyword():
    """_save_discovery_log 带 keyword/filters → 触发上报；不带 → 不上报。"""
    cands = [_mk(status="ok")]
    calls = []
    with mock.patch.object(od, "DISCOVERY_CACHE_DIR", Path(tempfile.mkdtemp())), \
         mock.patch.object(od, "_spawn_discovery_report",
                           side_effect=lambda k, f, c: calls.append((k, f, c))):
        od._save_discovery_log(cands, keyword="kw", filters={"min_price": 1})
        od._save_discovery_log(cands)
    assert len(calls) == 1, f"应只在带 keyword/filters 时触发, got {len(calls)}"
    kw, filters, c = calls[0]
    assert kw == "kw" and filters == {"min_price": 1} and c is cands


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

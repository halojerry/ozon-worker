"""analytics_upload 上报模块回归（v0.34 C5 todo8）。

mock requests，不依赖真实网络 / 不依赖真实 Supabase token。覆盖：
(1) 上报成功 → 返回 uploaded/inserted/upserted，POST URL/body 字段对齐 worker 端点
(2) worker 不可达（requests 抛异常）→ log warn 不崩，返回 error dict
(3) 无 token → 跳过，返回 skipped，不发起请求
+ 字段归一化（_normalize_rows）对齐 worker/api/schemas.py
+ upload_in_background daemon thread 行为 + cmd_queries 接线
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))

import scripts.lib.analytics_upload as au


def _query_rows():
    return [{
        "query": "поилка", "count": 9494, "ca": 27.14, "avg_ca_rub": 1585,
        "uniq_sellers": 30, "ordering_amount": 920, "gmv": 1385552,
        "uniq_queries_w_ca": 2577, "unknown_field": "drop-me",
    }]


# ── (1) 上报成功 ──────────────────────────────────────────────────────────

def test_upload_success_queries():
    """上报成功 → 200/uploaded count，body 字段对齐 worker /analytics/queries。"""
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "ok", "inserted": 1, "upserted": 0}
    with mock.patch.object(au.requests, "post", return_value=resp) as post:
        result = au.upload_analytics("queries", _query_rows(), token="sk-test")
    assert result["uploaded"] == 1
    assert result["inserted"] == 1
    url = post.call_args.args[0]
    assert url.endswith("/api/v1/analytics/queries")
    body = post.call_args.kwargs["json"]
    assert body["token"] == "sk-test"
    assert body["queries"][0]["query"] == "поилка"
    assert body["queries"][0]["uniq_queries_wca"] == 2577  # uniq_queries_w_ca → uniq_queries_wca
    assert body["queries"][0]["count"] == 9494
    assert "unknown_field" not in body["queries"][0]       # 未知字段丢弃
    assert "ordering_amount" not in body["queries"][0]     # 非 queries 字段丢弃
    assert post.call_args.kwargs["timeout"] == au.UPLOAD_TIMEOUT


def test_upload_success_ozon_bestsellers():
    """ozon-bestsellers 字段重命名/映射：sku→sku_or_id、gmv_sum→ordering_amount 等。"""
    rows = [{
        "sku": "7969279", "name": "SvetoCopy Paper", "brand": "SvetoCopy",
        "gmv_sum": 6419792.282, "sold_count": 15136, "avg_price": 424.0,
        "category2_id": "17029017", "category3_id": 91400,
    }]
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "ok", "inserted": 1, "upserted": 0}
    with mock.patch.object(au.requests, "post", return_value=resp) as post:
        result = au.upload_analytics("ozon-bestsellers", rows, token="sk-test")
    assert result["uploaded"] == 1
    item = post.call_args.kwargs["json"]["items"][0]
    assert item["sku_or_id"] == "7969279"
    assert item["ordering_amount"] == 6419792.282
    assert item["ordering_count"] == 15136
    assert item["avg_price_rub"] == 424.0
    assert item["category_id"] == 17029017
    assert "gmv_sum" not in item and "sold_count" not in item  # 源字段不直传


# ── (2) worker 不可达 / HTTP 错误 ─────────────────────────────────────────

def test_upload_failure_worker_unreachable():
    """worker 不可达（requests 抛异常）→ log warn 不崩，返回 error dict。"""
    with mock.patch.object(
        au.requests, "post", side_effect=Exception("Connection refused")
    ):
        result = au.upload_analytics("queries", _query_rows(), token="sk-test")
    assert result["uploaded"] == 0
    assert "error" in result
    assert "Connection refused" in result["error"]


def test_upload_failure_http_500():
    """worker 返回 500 → 返回 error dict，不视为成功。"""
    resp = mock.MagicMock()
    resp.status_code = 500
    resp.text = "Internal Server Error"
    with mock.patch.object(au.requests, "post", return_value=resp):
        result = au.upload_analytics("queries", _query_rows(), token="sk-test")
    assert result["uploaded"] == 0
    assert "HTTP 500" in result["error"]


# ── (3) 无 token 跳过 ─────────────────────────────────────────────────────

def test_upload_no_token_skips_request():
    """无 token → 跳过，返回 skipped，不发起请求。"""
    with mock.patch.object(au.requests, "post") as post:
        result = au.upload_analytics("queries", _query_rows(), token=None)
    assert result["skipped"] is True
    post.assert_not_called()


# ── 字段归一化细节 ────────────────────────────────────────────────────────

def test_normalize_market_bestsellers():
    """market-bestsellers：product_name/daily_avg/category_id 映射。"""
    rows = [{
        "name": "Xiaomi Redmi 15", "brand": "Xiaomi",
        "gmv_sum": 82908845.527, "category2_id": "15621050",
        "daily_avg": 120,
    }]
    norm = au._normalize_rows("market-bestsellers", rows)
    assert len(norm) == 1
    item = norm[0]
    assert item["product_name"] == "Xiaomi Redmi 15"
    assert item["brand"] == "Xiaomi"
    assert item["category_id"] == 15621050
    assert item["ordering_amount"] == 82908845.527
    assert item["daily_avg"] == 120


def test_normalize_drops_rows_missing_required():
    """缺必填字段（query / sku / name）的行跳过。"""
    assert au._normalize_rows("queries", [{"count": 5}]) == []
    assert au._normalize_rows("queries", [{"query": "  ", "count": 5}]) == []
    assert au._normalize_rows("ozon-bestsellers", [{"name": "x"}]) == []
    assert au._normalize_rows("market-bestsellers", [{"other": "x"}]) == []  # 无 name/product_name/sku
    # 非 list 输入 → []
    assert au._normalize_rows("queries", "nope") == []
    # 未知 kind → []
    assert au._normalize_rows("unknown", [{"query": "x"}]) == []


# ── upload_in_background（daemon thread）──────────────────────────────────

def test_upload_in_background_no_token_skips():
    """upload_in_background 无 token → 不启动线程，不发起请求。"""
    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""):
        with mock.patch.object(au.threading, "Thread") as th:
            with mock.patch.object(au, "upload_analytics") as ua:
                au.upload_in_background("queries", _query_rows())
    ua.assert_not_called()
    th.assert_not_called()


def test_upload_in_background_starts_daemon_thread():
    """upload_in_background 有 token → 启动 daemon thread 调 upload_analytics。"""
    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        with mock.patch.object(au.threading, "Thread") as th:
            au.upload_in_background("queries", _query_rows())
    th.assert_called_once()
    kwargs = th.call_args.kwargs
    assert kwargs["daemon"] is True
    assert kwargs["name"] == "analytics-upload-queries"


def test_upload_in_background_empty_rows_skips():
    """空 rows → 直接返回，不读 token、不启动线程。"""
    with mock.patch("scripts.lib.config_store.get_mxou_token") as gt:
        with mock.patch.object(au.threading, "Thread") as th:
            au.upload_in_background("queries", [])
    gt.assert_not_called()
    th.assert_not_called()


# ── cmd_queries 接线 ──────────────────────────────────────────────────────

def test_cmd_queries_triggers_upload_on_success():
    """cmd_queries 采集成功 → 调 upload_in_background（all-queries 映射 kind=queries）。"""
    from scripts.cli import cmd_queries
    import argparse
    from scripts.lib import ozon_seller_analytics as osa

    rows = [{"query": "поилка", "count": 5}]
    with mock.patch.object(osa, "check_seller_login", return_value=True):
        with mock.patch.object(osa, "fetch_all_queries", return_value=rows):
            with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
                conn_cls.return_value = mock.MagicMock()
                with mock.patch("scripts.lib.analytics_upload.upload_in_background") as upb:
                    args = argparse.Namespace(
                        type="all-queries", keyword="поилка", sku="", category_id="",
                        price_min=None, price_max=None, export="csv", output="")
                    rc = cmd_queries(args)
    assert rc == 0
    upb.assert_called_once()
    assert upb.call_args.args[0] == "queries"
    assert upb.call_args.args[1] == rows


def test_cmd_queries_no_rows_skips_upload():
    """cmd_queries 无数据（rows 空）→ 不调 upload_in_background。"""
    from scripts.cli import cmd_queries
    import argparse
    from scripts.lib import ozon_seller_analytics as osa

    with mock.patch.object(osa, "check_seller_login", return_value=True):
        with mock.patch.object(osa, "fetch_all_queries", return_value=[]):
            with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
                conn_cls.return_value = mock.MagicMock()
                with mock.patch("scripts.lib.analytics_upload.upload_in_background") as upb:
                    args = argparse.Namespace(
                        type="all-queries", keyword="", sku="", category_id="",
                        price_min=None, price_max=None, export="csv", output="")
                    rc = cmd_queries(args)
    assert rc == 0
    upb.assert_not_called()


def test_cmd_queries_upload_trigger_failure_does_not_break():
    """cmd_queries 上报触发自身抛异常 → 被吞掉，主流程（CSV 导出）不受影响。"""
    from scripts.cli import cmd_queries
    import argparse
    from scripts.lib import ozon_seller_analytics as osa

    rows = [{"query": "поилка", "count": 5}]
    with mock.patch.object(osa, "check_seller_login", return_value=True):
        with mock.patch.object(osa, "fetch_all_queries", return_value=rows):
            with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
                conn_cls.return_value = mock.MagicMock()
                with mock.patch(
                    "scripts.lib.analytics_upload.upload_in_background",
                    side_effect=Exception("trigger boom"),
                ):
                    args = argparse.Namespace(
                        type="all-queries", keyword="поилка", sku="", category_id="",
                        price_min=None, price_max=None, export="csv", output="")
                    rc = cmd_queries(args)
    assert rc == 0


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

"""store 工具测试：analyze_store / run_store_action 直接 HTTP 调 worker（非 skill CLI）。

不依赖真实 worker/Chrome，monkeypatch worker_http 的 _request，断言：
1. analyze_store 调 GET /api/v1/stores/{id}/analysis + 返回结构化数据
2. run_store_action 调 POST /api/v1/stores/{id}/actions + operation 注入 payload
3. 失败（worker 不可达 / 5xx）返回 error dict，不 raise
4. 两个工具在 server 注册（dsh 可见 mcp__pounding__analyze_store / run_store_action）
"""

from __future__ import annotations

import asyncio

import pytest

from pounding_mcp import worker_http
from pounding_mcp.server import mcp
from pounding_mcp.worker_http import analyze_store, run_store_action

_ANALYSIS_SAMPLE = {
    "summary": {"product_count": 12, "low_stock_count": 3, "active_discount_count": 2, "avg_profit_rate": 0.31},
    "profit_trend": [{"snapshot_at": "2026-08-01T00:00:00", "profit_rate": 0.28, "sales_amount": 1000}],
    "low_margin_products": [],
    "out_of_stock_products": [{"product_id": "1", "name": "x", "stock": 0}],
    "promo_ready_products": [{"product_id": "2", "name": "y", "profit_rate": 0.4, "candidate_action": "可参与促销"}],
}


def test_analyze_store_calls_worker(monkeypatch):
    """mock _request → 确认调 GET /api/v1/stores/{id}/analysis + 返回结构化数据。"""
    captured = {}

    def fake_request(method, url, token, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["token"] = token
        return dict(_ANALYSIS_SAMPLE)

    monkeypatch.setattr(worker_http, "_request", fake_request)
    result = analyze_store("store_cred_1")

    assert captured["url"].endswith("/api/v1/stores/store_cred_1/analysis")
    assert captured["method"] == "GET"
    assert captured["token"] != ""
    assert result["summary"]["product_count"] == 12
    assert result["out_of_stock_products"][0]["stock"] == 0


def test_run_store_action_calls_worker(monkeypatch):
    """mock _request → 确认调 POST /api/v1/stores/{id}/actions + operation 注入 payload。"""
    captured = {}

    def fake_request(method, url, token, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["body"] = body
        return {"ok": True, "result": {"updated": 2}}

    monkeypatch.setattr(worker_http, "_request", fake_request)
    result = run_store_action("store_cred_1", "bulk_update_prices", {"prices": [{"offer_id": "o1", "price": "99"}]})

    assert captured["url"].endswith("/api/v1/stores/store_cred_1/actions")
    assert captured["method"] == "POST"
    assert captured["body"]["operation"] == "bulk_update_prices"
    assert captured["body"]["prices"][0]["price"] == "99"
    assert result["ok"] is True


def test_error_no_raise(monkeypatch):
    """worker 5xx / 不可达 → 返回 error dict，不 raise。"""
    for fake_result in (
        {"ok": False, "http_status": 502, "error": "bad gateway", "raw": ""},
        {"ok": False, "http_status": 0, "error": "Worker 不可达: timeout"},
    ):
        monkeypatch.setattr(worker_http, "_request", lambda *a, **k: dict(fake_result))

        res = analyze_store("store_cred_1")
        assert res["ok"] is False
        assert res["http_status"] in (502, 0)
        assert res["error"]

        res2 = run_store_action("store_cred_1", "bulk_archive", {"product_ids": ["1"]})
        assert res2["ok"] is False


def test_tool_registered():
    """两个工具注册成功（dsh 可见 mcp__pounding__analyze_store / run_store_action）。"""
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "analyze_store" in names
    assert "run_store_action" in names

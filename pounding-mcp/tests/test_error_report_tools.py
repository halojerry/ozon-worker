"""report_issue / list_error_reports 工具测试（v0.69 错误报告模板通道）。

不依赖真实 worker，monkeypatch worker_http._request，断言：
1. report_issue 组模板体（title/severity/category + reproduction/evidence 组装）
2. list_error_reports 列表与单条详情两条查询路径
3. 失败返回 error dict 不 raise
4. 两个工具在 server 注册（dsh 可见 mcp__pounding__report_issue / list_error_reports）
"""

from __future__ import annotations

import asyncio

from pounding_mcp import worker_http
from pounding_mcp.server import mcp
from pounding_mcp.worker_http import list_error_reports, report_issue


def _server_report_issue(**kwargs):
    """取 server 层 MCP 工具函数（模板参数组装层）并同步调用。"""
    from pounding_mcp import server as server_mod
    return server_mod.report_issue(**kwargs)


def test_server_tool_assembles_template(monkeypatch):
    """server 层：steps/command/expect/actual/task_ids 组装成 reproduction/evidence 模板体。"""
    captured = {}

    def fake_request(method, url, token, body=None):
        captured.update(method=method, url=url, body=body)
        return {"status": "ok", "report_id": "r-1", "tasks_attached": 2}

    monkeypatch.setattr(worker_http, "_request", fake_request)
    out = _server_report_issue(
        title="泡脚包三连失败", severity="high", category="upload_failed",
        description="三种失败形态",
        steps=["提交任务", "假成功"], command="cli.py graph --url x", expect="卡片", actual="无商品",
        task_ids=["t-1", "t-2"], item_id="623236101606",
        error_codes=["VALUE_MAX_LIMIT"],
    )
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/error_reports")
    b = captured["body"]
    assert b["title"] == "泡脚包三连失败" and b["severity"] == "high"
    assert b["reproduction"]["steps"] == ["提交任务", "假成功"]
    assert b["reproduction"]["expect"] == "卡片" and b["reproduction"]["actual"] == "无商品"
    assert b["evidence"]["task_ids"] == ["t-1", "t-2"]
    assert b["evidence"]["item_id"] == "623236101606"
    assert b["evidence"]["error_codes"] == ["VALUE_MAX_LIMIT"]
    assert out["tasks_attached"] == 2


def test_report_issue_builds_template_body(monkeypatch):
    """HTTP 层：传 reproduction/evidence 时进请求体。"""
    captured = {}

    def fake_request(method, url, token, body=None):
        captured["body"] = body
        return {"status": "ok", "report_id": "r-1"}

    monkeypatch.setattr(worker_http, "_request", fake_request)
    report_issue(
        title="泡脚包三连失败", severity="high", category="upload_failed",
        description="三种失败形态",
        reproduction={"steps": ["提交任务"], "expect": "卡片", "actual": "无商品"},
        evidence={"task_ids": ["t-1"], "item_id": "623236101606"},
    )
    b = captured["body"]
    assert b["title"] == "泡脚包三连失败" and b["severity"] == "high"
    assert b["reproduction"]["steps"] == ["提交任务"]
    assert b["evidence"]["task_ids"] == ["t-1"]
    assert b["evidence"]["item_id"] == "623236101606"


def test_report_issue_minimal_body(monkeypatch):
    """仅 title：reproduction/evidence 不进请求体（省键）。"""
    captured = {}

    def fake_request(method, url, token, body=None):
        captured["body"] = body
        return {"status": "ok", "report_id": "r-2"}

    monkeypatch.setattr(worker_http, "_request", fake_request)
    report_issue(title="CLI 崩溃", category="cli_bug")
    assert "reproduction" not in captured["body"]
    assert "evidence" not in captured["body"]
    assert captured["body"]["category"] == "cli_bug"


def test_list_error_reports_paths(monkeypatch):
    captured = []

    def fake_request(method, url, token, body=None):
        captured.append(url)
        return {"total": 0, "reports": []}

    monkeypatch.setattr(worker_http, "_request", fake_request)
    list_error_reports(status="new", limit=10)
    list_error_reports(report_id="r-9")
    assert captured[0].endswith("/api/v1/error_reports?limit=10&status=new")
    assert "report_id=r-9" in captured[1]


def test_error_no_raise(monkeypatch):
    for fake in ({"ok": False, "http_status": 429, "error": "rate limited", "raw": ""},
                 {"ok": False, "http_status": 0, "error": "Worker 不可达: timeout"}):
        monkeypatch.setattr(worker_http, "_request", lambda *a, **k: dict(fake))
        assert report_issue(title="x")["ok"] is False
        assert list_error_reports()["ok"] is False


def test_tools_registered():
    """工具注册成功（dsh 可见 mcp__pounding__report_issue / list_error_reports）。"""
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "report_issue" in names
    assert "list_error_reports" in names

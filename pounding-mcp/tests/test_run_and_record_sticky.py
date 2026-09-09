#!/usr/bin/env python3
"""F-D05 run_and_record 终态粘性单测（P1 波，2026-09-09 审计）。

审计发现（findings.md，中）：同步路径 run_and_record 在 _exec 返回后直接内联
写 status='completed'，绕过 _finish 的 _TERMINAL_STATUSES 终态粘性——执行期间
job_cancel 把任务置 cancelled 后仍被 completed 翻盘（4c42dcfb 只补了后台面）。

修复契约：run_and_record 的成功/失败状态写统一收敛到 _finish（粘性守卫内）。

运行：cd pounding-mcp && .venv/bin/python -m pytest tests/test_run_and_record_sticky.py -q
"""
from __future__ import annotations

from pounding_mcp.tasks import CollectTaskManager


def test_run_and_record_cancelled_stays_cancelled(tmp_path, monkeypatch):
    mgr = CollectTaskManager(store_path=tmp_path / "tasks.json")

    def fake_exec(kind, params):
        # 模拟执行期间另一会话 job_cancel（running → cancelled）
        for t in mgr.list():
            if t.get("status") == "running":
                assert mgr.cancel(t["id"]) is True
        return {"ok": True}

    monkeypatch.setattr("pounding_mcp.tasks._exec", fake_exec)
    out = mgr.run_and_record("graph", {"url": "x"})
    assert out == {"ok": True}
    tasks = mgr.list()
    assert tasks and tasks[0]["status"] == "cancelled", \
        "cancel 竞争后终态不得被 completed 翻盘（终态粘性）"


def test_run_and_record_success_records_completed(tmp_path, monkeypatch):
    """无竞争时正常 completed + summary（正向路径不回归）。"""
    mgr = CollectTaskManager(store_path=tmp_path / "tasks.json")
    monkeypatch.setattr("pounding_mcp.tasks._exec", lambda k, p: {"ok": True})
    mgr.run_and_record("graph", {"url": "x"})
    tasks = mgr.list()
    assert tasks and tasks[0]["status"] == "completed"

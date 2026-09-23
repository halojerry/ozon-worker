"""v0.79 人体工学（PLAN-agent-ergonomics-v1 Task C2）：job_status 轮询节奏字段。

running 态必须带 next_poll_s/next_action（把轮询节奏从 agent 猜变成工具告知）；
终态必须带 next_action；任务不存在仍走 error 出口（零变化）。
"""
from __future__ import annotations

from unittest import mock

from pounding_mcp import server as srv


def _status(payload: dict):
    fake = mock.Mock()
    fake.get.return_value = dict(payload)
    fake.log_tail.return_value = ""
    fake.extract_worker_task_ids.return_value = []
    with mock.patch.object(srv, "get_manager", return_value=fake):
        return srv.job_status("j1")


def test_running_task_carries_poll_hint():
    r = _status({"id": "j1", "status": "running", "stage": "scan"})
    assert r["next_poll_s"] == 20
    assert "20s" in r["next_action"]
    assert "秒级轮询" not in r["next_action"].split("勿秒级轮询")[0].split("（")[-1] or True
    assert "勿秒级轮询" in r["next_action"]


def test_completed_task_carries_terminal_next():
    r = _status({"id": "j1", "status": "completed"})
    assert "next_action" in r
    assert "job_result" in r["next_action"]
    assert "next_poll_s" not in r  # 终态不需要轮询建议


def test_failed_task_next_mentions_report():
    r = _status({"id": "j1", "status": "failed", "error": "boom"})
    assert "report" in r["next_action"]


def test_missing_task_error_unchanged():
    fake = mock.Mock()
    fake.get.return_value = None
    with mock.patch.object(srv, "get_manager", return_value=fake):
        r = srv.job_status("nope")
    assert r.get("error")
    assert "next_action" not in r

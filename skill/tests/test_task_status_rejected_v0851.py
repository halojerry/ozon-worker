"""check_task_status terminal 集合补 "rejected" 回归（v0.85.1 首战修正，2026-10-04）。

首战实录：follow --clone 五单全部被 Ozon 拒审（status=rejected），skill 侧
check_task_status 的 terminal 集合只有 completed/failed/cancelled —— `--wait`
对已拒任务按 10s 空转到 900s 超时，占死重采集闸（heavy gate）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_task_status_rejected_v0851.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.cloud_probe import check_task_status, poll_task_status


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self._payload


def test_rejected_is_terminal():
    """status=rejected → terminal=True（--wait 立即返回，不再空转 900s）。"""
    resp = _Resp({"id": "T-1", "status": "rejected",
                  "error_message": "Ozon 拒审", "result": {}})
    with mock.patch("scripts.cloud_probe._get_token", return_value=""), \
            mock.patch("requests.get", return_value=resp) as m_get:
        r = check_task_status("T-1")
    assert r["status"] == "rejected"
    assert r["terminal"] is True, "rejected 必须判终态（否则 --wait 空转到 900s）"
    assert r["ok"] is False
    assert m_get.call_count == 1


def test_rejected_terminates_poll_immediately():
    """poll_task_status 收到 rejected 立即返回（不烧轮询窗口）。"""
    resp = _Resp({"id": "T-1", "status": "rejected", "result": {}})
    with mock.patch("scripts.cloud_probe._get_token", return_value=""), \
            mock.patch("requests.get", return_value=resp), \
            mock.patch("scripts.cloud_probe.time.sleep") as m_sleep:
        r = poll_task_status("T-1", timeout=900)
    assert r["status"] == "rejected"
    assert r["terminal"] is True
    m_sleep.assert_not_called()


def test_legacy_terminal_statuses_unchanged():
    """completed/failed/cancelled 终态语义零变化。"""
    for status, ok in (("completed", True), ("failed", False), ("cancelled", False)):
        resp = _Resp({"id": "T", "status": status, "result": {}})
        with mock.patch("scripts.cloud_probe._get_token", return_value=""), \
                mock.patch("requests.get", return_value=resp):
            r = check_task_status("T")
        assert r["terminal"] is True and r["ok"] is ok, status


def test_non_terminal_still_polls():
    """running/pending 等非终态照旧继续轮询。"""
    resp = _Resp({"id": "T", "status": "running", "result": {}})
    with mock.patch("scripts.cloud_probe._get_token", return_value=""), \
            mock.patch("requests.get", return_value=resp):
        r = check_task_status("T")
    assert r["terminal"] is False and r["ok"] is False

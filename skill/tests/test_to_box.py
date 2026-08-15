#!/usr/bin/env python3
"""T9: skill --to-box 入采集箱单测（mock requests）。

覆盖:
  - `submit_draft` 正常路径: POST {base}/api/v1/drafts → {ok, draft_id}
  - 冷启动降级: 404 → 降级 submit_envelope（POST /submit_task）+ degraded 标记
  - 冷启动降级: 连接失败（ConnectionError/Timeout）→ 降级 submit_envelope
  - 非 404 错误（400）→ {ok: False} 且不降级（不掩盖真实错误）
  - worker_url 显式覆盖

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_to_box.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cloud_probe import submit_draft  # noqa: E402

API_BASE = "https://worker.mxou.cn"


# ── helpers ────────────────────────────────────────────────────────────


def _draft_resp(draft_id: str) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = 200
    resp.text = '{"id": "%s"}' % draft_id
    resp.json.return_value = {"id": draft_id, "source": "skill", "version": 1}
    return resp


def _http_resp(status: int, payload: dict | None = None) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status
    resp.text = str(payload or {})
    resp.json.return_value = payload
    return resp


def _task_resp(task_id: str) -> mock.Mock:
    """submit_envelope 成功响应形状（{ok, task_id}）。"""
    return _http_resp(200, {"ok": True, "task_id": task_id, "message": "submitted"})


def _patch_auth() -> mock.Mock:
    return mock.patch("scripts.lib.config_store._require_auth", return_value=None)


def _graph_input() -> dict:
    return {
        "token": "sk-test",
        "ozon_client_id": "4718259",
        "ozon_api_key": "k-test",
        "envelope": {"draft": {"title": "测试"}, "source": {}, "extensions": {}},
    }


@pytest.fixture(autouse=True)
def _auth_ok():
    with _patch_auth():
        yield


# ── 正常路径 ───────────────────────────────────────────────────────────


def test_submit_draft_posts_to_drafts_endpoint():
    """Given 完整 GraphInput，When submit_draft，Then POST /api/v1/drafts 返回 draft_id。"""
    with mock.patch("requests.post", return_value=_draft_resp("draft-uuid-1")) as m_post:
        res = submit_draft(_graph_input())

    assert res["ok"] is True
    assert res["draft_id"] == "draft-uuid-1"
    assert "已入采集箱" in res["message"]
    # 命中的必须是采集箱端点（非 /submit_task）
    assert m_post.call_args[0][0] == f"{API_BASE}/api/v1/drafts"
    assert m_post.call_args.kwargs["json"] == _graph_input()


def test_submit_draft_custom_worker_url():
    """Given worker_url 覆盖，When submit_draft，Then 使用覆盖地址。"""
    with mock.patch("requests.post", return_value=_draft_resp("draft-uuid-2")) as m_post:
        res = submit_draft(_graph_input(), worker_url="http://localhost:8080")

    assert res["ok"] is True
    assert m_post.call_args[0][0] == "http://localhost:8080/api/v1/drafts"


# ── 冷启动降级 ─────────────────────────────────────────────────────────


def test_submit_draft_404_falls_back_to_submit_envelope():
    """Given 老 worker 无 /drafts（404），When submit_draft，Then 降级直接上架并标记 degraded。"""
    def _route(url, **kwargs):
        if url.endswith("/api/v1/drafts"):
            return _http_resp(404, {"detail": "Not Found"})
        return _task_resp("task-uuid-fallback")

    with mock.patch("requests.post", side_effect=_route) as m_post:
        res = submit_draft(_graph_input())

    assert res["ok"] is True
    assert res["degraded"] is True
    assert res["task_id"] == "task-uuid-fallback"
    assert "已直接上架" in res["message"]
    # 降级后确实走了 /submit_task
    urls = [c[0][0] for c in m_post.call_args_list]
    assert f"{API_BASE}/submit_task" in urls


def test_submit_draft_connection_error_falls_back():
    """Given 连接失败，When submit_draft，Then 降级直接上架并标记 degraded。"""
    def _route(url, **kwargs):
        if url.endswith("/api/v1/drafts"):
            raise requests.ConnectionError("Worker unreachable")
        return _task_resp("task-uuid-fallback")

    with mock.patch("requests.post", side_effect=_route) as m_post:
        res = submit_draft(_graph_input())

    assert res["ok"] is True
    assert res["degraded"] is True
    assert res["task_id"] == "task-uuid-fallback"
    urls = [c[0][0] for c in m_post.call_args_list]
    assert f"{API_BASE}/submit_task" in urls


def test_submit_draft_timeout_falls_back():
    """Given 超时，When submit_draft，Then 降级直接上架。"""
    def _route(url, **kwargs):
        if url.endswith("/api/v1/drafts"):
            raise requests.Timeout("slow")
        return _task_resp("task-uuid-fallback")

    with mock.patch("requests.post", side_effect=_route):
        res = submit_draft(_graph_input())

    assert res["ok"] is True
    assert res["degraded"] is True


# ── 非 404 错误不降级 ──────────────────────────────────────────────────


def test_submit_draft_400_no_fallback():
    """Given /drafts 返回 400，When submit_draft，Then {ok: False} 且不降级。"""
    with mock.patch("requests.post", return_value=_http_resp(400, {"detail": "envelope 不能为空"})) as m_post:
        res = submit_draft(_graph_input())

    assert res["ok"] is False
    assert "envelope 不能为空" in res["error"]
    assert res.get("degraded") is not True
    # 只打了一次 /drafts，没有走 /submit_task
    assert len(m_post.call_args_list) == 1
    assert m_post.call_args_list[0][0][0] == f"{API_BASE}/api/v1/drafts"


def test_submit_draft_fallback_failure_surfaces_error():
    """Given 降级后直接上架也失败，When submit_draft，Then 透传失败且 degraded 标记保留。"""
    def _route(url, **kwargs):
        if url.endswith("/api/v1/drafts"):
            raise requests.ConnectionError("Worker unreachable")
        return _http_resp(500, {"message": "server error"})

    with mock.patch("requests.post", side_effect=_route):
        res = submit_draft(_graph_input())

    assert res["ok"] is False
    assert res["degraded"] is True
    assert res["error"]

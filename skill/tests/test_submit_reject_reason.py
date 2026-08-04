#!/usr/bin/env python3
"""submit_envelope 拒绝原因解析单测（v0.22）— agent 收到原因才知道怎么解决。

运行：
    cd skill && python3 tests/test_submit_reject_reason.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.cloud_probe import submit_envelope


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _patched_env():
    base_patch = mock.patch("scripts.cloud_probe._get_api_base", return_value="http://worker.test")
    token_patch = mock.patch("scripts.cloud_probe._get_token", return_value="sk-test")
    auth_patch = mock.patch("scripts.lib.config_store._require_auth", return_value=None)
    creds_patch = mock.patch(
        "scripts.cloud_probe._get_ozon_credentials",
        return_value={"client_id": "1", "api_key": "k"},
    )
    for p in (base_patch, token_patch, auth_patch, creds_patch):
        p.start()
    return (base_patch, token_patch, auth_patch, creds_patch)


def test_structured_reject_reason():
    """Worker 统一错误格式（ok/error_code/message/detail）→ 结构化透传。"""
    patched = _patched_env()
    try:
        with mock.patch("scripts.cloud_probe.requests.post") as m_post:
            m_post.return_value = _Resp(400, {
                "ok": False,
                "error_code": "INVALID_REQUEST",
                "message": "信封数据异常: weight=364000g > 50000g",
                "detail": {"sanity": "weight=364000g > 50000g"},
            })
            out = submit_envelope({"envelope": {"draft": {}}})
    finally:
        for p in patched:
            p.stop()
    assert out["ok"] is False
    assert out["error_code"] == "INVALID_REQUEST"
    assert "weight=364000g" in out["error"]
    assert out["http_status"] == 400


def test_fastapi_detail_reason():
    """FastAPI HTTPException 默认格式（detail 字符串）→ 取 detail 作为原因。"""
    patched = _patched_env()
    try:
        with mock.patch("scripts.cloud_probe.requests.post") as m_post:
            m_post.return_value = _Resp(402, {"detail": "Insufficient balance (current: 0.0). Please top up your MXOU account."})
            out = submit_envelope({"envelope": {"draft": {}}})
    finally:
        for p in patched:
            p.stop()
    assert out["ok"] is False
    assert "Insufficient balance" in out["error"]
    assert out["http_status"] == 402


def test_success_passthrough():
    """2xx → 原样返回 task_id。"""
    patched = _patched_env()
    try:
        with mock.patch("scripts.cloud_probe.requests.post") as m_post:
            m_post.return_value = _Resp(200, {"ok": True, "task_id": "t1"})
            out = submit_envelope({"envelope": {"draft": {}}})
    finally:
        for p in patched:
            p.stop()
    assert out["ok"] is True
    assert out["task_id"] == "t1"


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
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

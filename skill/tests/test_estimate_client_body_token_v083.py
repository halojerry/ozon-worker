#!/usr/bin/env python3
"""v0.83 gate B1: estimate_client 单信封端点必须带 body token。

worker ``POST /api/v1/estimate``（estimate_routes.router_estimate）用
``_extract_token_from_body`` 从**请求体 token 字段**取鉴权——只发 Bearer 会 401
"Token is required" → skill 恒「无预估」（gate v083 4/4 单复现）。本用例锁：
body 同时含 envelope 与 token，且 Bearer 头保留（先例 _query_logistics_from_worker）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_estimate_client_body_token_v083.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import estimate_client  # noqa: E402

_ENVELOPE = {
    "draft": {"purchase_cost": 50.0, "weight": 100},
    "extensions": {},
}


def _capture_post(status=200, payload=None):
    """mock requests.post，返回捕获的 (url, kwargs) 列表。"""
    captured: list = []

    class _Resp:
        status_code = status

        def json(self):
            return payload if payload is not None else {"price": 222.0}

    def _post(url, **kwargs):
        captured.append((url, kwargs))
        return _Resp()

    return captured, _post


def test_estimate_envelope_sends_body_token_and_bearer():
    """envelope 请求体带 token 字段 + 保留 Bearer 头（worker 双鉴权口径）。"""
    captured, post = _capture_post()
    with mock.patch.object(estimate_client, "_worker_base_and_token",
                           return_value=("http://localhost:8080", "tok-abc")), \
            mock.patch("requests.post", side_effect=post):
        out = estimate_client.estimate_envelope(_ENVELOPE)

    assert out == {"price": 222.0}
    assert len(captured) == 1
    url, kwargs = captured[0]
    assert url == "http://localhost:8080/api/v1/estimate"
    body = kwargs["json"]
    assert body["token"] == "tok-abc", "body token 缺失 → worker 401"
    assert body["envelope"] is _ENVELOPE
    assert kwargs["headers"]["Authorization"] == "Bearer tok-abc"


def test_estimate_envelope_token_not_clobbered_by_overrides():
    """overrides 不能覆盖鉴权 token（token 在 overrides 之后落）。"""
    captured, post = _capture_post()
    with mock.patch.object(estimate_client, "_worker_base_and_token",
                           return_value=("http://localhost:8080", "tok-abc")), \
            mock.patch("requests.post", side_effect=post):
        estimate_client.estimate_envelope(_ENVELOPE, overrides={"token": "evil", "margin_rate": 1.5})

    body = captured[0][1]["json"]
    assert body["token"] == "tok-abc"
    assert body["margin_rate"] == 1.5


def test_estimate_envelope_401_still_degrades_to_none():
    """非 200（如 401）仍按降级纪律返回 None，绝不回落本地公式。"""
    captured, post = _capture_post(status=401)
    with mock.patch.object(estimate_client, "_worker_base_and_token",
                           return_value=("http://localhost:8080", "tok-abc")), \
            mock.patch("requests.post", side_effect=post):
        assert estimate_client.estimate_envelope(_ENVELOPE) is None


def test_estimate_batch_bearer_only_unchanged():
    """batch 端点（Batch 鉴权走 Bearer）不注入 body token——保持原契约。"""
    captured, post = _capture_post(status=200, payload={"items": [
        {"index": 0, "ok": True, "price": 10.0}]})
    with mock.patch.object(estimate_client, "_worker_base_and_token",
                           return_value=("http://localhost:8080", "tok-abc")), \
            mock.patch("requests.post", side_effect=post):
        out = estimate_client.estimate_batch([estimate_client.build_batch_item(50.0, weight_g=100)])

    body = captured[0][1]["json"]
    assert "token" not in body, "batch 端点不应注入 body token"
    assert out and out[0]["price"] == 10.0

"""v0.55.1: New API 通用代理路由测试（T6）。

代理把 webui 同源 /api/* 请求（登录/订阅/钱包等 New API 端点）转发到 api.mxou.cn。
验证：路径透传 / header+body+query+cookie 透传 / 响应 status+headers+body 透传 /
/api/v1 排除 / /api 本身排除 / 超时降级。
纯 mock（requests 打桩），无需网络/PG。
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from routes import newapi_proxy_routes


class _FakeResponse:
    def __init__(self, status=200, body=b'{"ok":true}', headers=None):
        self.status_code = status
        self.content = body
        self.headers = headers or {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _patch_base(monkeypatch):
    monkeypatch.setattr(newapi_proxy_routes, "_base_url", lambda: "https://api.mxou.cn")


def test_proxy_forward_path_and_headers():
    """GET /api/user/self → 转发到 https://api.mxou.cn/api/user/self，透传 headers。"""
    fake = _FakeResponse()
    with patch("requests.request", return_value=fake) as m:
        resp = newapi_proxy_routes._proxy_request("GET", "user/self", {
            "Authorization": "Bearer tok", "New-Api-User": "42", "Cookie": "session=abc",
        }, None, None, None)
    m.assert_called_once()
    args, kwargs = m.call_args
    assert args[0] == "GET"
    assert args[1] == "https://api.mxou.cn/api/user/self"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["headers"]["New-Api-User"] == "42"
    assert kwargs["headers"]["Cookie"] == "session=abc"
    assert resp.status_code == 200


def test_proxy_forward_body_and_query():
    """POST body + query string 透传。"""
    fake = _FakeResponse(201, b'{"created":true}')
    body_bytes = json.dumps({"username": "a"}).encode()
    with patch("requests.request", return_value=fake) as m:
        resp = newapi_proxy_routes._proxy_request("POST", "subscription/admin/plans", {}, body_bytes, {"limit": "5"}, None)
    _, kwargs = m.call_args
    assert kwargs["data"] == body_bytes
    assert kwargs["params"] == {"limit": "5"}
    assert resp.status_code == 201
    assert resp.body == b'{"created":true}'


def test_proxy_excludes_api_v1():
    """/api/v1/* 不被代理接管（返回 None → 路由层 404/跳过）。"""
    assert newapi_proxy_routes._should_proxy("v1/health") is False
    assert newapi_proxy_routes._should_proxy("v1/admin/site/banners") is False


def test_proxy_accepts_newapi_prefixes():
    """user/ subscription/ option/ log/ 等 New API 前缀应代理。"""
    for path in ("user/login", "user/self", "subscription/plans", "user/topup/info",
                 "option/payment_compliance", "log/", "group", "redemption/",
                 "user/referral/summary", "subscription/admin/plans",
                 "status", "notice"):
        assert newapi_proxy_routes._should_proxy(path) is True, path


def test_proxy_timeout_fallback():
    """上游超时 → 返回 502 包装（不 raise）。"""
    with patch("requests.request", side_effect=Exception("timeout")):
        resp = newapi_proxy_routes._proxy_request("GET", "user/self", {}, None, None, None)
    assert resp.status_code == 502


def test_proxy_response_headers_forward():
    """响应 Content-Type + Set-Cookie 透传。"""
    fake = _FakeResponse(302, b"", {"Content-Type": "text/html", "Set-Cookie": "session=new"})
    with patch("requests.request", return_value=fake):
        resp = newapi_proxy_routes._proxy_request("GET", "user/logout", {}, None, None, None)
    assert resp.headers.get("Set-Cookie") == "session=new"
    assert resp.headers.get("Content-Type") == "text/html"

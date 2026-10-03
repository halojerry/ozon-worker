"""v0.76 T10(api-M4): logistics/quote Bearer 必填 + ``logistics:`` 前缀限流。

修复前：POST /api/v1/logistics/quote 的 token 走 **body 可选字段**——缺省直接
跳过鉴权（匿名可拉全量物流费率表），且无任何 rate limit（匿名可打满带 Ozon
凭证的 3PL 探测）。本文件锁定：
- 无 Bearer → 401 "Token is required"（``_require_bearer`` 同文案；修复前 200）。
- 带合法 Bearer → 200，body 原样透传报价实现。
- ``logistics:{clean_token}`` 独立限流键（不与提交限流额度互挤）→ 超限 429
  "rate limited"。

说明：R3a 起端点与 ``_logistics_quote_sync`` 迁 ``routes/ops_routes.py``——
``_logistics_quote_sync`` / ``rate_limiter`` 为路由模块级符号 → patch 路由模块
命名空间；``_verify_analytics_token`` 为 api.security 内部调用（``_require_bearer``）
→ patch ``api.security``。报价实现打替身，测试不依赖 PG 费率表（hermetic）。
不带 with 的 TestClient 不触发 lifespan。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_logistics_quote_auth_v076.py -q
"""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api.security as api_security  # noqa: E402
import routes.ops_routes as ops_routes  # noqa: E402
from main import RateLimiter, app  # noqa: E402

_URL = "/api/v1/logistics/quote"
_HEADERS = {"Authorization": "Bearer sk-probe-logistics"}
_BODY = {"weight_g": 480, "depth_cm": 20.0, "width_cm": 15.0, "height_cm": 10.0}


def _fake_quote(captured: dict):
    def _impl(body: dict) -> dict:
        captured.update(body)
        return {"logistics_cost_cny": 8.0, "channel": "RETS_Standard_A",
                "fallback_chain": []}
    return _impl


def test_no_token_401():
    """修复前：无 token（body 也不带）→ 200 返回费率；修复后 401。"""
    r = TestClient(app, raise_server_exceptions=False).post(_URL, json=_BODY)
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_with_token_200(monkeypatch):
    """Bearer 合法 → 200，body 原样透传报价实现（鉴权不改变请求语义）。"""
    monkeypatch.setattr(api_security, "_verify_analytics_token", lambda t: None)
    captured: dict = {}
    monkeypatch.setattr(ops_routes, "_logistics_quote_sync", _fake_quote(captured))
    r = TestClient(app).post(_URL, json=_BODY, headers=_HEADERS)
    assert r.status_code == 200
    assert r.json()["logistics_cost_cny"] == 8.0
    assert captured == _BODY


def test_rate_limit_429(monkeypatch):
    """同 token 超过 max_per_minute → 429；限流键带 ``logistics:`` 前缀。"""
    monkeypatch.setattr(api_security, "_verify_analytics_token", lambda t: None)
    captured: dict = {}
    monkeypatch.setattr(ops_routes, "_logistics_quote_sync", _fake_quote(captured))
    limiter = RateLimiter(max_per_minute=2)
    # R3a: 路由模块级 rate_limiter 绑定 → 换路由模块命名空间（换 main 引用会失靶）
    monkeypatch.setattr(ops_routes, "rate_limiter", limiter)
    c = TestClient(app)
    assert c.post(_URL, json=_BODY, headers=_HEADERS).status_code == 200
    assert c.post(_URL, json=_BODY, headers=_HEADERS).status_code == 200
    r = c.post(_URL, json=_BODY, headers=_HEADERS)
    assert r.status_code == 429
    assert r.json()["detail"] == "rate limited"
    # 前缀锁：限流计数键独立于提交限流（``logistics:{clean_token}``）
    assert all(str(k).startswith("logistics:") for k in limiter._requests)


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))

"""v0.76 T9(api-M3): store/health 鉴权 + 凭证 header 化 + 错误不回显原文。

修复前：GET /api/v1/store/health 完全无鉴权（匿名即可拿任意店铺凭证探测 Ozon
店铺配额/存在性），凭证只能走 query（进反代/访问日志留痕面），且异常文本
``message: str(e)`` 与 Ozon 原文 ``Ozon API error: {quota['error']}`` 原样进
200 响应体达客户端。本文件锁定：
- 无 Bearer → 401 "Token is required"（``_require_bearer`` 同文案）。
- 凭证优先 header（X-Ozon-Client-Id / X-Ozon-Api-Key），query 回落向后兼容
  （已弃用但不断供）。
- 上游异常 / Ozon error dict → 502 固定文案 "upstream store health check
  failed"，原文只进 logger，响应绝不回显。
- 成功 / 缺凭证（unknown）响应体结构不变。

说明：真实内部符号是 ``main.ozon_check_quota``（brief 里 ``_check_store_health``
为示意名）——``ozon_check_quota`` 自吞异常回 ``{"error": ...}`` dict，故
``quota["error"]`` 分支才是常态上游错误路径、raise 分支兜意外故障，两路都须
固定文案。``_verify_analytics_token`` 为 main 模块级函数 → patch ``main``
命名空间生效（同 test_require_bearer_v076 惯例）。不带 with 的 TestClient
不触发 lifespan（不连 PG / 不启动 worker）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_store_health_auth_v076.py -q
"""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod  # noqa: E402
from main import app  # noqa: E402

_LEAK_MARKER = "positive integer"  # Ozon 原文里的可断言标记词


def _ok_quota():
    return {"total_used": 1, "total_limit": 100, "daily_used": 1,
            "daily_limit": 100, "error": None}


def test_no_bearer_401():
    r = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/store/health?client_id=1&api_key=k")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_credentials_from_header(monkeypatch):
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    seen = {}

    def fake_check(client_id, api_key, timeout=10):
        seen.update(cid=client_id, ak=api_key)
        return _ok_quota()

    monkeypatch.setattr(main_mod, "ozon_check_quota", fake_check)
    r = TestClient(app).get("/api/v1/store/health", headers={
        "Authorization": "Bearer sk-x",
        "X-Ozon-Client-Id": "123", "X-Ozon-Api-Key": "AK-secret"})
    assert r.status_code == 200
    assert seen == {"cid": "123", "ak": "AK-secret"}
    # 成功响应体结构不变（向后兼容锁）
    body = r.json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "total_usage", "total_limit", "remaining",
                         "daily_usage", "daily_limit", "daily_remaining"}


def test_query_fallback_still_works(monkeypatch):
    """向后兼容锁：不带 header、凭证走 query 仍工作（T9 后 query 已弃用但不断供）。"""
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    seen = {}

    def fake_check(client_id, api_key, timeout=10):
        seen.update(cid=client_id, ak=api_key)
        return _ok_quota()

    monkeypatch.setattr(main_mod, "ozon_check_quota", fake_check)
    r = TestClient(app).get(
        "/api/v1/store/health?client_id=qcid&api_key=qkey",
        headers={"Authorization": "Bearer sk-x"})
    assert r.status_code == 200
    assert seen == {"cid": "qcid", "ak": "qkey"}


def test_missing_credentials_unknown_body(monkeypatch):
    """有 Bearer 缺凭证 → 200 unknown 结构保持不变（文案不回显凭证细节）。"""
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    r = TestClient(app).get("/api/v1/store/health",
                            headers={"Authorization": "Bearer sk-x"})
    assert r.status_code == 200
    assert r.json() == {"status": "unknown", "message": "需要提供 client_id 和 api_key"}


def test_raised_error_not_echoed(monkeypatch):
    """意外异常 → 502 固定文案，原文只进日志。"""
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)

    def boom(client_id, api_key, timeout=10):
        raise RuntimeError("Ozon API error: Client-Id header value should be "
                           + _LEAK_MARKER)

    monkeypatch.setattr(main_mod, "ozon_check_quota", boom)
    r = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/store/health",
        headers={"Authorization": "Bearer sk-x",
                 "X-Ozon-Client-Id": "1", "X-Ozon-Api-Key": "k"})
    assert r.status_code == 502
    assert r.json()["detail"] == "upstream store health check failed"
    assert _LEAK_MARKER not in r.text


def test_quota_error_body_not_echoed(monkeypatch):
    """ozon_check_quota 自吞异常回 {"error": ...} dict——常态上游错误路径同样不回原文。"""
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)

    def fake_check(client_id, api_key, timeout=10):
        return {"error": "Client-Id header value should be " + _LEAK_MARKER}

    monkeypatch.setattr(main_mod, "ozon_check_quota", fake_check)
    r = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/store/health",
        headers={"Authorization": "Bearer sk-x",
                 "X-Ozon-Client-Id": "1", "X-Ozon-Api-Key": "k"})
    assert r.status_code == 502
    assert r.json()["detail"] == "upstream store health check failed"
    assert _LEAK_MARKER not in r.text


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))

"""repo-gov 一期·租户 guard dependency 单测（BL-17 / A9 S12-01，TDD）。

覆盖 design-b2b-tenant-guard.md §3 Phase 1：
- api/deps_tenant.get_tenant / get_tenant_no_rate_limit：Bearer 提取 → 空 token 401 →
  剥 sk- → _verify_analytics_token → 限流（可选）→ resolve_tenant → request.state 缓存；
- routes/error_reports_routes 试点端点：POST/GET /error_reports + forensics 双路径，
  租户注入语义与 main.py 内联版等价（鉴权/限流/租户解析由 dependency 承担）。

main.py 依赖（_verify_analytics_token / rate_limiter / RATE_LIMIT_PER_MINUTE）全部
monkeypatch——不触网、不触 Supabase。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_tenant_guard_v075.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.deps_tenant import get_tenant, get_tenant_no_rate_limit  # noqa: E402
from routes import error_reports_routes as err_routes  # noqa: E402


@pytest.fixture()
def guard_calls(monkeypatch):
    """mock main.py 鉴权三件套 + resolve_tenant（记录调用参数），返回 calls dict。"""
    import main
    import services.tenant_service as tenant_service

    calls = {"verify": [], "limit": [], "resolve": []}

    def _fake_verify(clean_token):
        calls["verify"].append(clean_token)

    def _fake_check(clean_token):
        calls["limit"].append(clean_token)
        return (True, 99)

    def _fake_resolve(token):
        calls["resolve"].append(token)
        return "28"  # 生产租户形态：Supabase user_id 整数字符串

    monkeypatch.setattr(main, "_verify_analytics_token", _fake_verify)
    monkeypatch.setattr(main, "rate_limiter", mock.Mock(check=_fake_check))
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MINUTE", 300)
    monkeypatch.setattr(tenant_service, "resolve_tenant", _fake_resolve)
    return calls


def _build_client(monkeypatch, *, forensics_none=False):
    """挂试点 router 的最小 FastAPI app（root 路径 + /api/v1 前缀双 app）。"""
    import services.error_report_service as err_svc
    import services.forensics_service as forensics_svc

    monkeypatch.setattr(
        err_svc, "list_error_reports",
        lambda tenant_id, **kw: {"items": [], "total": 0, "tenant": tenant_id})
    monkeypatch.setattr(
        err_svc, "get_error_report", lambda tenant_id, rid: None)
    monkeypatch.setattr(
        err_svc, "create_error_report",
        lambda tenant_id, body, **kw: {"report_id": "r-1", "tenant": tenant_id})
    if forensics_none:
        monkeypatch.setattr(
            forensics_svc, "get_task_forensics", lambda tenant_id, task_id: None)
    else:
        monkeypatch.setattr(
            forensics_svc, "get_task_forensics",
            lambda tenant_id, task_id: {"task_id": task_id, "tenant": tenant_id})

    root_app = FastAPI()
    root_app.include_router(err_routes.root_router)  # /forensics/task/{id}
    v1_app = FastAPI()
    v1_app.include_router(err_routes.router, prefix="/api/v1")  # /api/v1/*
    return TestClient(root_app), TestClient(v1_app, raise_server_exceptions=False)


# ═══════════ dependency 单元行为 ═══════════

def test_dependency_missing_token_401(guard_calls):
    """空 Bearer → 401 "Token is required"（与内联版同文案）。"""
    req = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as ei:
        get_tenant(req)
    assert ei.value.status_code == 401
    assert ei.value.detail == "Token is required"


def test_dependency_happy_path_and_token_forms(guard_calls):
    """正常 Bearer：原始 token（含 sk-）给 resolve_tenant，clean token 给 verify/限流。"""
    req = Request({"type": "http", "headers": [
        (b"authorization", b"Bearer sk-test-token"),
    ]})
    tenant = get_tenant(req)
    assert tenant == "28"
    assert req.state.tenant == "28", "解析结果必须缓存到 request.state.tenant"
    assert guard_calls["resolve"] == ["sk-test-token"]
    assert guard_calls["verify"] == ["test-token"]
    assert guard_calls["limit"] == ["test-token"]


def test_dependency_no_rate_limit_variant_skips_limiter(guard_calls):
    """get_tenant_no_rate_limit：跳过限流（GET error_reports 现状逐字等价），其余同序。"""
    req = Request({"type": "http", "headers": [
        (b"authorization", b"Bearer tok"),
    ]})
    assert get_tenant_no_rate_limit(req) == "28"
    assert guard_calls["limit"] == []
    assert guard_calls["verify"] == ["tok"]


def test_dependency_rate_limited_429(guard_calls, monkeypatch):
    """限流版：rate_limiter.check False → 429 "Rate limit exceeded: max 300..."。"""
    import main

    monkeypatch.setattr(main.rate_limiter, "check", lambda tok: (False, 0))
    req = Request({"type": "http", "headers": [(b"authorization", b"Bearer tok")]})
    with pytest.raises(HTTPException) as ei:
        get_tenant(req)
    assert ei.value.status_code == 429
    assert ei.value.detail == "Rate limit exceeded: max 300 requests per minute"


# ═══════════ 试点路由行为（与 main.py 内联版等价性） ═══════════

def test_post_error_reports_requires_token(guard_calls, monkeypatch):
    """POST /api/v1/error_reports 无 Bearer → 401 "Token is required"（等价）。"""
    _, v1 = _build_client(monkeypatch)
    resp = v1.post("/api/v1/error_reports", json={"title": "x"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token is required"


def test_post_error_reports_tenant_injected(guard_calls, monkeypatch):
    """POST 成功路径：dependency 解析租户 → service 收到 Supabase user_id 租户 "28"。"""
    _, v1 = _build_client(monkeypatch)
    resp = v1.post("/api/v1/error_reports", json={"title": "x"},
                   headers={"Authorization": "Bearer sk-tok"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tenant"] == "28"
    assert guard_calls["limit"], "POST error_reports 现状有限流，dependency 必须检查"


def test_get_error_reports_no_rate_limit(guard_calls, monkeypatch):
    """GET /api/v1/error_reports：现状内联无限流 → dependency 不调 rate_limiter（逐字等价）。"""
    _, v1 = _build_client(monkeypatch)
    resp = v1.get("/api/v1/error_reports", headers={"Authorization": "Bearer sk-tok"})
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "28"
    assert guard_calls["limit"] == [], "GET error_reports 现状无限流检查"


def test_get_error_reports_detail_404(guard_calls, monkeypatch):
    """?report_id= 详情缺失 → 404 "report not found"（等价）。"""
    _, v1 = _build_client(monkeypatch)
    resp = v1.get("/api/v1/error_reports", params={"report_id": "nope"},
                  headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "report not found"


def test_forensics_both_paths_and_404(guard_calls, monkeypatch):
    """forensics 双路径（/api/v1 + 旧路径）租户注入；任务不属本租户 → 404。"""
    _, v1 = _build_client(monkeypatch)
    root, _ = _build_client(monkeypatch)

    resp = v1.get("/api/v1/forensics/task/t-1", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "t-1", "tenant": "28"}

    resp = root.get("/forensics/task/t-1", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "28"

    _, v2 = _build_client(monkeypatch, forensics_none=True)
    resp = v2.get("/api/v1/forensics/task/t-1", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "task not found"


def test_post_error_reports_rate_limited_429(guard_calls, monkeypatch):
    """POST 走限流版 dependency：check False → 429（与现状 POST error_reports 等价）。"""
    import main

    monkeypatch.setattr(main.rate_limiter, "check", lambda tok: (False, 0))
    _, v1 = _build_client(monkeypatch)
    resp = v1.post("/api/v1/error_reports", json={"title": "x"},
                   headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 429
    assert "Rate limit exceeded" in resp.json()["detail"]


def test_post_error_reports_invalid_body_422(guard_calls, monkeypatch):
    """POST body 是合法 JSON 但非对象 → 422 "body must be a JSON object"（校验留 handler）。"""
    _, v1 = _build_client(monkeypatch)
    resp = v1.post("/api/v1/error_reports", json=[1, 2],
                   headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "body must be a JSON object"

"""PRD M2: 账号级租户解析 + analytics scope 测试。

覆盖:未配置 Supabase 回退 key 哈希;已配置 key→user_id;401/503 fail-closed;
analytics scope 用户/admin;读端点 tenant 过滤与 hot-queries admin-only。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import tenant_service  # noqa: E402


class FakeSupabase:
    """tokens(key→user_id,status) + users(id→role)。"""

    def __init__(self, tokens=None, users=None):
        self._tokens = tokens or [{"key": "tok-ok", "user_id": "u-100", "status": 1, "deleted_at": None}]
        self._users = users or [{"id": "u-100", "role": 1}]
        self._fail_tokens = False
        self._fail_users = False

    def table(self, name):
        self._name = name
        return self

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def is_(self, col, val):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self._name == "tokens" and self._fail_tokens:
            raise RuntimeError("supabase down")
        if self._name == "users" and self._fail_users:
            raise RuntimeError("users down")
        if self._name == "tokens":
            rows = [r for r in self._tokens if str(r["key"]) == str(self._eq[1])]
        else:
            rows = [r for r in self._users if str(r["id"]) == str(self._eq[1])]
        return SimpleNamespace(data=rows)


def _patch_supabase(monkeypatch, supabase):
    monkeypatch.setattr(tenant_service, "get_supabase", lambda: supabase)


def test_resolve_tenant_fallback_key_hash(monkeypatch):
    _patch_supabase(monkeypatch, None)
    assert tenant_service.resolve_tenant("sk-abc") == tenant_service.key_derived_tenant("abc")
    assert tenant_service.resolve_tenant("abc") == tenant_service.key_derived_tenant("abc")


def test_resolve_tenant_user_id(monkeypatch):
    _patch_supabase(monkeypatch, FakeSupabase())
    tenant_service.clear_cache()
    assert tenant_service.resolve_tenant("sk-tok-ok") == "u-100"


def test_resolve_tenant_invalid_401(monkeypatch):
    _patch_supabase(monkeypatch, FakeSupabase(tokens=[{"key": "other", "user_id": "u-x", "status": 1}]))
    tenant_service.clear_cache()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        tenant_service.resolve_tenant("sk-tok-ok")
    assert ei.value.status_code == 401


def test_resolve_tenant_fail_closed_503(monkeypatch):
    fb = FakeSupabase()
    fb._fail_tokens = True
    _patch_supabase(monkeypatch, fb)
    tenant_service.clear_cache()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        tenant_service.resolve_tenant("sk-tok-ok")
    assert ei.value.status_code == 503


def test_analytics_scope_local_non_admin(monkeypatch):
    _patch_supabase(monkeypatch, None)
    scope = tenant_service.resolve_analytics_scope("sk-abc")
    assert scope == {"tenant_id": tenant_service.key_derived_tenant("abc"), "is_admin": False}


def test_analytics_scope_user_vs_admin(monkeypatch):
    _patch_supabase(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-user", "user_id": "u-1", "status": 1}],
        users=[{"id": "u-1", "role": 1}],
    ))
    tenant_service.clear_cache()
    assert tenant_service.resolve_analytics_scope("sk-tok-user")["is_admin"] is False

    _patch_supabase(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-admin", "user_id": "u-2", "status": 1}],
        users=[{"id": "u-2", "role": 10}],
    ))
    tenant_service.clear_cache()
    assert tenant_service.resolve_analytics_scope("sk-tok-admin")["is_admin"] is True


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def scalar(self):
        return 0


class _FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append((str(stmt), params))
        return _FakeRows([])


class _FakeEngine:
    def __init__(self):
        self.calls = []

    def connect(self):
        return _FakeConn(self)


class _FakeGetRequest:
    def __init__(self, token):
        self._token = token
        self.query_params = {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


def _setup_read_route(monkeypatch, supabase):
    """公共准备:patch scope 解析 + engine + 限流放行。"""
    _patch_supabase(monkeypatch, supabase)
    from main import rate_limiter
    monkeypatch.setattr(rate_limiter, "check", lambda t: (True, 100))
    engine = _FakeEngine()
    monkeypatch.setattr("storage.database.db.get_engine", lambda: engine)
    return engine


@pytest.mark.asyncio
async def test_market_overview_user_tenant_filter(monkeypatch):
    from routes import analytics_routes
    engine = _setup_read_route(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-user", "user_id": "u-1", "status": 1}],
        users=[{"id": "u-1", "role": 1}],
    ))
    tenant_service.clear_cache()
    resp = await analytics_routes.http_market_overview(_FakeGetRequest("sk-tok-user"))
    assert resp["total_orders"] == 0
    sqls = [s for s, _ in engine.calls]
    assert all("WHERE tenant_id=:t" in s for s in sqls[:4]), "用户态必须租户过滤"
    # bestseller_count 保持全局(无 tenant 过滤)
    assert "ozon_bestsellers" in sqls[4]


@pytest.mark.asyncio
async def test_market_overview_admin_global(monkeypatch):
    from routes import analytics_routes
    engine = _setup_read_route(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-admin", "user_id": "u-2", "status": 1}],
        users=[{"id": "u-2", "role": 10}],
    ))
    tenant_service.clear_cache()
    await analytics_routes.http_market_overview(_FakeGetRequest("sk-tok-admin"))
    sqls = [s for s, _ in engine.calls]
    assert all("WHERE tenant_id" not in s for s in sqls[:4]), "admin 看全局"


@pytest.mark.asyncio
async def test_sales_trend_user_tenant_filter(monkeypatch):
    from routes import analytics_routes
    engine = _setup_read_route(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-user", "user_id": "u-1", "status": 1}],
        users=[{"id": "u-1", "role": 1}],
    ))
    tenant_service.clear_cache()
    await analytics_routes.http_sales_trend(_FakeGetRequest("sk-tok-user"))
    sql = engine.calls[0][0]
    assert "AND tenant_id=:t" in sql


@pytest.mark.asyncio
async def test_hot_queries_admin_only(monkeypatch):
    from fastapi import HTTPException
    from routes import analytics_routes
    engine = _setup_read_route(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-user", "user_id": "u-1", "status": 1}],
        users=[{"id": "u-1", "role": 1}],
    ))
    tenant_service.clear_cache()
    with pytest.raises(HTTPException) as ei:
        await analytics_routes.http_hot_queries(_FakeGetRequest("sk-tok-user"))
    assert ei.value.status_code == 403
    # admin 放行
    _setup_read_route(monkeypatch, FakeSupabase(
        tokens=[{"key": "tok-admin", "user_id": "u-2", "status": 1}],
        users=[{"id": "u-2", "role": 10}],
    ))
    tenant_service.clear_cache()
    resp = await analytics_routes.http_hot_queries(_FakeGetRequest("sk-tok-admin"))
    assert resp["items"] == [] and resp["scope"] == "global"

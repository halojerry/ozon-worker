"""v0.83 批⑤ discovery_runs canonical session（worker 侧）。

覆盖（方案 docs/PLAN-v083-quality-campaign-v1.md §3⑤ 验收闸）：
1. init_data 迁移幂等（结构 DDL 重复跑 no-op）。
2. 上报幂等：带 session_run_id → ON CONFLICT(session_run_id) DO UPDATE；响应 upserted。
3. 请求体上限 4MB → 413。
4. 老行 legacy:true（session_json null + candidates 回退 candidates_json）。
5. 新读端点跨租户 404（token_fp 归属判定）+ 非 owner 不泄漏存在性。
6. list 返回 session_run_id 字段。
纯 mock DB / mock Supabase，不真连网络。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from services.tenant_service import token_fingerprint


# ── fakes ────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else 0


class _WriteConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        try:
            self._engine.compiled_params.append(stmt.compile().params)
        except Exception:
            self._engine.compiled_params.append({})
        rc = self._engine.rowcounts.pop(0) if self._engine.rowcounts else 0
        return _FakeResult(rowcount=rc)

    def commit(self):
        pass


class _FakeEngine:
    def __init__(self, rowcounts=None, one_row=None):
        self.rowcounts = list(rowcounts or [])
        self.one_row = one_row
        self.calls = []
        self.compiled_params = []

    def connect(self):
        return _WriteConn(self)


class _ReadConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        if "COUNT(*)" in str(stmt):
            return _FakeResult(rows=[len(self._engine.rows)])
        return _FakeResult(rows=self._engine.rows)

    def commit(self):
        pass


class _ReadEngine:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def connect(self):
        return _ReadConn(self)


class _FakeRequest:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._body


class _FakeGetRequest:
    def __init__(self, token):
        self._token = token
        self.query_params = {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


RID = "disc_260928_101530_a1b2c3"
VALID_SESSION_BODY = {
    "token": "sk-tenant-a",
    "keyword": "宠物饮水机",
    "filters": {"min_margin": 0.2},
    "candidates": [{"ozon_product_id": "111", "status": "profitable"}],
    "session_run_id": RID,
    "schema_version": "discover.session.v1",
    "session_json": {"schema_version": "discover.session.v1",
                     "session_run_id": RID, "summary": {"total": 1}},
}


def _post(body, monkeypatch, rowcounts=None, headers=None):
    import main
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    engine = _FakeEngine(rowcounts or [1])
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    result = asyncio.run(main.v1_discovery_report_run(_FakeRequest(body, headers)))
    return result, engine


def _get(run_id, token, monkeypatch, rows):
    import main
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    engine = _ReadEngine(rows)
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    return asyncio.run(main.v1_discovery_get_run(run_id, _FakeGetRequest(token)))


# ── 1. 迁移幂等 ──────────────────────────────────────────────────────

def test_migration_idempotent():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "init_data_mod", str(Path(__file__).resolve().parent.parent / "scripts" / "init_data.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls: list[str] = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params=None):
            calls.append(str(stmt))
            return _FakeResult(rowcount=0)

        def commit(self):
            pass

    class _Eng:
        def connect(self):
            return _Conn()

    eng = _Eng()
    mod.migrate_discovery_session_v083(eng)
    first = len(calls)
    mod.migrate_discovery_session_v083(eng)   # 二次跑 no-op（IF NOT EXISTS）
    assert first == 4
    assert len(calls) == 8
    joined = " ".join(calls)
    assert "ADD COLUMN IF NOT EXISTS session_run_id" in joined
    assert "ADD COLUMN IF NOT EXISTS schema_version" in joined
    assert "ADD COLUMN IF NOT EXISTS session_json" in joined
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_runs_session_run_id" in joined
    assert "WHERE session_run_id IS NOT NULL" in joined


# ── 2. 上报幂等 upsert ───────────────────────────────────────────────

def test_post_session_upsert_on_conflict(monkeypatch):
    resp, engine = _post(VALID_SESSION_BODY, monkeypatch, rowcounts=[1])
    assert resp["status"] == "ok"
    assert resp["upserted"] == 1
    sql = engine.calls[0]
    assert "ON CONFLICT" in sql and "session_run_id" in sql
    params = engine.compiled_params[0]
    assert any(k.split("_m")[0] == "session_json" for k in params)


def test_post_without_session_legacy_insert(monkeypatch):
    body = {"token": "sk-tenant-a", "keyword": "kw", "candidates": []}
    resp, engine = _post(body, monkeypatch, rowcounts=[1])
    assert resp == {"status": "ok", "inserted": 1, "upserted": 0}
    assert "ON CONFLICT" not in engine.calls[0]


# ── 3. 4MB 上限 ──────────────────────────────────────────────────────

def test_post_body_too_large_413(monkeypatch):
    import main
    headers = {"content-length": str(4 * 1024 * 1024 + 1)}
    with pytest.raises(main.HTTPException) as ei:
        _post(VALID_SESSION_BODY, monkeypatch, rowcounts=[], headers=headers)
    assert ei.value.status_code == 413


def test_post_body_under_limit_ok(monkeypatch):
    headers = {"content-length": "1024"}
    resp, _ = _post(VALID_SESSION_BODY, monkeypatch, rowcounts=[1], headers=headers)
    assert resp["status"] == "ok"


# ── 4. 老行 legacy + 5. 跨租户 404 ───────────────────────────────────

def test_get_legacy_row(monkeypatch):
    """老行（session_json NULL）→ legacy:true + candidates 回退 candidates_json。"""
    rows = [(RID, None, "kw", None, [{"ozon_product_id": "legacy1"}],
             datetime(2026, 8, 17, 10, 0, 0), token_fingerprint("tenant-a"))]
    resp = _get(RID, "sk-tenant-a", monkeypatch, rows)
    assert resp["legacy"] is True
    assert resp["session_json"] is None
    assert resp["candidates"] == [{"ozon_product_id": "legacy1"}]


def test_get_session_row(monkeypatch):
    rows = [(RID, "discover.session.v1", "kw",
             {"session_run_id": RID, "summary": {"total": 3}},
             [], datetime(2026, 8, 17, 10, 0, 0), token_fingerprint("tenant-a"))]
    resp = _get(RID, "sk-tenant-a", monkeypatch, rows)
    assert resp["legacy"] is False
    assert resp["session_json"]["summary"]["total"] == 3
    assert resp["schema_version"] == "discover.session.v1"


def test_get_cross_tenant_404(monkeypatch):
    import main
    rows = [(RID, "discover.session.v1", "kw", {"session_run_id": RID}, [],
             datetime(2026, 8, 17, 10, 0, 0), token_fingerprint("tenant-a"))]
    with pytest.raises(main.HTTPException) as ei:
        _get(RID, "sk-tenant-b", monkeypatch, rows)
    assert ei.value.status_code == 404


def test_get_missing_404(monkeypatch):
    import main
    with pytest.raises(main.HTTPException) as ei:
        _get(RID, "sk-tenant-a", monkeypatch, [])
    assert ei.value.status_code == 404


def test_get_bad_run_id_404(monkeypatch):
    import main
    with pytest.raises(main.HTTPException) as ei:
        _get("../../etc/passwd", "sk-tenant-a", monkeypatch, [])
    assert ei.value.status_code == 404


# ── 6. list 带 session_run_id ────────────────────────────────────────

def test_list_returns_session_run_id(monkeypatch):
    import main
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    rows = [
        ("1", "kw", None, [], datetime(2026, 8, 17, 10, 0, 0), "tenant-a", RID),
        ("2", "kw2", None, [], datetime(2026, 8, 17, 9, 0, 0), "tenant-a", None),
    ]
    engine = _ReadEngine(rows)
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    resp = asyncio.run(main.v1_discovery_list_runs(_FakeGetRequest("sk-tenant-a")))
    assert resp["items"][0]["session_run_id"] == RID
    assert resp["items"][1]["session_run_id"] is None


def test_list_tolerates_legacy_six_tuple(monkeypatch):
    """旧 6 列行（无 session_run_id 列，测试夹具）不炸。"""
    import main
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    rows = [("1", "kw", None, [], datetime(2026, 8, 17, 10, 0, 0), "tenant-a")]
    engine = _ReadEngine(rows)
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    resp = asyncio.run(main.v1_discovery_list_runs(_FakeGetRequest("sk-tenant-a")))
    assert resp["items"][0]["session_run_id"] is None

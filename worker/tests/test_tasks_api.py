"""T8 任务列表端点：租户隔离 + 分页 + progress/product_summary 字段 + 无 token → 401。

mock Supabase（鉴权）+ mock DB（services.task_service.get_engine），不真实请求。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod
from routes.tasks_routes import router as tasks_router
from services import task_service


class FakeRequest:
    def __init__(self, query_params: dict):
        self.query_params = query_params
        self.headers = {}

    async def body(self) -> bytes:
        return b""

    async def json(self):
        return {}


def _valid_supabase(user_id: str = "u1") -> MagicMock:
    """返回 tokens 表命中一条有效记录的 Supabase mock（user_id 可指定租户）。"""
    fake = MagicMock()
    chain = fake.table.return_value.select.return_value.eq.return_value.is_.return_value.execute
    chain.return_value.data = [{
        "user_id": user_id,
        "key": "tok123",
        "remain_quota": 999,
        "status": 1,
        "expired_time": -1,
        "unlimited_quota": False,
    }]
    return fake


class FakeRow:
    """模拟 SQLAlchemy Row（属性访问）。"""

    def __init__(self, id, status, result, progress, created_at, updated_at):
        self.id = id
        self.status = status
        self.result = result
        self.progress = progress
        self.created_at = created_at
        self.updated_at = updated_at


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """模拟 engine.connect()：按 tenant_id 过滤 + LIMIT/OFFSET 切片，记录 SQL 参数。"""

    def __init__(self, rows_by_tenant):
        self._rows_by_tenant = rows_by_tenant
        self.calls = []

    def execute(self, sql, params=None):
        params = dict(params or {})
        self.calls.append((str(sql), params))
        tenant = params.get("tenant_id")
        rows = self._rows_by_tenant.get(tenant, [])
        if "COUNT(*)" in str(sql):
            return FakeResult([len(rows)])
        limit = params.get("limit", len(rows))
        offset = params.get("offset", 0)
        return FakeResult(rows[offset:offset + limit])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEngine:
    def __init__(self, rows_by_tenant):
        self._rows_by_tenant = rows_by_tenant
        self.last_conn = None

    def connect(self):
        self.last_conn = FakeConn(self._rows_by_tenant)
        return self.last_conn


def _rows_for_tenant():
    """u1 两条（created_at DESC 已排好），u2 一条。"""
    return {
        "u1": [
            FakeRow(
                "11111111-1111-1111-1111-111111111111", "completed",
                {"product_summary": [{"title": "A"}]},
                {"percent": 100, "stage": "done"},
                "2026-08-15T10:00:00", "2026-08-15T10:05:00",
            ),
            FakeRow(
                "22222222-2222-2222-2222-222222222222", "running",
                None, {"percent": 50, "stage": "pricing"},
                "2026-08-15T09:00:00", "2026-08-15T09:30:00",
            ),
        ],
        "u2": [
            FakeRow(
                "33333333-3333-3333-3333-333333333333", "failed",
                None, None, "2026-08-15T08:00:00", "2026-08-15T08:01:00",
            ),
        ],
    }


def _handler():
    return tasks_router.routes[0].endpoint


def _call(query_params: dict):
    return asyncio.run(_handler()(FakeRequest(query_params)))


# ============================================================
# 1. 租户隔离：A 看不到 B 的任务
# ============================================================

def test_tasks_tenant_isolation():
    engine = FakeEngine(_rows_for_tenant())
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase("u1")), \
         patch.object(task_service, "get_engine", return_value=engine):
        resp = _call({"token": "sk-tok123"})
    ids = [item["id"] for item in resp["items"]]
    assert "11111111-1111-1111-1111-111111111111" in ids
    assert "22222222-2222-2222-2222-222222222222" in ids
    assert "33333333-3333-3333-3333-333333333333" not in ids  # u2 的任务不可见
    assert resp["total"] == 2
    # SQL 确实按 tenant_id 过滤（两条查询都带 tenant_id 参数）
    assert len(engine.last_conn.calls) == 2
    assert all("tenant_id" in params for _, params in engine.last_conn.calls)


# ============================================================
# 2. 分页：limit/offset 生效
# ============================================================

def test_tasks_pagination():
    engine = FakeEngine(_rows_for_tenant())
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase("u1")), \
         patch.object(task_service, "get_engine", return_value=engine):
        page1 = _call({"token": "sk-tok123", "limit": "1", "offset": "0"})
        page2 = _call({"token": "sk-tok123", "limit": "1", "offset": "1"})
    assert [i["id"] for i in page1["items"]] == ["11111111-1111-1111-1111-111111111111"]
    assert [i["id"] for i in page2["items"]] == ["22222222-2222-2222-2222-222222222222"]
    assert page1["total"] == 2
    assert page1["limit"] == 1 and page1["offset"] == 0
    assert page2["limit"] == 1 and page2["offset"] == 1


def test_tasks_limit_capped_at_100():
    """limit 超过 100 被钳制到 100。"""
    engine = FakeEngine(_rows_for_tenant())
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase("u1")), \
         patch.object(task_service, "get_engine", return_value=engine):
        resp = _call({"token": "sk-tok123", "limit": "500"})
    assert resp["limit"] == 100


# ============================================================
# 3. progress / product_summary 字段透出
# ============================================================

def test_tasks_progress_and_product_summary():
    engine = FakeEngine(_rows_for_tenant())
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase("u1")), \
         patch.object(task_service, "get_engine", return_value=engine):
        resp = _call({"token": "sk-tok123"})
    completed = next(i for i in resp["items"] if i["status"] == "completed")
    assert completed["progress"] == {"percent": 100, "stage": "done"}
    assert completed["product_summary"] == [{"title": "A"}]
    running = next(i for i in resp["items"] if i["status"] == "running")
    assert running["progress"] == {"percent": 50, "stage": "pricing"}
    assert running["product_summary"] == []


# ============================================================
# 4. 鉴权：无 token → 401
# ============================================================

def test_tasks_no_token_401():
    with pytest.raises(HTTPException) as exc:
        _call({})
    assert exc.value.status_code == 401
"""v0.51: 管理员面板测试（mock Supabase users + 真实 PG 聚合）。

验收门（docs/PRD-admin-panel-v0.51.md §四）：
1. 管理员判定：role=admin → True；role=user → False；本地 local_dev → True
2. overview 聚合：用户数/店铺数/任务数/成功率
3. 用户列表拼装：Supabase users + PG 店铺/任务数
4. 用户详情：店铺列表 + 任务统计
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import admin_service

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过管理员测试")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


@pytest.fixture(autouse=True)
def _cleanup(_pg):
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE tenant_id LIKE 'admin-test%'"))
        conn.execute(text("DELETE FROM credentials WHERE tenant_id LIKE 'admin-test%'"))
    eng.dispose()


class _FakeSupabase:
    """users 表 fake：id → {role, quota, username}。"""

    def __init__(self, users: dict):
        self._users = users
        self._table = None
        self._query = {}

    def table(self, name):
        self._table = name
        return self

    def select(self, *cols):
        self._query["cols"] = cols
        return self

    def eq(self, col, val):
        self._query["eq"] = (col, val)
        return self

    def limit(self, n):
        self._query["limit"] = n
        return self

    def execute(self):
        if self._table == "users":
            eq = self._query.get("eq")
            if eq:
                col, val = eq
                data = [u for u in self._users.values() if u.get(col) == str(val)]
                return SimpleNamespace(data=data)
            return SimpleNamespace(data=list(self._users.values())[:1000])
        return SimpleNamespace(data=[])


def _seed_pg(tenant: str, client_id: str, task_count: int, completed: int):
    from services import credential_service
    cid = credential_service.store_credential(tenant, client_id, f"key-{client_id}")
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        for i in range(task_count):
            status = "completed" if i < completed else "failed"
            conn.execute(text(
                "INSERT INTO ozon_product_tasks (tenant_id, status, payload) "
                "VALUES (:t, :s, '{}'::jsonb)"
            ), {"t": tenant, "s": status})
        conn.execute(text(
            "UPDATE credentials SET is_default=true WHERE id=:id"), {"id": cid})
    eng.dispose()


# ============================================================
# 1. 管理员判定
# ============================================================

def test_is_admin_user_role_admin():
    fake = _FakeSupabase({"u1": {"id": "u1", "role": "admin", "username": "boss"}})
    with patch("main.get_supabase_client", return_value=fake):
        assert admin_service.is_admin_user("u1") is True


def test_is_admin_user_role_user():
    fake = _FakeSupabase({"u2": {"id": "u2", "role": "user", "username": "user"}})
    with patch("main.get_supabase_client", return_value=fake):
        assert admin_service.is_admin_user("u2") is False


def test_is_admin_local_dev():
    with patch("main.get_supabase_client", return_value=None):
        assert admin_service.is_admin_user("local_dev") is True


def test_require_admin_forbidden():
    fake = _FakeSupabase({"u2": {"id": "u2", "role": "user"}})
    with patch("main.get_supabase_client", return_value=fake):
        with pytest.raises(HTTPException) as ei:
            admin_service.require_admin("u2")
    assert ei.value.status_code == 403


# ============================================================
# 1b. is_admin_role 纯函数（v0.55：New API 整数角色体系）
# ============================================================

def test_is_admin_role_root_int():
    """role=100（RoleRootUser）→ 管理员。"""
    assert admin_service.is_admin_role(100) is True


def test_is_admin_role_admin_int():
    """role=10（RoleAdminUser）→ 管理员。"""
    assert admin_service.is_admin_role(10) is True


def test_is_admin_role_common_int():
    """role=1（RoleCommonUser）→ 非管理员。"""
    assert admin_service.is_admin_role(1) is False


def test_is_admin_role_guest_int():
    """role=0（RoleGuestUser）→ 非管理员。"""
    assert admin_service.is_admin_role(0) is False


def test_is_admin_role_str_compat():
    """字符串 '100'/'10'/'admin'/'root' 兼容（历史代码/测试形态）。"""
    assert admin_service.is_admin_role("100") is True
    assert admin_service.is_admin_role("10") is True
    assert admin_service.is_admin_role("admin") is True
    assert admin_service.is_admin_role("root") is True
    assert admin_service.is_admin_role("1") is False
    assert admin_service.is_admin_role("user") is False


def test_is_admin_role_edge():
    """None/空串/bool/非法字符串 → 非管理员（安全默认）。"""
    assert admin_service.is_admin_role(None) is False
    assert admin_service.is_admin_role("") is False
    assert admin_service.is_admin_role("  ") is False
    assert admin_service.is_admin_role(True) is False
    assert admin_service.is_admin_role(False) is False
    assert admin_service.is_admin_role("abc") is False


def test_is_admin_user_role_100_int():
    """Supabase 实际存储整数 100 → 管理员（修复前恒 False 的 bug 回归）。"""
    fake = _FakeSupabase({"u1": {"id": "u1", "role": 100, "username": "boss"}})
    with patch("main.get_supabase_client", return_value=fake):
        assert admin_service.is_admin_user("u1") is True


def test_is_admin_user_role_10_int():
    """Supabase 整数 10（admin）→ 管理员。"""
    fake = _FakeSupabase({"u1": {"id": "u1", "role": 10, "username": "op"}})
    with patch("main.get_supabase_client", return_value=fake):
        assert admin_service.is_admin_user("u1") is True


def test_is_admin_user_role_1_int():
    """Supabase 整数 1（普通用户）→ 非管理员。"""
    fake = _FakeSupabase({"u1": {"id": "u1", "role": 1, "username": "user"}})
    with patch("main.get_supabase_client", return_value=fake):
        assert admin_service.is_admin_user("u1") is False


# ============================================================
# 2. overview 聚合
# ============================================================

def test_overview_aggregates(_pg):
    _seed_pg("admin-test-ov", "9001", task_count=4, completed=3)
    fake = _FakeSupabase({
        "u1": {"id": "u1", "role": "user", "username": "a"},
        "u2": {"id": "u2", "role": "admin", "username": "b"},
    })
    with patch("main.get_supabase_client", return_value=fake):
        ov = admin_service.get_overview()
    assert ov["user_count"] >= 2
    assert ov["store_count"] >= 1
    assert ov["task_total"] >= 4
    assert ov["success_rate"] > 0
    assert ov["statistics"]["completed"] >= 3


# ============================================================
# 3. 用户列表拼装
# ============================================================

def test_list_users_joins_pg_counts(_pg):
    _seed_pg("admin-test-u1", "9002", task_count=5, completed=3)
    fake = _FakeSupabase({
        "admin-test-u1": {"id": "admin-test-u1", "role": "user", "username": "alice", "quota": 100},
    })
    with patch("main.get_supabase_client", return_value=fake):
        users = admin_service.list_users()
    alice = next(u for u in users if u["id"] == "admin-test-u1")
    assert alice["username"] == "alice"
    assert alice["role"] == "user"
    assert alice["store_count"] >= 1
    assert alice["task_count"] >= 5


# ============================================================
# 4. 用户详情
# ============================================================

def test_user_detail_stores_and_tasks(_pg):
    _seed_pg("admin-test-d1", "9003", task_count=6, completed=2)
    detail = admin_service.get_user_detail("admin-test-d1")
    assert detail["id"] == "admin-test-d1"
    assert detail["task_total"] >= 6
    assert detail["task_completed"] >= 2
    assert detail["task_failed"] >= 4
    assert any(s["ozon_client_id"] == "9003" for s in detail["stores"])


# ============================================================
# 5. 店铺列表（跨用户）
# ============================================================

def test_list_stores_cross_tenant(_pg):
    _seed_pg("admin-test-s1", "9101", task_count=1, completed=1)
    _seed_pg("admin-test-s2", "9102", task_count=1, completed=1)
    stores = admin_service.list_stores()
    client_ids = {s["ozon_client_id"] for s in stores}
    assert "9101" in client_ids
    assert "9102" in client_ids

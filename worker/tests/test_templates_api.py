"""P0-1: 上架配置模板 API 端点测试（真实 PG + TestClient + FakeSupabase）。

验收门（docs/PRD-listing-template-v0.44.md §五）：
1. 鉴权：无 token / 坏 token → 401
2. CRUD 端点 + 租户隔离
3. 设默认端点（清旧默认）
4. config 白名单 422

需要本地 Docker PG；PG 不可达时 skip。
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

TENANTS = ("tenant-a", "tenant-b")
TOKEN_MAP = {"tokA": "tenant-a", "tokB": "tenant-b"}


class FakeSupabase:
    """tokens 表 fake：key → user_id 映射（租户隔离测试用）。"""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def table(self, name):
        return _FakeTokensTable(self._mapping)


class _FakeTokensTable:
    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping
        self._key = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        if col == "key":
            self._key = val
        return self

    def is_(self, col, val):
        return self

    def execute(self):
        uid = self._mapping.get(self._key)
        if uid is None:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[{
            "user_id": uid, "key": self._key, "remain_quota": 999,
            "status": 1, "expired_time": -1, "unlimited_quota": False,
        }])


@pytest.fixture(scope="module")
def client():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过模板 API 测试")
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """鉴权 mock（rate_limiter 放行 + Supabase 按 key 分租户）。"""
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=FakeSupabase(TOKEN_MAP)):
        yield


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM listing_templates WHERE tenant_id IN ('tenant-a','tenant-b')"
        ))
    eng.dispose()


def _auth_headers(tenant: str) -> dict:
    token = "tokA" if tenant == "tenant-a" else "tokB"
    return {"Authorization": f"Bearer sk-{token}"}


def _create(client, tenant: str, payload: dict) -> dict:
    resp = client.post("/api/v1/templates", json=payload, headers=_auth_headers(tenant))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ============================================================
# 1. 鉴权
# ============================================================

def test_auth_required(client):
    resp = client.get("/api/v1/templates")
    assert resp.status_code == 401
    resp = client.post("/api/v1/templates", json={"name": "x"})
    assert resp.status_code == 401


# ============================================================
# 2. CRUD + 租户隔离
# ============================================================

def test_create_and_list(client):
    tpl = _create(client, "tenant-a", {
        "name": "高利润", "config": {"margin_rate": 0.35, "offer_id_prefix": "W1"}})
    assert tpl["config"]["margin_rate"] == 0.35
    items = client.get("/api/v1/templates", headers=_auth_headers("tenant-a")).json()
    assert len(items) == 1
    assert items[0]["id"] == tpl["id"]
    # 租户隔离：B 看不到 A
    items_b = client.get("/api/v1/templates", headers=_auth_headers("tenant-b")).json()
    assert len(items_b) == 0


def test_patch(client):
    tpl = _create(client, "tenant-a", {"name": "a", "config": {"margin_rate": 0.2}})
    resp = client.patch(
        f"/api/v1/templates/{tpl['id']}",
        json={"config": {"margin_rate": 0.4}},
        headers=_auth_headers("tenant-a"),
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["margin_rate"] == 0.4


def test_patch_foreign_tenant_404(client):
    tpl = _create(client, "tenant-a", {"name": "a"})
    resp = client.patch(
        f"/api/v1/templates/{tpl['id']}",
        json={"name": "hack"},
        headers=_auth_headers("tenant-b"),
    )
    assert resp.status_code == 404


def test_delete(client):
    tpl = _create(client, "tenant-a", {"name": "a"})
    resp = client.delete(
        f"/api/v1/templates/{tpl['id']}", headers=_auth_headers("tenant-a"))
    assert resp.status_code == 204
    items = client.get("/api/v1/templates", headers=_auth_headers("tenant-a")).json()
    assert len(items) == 0


# ============================================================
# 3. 设默认端点
# ============================================================

def test_set_default_endpoint(client):
    t1 = _create(client, "tenant-a", {"name": "t1"})
    t2 = _create(client, "tenant-a", {"name": "t2"})
    resp = client.post(
        f"/api/v1/templates/{t1['id']}/default", headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True
    resp2 = client.post(
        f"/api/v1/templates/{t2['id']}/default", headers=_auth_headers("tenant-a"))
    assert resp2.status_code == 200
    # t1 默认被清
    got = client.get("/api/v1/templates", headers=_auth_headers("tenant-a")).json()
    by_id = {x["id"]: x for x in got}
    assert by_id[t1["id"]]["is_default"] is False
    assert by_id[t2["id"]]["is_default"] is True


# ============================================================
# 4. 白名单校验
# ============================================================

def test_config_unknown_key_422(client):
    resp = client.post(
        "/api/v1/templates",
        json={"name": "x", "config": {"evil": 1}},
        headers=_auth_headers("tenant-a"),
    )
    assert resp.status_code == 422
    assert "非法字段" in resp.text


def test_config_bad_numeric_422(client):
    resp = client.post(
        "/api/v1/templates",
        json={"name": "x", "config": {"margin_rate": 5}},
        headers=_auth_headers("tenant-a"),
    )
    assert resp.status_code == 422

"""PRD M5(P2): 店铺数据硬删除测试(管理端授权 + confirm + 开关,真实 PG)。

覆盖:开关未启用 → 403;未 confirm → 400;非管理员 → 403;启用+confirm →
店级缓存/历史/成本/货源全清 + store_operation_log 审计行;凭证吊销记录与
用户草稿不删。
"""
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"

import main as main_mod  # noqa: E402
import services.tenant_service as tenant_service_mod  # noqa: E402

TENANT = "local_dev"


class _FakeTokens:
    def __init__(self, rows):
        self._rows = rows
        self._eqs = []

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def is_(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        eqs, self._eqs = self._eqs, []
        filtered = self._rows
        for col, val in eqs:
            expect = None if val == "null" else val
            filtered = [r for r in filtered if str(r.get(col)) == str(expect)]
        return SimpleNamespace(data=filtered[:1])


class _FakeUsers:
    def __init__(self, rows):
        self._rows = rows
        self._eqs = []

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        eqs, self._eqs = self._eqs, []
        filtered = self._rows
        for col, val in eqs:
            expect = None if val == "null" else val
            filtered = [r for r in filtered if str(r.get(col)) == str(expect)]
        return SimpleNamespace(data=filtered[:1])


class FakeSupabase:
    def __init__(self):
        self._tokens = [
            {"user_id": "local_dev", "key": "tokAdmin", "deleted_at": None},
            {"user_id": "user-common", "key": "tokUser", "deleted_at": None},
        ]
        self._users = [
            {"id": "local_dev", "role": "admin"},
            {"id": "user-common", "role": 1},
        ]

    def table(self, name):
        if name == "tokens":
            return _FakeTokens(self._tokens)
        if name == "users":
            return _FakeUsers(self._users)
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    fake = FakeSupabase()
    with TestClient(main_mod.app) as c:
        yield c, fake, eng


@pytest.fixture(autouse=True)
def _supabase_patch(monkeypatch):
    """全模块统一注入 FakeSupabase(管理员/普通用户双 token),gate 测试也需要走到鉴权。"""
    fake = FakeSupabase()
    monkeypatch.setattr(tenant_service_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(main_mod, "get_supabase_client", lambda: fake)
    return fake


def _make_credential(eng) -> tuple[str, str]:
    tenant = f"erase-{uuid.uuid4().hex[:10]}"
    client_id = f"9{uuid.uuid4().int % 10**7}"
    cred_id = str(uuid.uuid4())
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO credentials (id, tenant_id, ozon_client_id, api_key_masked, "
            "ozon_api_key_enc, status) VALUES (:id, :t, :c, 'masked', :enc, 'active')"
        ), {"id": cred_id, "t": tenant, "c": client_id, "enc": b"x"})
        conn.execute(text(
            "INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, "
            "status, raw_status, products, product_count, warehouse, delivery_method, "
            "cancel_reason, cancellation) VALUES "
            "(:t, :c, 'erase-order-1', 'delivered', 'delivered', '[]', 1, 'w', 'fbs', '', '')"
        ), {"t": tenant, "c": cred_id})
        conn.execute(text(
            "INSERT INTO ozon_products_cache (tenant_id, credential_id, product_id, "
            "offer_id, name, image, price, stock, currency, archived, status) VALUES "
            "(:t, :c, 'erase-p-1', 'offer-1', '硬删商品', '', 100, 5, 'RUB', false, '')"
        ), {"t": tenant, "c": cred_id})
        conn.execute(text(
            "INSERT INTO source_candidates (tenant_id, credential_id, product_id, "
            "source_offer_id, source_url, match_method) VALUES "
            "(:t, :c, 'erase-p-1', 'offer-1', 'https://detail.1688.com/offer/1.html', 'manual')"
        ), {"t": tenant, "c": cred_id})
        conn.execute(text(
            "INSERT INTO store_sync_jobs (tenant_id, credential_id, kind, status, trigger) "
            "VALUES (:t, :c, 'initial', 'ok', 'manual')"
        ), {"t": tenant, "c": cred_id})
    return tenant, cred_id


def _count(eng, tenant: str, cred_id: str) -> int:
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT "
            " (SELECT COUNT(*) FROM ozon_orders_cache WHERE tenant_id=:t AND credential_id::text=:c)"
            " + (SELECT COUNT(*) FROM ozon_products_cache WHERE tenant_id=:t AND credential_id::text=:c)"
            " + (SELECT COUNT(*) FROM source_candidates WHERE tenant_id=:t AND credential_id::text=:c)"
            " + (SELECT COUNT(*) FROM store_sync_jobs WHERE tenant_id=:t AND credential_id::text=:c)"
        ), {"t": tenant, "c": cred_id}).scalar()
    return int(row or 0)


def test_hard_delete_gate_disabled(client):
    c, _, eng = client
    tenant, cred_id = _make_credential(eng)
    os.environ.pop("ADMIN_HARD_DELETE_ENABLED", None)
    resp = c.delete(
        f"/api/v1/credentials/{cred_id}/data?confirm=true",
        headers={"Authorization": "Bearer tokAdmin"},
    )
    assert resp.status_code == 403
    assert "未启用" in resp.json()["detail"]


def test_hard_delete_requires_confirm(client):
    c, _, eng = client
    tenant, cred_id = _make_credential(eng)
    os.environ["ADMIN_HARD_DELETE_ENABLED"] = "1"
    resp = c.delete(
        f"/api/v1/credentials/{cred_id}/data",
        headers={"Authorization": "Bearer tokAdmin"},
    )
    assert resp.status_code == 400
    assert "confirm" in resp.json()["detail"]


def test_hard_delete_non_admin_forbidden(client):
    c, _, eng = client
    tenant, cred_id = _make_credential(eng)
    os.environ["ADMIN_HARD_DELETE_ENABLED"] = "1"
    resp = c.delete(
        f"/api/v1/credentials/{cred_id}/data?confirm=true",
        headers={"Authorization": "Bearer tokUser"},
    )
    assert resp.status_code == 403


def test_hard_delete_ok(client):
    c, _, eng = client
    tenant, cred_id = _make_credential(eng)
    os.environ["ADMIN_HARD_DELETE_ENABLED"] = "1"
    assert _count(eng, tenant, cred_id) == 4
    resp = c.delete(
        f"/api/v1/credentials/{cred_id}/data?confirm=true",
        headers={"Authorization": "Bearer tokAdmin"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted_total"] >= 4
    assert _count(eng, tenant, cred_id) == 0
    # 审计行存在
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT operation, result, operator FROM store_operation_log "
            "WHERE tenant_id=:t AND credential_id::text=:c AND operation='hard_delete'"
        ), {"t": tenant, "c": cred_id}).fetchone()
    assert row is not None
    assert row[0] == "hard_delete" and row[1] == "success" and row[2] == "local_dev"
    # 凭证吊销记录保留(不删 credentials 行)
    with eng.connect() as conn:
        cred = conn.execute(text(
            "SELECT 1 FROM credentials WHERE id::text=:c"
        ), {"c": cred_id}).fetchone()
    assert cred is not None
    os.environ.pop("ADMIN_HARD_DELETE_ENABLED", None)

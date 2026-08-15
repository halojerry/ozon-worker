"""T5: 凭证 CRUD + validate 端点测试（真实 PG + mock Ozon API probe）。

验收门（docs/PLAN-webui-v1.md §5 T5）：
1. 响应 JSON 永不出现明文 api_key / ozon_api_key_enc（键名 + 明文值双重断言）
2. 租户隔离：A 看不到 B 的凭证（list/rotate/revoke/validate 均按 tenant_id 过滤）
3. validate 坏 key → valid:false + reason；Ozon API 异常 → valid:false
4. is_default=true 时同租户旧默认自动清（uq_credentials_default 唯一索引冲突处理）
5. 轮换：旧行 revoked + 新行 active + last_rotated_at 更新

需要本地 Docker PG（credentials 表已由 T1 建好）；PG 不可达时 skip。
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

# 32 字节 AES-256 主密钥（测试用）
MASTER_KEY = "0123456789abcdef0123456789abcdef"

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
        pytest.skip(f"PG 不可用（{exc}），跳过凭证 API 测试")
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """CREDENTIAL_MASTER_KEY + 鉴权 mock（rate_limiter 放行 + Supabase 按 key 分租户）。"""
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=FakeSupabase(TOKEN_MAP)):
        yield


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM credentials WHERE tenant_id IN ('tenant-a','tenant-b')"
        ))
    eng.dispose()


def _auth_headers(tenant: str) -> dict:
    token = "tokA" if tenant == "tenant-a" else "tokB"
    return {"Authorization": f"Bearer sk-{token}"}


def _create(client, tenant: str, payload: dict) -> dict:
    resp = client.post("/api/v1/credentials", json=payload, headers=_auth_headers(tenant))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _db_rows(sql: str, params: dict | None = None) -> list:
    eng = create_engine(DB_URL)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql), params or {}).fetchall()
    finally:
        eng.dispose()


# ============================================================
# 1. 掩码与明文防护
# ============================================================

def test_create_returns_masked_no_plaintext(client):
    body = _create(client, "tenant-a", {
        "ozon_client_id": "111", "api_key": "secret-key-1234", "shop_name": "店铺A",
    })
    assert body["api_key_masked"] == "****1234"
    raw = json.dumps(body)
    assert "secret-key-1234" not in raw, "响应泄漏明文 api_key"
    assert "ozon_api_key_enc" not in body
    assert "api_key" not in body
    # DB 存密文（BYTEA），非明文
    rows = _db_rows(
        "SELECT ozon_api_key_enc, api_key_masked FROM credentials "
        "WHERE tenant_id='tenant-a' AND ozon_client_id='111'"
    )
    assert b"secret-key-1234" not in bytes(rows[0][0])
    assert rows[0][1] == "****1234"


def test_list_returns_masked_only(client):
    _create(client, "tenant-a", {"ozon_client_id": "101", "api_key": "list-key-1010"})
    resp = client.get("/api/v1/credentials", headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200
    for item in resp.json():
        assert "api_key" not in item
        assert "ozon_api_key_enc" not in item
        assert item["api_key_masked"].startswith("****")


# ============================================================
# 2. 唯一性与默认店铺
# ============================================================

def test_create_duplicate_client_id_409(client):
    _create(client, "tenant-a", {"ozon_client_id": "222", "api_key": "key-one-2222"})
    resp = client.post("/api/v1/credentials",
                       json={"ozon_client_id": "222", "api_key": "key-two-2222"},
                       headers=_auth_headers("tenant-a"))
    assert resp.status_code == 409


def test_set_default_clears_old_default(client):
    _create(client, "tenant-a", {"ozon_client_id": "666", "api_key": "key-AAAA-6666", "is_default": True})
    _create(client, "tenant-a", {"ozon_client_id": "777", "api_key": "key-BBBB-7777", "is_default": True})
    rows = _db_rows(
        "SELECT ozon_client_id, is_default FROM credentials "
        "WHERE tenant_id='tenant-a' AND status='active'"
    )
    assert dict(rows) == {"666": False, "777": True}


# ============================================================
# 3. 租户隔离
# ============================================================

def test_list_tenant_isolation(client):
    _create(client, "tenant-a", {"ozon_client_id": "333", "api_key": "aaa-key-3333"})
    _create(client, "tenant-a", {"ozon_client_id": "444", "api_key": "bbb-key-4444"})
    _create(client, "tenant-b", {"ozon_client_id": "555", "api_key": "ccc-key-5555"})
    ra = client.get("/api/v1/credentials", headers=_auth_headers("tenant-a"))
    rb = client.get("/api/v1/credentials", headers=_auth_headers("tenant-b"))
    ids_a = {c["ozon_client_id"] for c in ra.json()}
    ids_b = {c["ozon_client_id"] for c in rb.json()}
    assert ids_a == {"333", "444"}
    assert ids_b == {"555"}
    assert ids_a.isdisjoint(ids_b), "A 看到了 B 的凭证"


def test_cross_tenant_operations_404(client):
    c = _create(client, "tenant-a", {"ozon_client_id": "1313", "api_key": "key-DDDD-1313"})
    rp = client.patch(f"/api/v1/credentials/{c['id']}", json={"api_key": "x"},
                      headers=_auth_headers("tenant-b"))
    rd = client.delete(f"/api/v1/credentials/{c['id']}", headers=_auth_headers("tenant-b"))
    rv = client.post(f"/api/v1/credentials/{c['id']}/validate", headers=_auth_headers("tenant-b"))
    assert rp.status_code == 404
    assert rd.status_code == 404
    assert rv.status_code == 404
    # A 的凭证未被 B 改动
    listed = client.get("/api/v1/credentials", headers=_auth_headers("tenant-a")).json()
    assert any(x["ozon_client_id"] == "1313" for x in listed)


# ============================================================
# 4. 轮换 / 吊销
# ============================================================

def test_rotate_revokes_old_creates_new(client):
    old = _create(client, "tenant-a", {
        "ozon_client_id": "888", "api_key": "old-key-8888", "is_default": True,
    })
    resp = client.patch(f"/api/v1/credentials/{old['id']}", json={"api_key": "new-key-9999"},
                        headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200, resp.text
    new = resp.json()
    assert new["api_key_masked"] == "****9999"
    assert new["ozon_client_id"] == "888"
    assert new["is_default"] is True  # 默认标记继承到新行
    assert new["last_rotated_at"] is not None
    rows = _db_rows(
        "SELECT status, api_key_masked, last_rotated_at FROM credentials "
        "WHERE tenant_id='tenant-a' AND ozon_client_id LIKE '888%' ORDER BY created_at"
    )
    assert len(rows) == 2, f"轮换应产生 2 行（旧 revoked + 新 active），实际 {rows}"
    assert rows[0][0] == "revoked" and rows[0][1] == "****8888"
    assert rows[1][0] == "active" and rows[1][1] == "****9999"
    assert rows[1][2] is not None, "新行 last_rotated_at 应已更新"


def test_revoke_soft_delete(client):
    c = _create(client, "tenant-a", {"ozon_client_id": "999", "api_key": "key-CCCC-9999"})
    resp = client.delete(f"/api/v1/credentials/{c['id']}", headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200
    listed = client.get("/api/v1/credentials", headers=_auth_headers("tenant-a")).json()
    assert all(x["ozon_client_id"] != "999" for x in listed)
    rows = _db_rows("SELECT status FROM credentials WHERE id::text=:id", {"id": c["id"]})
    assert rows[0][0] == "revoked"


# ============================================================
# 5. validate
# ============================================================

def test_validate_ok(client):
    c = _create(client, "tenant-a", {"ozon_client_id": "1010", "api_key": "valid-key-1010"})
    with patch("services.credential_service.ozon_post",
               return_value={"result": {"items": []}}) as m:
        resp = client.post(f"/api/v1/credentials/{c['id']}/validate",
                           headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["reason"] == "ok"
    # 服务端解密后拿明文调 Ozon probe（仅服务端内部，不落响应）
    assert m.call_args.args[0] == "1010"
    assert m.call_args.args[1] == "valid-key-1010"
    assert m.call_args.args[2] == "/v1/product/info/list"


def test_validate_bad_key(client):
    c = _create(client, "tenant-a", {"ozon_client_id": "1111", "api_key": "bad-key-1111"})
    resp_401 = requests.Response()
    resp_401.status_code = 401
    with patch("services.credential_service.ozon_post",
               side_effect=requests.HTTPError(response=resp_401)):
        resp = client.post(f"/api/v1/credentials/{c['id']}/validate",
                           headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["reason"] == "invalid_key"


def test_validate_ozon_error(client):
    c = _create(client, "tenant-a", {"ozon_client_id": "1212", "api_key": "any-key-1212"})
    with patch("services.credential_service.ozon_post",
               side_effect=requests.ConnectionError("boom")):
        resp = client.post(f"/api/v1/credentials/{c['id']}/validate",
                           headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["reason"] == "ozon_api_error"


# ============================================================
# 6. 鉴权
# ============================================================

def test_auth_required_401(client):
    assert client.get("/api/v1/credentials").status_code == 401
    assert client.post("/api/v1/credentials",
                       json={"ozon_client_id": "x", "api_key": "y"}).status_code == 401

"""PRD M3/M5b: source_candidates 落库/派生/上报 API 测试(真实 PG)。

覆盖:service upsert(新增/更新/缺字段跳过)、discover 回调派生(占位店)、
list_by_product(含占位店候选)、POST /api/v1/source-candidates(client_id 解析/
无店占位)、worker 上架回填(learning_record_node 顺带写)。
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

TENANT = main_mod._key_user_id("tokSrc")


class FakeTokensTable:
    def __init__(self):
        self._rows = [{"user_id": "tenant-src", "key": "tokSrc", "status": 1, "deleted_at": None}]
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
            filtered = [r for r in filtered if str(r.get(col)) == str(val)]
        return SimpleNamespace(data=filtered[:1])


class FakeSupabase:
    def table(self, name):
        if name == "tokens":
            return FakeTokensTable()
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM source_candidates WHERE tenant_id=:t"
        ), {"t": TENANT})
        conn.execute(text(
            "DELETE FROM credentials WHERE tenant_id=:t AND ozon_client_id LIKE 'src%'"
        ), {"t": TENANT})
        conn.execute(text(
            "INSERT INTO credentials (id, tenant_id, ozon_client_id, api_key_masked, "
            "ozon_api_key_enc, status) VALUES "
            "(:id, :t, :client, 'src-masked', :enc, 'active')"
        ), {
            "id": str(uuid.uuid4()), "t": TENANT, "client": "src-store-1",
            "enc": b"placeholder",
        })
    os.environ["STORE_SYNC_JOBS_ENABLED"] = "1"
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM source_candidates WHERE tenant_id=:t"
        ), {"t": TENANT})


def _store_id(eng) -> str:
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT id::text FROM credentials WHERE tenant_id=:t AND ozon_client_id='src-store-1'"
        ), {"t": TENANT}).fetchone()
    return str(row[0])


def test_service_upsert_and_list(client):
    from services.source_candidate_service import (
        list_by_product,
        upsert_source_candidates,
    )
    eng = create_engine(DB_URL)
    store = _store_id(eng)
    r = upsert_source_candidates(TENANT, store, [{
        "product_id": "p-src-1",
        "source_url": "https://detail.1688.com/offer/111222333.html",
        "price_cny": 12.5,
        "match_score": 0.9,
        "match_method": "aibuy",
    }])
    assert r["inserted"] == 1
    r2 = upsert_source_candidates(TENANT, store, [{
        "product_id": "p-src-1",
        "source_url": "https://detail.1688.com/offer/111222333.html",
        "price_cny": 11.0,
        "match_score": 0.95,
        "match_method": "cdp",
    }])
    assert r2["updated"] == 1
    items = list_by_product(TENANT, store, "p-src-1")
    assert len(items) == 1
    assert items[0]["match_method"] == "cdp"
    assert items[0]["price_cny"] == 11.0


def test_derive_from_discovery_run_uses_placeholder(client):
    from services.source_candidate_service import (
        NO_STORE_CREDENTIAL,
        derive_from_discovery_run,
        list_by_product,
    )
    eng = create_engine(DB_URL)
    store = _store_id(eng)
    n = derive_from_discovery_run(TENANT, [
        {"ozon_product_id": "p-disc-1", "status": "profitable",
         "match_1688_url": "https://detail.1688.com/offer/444555666.html",
         "match_1688_price": 8.8, "profit_margin": 0.35},
        {"ozon_product_id": "p-disc-2", "status": "no_match"},
        {"ozon_product_id": "p-disc-3", "status": "ok",
         "match_1688_url": "https://detail.1688.com/offer/777888999.html"},
    ])
    assert n == 2
    items = list_by_product(TENANT, store, "p-disc-1")
    assert len(items) == 1
    assert items[0]["source_url"].endswith("444555666.html")
    assert items[0]["match_method"] == "discovery"
    # 无店绑定时占位店可查
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(*) FROM source_candidates "
            "WHERE tenant_id=:t AND credential_id::text=:c AND product_id='p-disc-3'"
        ), {"t": TENANT, "c": NO_STORE_CREDENTIAL}).fetchone()
    assert int(row[0]) == 1


def test_report_endpoint_with_client_id(client):
    eng = create_engine(DB_URL)
    store = _store_id(eng)
    resp = client.post("/api/v1/source-candidates", json={
        "token": "tokSrc",
        "client_id": "src-store-1",
        "candidates": [{
            "product_id": "p-api-1",
            "source_url": "https://detail.1688.com/offer/123123123.html",
            "price_cny": 5.5,
            "match_method": "ak",
        }],
    })
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 1
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT credential_id::text FROM source_candidates "
            "WHERE tenant_id=:t AND product_id='p-api-1'"
        ), {"t": TENANT}).fetchone()
    assert str(row[0]) == store


def test_report_endpoint_no_store_placeholder(client):
    resp = client.post("/api/v1/source-candidates", json={
        "token": "tokSrc",
        "candidates": [{
            "product_id": "p-api-2",
            "source_url": "https://detail.1688.com/offer/999888777.html",
            "match_method": "image",
        }],
    })
    assert resp.status_code == 200
    from services.source_candidate_service import NO_STORE_CREDENTIAL
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT credential_id::text FROM source_candidates "
            "WHERE tenant_id=:t AND product_id='p-api-2'"
        ), {"t": TENANT}).fetchone()
    assert str(row[0]) == NO_STORE_CREDENTIAL


def test_report_endpoint_rejects_empty(client):
    resp = client.post("/api/v1/source-candidates", json={
        "token": "tokSrc",
        "candidates": [],
    })
    assert resp.status_code == 400


def test_get_product_source_candidates_endpoint(client):
    """GET /products/{id}/source-candidates:归属校验 + 候选列表(含占位店)。"""
    eng = create_engine(DB_URL)
    store = _store_id(eng)
    client.post("/api/v1/source-candidates", json={
        "token": "tokSrc",
        "client_id": "src-store-1",
        "candidates": [{
            "product_id": "p-get-1",
            "source_url": "https://detail.1688.com/offer/555666777.html",
            "price_cny": 6.6,
            "match_score": 0.8,
            "match_method": "keyword",
        }],
    })
    resp = client.get(
        f"/api/v1/products/p-get-1/source-candidates?credential_id={store}",
        headers={"Authorization": "Bearer tokSrc"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["match_method"] == "keyword"
    assert items[0]["price_cny"] == 6.6

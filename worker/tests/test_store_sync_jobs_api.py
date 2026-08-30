"""PRD M1: 任务化同步 API 测试(特性开关=1,真实 PG,后台调度停用)。

覆盖:绑定即入队 initial、POST /sync → 202 + job 去重、sync-jobs 历史、
sync-job 详情、sync-status 扩展字段、读取空缓存返回 never 且不触达 Ozon。
"""
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
MASTER_KEY = "0123456789abcdef0123456789abcdef"

os.environ["CREDENTIAL_MASTER_KEY"] = MASTER_KEY
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"

import main as main_mod  # noqa: E402

# 当前鉴权仍为 key 派生租户(M2 账号级身份改造前);tokens user_id 仅供 analytics
TENANT_J = main_mod._key_user_id("tokJ")


class FakeTokensTable:
    def __init__(self):
        self._rows = [{"user_id": "tenant-j", "key": "tokJ", "status": 1, "deleted_at": None}]
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
    _prev_sync_env = os.environ.get("STORE_SYNC_JOBS_ENABLED")
    os.environ["STORE_SYNC_JOBS_ENABLED"] = "1"
    try:
        eng = create_engine(DB_URL)
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    os.environ["STORE_SYNC_JOBS_ENABLED"] = "1"
    _cleanup()
    with patch("main.get_supabase_client", return_value=FakeSupabase()):
        with patch("services.store_sync_scheduler._dispatch_once",
                   new=async_noop()):
            with TestClient(main_mod.app) as c:
                yield c
    _cleanup()
    if _prev_sync_env is None:
        os.environ.pop("STORE_SYNC_JOBS_ENABLED", None)
    else:
        os.environ["STORE_SYNC_JOBS_ENABLED"] = _prev_sync_env


def async_noop():
    async def _noop():
        return None
    return _noop


def _auth_headers() -> dict:
    return {"Authorization": "Bearer sk-tokJ"}


def _db(sql: str, params: dict | None = None) -> list:
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


def _cleanup() -> None:
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        for t in ("store_sync_jobs", "credential_sync_state",
                  "ozon_orders_cache", "ozon_products_cache"):
            conn.execute(text(f"DELETE FROM {t}"))
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": TENANT_J})


def _create_cred(client) -> dict:
    resp = client.post("/api/v1/credentials", json={
        "ozon_client_id": f"7777{uuid.uuid4().int % 10**4}",
        "api_key": "k-job-001", "shop_name": "任务店",
    }, headers=_auth_headers())
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_bind_enqueues_initial_job(client):
    c = _create_cred(client)
    rows = _db("SELECT kind, trigger, status FROM store_sync_jobs WHERE tenant_id=:t",
               {"t": TENANT_J})
    assert len(rows) == 1
    assert rows[0][0] == "initial" and rows[0][1] == "bind" and rows[0][2] == "pending"


def test_manual_sync_returns_202_and_dedupe(client):
    c = _create_cred(client)
    r1 = client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers())
    assert r1.status_code == 202
    j1 = r1.json()
    assert j1["status"] == "pending"
    # 同店在途 job 去重:再次手动同步返回同一 job
    r2 = client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers())
    assert r2.status_code == 202
    assert r2.json()["job_id"] == j1["job_id"]
    # 历史 + 详情
    hist = client.get(f"/api/v1/stores/{c['id']}/sync-jobs", headers=_auth_headers())
    assert hist.status_code == 200
    assert hist.json()["total"] >= 1
    detail = client.get(f"/api/v1/sync-jobs/{j1['job_id']}", headers=_auth_headers())
    assert detail.status_code == 200
    assert detail.json()["id"] == j1["job_id"]


def test_sync_status_extended(client):
    c = _create_cred(client)
    st = client.get(f"/api/v1/stores/{c['id']}/sync-status", headers=_auth_headers())
    assert st.status_code == 200
    body = st.json()
    for key in ("sync_enabled", "sync_interval_minutes", "sync_products_interval_minutes",
                "current_job", "is_stale", "last_success_at", "consecutive_failures"):
        assert key in body, f"sync-status 缺字段 {key}"
    assert body["sync_enabled"] is True
    assert body["current_job"] is not None and body["current_job"]["status"] == "pending"


def test_read_empty_cache_never_no_ozon(client):
    c = _create_cred(client)
    with patch("utils.ozon_client.ozon_post",
               side_effect=AssertionError("读取空缓存不应触达 Ozon")):
        r = client.get(f"/api/v1/orders?credential_id={c['id']}", headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    # 绑定后 initial job 在途 → 显示 syncing(核心:读缓存不触达 Ozon、不阻塞)
    assert body["sync_status"] == "syncing"
    assert body["last_synced_at"] is None
    # 店铺统计同样不触发同步
    with patch("utils.ozon_client.ozon_post",
               side_effect=AssertionError("读取统计不应触达 Ozon")):
        st = client.get(f"/api/v1/stores/{c['id']}/stats", headers=_auth_headers())
    assert st.status_code == 200
    assert st.json()["data_freshness"]["is_stale"] is False


def test_sync_config_update(client):
    c = _create_cred(client)
    r = client.patch(f"/api/v1/stores/{c['id']}/sync-config", json={
        "sync_enabled": False,
        "sync_interval_minutes": 30,
        "sync_products_interval_minutes": 60,
    }, headers=_auth_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sync_enabled"] is False
    assert body["sync_interval_minutes"] == 30
    assert body["sync_products_interval_minutes"] == 60
    # 同步状态联动
    st = client.get(f"/api/v1/stores/{c['id']}/sync-status", headers=_auth_headers())
    assert st.json()["sync_enabled"] is False
    # 间隔下限校验(免 api_key)
    bad = client.patch(f"/api/v1/stores/{c['id']}/sync-config", json={
        "sync_interval_minutes": 1,
    }, headers=_auth_headers())
    assert bad.status_code == 422


def test_sync_all_and_cooldown(client):
    c1 = _create_cred(client)
    c2 = _create_cred(client)
    r = client.post("/api/v1/stores/sync-all", headers=_auth_headers())
    assert r.status_code == 200, r.text
    # 模块内凭证会跨用例累积 → 断言相对计数(至少覆盖本用例新建的 2 个)
    assert r.json()["enqueued"] >= 2
    assert len(r.json()["job_ids"]) == r.json()["enqueued"]
    # 60s 冷却
    r2 = client.post("/api/v1/stores/sync-all", headers=_auth_headers())
    assert r2.status_code == 429


def test_domain_read_endpoints(client):
    c = _create_cred(client)
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO ozon_returns_cache
                (tenant_id, credential_id, return_id, posting_number, order_id, return_type,
                 schema, reason, compensation_status, status)
            VALUES (:t, :c, 9001, 'PN-R1', 'O1', 'fbs', 's', '不想要了', 'compensated', 'in_progress')
            """
        ), {"t": TENANT_J, "c": uuid.UUID(c["id"])})
        conn.execute(text(
            """
            INSERT INTO warehouse_cache (tenant_id, credential_id, warehouse_id, name, is_rfbs)
            VALUES (:t, :c, 1020005025772440, 'viola', FALSE)
            """
        ), {"t": TENANT_J, "c": uuid.UUID(c["id"])})
        conn.execute(text(
            """
            INSERT INTO ozon_store_analytics_daily (tenant_id, credential_id, stat_date, metric, value)
            VALUES (:t, :c, CURRENT_DATE, 'orders_count', 5)
            """
        ), {"t": TENANT_J, "c": uuid.UUID(c["id"])})
        conn.execute(text(
            "UPDATE credentials SET is_default=TRUE WHERE tenant_id=:t AND id::text=:c"
        ), {"t": TENANT_J, "c": c["id"]})
    r = client.get(f"/api/v1/stores/{c['id']}/returns", headers=_auth_headers())
    assert r.status_code == 200 and r.json()["total"] == 1
    assert r.json()["items"][0]["posting_number"] == "PN-R1"
    a = client.get(f"/api/v1/stores/{c['id']}/analytics-daily?days=7", headers=_auth_headers())
    assert a.status_code == 200 and a.json()["items"][0]["metric"] == "orders_count"
    w = client.get("/api/v1/stores/warehouses", headers=_auth_headers())
    assert w.status_code == 200
    assert len(w.json()["items"]) == 1 and w.json()["items"][0]["name"] == "viola"


def test_product_source_endpoints(client):
    c = _create_cred(client)
    r = client.patch(f"/api/v1/products/PROD-1/source", json={
        "credential_id": c["id"],
        "purchase_url": "https://1688.example/o1",
        "purchase_cost": 12.5,
        "supplier": "1688店铺",
    }, headers=_auth_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["purchase_cost"]) == 12.5
    assert body["cost_source"] == "manual"
    g = client.get(f"/api/v1/products/PROD-1/cost?credential_id={c['id']}", headers=_auth_headers())
    assert g.status_code == 200
    assert g.json()["history"] == []
    # 跨租户归属校验 → 404
    bad = client.patch("/api/v1/products/PROD-1/source", json={
        "credential_id": "00000000-0000-0000-0000-000000000000",
        "purchase_cost": 1.0,
    }, headers=_auth_headers())
    assert bad.status_code == 404


def test_admin_sync_health_and_daily_metrics(client):
    c = _create_cred(client)
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, orders_last_synced_at, products_last_synced_at,
                 orders_error, products_error, last_success_at, consecutive_failures)
            VALUES (:t, :c, NOW(), NOW(), '', '', NOW(), 0)
            """
        ), {"t": TENANT_J, "c": uuid.UUID(c["id"])})
        conn.execute(text(
            """
            INSERT INTO store_daily_metrics
                (tenant_id, credential_id, store_id, stat_date, order_count, sales_amount,
                 product_count)
            VALUES (:t, :c, :s, CURRENT_DATE, 3, 300.0, 5)
            """
        ), {"t": TENANT_J, "c": uuid.UUID(c["id"]), "s": c["id"]})
    with patch("services.admin_service.is_admin_user", return_value=True):
        r = client.get("/api/v1/admin/sync-health", headers=_auth_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["total"] >= 1
        assert body["items"][0]["ozon_client_id"]
    # 非 admin → 403
    assert client.get("/api/v1/admin/sync-health", headers=_auth_headers()).status_code == 403
    # 日聚合指标端点
    d = client.get(f"/api/v1/stores/{c['id']}/daily-metrics?days=7", headers=_auth_headers())
    assert d.status_code == 200
    assert d.json()["items"][0]["order_count"] == 3

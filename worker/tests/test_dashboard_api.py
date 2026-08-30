"""工作台聚合端点测试(真实 PG):今日/趋势/热销/最近订单/租户隔离。"""
import os
import sys
import uuid
from pathlib import Path

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
os.environ["SKIP_STORE_SYNC"] = "1"  # 本文件不测同步,避免调度器捡到存量店阻塞 shutdown

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokDash")
CRED = str(uuid.uuid4())


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    _seed(eng)
    with TestClient(main_mod.app) as c:
        yield c
    _cleanup(eng)


def _seed(eng):
    with eng.begin() as conn:
        # 清掉历史残留 pending/running 任务(防 task_processor 启动即执行 → shutdown 排空 5min)
        conn.execute(text(
            "DELETE FROM ozon_product_tasks WHERE status IN ('pending','running') "
            "AND created_at < NOW() - INTERVAL '30 seconds'"
        ))
        conn.execute(text("DELETE FROM ozon_orders_cache WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM ozon_products_cache WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM store_daily_metrics WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM credential_sync_state WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text(
            "DELETE FROM credentials WHERE tenant_id=:t AND ozon_client_id='dash-1'"
        ), {"t": TENANT})
        conn.execute(text(
            "INSERT INTO credentials (id, tenant_id, ozon_client_id, api_key_masked, "
            "ozon_api_key_enc, status) VALUES (:id, :t, 'dash-1', 'm', :enc, 'active')"
        ), {"id": CRED, "t": TENANT, "enc": b"x"})
        conn.execute(text(
            "INSERT INTO store_daily_metrics (tenant_id, credential_id, store_id, stat_date, "
            "order_count, sales_amount, commission_amount, profit_amount, product_count) "
            "VALUES (:t, :c, 's1', CURRENT_DATE, 3, 1500, 150, 300, 2), "
            "(:t, :c, 's1', CURRENT_DATE - 1, 5, 2500, 250, 500, 2)"
        ), {"t": TENANT, "c": CRED})
        conn.execute(text(
            "INSERT INTO ozon_products_cache (tenant_id, credential_id, product_id, offer_id, "
            "name, image, price, stock, currency, archived, status) VALUES "
            "(:t, :c, 'p-1', 'o-1', '热销商品A', '', 100, 5, 'RUB', false, ''), "
            "(:t, :c, 'p-2', 'o-2', '归档商品', '', 50, 1, 'RUB', true, ''), "
            "(:t, :c, 'p-3', 'o-3', '错误商品', '', 30, 0, 'RUB', false, 'error')"
        ), {"t": TENANT, "c": CRED})
        conn.execute(text(
            "INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status, "
            "raw_status, products, product_count, total_amount, warehouse, delivery_method, "
            "cancel_reason, cancellation) VALUES "
            "(:t, :c, 'ord-1', 'delivered', 'delivered', "
            "'[{\"product_id\":\"p-1\",\"name\":\"热销商品A\",\"quantity\":\"10\"}]', 1, 999, "
            "'w', 'fbs', '', '')"
        ), {"t": TENANT, "c": CRED})
        conn.execute(text(
            "INSERT INTO credential_sync_state "
            "(tenant_id, credential_id, orders_error, products_error, last_success_at) "
            "VALUES (:t, :c, '', '', NOW())"
        ), {"t": TENANT, "c": CRED})
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) "
            "VALUES (:id, :t, 'pending_moderation', '{}')"
        ), {"id": str(uuid.uuid4()), "t": TENANT})


def _cleanup(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM ozon_product_tasks WHERE tenant_id=:t"
        ), {"t": TENANT})
        conn.execute(text("DELETE FROM credential_sync_state WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM ozon_orders_cache WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM ozon_products_cache WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM store_daily_metrics WHERE tenant_id=:t"), {"t": TENANT})
        conn.execute(text("DELETE FROM credentials WHERE id=:c"), {"c": CRED})


def test_dashboard_overview(client):
    resp = client.get("/api/v1/dashboard/overview", headers={"Authorization": "Bearer tokDash"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["today"]["orders_count"] == 3
    assert data["today"]["sales_amount"] == 1500.0
    assert data["today"]["profit_amount"] == 300.0
    assert data["active_products"] == 1  # 只算未归档且非 error
    assert data["pending_tasks"] == 1
    assert data["store_count"] == 1
    assert len(data["trend"]) == 2
    assert data["trend"][-1]["orders"] == 3
    assert data["hot_products"][0]["name"] == "热销商品A"
    assert data["hot_products"][0]["quantity"] == 10
    assert data["latest_orders"][0]["posting_number"] == "ord-1"
    assert data["last_synced_at"] is not None


def test_dashboard_tenant_isolation(client):
    other = main_mod._key_user_id("tokDashOther")
    resp = client.get("/api/v1/dashboard/overview", headers={"Authorization": "Bearer tokDashOther"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["today"]["orders_count"] == 0
    assert data["active_products"] == 0
    assert data["latest_orders"] == []

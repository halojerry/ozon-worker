"""PRD M3: 成本货源域测试 — product_costs 优先级/历史、订单 real_profit、重算回填。"""
import datetime
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import product_cost_service, store_sync_service  # noqa: E402
from utils.credential_cipher import encrypt  # noqa: E402

os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")


@pytest.fixture()
def cred():
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    client_id = f"5{uuid.uuid4().int % 10**7}"
    enc_key = encrypt("test-api-key", f"{tenant}:{client_id}")
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        row = conn.execute(text(
            """
            INSERT INTO credentials (tenant_id, ozon_client_id, ozon_api_key_enc, api_key_masked,
                                     status, sync_enabled)
            VALUES (:t, :c, :enc, '****', 'active', TRUE)
            RETURNING id::text
            """
        ), {"t": tenant, "c": client_id, "enc": enc_key}).fetchone()
        conn.execute(text(
            "INSERT INTO fx_rates (date, cny_to_rub, source) VALUES (CURRENT_DATE, 12.0, 'test') "
            "ON CONFLICT (date) DO UPDATE SET cny_to_rub=12.0"
        ))
    cid = str(row[0])
    yield tenant, cid
    with eng.begin() as conn:
        for t in ("ozon_orders_cache", "ozon_products_cache", "credential_sync_state",
                  "store_metrics_history", "store_sync_jobs", "order_line_costs",
                  "product_costs", "product_cost_history"):
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": tenant})


def _orders_mock(posting_a_b, posting_a):
    def _mk(pn, lines):
        return {
            "posting_number": pn, "status": "delivered",
            "in_process_at": "2026-08-01T00:00:00Z",
            "products": lines,
            "financial_data": {"products": [
                {"price": 100.0, "quantity": 1, "product_id": 1,
                 "commission": {"amount": 10.0}},
                {"price": 50.0, "quantity": 1, "product_id": 2,
                 "commission": {"amount": 5.0}},
            ] if len(lines) > 1 else [
                {"price": 100.0, "quantity": 1, "product_id": 1,
                 "commission": {"amount": 10.0}},
            ], "services": []},
            "analytics_data": {}, "delivery_method": {},
        }

    def _handler(client_id, api_key, path, body=None, **kw):
        if path == "/v4/posting/fbs/list":
            postings = []
            if posting_a_b:
                postings.append(_mk("PN-AB", [
                    {"name": "A", "sku": 1, "quantity": 1, "price": {"amount": "100.00"}, "offer_id": "o1"},
                    {"name": "B", "sku": 2, "quantity": 1, "price": {"amount": "50.00"}, "offer_id": "o2"},
                ]))
            if posting_a:
                postings.append(_mk("PN-A", [
                    {"name": "A", "sku": 1, "quantity": 1, "price": {"amount": "100.00"}, "offer_id": "o1"},
                ]))
            return {"has_next": False, "cursor": "", "postings": postings}
        if path == "/v3/product/list":
            return {"result": {"total": 0, "items": []}}
        if path == "/v3/product/info/list":
            return {"items": [
                {"id": 1, "images": ["http://img/1.jpg"]},
                {"id": 2, "images": ["http://img/2.jpg"]},
            ]}
        return {"result": {}}
    return _handler


def test_order_real_profit_cost_and_missing(cred):
    tenant, cid = cred
    with create_engine(DB_URL).begin() as conn:
        conn.execute(text(
            "INSERT INTO product_costs (tenant_id, credential_id, product_id, offer_id, "
            "purchase_url, purchase_cost, cost_source) "
            "VALUES (:t, :c, '1', 'o1', 'https://1688/a', 5.0, 'envelope')"
        ), {"t": tenant, "c": cid})
    with patch("utils.ozon_client.ozon_post",
               side_effect=_orders_mock(posting_a_b=True, posting_a=True)):
        store_sync_service.sync_store(tenant, cid)
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        ab = conn.execute(text(
            "SELECT real_profit FROM ozon_orders_cache WHERE posting_number='PN-AB'"
        )).fetchone()
        a = conn.execute(text(
            "SELECT real_profit FROM ozon_orders_cache WHERE posting_number='PN-A'"
        )).fetchone()
        line = conn.execute(text(
            "SELECT source_cost, fx_rate, revenue_rub, cost_version FROM order_line_costs "
            "WHERE posting_number='PN-A' AND product_id='1'"
        )).fetchone()
    assert ab[0] is None, "缺成本行 → real_profit 必须 NULL(不编造)"
    # PN-A: 100 - 10(佣金) - 5*12(成本) = 30
    assert float(a[0]) == 30.0
    assert float(line[0]) == 5.0 and float(line[1]) == 12.0
    assert float(line[2]) == 100.0 and int(line[3]) == 1


def test_manual_override_and_recalc(cred):
    tenant, cid = cred
    with create_engine(DB_URL).begin() as conn:
        conn.execute(text(
            "INSERT INTO product_costs (tenant_id, credential_id, product_id, offer_id, "
            "purchase_url, purchase_cost, cost_source) "
            "VALUES (:t, :c, '1', 'o1', 'https://1688/a', 5.0, 'envelope')"
        ), {"t": tenant, "c": cid})
    with patch("utils.ozon_client.ozon_post",
               side_effect=_orders_mock(posting_a_b=False, posting_a=True)):
        store_sync_service.sync_store(tenant, cid)
    # manual 覆盖成本 → 重算订单
    product_cost_service.upsert_manual(
        tenant, cid, "1", "o1", purchase_url="https://1688/a2",
        purchase_cost=8.0, supplier="店X")
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT purchase_cost, cost_source, purchase_url, supplier FROM product_costs "
            "WHERE tenant_id=:t AND product_id='1'"), {"t": tenant}).fetchone()
        assert float(row[0]) == 8.0 and row[1] == "manual"
        assert row[2] == "https://1688/a2" and row[3] == "店X"
        # 100 - 10(佣金) - 8*12(成本) = -6
        assert float(conn.execute(text(
            "SELECT real_profit FROM ozon_orders_cache WHERE posting_number='PN-A'"
        )).fetchone()[0]) == -6.0
        hist = conn.execute(text(
            "SELECT old_cost, new_cost FROM product_cost_history WHERE tenant_id=:t"),
            {"t": tenant}).fetchall()
        assert any(float(h[0]) == 5.0 and float(h[1]) == 8.0 for h in hist)
        # envelope 后补不得覆盖 manual
        product_cost_service.upsert_from_envelope(
            tenant, cid, "1", "o1",
            {"draft": {"purchase_cost": 3.0}, "source": {"purchase_url": "https://1688/x"}})
        assert float(conn.execute(text(
            "SELECT purchase_cost FROM product_costs WHERE tenant_id=:t AND product_id='1'"),
            {"t": tenant}).fetchone()[0]) == 8.0

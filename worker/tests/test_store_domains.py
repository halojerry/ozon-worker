"""PRD M3: 五域同步测试(退货/促销/仓库/分析/评分)真实 PG + mock Ozon。

覆盖:全域落盘(ozon_returns_cache/warehouse_cache/ozon_store_analytics_daily/
credentials.rating + domain_state)、快照 active_discount_count 真值、域水位节流。
"""
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

from services import store_sync_service  # noqa: E402
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
    client_id = f"6{uuid.uuid4().int % 10**7}"
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
    cid = str(row[0])
    yield tenant, cid
    with eng.begin() as conn:
        for t in ("ozon_orders_cache", "ozon_products_cache", "credential_sync_state",
                  "store_metrics_history", "store_sync_jobs", "ozon_returns_cache",
                  "warehouse_cache", "ozon_store_analytics_daily"):
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": tenant})


def _domain_mock(calls=None):
    """mock:orders/products 空;五域返回可解析结构。"""
    calls = calls if calls is not None else []

    def _handler(client_id, api_key, path, body=None, **kw):
        calls.append(path)
        if path == "/v4/posting/fbs/list":
            return {"has_next": False, "cursor": "", "postings": []}
        if path == "/v3/product/list":
            return {"result": {"total": 0, "items": []}}
        if path == "/v1/returns/list":
            return {"returns": [
                {"id": 9001, "posting_number": "PN-R1", "order_id": "O1",
                 "type": "fbs", "schema": "return_schema", "return_reason_name": "不想要了",
                 "compensation_status": "compensated", "product": {"name": "Товар"},
                 "status": "in_progress"},
            ], "has_next": False}
        if path == "/v1/actions":
            return {"result": {"actions": [{"action_id": 1}, {"action_id": 2}]}}
        if path == "/v2/warehouse/list":
            return {"warehouses": [{"warehouse_id": 1020005025772440, "name": "viola", "is_rfbs": False}]}
        if path == "/v1/analytics/data":
            return {"result": {"data": [
                {"dimensions": [{"id": "2026-08-29", "name": ""}], "metrics": [10, 2, 1, 50]},
            ], "totals": []}}
        if path == "/v1/rating/summary":
            return {"groups": [
                {"group_name": "Оценка продавца", "items": [
                    {"name": "Оценка товаров", "current_value": 4.7, "status": "OK"}]},
            ], "localization_index": 92.5}
        return {"result": {}}
    return _handler


def test_domains_sync_and_persist(cred):
    tenant, cid = cred
    with patch("utils.ozon_client.ozon_post", side_effect=_domain_mock()):
        r = store_sync_service.sync_store(tenant, cid, force_domains=True)
    assert r["returns"]["synced"] == 1
    assert r["actions"]["count"] == 2
    assert r["warehouse"]["synced"] == 1
    assert r["analytics"]["synced"] == 4
    assert r["rating"]["rating"] == 4.7
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM ozon_returns_cache WHERE tenant_id=:t"), {"t": tenant}).scalar() == 1
        assert conn.execute(text(
            "SELECT COUNT(*) FROM warehouse_cache WHERE tenant_id=:t"), {"t": tenant}).scalar() == 1
        assert conn.execute(text(
            "SELECT COUNT(*) FROM ozon_store_analytics_daily WHERE tenant_id=:t"), {"t": tenant}).scalar() == 4
        rt = conn.execute(text(
            "SELECT rating_total, rating_localization_index FROM credentials WHERE tenant_id=:t"),
            {"t": tenant}).fetchone()
        assert float(rt[0]) == 4.7 and float(rt[1]) == 92.5
        # 快照 active_discount_count = 促销真值
        snap = conn.execute(text(
            "SELECT active_discount_count FROM store_metrics_history WHERE tenant_id=:t"),
            {"t": tenant}).fetchone()
        assert int(snap[0]) == 2


def test_domains_throttled_by_watermark(cred):
    tenant, cid = cred
    with patch("utils.ozon_client.ozon_post", side_effect=_domain_mock()):
        store_sync_service.sync_store(tenant, cid, force_domains=True)

    def _throttle_mock(calls):
        def _handler(client_id, api_key, path, body=None, **kw):
            calls.append(path)
            if path in ("/v1/returns/list", "/v1/actions", "/v2/warehouse/list",
                        "/v1/analytics/data", "/v1/rating/summary"):
                raise AssertionError(f"域 {path} 未节流")
            if path == "/v4/posting/fbs/list":
                return {"has_next": False, "postings": []}
            if path == "/v3/product/list":
                return {"result": {"total": 0, "items": []}}
            return {"result": {}}
        return _handler

    calls = []
    with patch("utils.ozon_client.ozon_post", side_effect=_throttle_mock(calls)):
        store_sync_service.sync_store(tenant, cid, force_domains=False)
    assert not any(p in calls for p in (
        "/v1/returns/list", "/v1/actions", "/v2/warehouse/list",
        "/v1/analytics/data", "/v1/rating/summary"))

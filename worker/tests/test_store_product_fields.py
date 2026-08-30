"""PRD M1: 商品域扩列测试 — 真实 Ozon 响应形态(M0 探针冻结)。

覆盖:info/list 顶层 items + id 字段;price/old_price/min_price 字符串与空串;
is_archived/is_autoarchived → archived/archived_at;errors → status='error' + error jsonb。
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
    client_id = f"8{uuid.uuid4().int % 10**7}"
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
                  "store_metrics_history", "store_sync_jobs"):
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": tenant})


def _ozon_real_shape(items):
    def _handler(client_id, api_key, path, body=None, **kw):
        if path == "/v4/posting/fbs/list":
            return {"has_next": False, "cursor": "", "postings": []}
        if path == "/v3/product/list":
            return {"result": {"total": len(items), "items": [
                {"product_id": it["id"], "offer_id": it["offer_id"]} for it in items]}}
        if path == "/v3/product/info/list":
            return {"items": items}  # M0 实测:顶层 items
        return {"result": {}}
    return _handler


def test_product_tier_prices_and_archived(cred):
    tenant, cid = cred
    items = [{
        "id": 5811470489,
        "offer_id": "1048104854989_8",
        "name": "Товар 1",
        "images": ["http://img/1.jpg"],
        "price": "55.00",            # 字符串
        "old_price": "62.00",
        "min_price": "",             # 空串
        "stocks": {"present": 7},
        "is_archived": True,
        "is_autoarchived": False,
        "errors": [{"code": 123, "message": "test error", "state": "test"}],
        "statuses": {"status": "price_sent", "moderate_status": "approved"},
    }]
    with patch("utils.ozon_client.ozon_post", side_effect=_ozon_real_shape(items)):
        store_sync_service.sync_store(tenant, cid)
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT price, old_price, min_price, archived, status, error, archived_at "
            "FROM ozon_products_cache WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant, "c": cid}).fetchone()
    assert float(row[0]) == 55.0
    assert float(row[1]) == 62.0
    assert row[2] is None          # 空串 min_price → NULL
    assert row[3] is True          # is_archived 权威字段
    assert row[4] == "archived"    # archived 优先于 error
    assert row[5] is not None and row[5][0]["message"] == "test error"
    assert row[6] is not None      # archived_at 落库


def test_product_error_status_when_not_archived(cred):
    tenant, cid = cred
    items = [{
        "id": 5811610712,
        "offer_id": "o-err",
        "name": "Товар 2",
        "price": "120.00",
        "is_archived": False,
        "is_autoarchived": False,
        "errors": [{"code": 404, "message": "bad attribute", "state": "new"}],
    }]
    with patch("utils.ozon_client.ozon_post", side_effect=_ozon_real_shape(items)):
        store_sync_service.sync_store(tenant, cid)
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT archived, status, error FROM ozon_products_cache WHERE tenant_id=:t"
        ), {"t": tenant}).fetchone()
    assert row[0] is False
    assert row[1] == "error"
    assert row[2] is not None


def test_product_source_filter_matched_unmatched(cred):
    """货源筛选:source=unmatched/matched 过滤 product_costs 有无行(工作台「未匹配货源」)。"""
    tenant, cid = cred
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO ozon_products_cache (tenant_id, credential_id, product_id, offer_id, "
            "name, image, price, stock, currency, archived, status) VALUES "
            "(:t, :c, 'src-p-1', 'o-1', '有货源', '', 100, 1, 'RUB', false, ''), "
            "(:t, :c, 'src-p-2', 'o-2', '无货源', '', 50, 1, 'RUB', false, '')"
        ), {"t": tenant, "c": cid})
        conn.execute(text(
            "INSERT INTO product_costs (tenant_id, credential_id, product_id, offer_id, "
            "purchase_url, purchase_cost, currency, cost_source) VALUES "
            "(:t, :c, 'src-p-1', 'o-1', 'https://detail.1688.com/offer/1.html', 10, 'CNY', 'manual')"
        ), {"t": tenant, "c": cid})
    with patch("services.credential_service.get_decrypted", return_value=("999", "x")), \
         patch("services.store_sync_service._needs_products_sync", return_value=False), \
         patch("services.store_sync_service._jobs_enabled", return_value=True):
        unmatched = store_sync_service.list_cached_products(
            tenant, cid, source="unmatched", lazy_sync=False)
        matched = store_sync_service.list_cached_products(
            tenant, cid, source="matched", lazy_sync=False)
    assert [i["product_id"] for i in unmatched["items"]] == ["src-p-2"]
    assert [i["product_id"] for i in matched["items"]] == ["src-p-1"]
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_costs WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant, "c": cid})

"""P1a: 在线商品批量操作测试（mock ozon_post 请求体断言）。

验收门（docs/PRD-product-bulk-v0.52.md §四）：
1. bulk-prices：请求体 prices 数组透传 + 成功 + 无默认 400 + 502
2. bulk-stocks：请求体 stocks 透传
3. bulk-archive：archive=true → /archive；false → /unarchive；product_id 转 int
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import shelf_service

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
MASTER_KEY = "0123456789abcdef0123456789abcdef"
TENANT = "tenant-A"


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过批量操作测试")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


@pytest.fixture(autouse=True)
def _cleanup(_pg):
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": TENANT})
    eng.dispose()


def _store_default_credential() -> str:
    from services import credential_service
    cred_id = credential_service.store_credential(TENANT, "222222", "key-2")
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("UPDATE credentials SET is_default=true WHERE id=:id"), {"id": cred_id})
    eng.dispose()
    return cred_id


def _fake_ozon(result=None, error=None):
    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls = getattr(_fake, "calls", [])
        calls.append({"endpoint": endpoint, "body": body})
        _fake.calls = calls
        if error is not None:
            raise error
        return {"result": result or {"ok": True}}
    _fake.calls = []
    return _fake


# ============================================================
# 1. 批量改价
# ============================================================

def test_bulk_prices_success(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("utils.ozon_client.ozon_post", fake):
        r = shelf_service.bulk_update_prices(TENANT, [
            {"offer_id": "o1", "price": "100", "old_price": "120"},
            {"offer_id": "o2", "price": "50"},
        ])
    assert r["ok"] is True
    assert fake.calls[0]["endpoint"] == "/v1/product/import/prices"
    assert fake.calls[0]["body"]["prices"][0]["offer_id"] == "o1"
    assert fake.calls[0]["body"]["prices"][1]["price"] == "50"


def test_bulk_prices_no_default_400(_pg):
    with pytest.raises(HTTPException) as ei:
        shelf_service.bulk_update_prices(TENANT, [{"offer_id": "o1", "price": "10"}])
    assert ei.value.status_code == 400


def test_bulk_prices_502(_pg):
    _store_default_credential()
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            shelf_service.bulk_update_prices(TENANT, [{"offer_id": "o1", "price": "10"}])
    assert ei.value.status_code == 502


# ============================================================
# 2. 批量改库存
# ============================================================

def test_bulk_stocks_success(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("utils.ozon_client.ozon_post", fake):
        r = shelf_service.bulk_update_stocks(TENANT, [
            {"offer_id": "o1", "product_id": 5080316536, "stock": 100},
        ])
    assert r["ok"] is True
    assert fake.calls[0]["endpoint"] == "/v2/products/stocks"
    assert fake.calls[0]["body"]["stocks"][0]["stock"] == 100


# ============================================================
# 3. 批量归档/恢复
# ============================================================

def test_bulk_archive_true(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("utils.ozon_client.ozon_post", fake):
        r = shelf_service.bulk_archive(TENANT, ["5080316536", "5133087723"], archive=True)
    assert r["ok"] is True
    assert fake.calls[0]["endpoint"] == "/v1/product/archive"
    assert fake.calls[0]["body"]["product_id"] == [5080316536, 5133087723]  # int 数组


def test_bulk_archive_false_unarchive(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("utils.ozon_client.ozon_post", fake):
        shelf_service.bulk_archive(TENANT, ["5080316536"], archive=False)
    assert fake.calls[0]["endpoint"] == "/v1/product/unarchive"


def test_bulk_archive_non_digit_skipped(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("utils.ozon_client.ozon_post", fake):
        shelf_service.bulk_archive(TENANT, ["5080316536", "abc"], archive=True)
    assert fake.calls[0]["body"]["product_id"] == [5080316536]

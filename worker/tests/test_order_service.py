"""P0-4: 订单服务测试（mock ozon_post + 状态映射 + 提取 + 错误路径）。

验收门（docs/PRD-orders-v0.47.md §五）：
1. 状态映射全枚举（Ozon raw status → 统一 7 态）
2. products/financial/warehouse 标准化提取
3. 无默认店铺 → 400；Ozon API 失败 → 502
4. 租户隔离（credential 归属校验走 get_decrypted）
"""
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import order_service

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
        pytest.skip(f"PG 不可用（{exc}），跳过订单服务测试")


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


def _store_credential(tenant: str, client_id: str, api_key: str, is_default=False) -> str:
    from services import credential_service
    return credential_service.store_credential(tenant, client_id, api_key)


def _fake_ozon(payload: dict, error: Exception | None = None):
    """mock ozon_post：记录调用，返回固定 payload 或抛错。"""
    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls = getattr(_fake, "calls", [])
        calls.append({"client_id": client_id, "endpoint": endpoint, "body": body})
        _fake.calls = calls
        if error is not None:
            raise error
        return {"result": payload}
    _fake.calls = []
    return _fake


def _posting(status: str, **kw) -> dict:
    p = {
        "posting_number": f"PN-{status}",
        "status": status,
        "in_process_at": "2026-08-15T10:00:00Z",
        "products": [{"name": "测试商品", "sku": 123, "quantity": 2, "price": "99.5", "offer_id": "16880001"}],
        "financial_data": {
            "products": [{"price": "99.5", "commission_amount": "9.95"}],
        },
        "delivery_method": {"name": "Стандарт", "warehouse": "Москва"},
        "analytics_data": {"warehouse": "Москва"},
    }
    p.update(kw)
    return p


# ============================================================
# 1. 状态映射全枚举
# ============================================================

def test_status_map_full():
    assert order_service.map_status("awaiting_registration") == "pending"
    assert order_service.map_status("acceptance_in_progress") == "pending"
    assert order_service.map_status("arbitrary_available") == "awaiting"
    assert order_service.map_status("arbitrary_not_enough_for_package") == "awaiting"
    assert order_service.map_status("arbitrary_waiting_for_shipment") == "waiting"
    assert order_service.map_status("arbitrary_cancelled_by_merchant") == "waiting"
    assert order_service.map_status("driver_pickup") == "delivering"
    assert order_service.map_status("delivering") == "delivering"
    assert order_service.map_status("delivered") == "delivered"
    assert order_service.map_status("cancelled") == "cancelled"
    assert order_service.map_status("cancelled_by_merchant") == "cancelled"
    assert order_service.map_status("cancelled_by_customer") == "cancelled"
    assert order_service.map_status("cancelled_by_ozon") == "cancelled"
    assert order_service.map_status("cancelled_arbitrary") == "cancelled"
    assert order_service.map_status("unknown_future_status") == "other"


# ============================================================
# 2. 标准化提取
# ============================================================

def test_extract_products_and_financial(_pg):
    cred = _store_credential(TENANT, "222222", "key-2")
    fake = _fake_ozon({"postings": [_posting("delivering")], "total": 1})
    with patch("services.order_service.ozon_post", fake):
        result = order_service.list_orders(TENANT, credential_id=cred)
    item = result["items"][0]
    assert item["status"] == "delivering"
    assert item["raw_status"] == "delivering"
    assert item["posting_number"] == "PN-delivering"
    assert item["total_amount"] == 99.5
    assert item["commission_amount"] == 9.95
    assert item["profit"] == 89.55
    assert item["product_count"] == 2
    assert item["warehouse"] == "Москва"
    assert item["delivery_method"] == "Стандарт"
    assert item["products"][0]["name"] == "测试商品"
    assert result["total"] == 1
    assert result["store"]["ozon_client_id"] == "222222"
    # 请求体：with.financial_data 打开 + since 默认 30 天
    body = fake.calls[0]["body"]
    assert body["with"]["financial_data"] is True
    assert "since" in body["filter"]


def test_cancelled_posting_extracts_reason(_pg):
    cred = _store_credential(TENANT, "222222", "key-2")
    posting = _posting(
        "cancelled_by_customer",
        cancel_reason="buyer refused",
        cancellation={"reason": "buyer refused", "cancellation_type": "client"},
    )
    fake = _fake_ozon({"postings": [posting]})
    with patch("services.order_service.ozon_post", fake):
        result = order_service.list_orders(TENANT, credential_id=cred)
    item = result["items"][0]
    assert item["status"] == "cancelled"
    assert item["cancel_reason"] == "buyer refused"
    assert item["cancellation"] == "client"


def test_status_filter_passed_to_api(_pg):
    cred = _store_credential(TENANT, "222222", "key-2")
    fake = _fake_ozon({"postings": [], "total": 0})
    with patch("services.order_service.ozon_post", fake):
        order_service.list_orders(TENANT, credential_id=cred, status="delivered")
    assert fake.calls[0]["body"]["filter"]["status"] == "delivered"


# ============================================================
# 3. 错误路径
# ============================================================

def test_no_default_store_400(_pg):
    with pytest.raises(HTTPException) as ei:
        order_service.list_orders(TENANT)
    assert ei.value.status_code == 400
    assert "默认店铺" in ei.value.detail


def test_ozon_api_error_502(_pg):
    cred = _store_credential(TENANT, "222222", "key-2")
    fake = _fake_ozon({}, error=RuntimeError("boom"))
    with patch("services.order_service.ozon_post", fake):
        with pytest.raises(HTTPException) as ei:
            order_service.list_orders(TENANT, credential_id=cred)
    assert ei.value.status_code == 502
    assert "Ozon" in ei.value.detail


# ============================================================
# 4. 租户隔离（凭证归属）
# ============================================================

def test_foreign_tenant_credential_404(_pg):
    _store_credential("tenant-B", "333333", "key-3")  # B 的凭证
    with pytest.raises(HTTPException) as ei:
        order_service.list_orders(TENANT, credential_id=str(uuid.uuid4()))
    assert ei.value.status_code == 404

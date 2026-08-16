"""P1-2: 订单写入操作测试（备货发货 / 取消原因 / 取消订单，mock ozon_post）。

验收门（docs/PRD-order-actions-v0.49.md §四）：
1. ship：请求体断言（packages/posting_number）+ 成功 + 无默认店铺 400 + Ozon 失败 502
2. cancel-reasons：成功 [{id,title}] + 失败 502
3. cancel：请求体断言（cancel_reason_id）+ 成功 + 失败 502
"""
import os
import sys
from pathlib import Path
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
        pytest.skip(f"PG 不可用（{exc}），跳过订单写入测试")


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


# ============================================================
# 1. 备货发货 ship
# ============================================================

def test_ship_success(_pg):
    _store_default_credential()

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        assert endpoint == "/v4/posting/fbs/ship"
        assert body["packages"][0]["posting_number"] == "PN-001"
        assert body["packages"][0]["packages_count"] == 1
        return {"result": {"result": "ok"}}

    with patch("services.order_service.ozon_post", _fake):
        result = order_service.ship_order(TENANT, "PN-001")
    assert result["ok"] is True
    assert result["posting_number"] == "PN-001"


def test_ship_no_default_store_400(_pg):
    with pytest.raises(HTTPException) as ei:
        order_service.ship_order(TENANT, "PN-001")
    assert ei.value.status_code == 400
    assert "默认店铺" in ei.value.detail


def test_ship_ozon_error_502(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            order_service.ship_order(TENANT, "PN-001")
    assert ei.value.status_code == 502
    assert "备货发货" in ei.value.detail


# ============================================================
# 2. 取消原因列表
# ============================================================

def test_cancel_reasons_success(_pg):
    _store_default_credential()

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        assert endpoint == "/v1/posting/fbs/cancel-reason"
        assert body["posting_number"] == "PN-001"
        return {"result": {"cancel_reasons": [
            {"id": 1, "title": "买家要求取消"},
            {"id": 2, "title": "商品缺货"},
        ]}}

    with patch("services.order_service.ozon_post", _fake):
        reasons = order_service.list_cancel_reasons(TENANT, "PN-001")
    assert len(reasons) == 2
    assert reasons[0] == {"id": 1, "title": "买家要求取消"}
    assert reasons[1]["title"] == "商品缺货"


def test_cancel_reasons_ozon_error_502(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            order_service.list_cancel_reasons(TENANT, "PN-001")
    assert ei.value.status_code == 502


# ============================================================
# 3. 取消订单
# ============================================================

def test_cancel_success(_pg):
    _store_default_credential()

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        assert endpoint == "/v2/posting/fbs/cancel"
        assert body["posting_number"] == "PN-001"
        assert body["cancel_reason_id"] == 2
        return {"result": {"result": "canceled"}}

    with patch("services.order_service.ozon_post", _fake):
        result = order_service.cancel_order(TENANT, "PN-001", 2)
    assert result["ok"] is True


def test_cancel_ozon_error_502(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            order_service.cancel_order(TENANT, "PN-001", 1)
    assert ei.value.status_code == 502
    assert "取消订单" in ei.value.detail


# ============================================================
# 4. credential_id 优先于默认店铺
# ============================================================

def test_ship_with_explicit_credential(_pg):
    cred_id = _store_default_credential()

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        assert client_id == "222222"
        return {"result": {"ok": True}}

    with patch("services.order_service.ozon_post", _fake):
        result = order_service.ship_order(TENANT, "PN-001", credential_id=cred_id)
    assert result["ok"] is True

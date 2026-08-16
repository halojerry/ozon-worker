"""P1-1: 订单标注（notes）读写 + 面单代理测试。

验收门（docs/PRD-order-notes-v0.48.md §四）：
1. notes upsert/get 租户隔离（A 看不到 B）
2. 无记录返回空模板（不 404）
3. label 代理：成功返回 base64 / 无默认店铺 400 / Ozon 失败 502
"""
import base64
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
TENANT_A = "tenant-A"
TENANT_B = "tenant-B"


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过订单标注测试")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


@pytest.fixture(autouse=True)
def _cleanup(_pg):
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM order_notes WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT_A, "b": TENANT_B})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT_A, "b": TENANT_B})
    eng.dispose()


def _store_credential(tenant: str, client_id: str, api_key: str, is_default=False) -> str:
    from services import credential_service
    cred_id = credential_service.store_credential(tenant, client_id, api_key)
    if is_default:
        eng = create_engine(DB_URL)
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE credentials SET is_default=true WHERE id=:id"
            ), {"id": cred_id})
        eng.dispose()
    return cred_id


# ============================================================
# 1. notes upsert/get + 租户隔离
# ============================================================

def test_upsert_and_get(_pg):
    order_service.upsert_order_notes(TENANT_A, "PN-001", {
        "source_url": "https://detail.1688.com/offer/1.html",
        "source_cost": 12.5,
        "source_remark": "1688 直采",
        "purchase_no": "CG-001",
        "purchase_carrier": "圆通",
        "purchase_tracking": "YT123456",
    })
    got = order_service.get_order_notes(TENANT_A, "PN-001")
    assert got["source_url"] == "https://detail.1688.com/offer/1.html"
    assert got["source_cost"] == 12.5
    assert got["source_remark"] == "1688 直采"
    assert got["purchase_no"] == "CG-001"
    assert got["purchase_carrier"] == "圆通"
    assert got["purchase_tracking"] == "YT123456"


def test_upsert_idempotent(_pg):
    order_service.upsert_order_notes(TENANT_A, "PN-002", {"source_url": "a", "source_cost": 1})
    order_service.upsert_order_notes(TENANT_A, "PN-002", {"source_url": "b", "source_cost": 2})
    got = order_service.get_order_notes(TENANT_A, "PN-002")
    assert got["source_url"] == "b"
    assert got["source_cost"] == 2.0


def test_tenant_isolation(_pg):
    order_service.upsert_order_notes(TENANT_A, "PN-003", {"source_url": "A 的货源"})
    got_b = order_service.get_order_notes(TENANT_B, "PN-003")
    assert got_b["source_url"] == ""  # B 看不到 A 的 notes → 空模板


def test_missing_returns_empty_template(_pg):
    got = order_service.get_order_notes(TENANT_A, "PN-NOPE")
    assert got["posting_number"] == "PN-NOPE"
    assert got["source_url"] == ""
    assert got["source_cost"] is None


def test_bad_source_cost_422(_pg):
    with pytest.raises(HTTPException) as ei:
        order_service.upsert_order_notes(TENANT_A, "PN-004", {"source_cost": "abc"})
    assert ei.value.status_code == 422


# ============================================================
# 2. label 面单代理
# ============================================================

def test_label_success(_pg):
    cred = _store_credential(TENANT_A, "222222", "key-2", is_default=True)
    fake_pdf = b"%PDF-1.4 fake"
    b64 = base64.b64encode(fake_pdf).decode()

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        assert endpoint == "/v2/posting/fbs/package-label"
        assert body["posting_number"] == "PN-001"
        return {"result": {"pdf": b64}}

    with patch("services.order_service.ozon_post", _fake):
        label = order_service.get_order_label(TENANT_A, "PN-001")
    assert label["posting_number"] == "PN-001"
    assert label["content_type"] == "application/pdf"
    assert label["label_base64"] == b64


def test_label_uses_default_credential(_pg):
    _store_credential(TENANT_A, "222222", "key-2", is_default=True)
    with patch("services.order_service.ozon_post", return_value={"result": {"pdf": "b64"}}):
        label = order_service.get_order_label(TENANT_A, "PN-001")
    assert label["label_base64"] == "b64"


def test_label_no_default_store_400(_pg):
    with pytest.raises(HTTPException) as ei:
        order_service.get_order_label(TENANT_A, "PN-001")
    assert ei.value.status_code == 400
    assert "默认店铺" in ei.value.detail


def test_label_ozon_error_502(_pg):
    _store_credential(TENANT_A, "222222", "key-2", is_default=True)
    with patch("services.order_service.ozon_post", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            order_service.get_order_label(TENANT_A, "PN-001")
    assert ei.value.status_code == 502


def test_label_empty_pdf_502(_pg):
    _store_credential(TENANT_A, "222222", "key-2", is_default=True)
    with patch("services.order_service.ozon_post", return_value={"result": {}}):
        with pytest.raises(HTTPException) as ei:
            order_service.get_order_label(TENANT_A, "PN-001")
    assert ei.value.status_code == 502

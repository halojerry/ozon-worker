"""P1-3: 订单批量操作测试（批量面单 / 批量备货，失败隔离）。

验收：
1. 批量备货：逐单 ship，成功单进入 shipped，失败单进入 failed（带原因）
2. 批量面单：逐单拉取 label，成功单返回 base64，失败单隔离
3. 无默认店铺 → 全部 failed（不抛整体 400）
4. 空列表 → 空结果不报错
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
TENANT = "tenant-batch"


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过批量测试")


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


def _store_default_credential():
    from services import credential_service
    cred_id = credential_service.store_credential(TENANT, "222222", "key-2")
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("UPDATE credentials SET is_default=true WHERE id=:id"), {"id": cred_id})
    eng.dispose()
    return cred_id


def _fake_ozon(label_ok=True):
    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        if endpoint == "/v2/posting/fbs/package-label":
            if not label_ok:
                raise RuntimeError("label boom")
            return {"result": {"pdf": "cGRmZGF0YQ=="}}
        if endpoint == "/v4/posting/fbs/ship":
            return {"result": {"ok": True}}
        if endpoint == "/v1/posting/fbs/cancel-reason":
            return {"result": {"cancel_reasons": [{"id": 1, "title": "原因A"}]}}
        if endpoint == "/v2/posting/fbs/cancel":
            return {"result": {"ok": True}}
        return {"result": {}}
    return _fake


# ── 批量备货 ──

def test_batch_ship_all_success(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", _fake_ozon()):
        r = order_service.batch_ship_orders(TENANT, ["PN-1", "PN-2"])
    assert r["ok"] is True
    assert r["shipped"] == ["PN-1", "PN-2"]
    assert r["failed"] == []


def test_batch_ship_failure_isolated(_pg):
    _store_default_credential()
    def _flaky(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        packages = body.get("packages") or []
        pn = packages[0].get("posting_number") if packages else ""
        if pn == "PN-BAD":
            raise HTTPException(status_code=400, detail="订单状态不允许备货")
        return {"result": {"ok": True}}
    with patch("services.order_service.ozon_post", _flaky):
        r = order_service.batch_ship_orders(TENANT, ["PN-OK", "PN-BAD", "PN-OK2"])
    assert r["ok"] is False
    assert r["shipped"] == ["PN-OK", "PN-OK2"]
    assert len(r["failed"]) == 1
    assert r["failed"][0]["posting_number"] == "PN-BAD"
    assert "不允许备货" in r["failed"][0]["error"]


def test_batch_ship_no_default_store(_pg):
    with patch("services.order_service.ozon_post", _fake_ozon()):
        r = order_service.batch_ship_orders(TENANT, ["PN-1"])
    assert r["ok"] is False
    assert r["shipped"] == []
    assert len(r["failed"]) == 1
    assert "默认店铺" in r["failed"][0]["error"]


# ── 批量面单 ──

def test_batch_labels_all_success(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", _fake_ozon()):
        r = order_service.batch_order_labels(TENANT, ["PN-1", "PN-2"])
    assert r["ok"] is True
    assert len(r["items"]) == 2
    assert r["items"][0]["posting_number"] == "PN-1"
    assert r["items"][0]["label_base64"] == "cGRmZGF0YQ=="
    assert r["failed"] == []


def test_batch_labels_failure_isolated(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", _fake_ozon(label_ok=False)):
        r = order_service.batch_order_labels(TENANT, ["PN-1", "PN-2"])
    assert r["ok"] is False
    assert r["items"] == []
    assert len(r["failed"]) == 2
    assert "boom" in r["failed"][0]["error"]


def test_batch_empty_lists(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", _fake_ozon()):
        r1 = order_service.batch_ship_orders(TENANT, [])
        r2 = order_service.batch_order_labels(TENANT, [])
    assert r1 == {"ok": True, "shipped": [], "failed": []}
    assert r2 == {"ok": True, "items": [], "failed": []}

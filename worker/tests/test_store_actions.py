"""todo 7: 店铺执行端点 + promo_client 契约锁定测试（mock ozon_post 隔离）。

验证命令（need PG for shelf_service credential/op_log; else mock):
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
      PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_store_actions.py -q

锁定（对抗 misleading_success_output——不靠「无报错」，断言真日志行 + 端点白名单）：
1. test_bulk_update_prices_ok: bulk_update_prices 成功 + _write_operation_log 写 before/after/result=success
2. test_actions_register_ok: promo_client 活动报名 → 成功/失败均写日志
3. test_bulk_archive_ok: 上下架成功日志
4. test_promo_client_list_actions: list_actions 走 worker ozon_post（endpoint=/v1/actions，非 Performance）
5. test_no_performance_api_called: promo_client 全部端点不含 /api/client/*
6. test_create_discount_contract / test_create_voucher_contract: swagger 必填字段断言
7. test_create_voucher_enum_invalid: 折扣/优惠券枚举外 → 400（参数错误）
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
MASTER_KEY = "0123456789abcdef0123456789abcdef"
TENANT = "tenant-store-actions"
CRED_ID = str(uuid.uuid4())


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过需 PG 的接线测试")
    from storage.database.shared.model import Base
    eng = create_engine(DB_URL)
    Base.metadata.create_all(bind=eng)
    eng.dispose()


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
        conn.execute(text("DELETE FROM store_operation_log WHERE tenant_id=:t"), {"t": TENANT})
    eng.dispose()


def _proc_pg() -> bool:
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


def _store_credential() -> str:
    from services import credential_service
    cred_id = credential_service.store_credential(TENANT, "222222", "key-2")
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("UPDATE credentials SET is_default=true WHERE id=:id"), {"id": cred_id})
    eng.dispose()
    return str(cred_id)


def _oplog_rows() -> list:
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT operation, target_id, before, after, result, error "
            "FROM store_operation_log WHERE tenant_id=:t ORDER BY id"
        ), {"t": TENANT}).fetchall()
    eng.dispose()
    return rows


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
# 1. bulk_update_prices → 成功 + _write_operation_log (before/after/result=success)
# ============================================================

def test_bulk_update_prices_ok(_pg):
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    fake = _fake_ozon({"ok": True})
    body = {"operation": "bulk_update_prices",
            "prices": [{"offer_id": "o1", "price": "100", "old_price": "120"}]}
    with patch("utils.ozon_client.ozon_post", fake):
        r = _execute(TENANT, cred, body)
    assert r["ok"] is True
    assert fake.calls[0]["endpoint"] == "/v1/product/import/prices"
    # 接线 op_log：before/after/result=success 真断言
    rows = _oplog_rows()
    assert len(rows) == 1
    assert rows[0].operation == "update_price"
    assert rows[0].result == "success"
    assert rows[0].after is not None


# ============================================================
# 2. actions_register → promo_client 活动报名（成功/失败均写日志）
# ============================================================

def test_actions_register_ok(_pg):
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    fake = _fake_ozon({"action_id": 55})
    body = {"operation": "actions_register", "action_id": 55,
            "products": [{"sku": 5080316536, "discount_percent": 5}]}
    with patch("utils.ozon_client.ozon_post", fake):
        r = _execute(TENANT, cred, body)
    assert r["ok"] is True
    assert fake.calls[0]["endpoint"] == "/v1/seller-actions/products/add"
    assert fake.calls[0]["body"]["action_id"] == 55
    assert fake.calls[0]["body"]["products"][0]["sku"] == 5080316536
    rows = _oplog_rows()
    assert rows and rows[0].operation == "actions_register"
    assert rows[0].result == "success"


def test_actions_register_failed_writes_log(_pg):
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    body = {"operation": "actions_register", "action_id": 55,
            "products": [{"sku": 5080316536}], "credential_id": cred}
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("boom")), \
            pytest.raises(HTTPException) as ei:
        _execute(TENANT, cred, body)
    assert ei.value.status_code == 502
    rows = _oplog_rows()
    assert rows and rows[0].operation == "actions_register"
    assert rows[0].result == "failed"
    assert "boom" in (rows[0].error or "")


# ============================================================
# 3. bulk_archive → 上下架成功日志
# ============================================================

def test_bulk_archive_ok(_pg):
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    fake = _fake_ozon({"ok": True})
    body = {"operation": "bulk_archive", "product_ids": ["5080316536"], "archive": True}
    with patch("utils.ozon_client.ozon_post", fake):
        r = _execute(TENANT, cred, body)
    assert r["ok"] is True
    assert fake.calls[0]["endpoint"] == "/v1/product/archive"
    rows = _oplog_rows()
    assert rows and rows[0].operation == "archive"
    assert rows[0].result == "success"


# ============================================================
# 3b. bulk 分支失败（P1 修复）→ shelf_service 抛异常时**仍落 result=failed**
# ============================================================

def test_bulk_prices_failed_writes_log(_pg):
    """P1 修复：bulk_update_prices 失败不再只 raise，还接 result=failed 日志行。"""
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    body = {"operation": "bulk_update_prices",
            "prices": [{"offer_id": "o1", "price": "100"}]}
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("price boom")), \
            pytest.raises(HTTPException) as ei:
        _execute(TENANT, cred, body)
    assert ei.value.status_code == 502
    rows = _oplog_rows()
    assert rows and rows[0].operation == "update_price"
    assert rows[0].result == "failed"
    assert "price boom" in (rows[0].error or "")


def test_bulk_stocks_failed_writes_log(_pg):
    """P1 修复：bulk_update_stocks 失败落 result=failed 日志行。"""
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    body = {"operation": "bulk_update_stocks",
            "stocks": [{"offer_id": "o1", "stock": 3}]}
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("stock boom")), \
            pytest.raises(HTTPException) as ei:
        _execute(TENANT, cred, body)
    assert ei.value.status_code == 502
    rows = _oplog_rows()
    assert rows and rows[0].operation == "update_stock"
    assert rows[0].result == "failed"
    assert "stock boom" in (rows[0].error or "")


def test_bulk_archive_failed_writes_log(_pg):
    """P1 修复：bulk_archive 失败落 result=failed 日志行。"""
    cred = _store_credential()
    from routes.store_actions_routes import _execute
    body = {"operation": "bulk_archive", "product_ids": ["5080316536"], "archive": True}
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("archive boom")), \
            pytest.raises(HTTPException) as ei:
        _execute(TENANT, cred, body)
    assert ei.value.status_code == 502
    rows = _oplog_rows()
    assert rows and rows[0].operation == "archive"
    assert rows[0].result == "failed"
    assert "archive boom" in (rows[0].error or "")


# ============================================================
# 4. promo_client.list_actions → worker ozon_post（endpoint=/v1/actions，非 Performance）
# ============================================================

def test_promo_client_list_actions(_pg):
    from utils import promo_client
    fake = _fake_ozon({"items": []})
    with patch("utils.ozon_client.ozon_post", fake):
        r = promo_client.list_actions("cid", "key", limit=5)
    # 走 worker ozon_post，端点必须是 /v1/actions（非 /api/client/*）
    assert fake.calls[0]["endpoint"] == "/v1/actions"
    assert r == {"items": []}
    assert promo_client.METHOD_ENDPOINTS["list_actions"] == "/v1/actions"


# ============================================================
# 5. promo_client 不含 /api/client/*（Performance API 隔离）
# ============================================================

def test_no_performance_api_called():
    from utils import promo_client
    # 本模块所有端点必须属于 Seller API 白名单，绝不含 Performance 前缀
    for ep in promo_client.METHOD_ENDPOINTS.values():
        assert not ep.startswith("/api/client"), f"promo_client 泄漏 Performance 端点 {ep}"
    # 白名单本身也不应出现 /api/client
    for ep in promo_client.ALLOWED_ENDPOINTS:
        assert not ep.startswith("/api/client")


# ============================================================
# 6. create_discount / create_voucher 契约（swagger 必填字段）
# ============================================================

def test_create_discount_contract():
    from utils import promo_client
    fake = _fake_ozon({"action_id": 1})
    with patch("utils.ozon_client.ozon_post", fake):
        promo_client.create_discount(
            "cid", "key", date_start="2026-08-22", date_end="2026-08-28",
            min_action_percent=5, title="周末促销")
    body = fake.calls[0]["body"]
    # swagger required: date_end / date_start / min_action_percent
    assert body["date_start"] == "2026-08-22"
    assert body["date_end"] == "2026-08-28"
    assert body["min_action_percent"] == 5
    assert body["title"] == "周末促销"


def test_create_voucher_contract():
    from utils import promo_client
    fake = _fake_ozon({"action_id": 2})
    vp = {"count_codes": 10, "is_private": False, "type": "MULTIPLE"}
    with patch("utils.ozon_client.ozon_post", fake):
        promo_client.create_voucher(
            "cid", "key", title="新人券", budget=5000,
            date_start="2026-08-22", date_end="2026-08-28",
            discount_type="PERCENT", discount_value=10, voucher_parameters=vp)
    body = fake.calls[0]["body"]
    assert body["title"] == "新人券"
    assert body["budget"] == 5000
    assert body["discount_type"] == "PERCENT"
    assert body["voucher_parameters"] == vp


def test_create_voucher_enum_invalid():
    from utils import promo_client
    with pytest.raises(ValueError):
        promo_client.create_voucher(
            "cid", "key", title="x", budget=1, date_start="a", date_end="b",
            discount_type="BAD", discount_value=1,
            voucher_parameters={"count_codes": 1, "is_private": True, "type": "ONE"})


# ============================================================
# 8. 路由入口：不支持的 operation → 400
# ============================================================

def test_unsupported_operation_400(_pg):
    from routes.store_actions_routes import _execute
    cred = _store_credential()
    with pytest.raises(HTTPException) as ei:
        _execute(TENANT, cred, {"operation": "not_real"})
    assert ei.value.status_code == 400

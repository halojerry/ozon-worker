"""v0.50: Ozon 在线商品实时拉取测试（mock ozon_post 两步拼接）。

验收门（docs/PRD-ozon-shelf-v0.50.md §三）：
1. /v3/product/list + /v1/product/info/list 两步拼接正确
2. 字段提取（name/image/price/stock/currency）
3. 无默认店铺 400；Ozon 失败 502；info 失败降级
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
        pytest.skip(f"PG 不可用（{exc}），跳过在线商品测试")


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


def _fake_ozon(list_result: dict, info_result: dict | None = None, info_error: Exception | None = None):
    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls = getattr(_fake, "calls", [])
        calls.append({"client_id": client_id, "endpoint": endpoint, "body": body})
        _fake.calls = calls
        if endpoint == "/v3/product/list":
            return {"result": list_result}
        if endpoint == "/v3/product/info/list":
            if info_error is not None:
                raise info_error
            return {"result": info_result or {}}
        raise AssertionError(f"unexpected endpoint: {endpoint}")
    _fake.calls = []
    return _fake


def _list_result():
    return {
        "items": [
            {"product_id": 5080316536, "offer_id": "test-update-123"},
            {"product_id": 5133087723, "offer_id": "822637635597"},
        ],
        "total": 2,
    }


def _info_result():
    # ⚠️ /v3/product/info/list 结构：price 顶层字符串、currency_code 顶层、stocks.stocks[].present
    return {
        "items": [
            {"id": 5080316536, "offer_id": "test-update-123", "name": "商品A",
             "images": ["https://example.com/a.jpg"],
             "price": "99.5", "currency_code": "RUB",
             "stocks": {"has_stock": True, "stocks": [{"present": 10, "reserved": 0}]}},
            {"id": 5133087723, "offer_id": "822637635597", "name": "商品B",
             "images": [], "price": "", "currency_code": "CNY",
             "stocks": {"has_stock": True, "stocks": [{"present": 0, "reserved": 0}]}},
        ]
    }


# ============================================================
# 1. 两步拼接 + 字段提取
# ============================================================

def test_list_ozon_products_success(_pg):
    _store_default_credential()
    fake = _fake_ozon(_list_result(), _info_result())
    with patch("utils.ozon_client.ozon_post", fake):
        result = shelf_service.list_ozon_products(TENANT)

    # 两步调用：list → info
    eps = [c["endpoint"] for c in fake.calls]
    assert eps == ["/v3/product/list", "/v3/product/info/list"]
    assert fake.calls[0]["body"]["filter"] == {"visibility": "ALL"}
    assert fake.calls[1]["body"]["product_id"] == [5080316536, 5133087723]

    assert result["total"] == 2
    items = result["items"]
    assert items[0]["product_id"] == "5080316536"
    assert items[0]["name"] == "商品A"
    assert items[0]["image"] == "https://example.com/a.jpg"
    assert items[0]["price"] == 99.5
    assert items[0]["currency"] == "RUB"
    assert items[0]["stock"] == 10
    # 商品B：无图/无价 → 兜底
    assert items[1]["image"] is None
    assert items[1]["price"] is None
    assert items[1]["stock"] == 0


# ============================================================
# 2. 错误路径
# ============================================================

def test_no_default_store_400(_pg):
    with pytest.raises(HTTPException) as ei:
        shelf_service.list_ozon_products(TENANT)
    assert ei.value.status_code == 400
    assert "默认店铺" in ei.value.detail


def test_list_ozon_error_502(_pg):
    _store_default_credential()
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            shelf_service.list_ozon_products(TENANT)
    assert ei.value.status_code == 502
    assert "商品列表" in ei.value.detail


def test_info_error_degrades_to_list(_pg):
    """info 失败不阻断——降级返回列表（只有 id/offer_id）。"""
    _store_default_credential()
    fake = _fake_ozon(_list_result(), info_error=RuntimeError("info boom"))
    with patch("utils.ozon_client.ozon_post", fake):
        result = shelf_service.list_ozon_products(TENANT)
    assert result["total"] == 2
    assert result["items"][0]["name"] == "test-update-123"  # 兜底用 offer_id
    assert result["items"][0]["price"] is None


# ============================================================
# 3. 显式 credential
# ============================================================

def test_explicit_credential(_pg):
    cred_id = _store_default_credential()
    fake = _fake_ozon(_list_result(), _info_result())
    with patch("utils.ozon_client.ozon_post", fake):
        result = shelf_service.list_ozon_products(TENANT, credential_id=cred_id)
    assert result["store"]["ozon_client_id"] == "222222"

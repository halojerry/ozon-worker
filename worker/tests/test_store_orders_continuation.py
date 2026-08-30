"""PRD M1: 订单续传测试(窗口+cursor 持久化,水位不提前推进)。

覆盖:预算耗尽(25 页)且 has_next → orders_sync_incomplete=true + cursor/窗口落库 +
orders_last_synced_at 不推进;续传同窗口继续 → 完成清标志 + 水位推进到窗口 to。
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
    client_id = f"7{uuid.uuid4().int % 10**7}"
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


def _paged_orders(page_limit=25, stop_after=None):
    """mock:前 page_limit 次返回 has_next=True + cursor;之后返回 has_next=False(1 条)。"""
    state = {"n": 0}

    def _handler(client_id, api_key, path, body=None, **kw):
        if path == "/v4/posting/fbs/list":
            n = state["n"]
            state["n"] += 1
            if stop_after is not None and n >= stop_after:
                return {"has_next": False, "cursor": "", "postings": [
                    {"posting_number": f"PN-C-{n}", "status": "delivered",
                     "in_process_at": "2026-08-01T00:00:00Z", "products": [],
                     "financial_data": {"products": [], "services": []},
                     "analytics_data": {}, "delivery_method": {}},
                ]}
            return {"has_next": True, "cursor": f"cur-{n}", "postings": [
                {"posting_number": f"PN-C-{n}", "status": "delivered",
                 "in_process_at": "2026-08-01T00:00:00Z", "products": [],
                 "financial_data": {"products": [], "services": []},
                 "analytics_data": {}, "delivery_method": {}},
            ]}
        if path == "/v3/product/list":
            return {"result": {"total": 0, "items": []}}
        return {"result": {}}
    return _handler


def _state(tenant, cid):
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT orders_last_synced_at, orders_sync_incomplete, orders_sync_cursor, "
            "orders_window_since, orders_window_to FROM credential_sync_state "
            "WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant, "c": cid}).fetchone()


def test_orders_truncate_then_resume(cred):
    tenant, cid = cred
    # 第一次:25 页预算耗尽且 has_next → 截断
    with patch("utils.ozon_client.ozon_post", side_effect=_paged_orders(page_limit=25)):
        r = store_sync_service.sync_store(tenant, cid)
    assert r["orders"]["synced"] == 25
    assert r["orders"]["incomplete"] is True
    st = _state(tenant, cid)
    assert st[1] is True                     # incomplete
    assert st[2] == "cur-24"                 # 最后一页 cursor 落库
    assert st[3] is not None and st[4] is not None  # 窗口落库
    assert st[0] is None                     # 水位不推进
    # 第二次:续传同窗口 → 完成
    with patch("utils.ozon_client.ozon_post", side_effect=_paged_orders(stop_after=0)):
        r2 = store_sync_service.sync_store(tenant, cid)
    assert r2["orders"]["synced"] == 1
    assert r2["orders"]["incomplete"] is False
    st2 = _state(tenant, cid)
    assert st2[1] is False
    assert st2[2] is None
    assert st2[0] is not None                # 水位推进
    # 窗口清除
    assert st2[3] is None and st2[4] is None


def test_orders_complete_in_one_pass(cred):
    tenant, cid = cred
    with patch("utils.ozon_client.ozon_post", side_effect=_paged_orders(stop_after=0)):
        r = store_sync_service.sync_store(tenant, cid)
    assert r["orders"]["synced"] == 1
    assert r["orders"]["incomplete"] is False
    st = _state(tenant, cid)
    assert st[1] is False and st[0] is not None

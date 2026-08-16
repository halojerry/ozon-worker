"""P2c: 订单消息发送测试（chat/start + send/message + 模板 + 记录）。

验收门（docs/PRD-order-message-v0.53.md §四）：
1. chat/start → send/message 两步请求体断言
2. 模板列表（3 种内置）+ 占位符替换
3. 消息长度校验（空 422 / 超长截断 1000）
4. 发送记录 upsert（成功/失败都留痕）
5. 无默认 400 / Ozon 失败 502
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
        pytest.skip(f"PG 不可用（{exc}），跳过消息测试")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


@pytest.fixture(autouse=True)
def _cleanup(_pg):
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM order_messages WHERE tenant_id=:t"), {"t": TENANT})
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


def _fake_ozon(chat_result=None, send_result=None, error=None):
    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls = getattr(_fake, "calls", [])
        calls.append({"endpoint": endpoint, "body": body})
        _fake.calls = calls
        if error is not None:
            raise error
        if endpoint == "/v1/chat/start":
            return {"result": chat_result or {"chat_id": "chat-123"}}
        return {"result": send_result or {"ok": True}}
    _fake.calls = []
    return _fake


# ============================================================
# 1. 模板
# ============================================================

def test_message_templates():
    tpls = order_service.get_message_templates()
    keys = [t["key"] for t in tpls]
    assert keys == ["passport", "pickup", "review"]
    assert all(t["name"] and "[货件编号]" in t["text"] for t in tpls)


def test_fill_template_placeholders():
    filled = order_service._fill_template(
        "Здравствуйте [货件编号] ([商品名称])", "PN-001", "Товар X")
    assert "PN-001" in filled
    assert "Товар X" in filled


# ============================================================
# 2. 发送闭环
# ============================================================

def test_send_message_success(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("services.order_service.ozon_post", fake):
        r = order_service.send_order_message(TENANT, "PN-001", "Здравствуйте!")
    assert r["ok"] is True
    assert r["chat_id"] == "chat-123"
    eps = [c["endpoint"] for c in fake.calls]
    assert eps == ["/v1/chat/start", "/v1/chat/send/message"]
    assert fake.calls[0]["body"]["posting_number"] == "PN-001"
    assert fake.calls[1]["body"]["message"] == "Здравствуйте!"


def test_send_message_recorded(_pg):
    _store_default_credential()
    with patch("services.order_service.ozon_post", _fake_ozon()):
        order_service.send_order_message(TENANT, "PN-002", "Тест", template_key="review")
    rec = order_service.list_order_messages(TENANT)
    assert rec["total"] == 1
    assert rec["items"][0]["posting_number"] == "PN-002"
    assert rec["items"][0]["template_key"] == "review"
    assert rec["items"][0]["status"] == "sent"


def test_send_message_failure_recorded(_pg):
    _store_default_credential()
    fake = _fake_ozon(error=RuntimeError("boom"))
    with patch("services.order_service.ozon_post", fake):
        with pytest.raises(HTTPException) as ei:
            order_service.send_order_message(TENANT, "PN-003", "Тест")
    assert ei.value.status_code == 502
    rec = order_service.list_order_messages(TENANT)
    assert rec["items"][0]["status"] == "failed"
    assert "boom" in rec["items"][0]["error"]


# ============================================================
# 3. 校验
# ============================================================

def test_send_empty_message_422(_pg):
    _store_default_credential()
    with pytest.raises(HTTPException) as ei:
        order_service.send_order_message(TENANT, "PN-004", "   ")
    assert ei.value.status_code == 422


def test_send_long_message_truncated(_pg):
    _store_default_credential()
    fake = _fake_ozon()
    with patch("services.order_service.ozon_post", fake):
        order_service.send_order_message(TENANT, "PN-005", "д" * 2000)
    sent = fake.calls[1]["body"]["message"]
    assert len(sent) == 1000  # 截断到 1000


def test_send_no_default_store_400(_pg):
    with pytest.raises(HTTPException) as ei:
        order_service.send_order_message(TENANT, "PN-006", "Тест")
    assert ei.value.status_code == 400

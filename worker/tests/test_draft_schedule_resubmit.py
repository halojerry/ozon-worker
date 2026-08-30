"""PRD M4b: 采集箱 定时上架 / 失败重试 守卫 / 批量提交逻辑测试(真实 PG)。"""
import asyncio
import datetime
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import draft_service  # noqa: E402
from utils.credential_cipher import decrypt, encrypt  # noqa: E402

os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")


@pytest.fixture()
def env():
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    cred = uuid.uuid4()
    draft = uuid.uuid4()
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO product_drafts (id, tenant_id, payload, source) VALUES (:id, :t, '{}'::jsonb, 'test')"
        ), {"id": draft, "t": tenant})
    yield {"tenant": tenant, "cred": str(cred), "draft": str(draft)}
    with eng.begin() as conn:
        for sql, params in (
            ("DELETE FROM scheduled_listings WHERE tenant_id=:t", {"t": tenant}),
            ("DELETE FROM draft_submissions WHERE draft_id IN (SELECT id FROM product_drafts WHERE tenant_id=:t)", {"t": tenant}),
            ("DELETE FROM product_drafts WHERE tenant_id=:t", {"t": tenant}),
        ):
            try:
                conn.execute(text(sql), params)
            except Exception:
                pass


def test_schedule_listing_roundtrip(env):
    token_enc = encrypt("sk-test-token", f"{env['tenant']}:scheduled")
    with create_engine(DB_URL).begin() as conn:
        conn.execute(text(
            "INSERT INTO scheduled_listings (tenant_id, draft_id, credential_id, scheduled_at, status, token_enc) "
            "VALUES (:t, :d, :c, :at, 'pending', :enc)"
        ), {"t": env["tenant"], "d": uuid.UUID(env["draft"]), "c": env["cred"],
            "at": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
            "enc": token_enc})
    with create_engine(DB_URL).connect() as conn:
        row = conn.execute(text(
            "SELECT token_enc FROM scheduled_listings WHERE tenant_id=:t"), {"t": env["tenant"]}).fetchone()
    assert decrypt(bytes(row[0]), f"{env['tenant']}:scheduled") == "sk-test-token"


def test_has_active_submission(env):
    assert draft_service.has_active_submission(env["tenant"], env["draft"]) is False
    with create_engine(DB_URL).begin() as conn:
        conn.execute(text(
            "INSERT INTO draft_submissions (draft_id, credential_id, store_client_id, status) "
            "VALUES (:d, :c, '111', 'pending')"
        ), {"d": uuid.UUID(env["draft"]), "c": uuid.UUID(env["cred"])})
    assert draft_service.has_active_submission(env["tenant"], env["draft"]) is True


def test_process_scheduled_listings(env):
    tenant, cred, draft = env["tenant"], env["cred"], env["draft"]
    eng = create_engine(DB_URL)
    token_enc = encrypt("sk-tok", f"{tenant}:scheduled")
    with eng.begin() as conn:
        draft2 = uuid.uuid4()
        conn.execute(text(
            "INSERT INTO product_drafts (id, tenant_id, payload, source) VALUES (:id, :t, '{}'::jsonb, 'test')"
        ), {"id": draft2, "t": tenant})
        # 进行中 submission 挂在 draft2 上 → skip 场景
        conn.execute(text(
            "INSERT INTO draft_submissions (draft_id, credential_id, store_client_id, status) "
            "VALUES (:d, :c, '111', 'pending')"
        ), {"d": draft2, "c": uuid.UUID(cred)})
        ok_id = int(conn.execute(text(
            "INSERT INTO scheduled_listings (tenant_id, draft_id, credential_id, scheduled_at, status, token_enc) "
            "VALUES (:t, :d, :c, NOW() - interval '1 minute', 'pending', :enc) RETURNING id"
        ), {"t": tenant, "d": uuid.UUID(draft), "c": cred, "enc": token_enc}).fetchone()[0])
        skip_id = int(conn.execute(text(
            "INSERT INTO scheduled_listings (tenant_id, draft_id, credential_id, scheduled_at, status, token_enc) "
            "VALUES (:t, :d2, :c2, NOW() - interval '1 minute', 'pending', :enc) RETURNING id"
        ), {"t": tenant, "d2": draft2, "c2": cred, "enc": token_enc}).fetchone()[0])
    with patch.object(draft_service, "submit_draft", new=AsyncMock(return_value={"task_id": "T-1"})):
        results = asyncio.run(draft_service.process_scheduled_listings(limit=20))
    assert results["submitted"] == 1
    assert results["skipped"] == 1
    with eng.connect() as conn:
        ok = conn.execute(text("SELECT status, task_id FROM scheduled_listings WHERE id=:id"),
                          {"id": ok_id}).fetchone()
        skip = conn.execute(text("SELECT status FROM scheduled_listings WHERE id=:id"),
                            {"id": skip_id}).fetchone()
    assert ok[0] == "submitted" and ok[1] == "T-1"
    assert skip[0] == "skipped"

"""PRD M1: store_sync_jobs 任务服务测试(真实 PG,不可达时 skip)。

覆盖:入队去重(唯一部分索引)、SKIP LOCKED 认领、进度/完成、僵尸恢复、
分节 due 扫描(orders/products 独立水位 + incomplete)、连续失败统计。
"""
import datetime
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import store_sync_jobs as jobs  # noqa: E402


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")


@pytest.fixture()
def cred():
    """建一个 active+sync_enabled 凭证,返回 (tenant_id, credential_id)。"""
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    client_id = f"9{uuid.uuid4().int % 10**7}"
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        # 测试卫生:清空残留 job(claim_next 是全局的,避免捡到上次失败遗留行)
        conn.execute(text("DELETE FROM store_sync_jobs"))
        row = conn.execute(text(
            """
            INSERT INTO credentials (tenant_id, ozon_client_id, ozon_api_key_enc, api_key_masked,
                                     status, sync_enabled)
            VALUES (:t, :c, '\\x01', '****', 'active', TRUE)
            RETURNING id::text
            """
        ), {"t": tenant, "c": client_id}).fetchone()
    cid = str(row[0])
    yield tenant, cid
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM store_sync_jobs"))
        conn.execute(text(
            "DELETE FROM credential_sync_state WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text(
            "DELETE FROM credentials WHERE tenant_id=:t"), {"t": tenant})


def test_enqueue_dedupe(cred):
    tenant, cid = cred
    j1 = jobs.enqueue(tenant, cid, kind="initial", trigger="bind")
    assert j1["status"] == "pending"
    j2 = jobs.enqueue(tenant, cid, kind="incremental", trigger="scheduler")
    assert j2["id"] == j1["id"], "同店重复入队应返回在途 job"
    assert j1["kind"] == "initial"


def test_claim_finish_and_progress(cred):
    tenant, cid = cred
    job = jobs.enqueue(tenant, cid, kind="incremental", trigger="scheduler")
    claimed = jobs.claim_next()
    assert claimed is not None and claimed["id"] == job["id"]
    assert claimed["status"] == "running"
    jobs.update_progress(job["id"], orders_synced=3, products_synced=5, progress=40)
    jobs.finish(job["id"], status="ok")
    got = jobs.get_job(tenant, job["id"])
    assert got["status"] == "ok"
    assert got["orders_synced"] == 3 and got["products_synced"] == 5
    assert got["progress"] == 40


def test_claim_only_one_active(cred):
    tenant, cid = cred
    j1 = jobs.enqueue(tenant, cid, trigger="bind")
    jobs.claim_next()
    j2 = jobs.enqueue(tenant, cid, trigger="scheduler")
    assert j2["id"] == j1["id"]
    # 全部 pending 已认领 → 无可用 job
    assert jobs.claim_next() is None


def test_zombie_reset(cred):
    tenant, cid = cred
    job = jobs.enqueue(tenant, cid)
    jobs.claim_next()
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE store_sync_jobs SET started_at = NOW() - interval '2 hours' WHERE id=:id"
        ), {"id": job["id"]})
    assert jobs.zombie_reset(timeout_minutes=30) == 1
    got = jobs.get_job(tenant, job["id"])
    assert got["status"] == "pending"
    assert got["error"] == "zombie reset"


def test_due_credentials_sections(cred):
    tenant, cid = cred
    eng = create_engine(DB_URL)
    now = datetime.datetime.now(datetime.timezone.utc)
    with eng.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state (tenant_id, credential_id, orders_last_synced_at,
                                               products_last_synced_at, orders_error, products_error)
            VALUES (:t, :c, :o, NULL, '', '')
            """
        ), {"t": tenant, "c": cid, "o": now - datetime.timedelta(minutes=5)})
    due = jobs.due_credentials(now=now)
    row = next((d for d in due if d["credential_id"] == cid), None)
    assert row is not None
    assert "products" in row["sections"], "products 从未同步应 due"
    assert "orders" not in row["sections"], "orders 1h 前同步且间隔 15min 应未 due"
    assert row["incomplete"] is False


def test_due_incomplete(cred):
    tenant, cid = cred
    eng = create_engine(DB_URL)
    now = datetime.datetime.now(datetime.timezone.utc)
    with eng.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state (tenant_id, credential_id, orders_last_synced_at,
                                               products_last_synced_at, orders_sync_incomplete,
                                               orders_error, products_error)
            VALUES (:t, :c, :o, :o, TRUE, '', '')
            """
        ), {"t": tenant, "c": cid, "o": now})
    due = jobs.due_credentials(now=now)
    row = next((d for d in due if d["credential_id"] == cid), None)
    assert row is not None and row["incomplete"] is True
    assert row["sections"] == []


def test_failure_counter(cred):
    tenant, cid = cred
    eng = create_engine(DB_URL)
    jobs.mark_sync_failure(tenant, cid)
    jobs.mark_sync_failure(tenant, cid)
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT consecutive_failures FROM credential_sync_state "
            "WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant, "c": cid}).fetchone()
    assert int(row[0]) == 2
    job = jobs.enqueue(tenant, cid)
    jobs.claim_next()
    jobs.mark_sync_success(tenant, cid, job["id"])
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT consecutive_failures, last_job_id FROM credential_sync_state "
            "WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant, "c": cid}).fetchone()
    assert int(row[0]) == 0
    assert int(row[1]) == job["id"]

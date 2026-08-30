"""PRD M2: 存量租户迁移集成测试(真实 PG)。

覆盖:同店两 key(同 client)合并保留最新、子表 credential_id/store_id 重指向、
sku_key 前缀重写+同款去重+draft_submissions 重指向、is_default 清重、
孤儿租户保留、dry-run 报告、重跑幂等。
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import migrate_key_tenant_to_user as mig  # noqa: E402
from services.tenant_service import key_derived_tenant  # noqa: E402

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")

T1 = key_derived_tenant("k1")
T2 = key_derived_tenant("k2")
NEW = "u-master"
ORPHAN = key_derived_tenant("orphan-key")
MAPPING = {T1: NEW, T2: NEW}


def _cleanup():
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM draft_submissions WHERE store_client_id IN ('111','222')"))
        conn.execute(text("DELETE FROM credentials WHERE ozon_client_id IN ('111','222','333')"))
        for table, col, _ in mig.TENANT_TABLES:
            try:
                conn.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(:ts)"),
                             {"ts": [T1, T2, NEW, ORPHAN]})
            except Exception:
                pass
            conn.execute(text(f"DROP TABLE IF EXISTS _mig_backup_{table}"))


@pytest.fixture()
def seeded():
    _cleanup()
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        # credentials:同店两 key(111:A(t1)/B(t2,更新)) + 单店(C:t2, 222)
        def _cred(tenant, client, days, is_default=False):
            row = conn.execute(text(
                """
                INSERT INTO credentials (tenant_id, ozon_client_id, ozon_api_key_enc, api_key_masked,
                                         status, is_default, updated_at)
                VALUES (:t, :c, '\\x01', '****', 'active', :d, NOW() - make_interval(days => :days))
                RETURNING id::text
                """
            ), {"t": tenant, "c": client, "d": is_default, "days": days}).fetchone()
            return str(row[0])

        aid = _cred(T1, "111", 2)
        bid = _cred(T2, "111", 1, is_default=True)
        cid = _cred(T2, "222", 1)
        _cred(T1, "333", 1, is_default=True)

        # 订单缓存:同 posting 双租户(A/B) + 单店(C)
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status,
                                           raw_status, products, product_count, warehouse,
                                           delivery_method, cancel_reason, cancellation)
            VALUES (:t, :c, :pn, 's', 's', '[]'::jsonb, 0, '', '', '', '')
            """
        ), {"t": T1, "c": uuid.UUID(aid), "pn": "PN-X"})
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status,
                                           raw_status, products, product_count, warehouse,
                                           delivery_method, cancel_reason, cancellation)
            VALUES (:t, :c, :pn, 's', 's', '[]'::jsonb, 0, '', '', '', '')
            """
        ), {"t": T2, "c": uuid.UUID(bid), "pn": "PN-X"})
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status,
                                           raw_status, products, product_count, warehouse,
                                           delivery_method, cancel_reason, cancellation)
            VALUES (:t, :c, :pn, 's', 's', '[]'::jsonb, 0, '', '', '', '')
            """
        ), {"t": T2, "c": uuid.UUID(cid), "pn": "PN-Y"})

        # 商品任务:同 (client, offer) 双租户 → sku_key 重写后冲突
        task1 = str(conn.execute(text(
            "INSERT INTO ozon_product_tasks (tenant_id, sku_key, status, payload, priority, retry_count, max_retries, timeout_seconds, created_at) "
            "VALUES (:t, :sk, 'completed', '{}'::jsonb, 0, 0, 3, 1800, NOW() - interval '2 days') RETURNING id::text"
        ), {"t": T1, "sk": f"{T1}:111:o1"}).fetchone()[0])
        task2 = str(conn.execute(text(
            "INSERT INTO ozon_product_tasks (tenant_id, sku_key, status, payload, priority, retry_count, max_retries, timeout_seconds, created_at) "
            "VALUES (:t, :sk, 'completed', '{}'::jsonb, 0, 0, 3, 1800, NOW() - interval '1 days') RETURNING id::text"
        ), {"t": T2, "sk": f"{T2}:111:o1"}).fetchone()[0])
        draft2_id = str(conn.execute(text(
            "INSERT INTO product_drafts (tenant_id, payload, source) VALUES (:t, '{}'::jsonb, 'test') "
            "RETURNING id::text"
        ), {"t": T2}).fetchone()[0])
        conn.execute(text(
            "INSERT INTO draft_submissions (draft_id, credential_id, store_client_id, status, submitted_task_id) "
            "VALUES (:d, :c, '111', 'failed', :tid)"
        ), {"d": uuid.UUID(draft2_id), "c": uuid.UUID(bid), "tid": task1})

        # 草稿:双租户 + 孤儿
        conn.execute(text(
            "INSERT INTO product_drafts (tenant_id, payload, source) VALUES (:t, '{}'::jsonb, 'test')"
        ), {"t": T1})
        conn.execute(text(
            "INSERT INTO product_drafts (tenant_id, payload, source) VALUES (:t, '{}'::jsonb, 'test')"
        ), {"t": ORPHAN})

        # 模板:双租户各一个默认
        lt1 = str(conn.execute(text(
            "INSERT INTO listing_templates (tenant_id, name, description, platform, is_default, config) "
            "VALUES (:t, 'n1', '', 'OZON', TRUE, '{}'::jsonb) RETURNING id::text"
        ), {"t": T1}).fetchone()[0])
        lt2 = str(conn.execute(text(
            "INSERT INTO listing_templates (tenant_id, name, description, platform, is_default, config) "
            "VALUES (:t, 'n2', '', 'OZON', TRUE, '{}'::jsonb) RETURNING id::text"
        ), {"t": T2}).fetchone()[0])

    yield {"aid": aid, "bid": bid, "cid": cid, "task1": task1, "task2": task2, "lt1": lt1, "lt2": lt2}

    _cleanup()


def _db(sql, params=None):
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


def test_dry_run_report(seeded):
    eng = create_engine(DB_URL)
    rep = mig.report(eng, MAPPING)
    assert rep["mapping"] == 2
    assert rep["tables"]["credentials"][T1] >= 2 and rep["tables"]["credentials"][T2] >= 2
    assert rep["merge_preview"]["credential_dups"], "同店两 key 应预览到合并冲突"


def test_apply_merge_idempotent(seeded):
    eng = create_engine(DB_URL)
    mig.apply(eng, MAPPING)
    # credentials:u-master 下 client 111 仅 1 个 active(幸存 B),222 1 个
    rows = _db(
        "SELECT ozon_client_id, status FROM credentials WHERE tenant_id=:t AND status='active' ORDER BY ozon_client_id",
        {"t": NEW})
    assert [(r[0], r[1]) for r in rows] == [("111", "active"), ("222", "active"), ("333", "active")]
    assert _db("SELECT COUNT(*) FROM credentials WHERE tenant_id=:t", {"t": T1})[0][0] == 0
    assert _db("SELECT COUNT(*) FROM credentials WHERE tenant_id=:t", {"t": T2})[0][0] == 0
    # 订单缓存:PN-X 仅 1 行且 credential 指向幸存;PN-Y 保留
    pnx = _db("SELECT credential_id::text FROM ozon_orders_cache WHERE tenant_id=:t AND posting_number='PN-X'", {"t": NEW})
    assert len(pnx) == 1 and pnx[0][0] == seeded["bid"]
    assert len(_db("SELECT 1 FROM ozon_orders_cache WHERE tenant_id=:t AND posting_number='PN-Y'", {"t": NEW})) == 1
    # 任务:sku_key 前缀重写 + 同款去重保留 task2;submission 重指向
    tasks = _db("SELECT id::text, sku_key FROM ozon_product_tasks WHERE tenant_id=:t", {"t": NEW})
    assert len(tasks) == 1 and tasks[0][0] == seeded["task2"]
    assert tasks[0][1] == f"{NEW}:111:o1"
    sub = _db("SELECT submitted_task_id FROM draft_submissions WHERE store_client_id='111'")
    assert sub[0][0] == seeded["task2"]
    # 模板默认仅 1
    assert _db("SELECT COUNT(*) FROM listing_templates WHERE tenant_id=:t AND is_default", {"t": NEW})[0][0] == 1
    # 草稿:双租户都迁到 new;孤儿保留
    assert _db("SELECT COUNT(*) FROM product_drafts WHERE tenant_id=:t", {"t": NEW})[0][0] == 2
    assert _db("SELECT COUNT(*) FROM product_drafts WHERE tenant_id=:t", {"t": ORPHAN})[0][0] == 1
    # 幂等:重跑不报错、状态不变
    mig.apply(eng, MAPPING)
    assert _db("SELECT COUNT(*) FROM credentials WHERE tenant_id=:t AND status='active'", {"t": NEW})[0][0] == 3
    assert _db("SELECT COUNT(*) FROM ozon_orders_cache WHERE tenant_id=:t", {"t": NEW})[0][0] == 2

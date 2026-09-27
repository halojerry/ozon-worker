"""v0.81 card_audit 域数据层测试（PLAN-card-audit-sweep-v1 §4）— 真实 PG,不可达时 skip。

覆盖:card_audit_finding open 部分唯一索引(同卡同不变量仅一行 open/状态流转后可再开)、
_record_finding 幂等语义(open 原位更新/resolved 流转/复现新行)、
ozon_products_cache.moderate_status 加列 + _upsert_products 写入 declined。
"""
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

from services import store_sync_service  # noqa: E402
from services.card_audit_service import _record_finding  # noqa: E402
from storage.database.shared.model import CardAuditFinding, OzonProductCache  # noqa: E402
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
def eng():
    engine = create_engine(DB_URL)
    yield engine


@pytest.fixture()
def cred(eng):
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    client_id = f"7{uuid.uuid4().int % 10**7}"
    enc_key = encrypt("test-api-key", f"{tenant}:{client_id}")
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
        conn.execute(text("DELETE FROM card_audit_finding WHERE tenant_id=:t"), {"t": tenant})
        for t in ("ozon_orders_cache", "ozon_products_cache", "credential_sync_state",
                  "store_metrics_history", "store_sync_jobs"):
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": tenant})


# ── 模型与索引 ────────────────────────────────────────────────


def test_model_has_table_and_columns():
    """ORM 模型注册：表名/不变量列/状态列/部分唯一索引谓词。"""
    assert CardAuditFinding.__tablename__ == "card_audit_finding"
    cols = {c.name for c in CardAuditFinding.__table__.columns}
    assert {"id", "tenant_id", "credential_id", "ozon_product_id", "invariant",
            "severity", "detail", "status", "created_at", "updated_at"} <= cols
    idx = CardAuditFinding.__table__.indexes
    open_idx = [i for i in idx if i.name == "uq_card_audit_finding_open"]
    assert open_idx and open_idx[0].unique and open_idx[0].dialect_options["postgresql"]["where"] is not None


def test_partial_unique_index_one_open_per_pair(eng, cred):
    """同卡同不变量只允许一行 open；不同不变量/状态流转后可再开。"""
    tenant, cid = cred
    with eng.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO card_audit_finding (tenant_id, credential_id, ozon_product_id, invariant, severity, detail, status)
            VALUES (:t, :c, '999', 'declined', 'high', '{}'::jsonb, 'open')
            """
        ), {"t": tenant, "c": cid})
        # 不同不变量 → OK
        conn.execute(text(
            """
            INSERT INTO card_audit_finding (tenant_id, credential_id, ozon_product_id, invariant, severity, detail, status)
            VALUES (:t, :c, '999', 'rating_gap', 'low', '{}'::jsonb, 'open')
            """
        ), {"t": tenant, "c": cid})
    # 同 (卡, 不变量) 第二行 open → 违反部分唯一索引（独立事务：违反后事务作废）
    with pytest.raises(Exception):
        with eng.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO card_audit_finding (tenant_id, credential_id, ozon_product_id, invariant, severity, detail, status)
                VALUES (:t, :c, '999', 'declined', 'high', '{}'::jsonb, 'open')
                """
            ), {"t": tenant, "c": cid})
    # 流转出 open → 可再开
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE card_audit_finding SET status='wontfix' "
            "WHERE tenant_id=:t AND ozon_product_id='999' AND invariant='declined'"
        ), {"t": tenant})
        conn.execute(text(
            """
            INSERT INTO card_audit_finding (tenant_id, credential_id, ozon_product_id, invariant, severity, detail, status)
            VALUES (:t, :c, '999', 'declined', 'high', '{}'::jsonb, 'open')
            """
        ), {"t": tenant, "c": cid})


# ── _record_finding 幂等语义 ─────────────────────────────────


def test_record_finding_open_upsert_in_place(eng, cred):
    tenant, cid = cred
    _record_finding(tenant, cid, "111", "declined", "high", {"n": 1})
    _record_finding(tenant, cid, "111", "declined", "high", {"n": 2})
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT status, severity, detail FROM card_audit_finding "
            "WHERE tenant_id=:t AND ozon_product_id='111'"
        ), {"t": tenant}).fetchall()
    assert len(rows) == 1, "重复轮次不重复开 open 行"
    assert rows[0][0] == "open" and rows[0][2]["n"] == 2


def test_record_finding_resolved_transition_and_recurrence(eng, cred):
    tenant, cid = cred
    _record_finding(tenant, cid, "222", "rating_gap", "medium", {"rating": 60})
    _record_finding(tenant, cid, "222", "rating_gap", "low", {"rating": 60}, resolved=True)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT status FROM card_audit_finding WHERE tenant_id=:t AND ozon_product_id='222'"
        ), {"t": tenant}).fetchall()
    assert [r[0] for r in rows] == ["auto_fixed"], "open 行流转 auto_fixed，不新开"
    # 已修但复现 → 新 open 行（auto_fixed 行保留）
    _record_finding(tenant, cid, "222", "rating_gap", "medium", {"rating": 55})
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT status FROM card_audit_finding WHERE tenant_id=:t AND ozon_product_id='222' "
            "ORDER BY created_at"
        ), {"t": tenant}).fetchall()
    assert sorted(r[0] for r in rows) == ["auto_fixed", "open"]


def test_record_finding_wontfix_not_revived(eng, cred):
    """wontfix 行不被 open upsert 复活；复现另开新行。"""
    tenant, cid = cred
    _record_finding(tenant, cid, "333", "price_sanity", "medium", {"n": 1}, resolved=True)
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE card_audit_finding SET status='wontfix' WHERE tenant_id=:t"
        ), {"t": tenant})
    _record_finding(tenant, cid, "333", "price_sanity", "medium", {"n": 2})
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT status FROM card_audit_finding WHERE tenant_id=:t AND ozon_product_id='333'"
        ), {"t": tenant}).fetchall()
    assert sorted(r[0] for r in rows) == ["open", "wontfix"]


# ── ozon_products_cache.moderate_status ──────────────────────


def test_orm_products_cache_has_moderate_status():
    assert "moderate_status" in {c.name for c in OzonProductCache.__table__.columns}


def test_upsert_products_writes_moderate_status(eng, cred):
    """_upsert_products 落 statuses.moderate_status（declined 可发现底座）。"""
    tenant, cid = cred
    items = [{"product_id": "777001", "offer_id": "o1"},
             {"product_id": "777002", "offer_id": "o2"}]
    info_map = {
        "777001": {"id": 777001, "offer_id": "o1", "name": "卡A",
                   "statuses": {"moderate_status": "declined", "status": "price_sent"}},
        "777002": {"id": 777002, "offer_id": "o2", "name": "卡B",
                   "statuses": {"moderate_status": "approved"}},
    }
    store_sync_service._upsert_products(tenant, cid, items, info_map)
    with eng.connect() as conn:
        rows = {r[0]: r[1] for r in conn.execute(text(
            "SELECT product_id, moderate_status FROM ozon_products_cache WHERE tenant_id=:t"
        ), {"t": tenant}).fetchall()}
    assert rows.get("777001") == "declined"
    assert rows.get("777002") == "approved"

    # 状态翻转覆盖（approved → declined 同卡更新）
    info_map["777002"]["statuses"]["moderate_status"] = "declined"
    store_sync_service._upsert_products(tenant, cid, items, info_map)
    with eng.connect() as conn:
        v = conn.execute(text(
            "SELECT moderate_status FROM ozon_products_cache "
            "WHERE tenant_id=:t AND product_id='777002'"
        ), {"t": tenant}).scalar()
    assert v == "declined"


def test_migration_registered(eng):
    """init_data.migrate_card_audit_v081 幂等重跑 + schema_migrations 登记。"""
    from scripts.init_data import migrate_card_audit_v081
    migrate_card_audit_v081(eng)  # 二次运行 no-op
    with eng.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM schema_migrations WHERE version='v081_card_audit'"
        )).scalar()
    assert int(n) >= 1

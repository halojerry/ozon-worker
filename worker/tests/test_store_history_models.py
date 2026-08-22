"""三张历史沉淀表模型单测：StoreMetricsHistory / StoreOperationLog / SelectionInsight。

锁定（harness-store-analysis 计划 todo 1）：
1. store_metrics_history append-only → 同 store 多次 snapshot 各自成行，id 递增
2. selection_insights 唯一键 = (keyword, contributed_by_token_id) → 重复插入抛 IntegrityError
3. store_operation_log append-only → before/after/result 字段可读出

依赖真实 PG（PGDATABASE_URL），Table.create_all 建表；无双节点表则自动创建。
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine

from storage.database.shared.model import (
    Base,
    StoreMetricsHistory,
    StoreOperationLog,
    SelectionInsight,
)

PGDATABASE_URL = os.environ.get("PGDATABASE_URL")


@pytest.fixture(scope="module")
def engine():
    if not PGDATABASE_URL:
        pytest.skip("PGDATABASE_URL not set; skip real-PG model tests")
    eng = create_engine(PGDATABASE_URL)
    base_tables = set(Base.metadata.tables)
    Base.metadata.create_all(bind=eng)
    # 全量跑时同库会被其他文件污染，start 清空三张历史表（本文件测试从干净状态开始）。
    with eng.begin() as conn:
        from sqlalchemy import text
        for t in (StoreMetricsHistory, StoreOperationLog, SelectionInsight):
            conn.execute(text(f"DELETE FROM {t.__tablename__}"))
    yield eng
    Base.metadata.drop_all(
        bind=eng,
        tables=[
            m.__table__ for m in (StoreMetricsHistory, StoreOperationLog, SelectionInsight)
        ],
        checkfirst=True,
    )


@pytest.fixture()
def session(engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine, autoflush=False)
    s = Session()
    yield s
    s.rollback()
    s.close()


def test_store_metrics_append(engine, session):
    """同一 store 两条 snapshot_at 不同 → 两条各自成行，id 递增。"""
    store_id = "store-1"
    cred_id = uuid.uuid4()
    tenant = "tenant-a"

    m1 = StoreMetricsHistory(
        tenant_id=tenant,
        credential_id=cred_id,
        store_id=store_id,
        snapshot_at=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        order_count=10,
        sales_amount=1000.0,
    )
    m2 = StoreMetricsHistory(
        tenant_id=tenant,
        credential_id=cred_id,
        store_id=store_id,
        snapshot_at=datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc),
        order_count=12,
        sales_amount=1200.0,
    )
    session.add_all([m1, m2])
    session.commit()

    rows = (
        session.query(StoreMetricsHistory)
        .filter_by(store_id=store_id)
        .order_by(StoreMetricsHistory.id)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].id < rows[1].id, "append-only：后插入行 id 应更大"
    assert rows[0].snapshot_at != rows[1].snapshot_at
    assert rows[0].order_count == 10
    assert rows[0].sales_amount == 1000.0
    # 无成本字段默认 NULL（不填 0）
    assert rows[0].profit_amount is None
    assert rows[0].profit_rate is None
    # raw 默认空对象
    assert rows[0].raw == {}


def test_store_operation_append(engine, session):
    """插入一条含 before/after/result 的操作日志，可读出。"""
    op = StoreOperationLog(
        tenant_id="tenant-b",
        credential_id=uuid.uuid4(),
        store_id="store-2",
        operation="sync_prices",
        target_id="posting-001",
        before={"price": "100 RUB"},
        after={"price": "90 RUB"},
        result="success",
        error=None,
        operator="halojerry",
    )
    session.add(op)
    session.commit()

    row = (
        session.query(StoreOperationLog)
        .filter_by(target_id="posting-001")
        .one()
    )
    assert row.operation == "sync_prices"
    assert row.before == {"price": "100 RUB"}
    assert row.after == {"price": "90 RUB"}
    assert row.result == "success"
    assert row.operator == "halojerry"
    assert row.created_at is not None


def test_selection_insight_unique(engine, session):
    """同 (keyword, contributed_by_token_id) 二次插入抛 IntegrityError。"""
    token = "tk-fixed-abc"
    s1 = SelectionInsight(
        keyword="宠物饮水机",
        contributed_by_token_id=token,
        avg_price_rub=500.0,
        avg_profit_margin=0.25,
        match_1688_count=12,
        sold_count=300,
        source="fetched",
    )
    session.add(s1)
    session.commit()

    s2 = SelectionInsight(
        keyword="宠物饮水机",
        contributed_by_token_id=token,
        avg_price_rub=480.0,
        avg_profit_margin=0.30,
    )
    session.add(s2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 不同 token 可重复（唯一键含 token）
    s3 = SelectionInsight(
        keyword="宠物饮水机",
        contributed_by_token_id="tk-other-xyz",
        sold_count=10,
    )
    session.add(s3)
    session.commit()
    count = (
        session.query(SelectionInsight)
        .filter_by(keyword="宠物饮水机")
        .count()
    )
    assert count == 2


def test_unique_key_column_name_is_contributed_by_token_id():
    """唯一键列名必须用 contributed_by_token_id（不用歧义 token）。"""
    names = [c.name for c in SelectionInsight.__table__.columns]
    assert "contributed_by_token_id" in names
    uq = SelectionInsight.__table__.constraints
    cols = []
    for c in uq:
        if c.name == "uq_selection_insight_keyword_token":
            cols = [x.name for x in c.columns]
    assert cols == ["keyword", "contributed_by_token_id"]


def test_model_identity_columns():
    """三表主键均为 BigInteger Identity；前两表无业务唯一键。"""
    for model in (StoreMetricsHistory, StoreOperationLog):
        pk = list(model.__table__.primary_key.columns)
        assert len(pk) == 1 and pk[0].name == "id"
        assert any(c.identity is not None for c in pk), f"{model.__name__} id 应为 identity"
        uniq = [
            c.name
            for c in model.__table__.constraints
            if c.name and "uq_" in str(c.name)
        ]
        assert uniq == [], f"{model.__name__} 不应有业务唯一键"

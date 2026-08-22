"""v0.60: 定时同步落历史 — sync_store 末尾追加 store_metrics_history 快照。

锁定（计划 todo 2）：
1. _append_metrics_snapshot 插入一行快照，snapshot_at 非空，profit_amount/profit_rate 为 None
2. 同一 store 调两次 → 两行（append-only，snapshot_at 不同）
3. 聚合/插入抛错 → 静默降级（log warning，不 raise）
4. snapshot_at 接近 NOW()

输入输出用 mock 隔离（_append_metrics_snapshot 的聚合/商品数查询），但快照写入走真实
PG——断言的是「真行 + profit 为 None」而非「无报错」。PG 不可达时 skip。
运行：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_store_metrics_sync.py -q
"""
from __future__ import annotations

import datetime
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import store_sync_service as svc
from storage.database.shared.model import StoreMetricsHistory

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)


@pytest.fixture(scope="module")
def engine():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过快照落历史测试")
    from storage.database.shared.model import Base
    Base.metadata.create_all(bind=eng)
    yield eng


def _cleanup(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM store_metrics_history"))


def _mock_stats(overrides=None):
    base = {
        "today_orders": 3,
        "today_sales_amount": 45.9,
        "today_commission": 4.59,
        "today_profit": 41.31,
        "today_product_count": 3,
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_product_counts(overrides=None):
    base = {"product_count": 5, "low_stock_count": 2}
    if overrides:
        base.update(overrides)
    return base


def test_append_creates_snapshot(engine):
    """聚合 + 商品数查询后，store_metrics_history 插入一行，profit/profit_rate 为 None。"""
    _cleanup(engine)
    tenant = "tenant-metrics"
    cred = uuid.uuid4()

    with patch.object(svc, "get_store_stats", return_value=_mock_stats()), \
         patch.object(svc, "_product_counts", return_value=_mock_product_counts()):
        svc._append_metrics_snapshot(tenant, cred)

    with engine.connect() as conn:
        rows = conn.execute(select(StoreMetricsHistory)).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.profit_amount is None, "无成本字段 → profit_amount 必须 NULL"
    assert row.profit_rate is None, "无成本字段 → profit_rate 必须 NULL"
    assert row.snapshot_at is not None
    assert row.store_id == str(cred)
    assert row.order_count == 3
    assert row.sales_amount == 45.9
    assert row.commission_amount == 4.59
    assert row.product_count == 5
    assert row.low_stock_count == 2
    assert row.raw["stats"]["today_orders"] == 3


def test_same_store_twice_two_rows(engine):
    """同一 store 调两次 _append_metrics_snapshot → 两行（append-only，snapshot_at 不同）。"""
    _cleanup(engine)
    tenant = "tenant-metrics"
    cred = uuid.uuid4()

    with patch.object(svc, "get_store_stats", return_value=_mock_stats()), \
         patch.object(svc, "_product_counts", return_value=_mock_product_counts()):
        svc._append_metrics_snapshot(tenant, cred)
        svc._append_metrics_snapshot(tenant, cred)

    with engine.connect() as conn:
        rows = conn.execute(select(StoreMetricsHistory)).fetchall()
    assert len(rows) == 2, "append-only：同 store 两次应两行"
    assert rows[0].snapshot_at != rows[1].snapshot_at, "两次快照时间应不同"


def test_sync_failure_silent(engine):
    """聚合/插入抛错 → _append_metrics_snapshot 不 raise（log warning）。"""
    _cleanup(engine)
    tenant = "tenant-metrics"
    cred = uuid.uuid4()

    with patch.object(svc, "get_store_stats", side_effect=RuntimeError("boom")):
        svc._append_metrics_snapshot(tenant, cred)  # 不应 raise

    with engine.connect() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM store_metrics_history")).scalar()
    assert count == 0, "聚合失败不应落行"


def test_snapshot_at_now(engine):
    """snapshot_at 接近 NOW()（±30s）。"""
    _cleanup(engine)
    tenant = "tenant-metrics"
    cred = uuid.uuid4()

    with patch.object(svc, "get_store_stats", return_value=_mock_stats()), \
         patch.object(svc, "_product_counts", return_value=_mock_product_counts()):
        svc._append_metrics_snapshot(tenant, cred)

    with engine.connect() as conn:
        row = conn.execute(select(StoreMetricsHistory)).fetchone()
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = abs((row.snapshot_at - now).total_seconds())
    assert delta <= 30, f"snapshot_at 应接近 NOW()，偏差 {delta}s"

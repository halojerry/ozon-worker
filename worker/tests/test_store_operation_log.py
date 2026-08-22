"""store_operation_log `_write_operation_log` helper 单测（todo 3）。

锁定（评审 M4 / todo 3 验收）：
1. 传 before/after → 插入一行可读出，含 before/after/result
2. before=None 仍无来源 → 写 NULL + warning（断言字段为 None + 日志记录）
3. result="failed" + error 也写入（不依赖成功率）
4. 多次调用 → 多行（append-only 语义）

依赖真实 PG（PGDATABASE_URL）；表不存在则 create_all。PG 不可达 → skip。
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from sqlalchemy import create_engine, text

PGDATABASE_URL = os.environ.get("PGDATABASE_URL")

TENANT = "tenant-oplog"
CRED_ID = uuid.uuid4()
STORE_ID = "store-oplog"
OP = "update_price"
TARGET = "555-001"


@pytest.fixture(scope="module")
def engine():
    if not PGDATABASE_URL:
        pytest.skip("PGDATABASE_URL not set; skip real-PG operation-log tests")
    from storage.database.shared.model import Base, StoreOperationLog

    eng = create_engine(PGDATABASE_URL)
    Base.metadata.create_all(bind=eng)
    yield eng
    # 清理本模块测试数据 + 表（drop 仅作用在模块级建表的缓存表上，保底）
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM store_operation_log WHERE tenant_id=:t"),
            {"t": TENANT},
        )


@pytest.fixture()
def clean(engine):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM store_operation_log WHERE tenant_id=:t"),
            {"t": TENANT},
        )
    yield


def _count(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(
            text("SELECT COUNT(*) FROM store_operation_log WHERE tenant_id=:t"),
            {"t": TENANT},
        ).scalar() or 0)


def _rows(engine) -> list:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT operation, target_id, before, after, result, error, operator "
                 "FROM store_operation_log WHERE tenant_id=:t ORDER BY id"),
            {"t": TENANT},
        ).fetchall()


def test_write_with_before_after(engine, clean):
    """传 before/after → 插入一行可读出，含 before/after/result。"""
    from services.store_operation_log import _write_operation_log

    _write_operation_log(
        TENANT, str(CRED_ID), STORE_ID, OP, TARGET,
        before={"price": "100", "stock": 5},
        after={"price": "80", "stock": 3},
        result="success",
        operator="test-op",
    )
    assert _count(engine) == 1
    row = _rows(engine)[0]
    assert row.operation == OP
    assert row.target_id == TARGET
    assert row.before == {"price": "100", "stock": 5}
    assert row.after == {"price": "80", "stock": 3}
    assert row.result == "success"
    assert row.operator == "test-op"


def test_write_before_none_writes_null(engine, clean, caplog):
    """before=None 且 cache/Ozon 均无来源 → 写 NULL + warning（不编造）。"""
    from services.store_operation_log import _write_operation_log

    with patch("services.store_operation_log._read_before_from_cache", return_value=None), \
         patch("services.store_operation_log._read_before_from_ozon", return_value=None), \
         caplog.at_level(logging.WARNING, logger="services.store_operation_log"):
        _write_operation_log(
            TENANT, str(CRED_ID), STORE_ID, OP, TARGET,
            before=None, after={"price": "80"}, result="pending",
        )
    # 真断言：字段为 NULL（不是编造的假 before）
    row = _rows(engine)[0]
    assert row.before is None
    assert "before" in caplog.text  # warning 被记录
    assert row.result == "pending"  # 即使 pending 也落行


def test_write_failed_result(engine, clean):
    """result="failed" + error 也写入（不依赖成功率）。"""
    from services.store_operation_log import _write_operation_log

    _write_operation_log(
        TENANT, str(CRED_ID), STORE_ID, "update_stock", TARGET,
        before={"price": "100", "stock": 5},
        after={"price": "100", "stock": 0},
        result="failed", error="Ozon 4xx: stock overflow",
    )
    row = _rows(engine)[0]
    assert row.result == "failed"
    assert row.error == "Ozon 4xx: stock overflow"
    assert row.before == {"price": "100", "stock": 5}  # before 照存


def test_append_multiple(engine, clean):
    """多次调用 → 多行（append-only 语义）。"""
    from services.store_operation_log import _write_operation_log

    for i in range(3):
        _write_operation_log(
            TENANT, str(CRED_ID), STORE_ID, OP, TARGET,
            before={"price": str(i)}, after={"price": str(i + 1)},
            result="success",
        )
    assert _count(engine) == 3
    rows = _rows(engine)
    befores = [r.before for r in rows]
    assert befores == [{"price": "0"}, {"price": "1"}, {"price": "2"}]

#!/usr/bin/env python3
"""F-C01 终态写守卫单测（P0 波修 1，2026-09-09 审计发现）。

审计发现（findings.md F-C01，高）：任务终态三个 UPDATE（completed/failed/rejected）
与 _handle_failure_sync 的永久 failed 写点均 `WHERE id=:task_id` 无 status 谓词——
配合 stale 恢复/zombie_reset，旧 run 迟到的终态写可翻盘已被新 run 认领的任务
（双跑重复上架/烧额度）。

修复契约（本文件锁定）：
  1. `SupabaseTaskProcessor._write_terminal_status(task_id, status, result_json,
     error_message=None) -> bool`：唯一终态写入口，SQL 带 `AND status='running'`，
     rowcount=0（任务已被取消/重置/认领）返回 False；
  2. 认领 `_claim_next_task` 同步刷 `updated_at`（防刚认领的旧行即刻 stale 误判）。

运行（需本地 PG 5433，不可达自动 skip）：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_terminal_write_guard.py -q
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)


def _pg_available() -> bool:
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="本地 PG 不可达")


_MADE: list[str] = []


@pytest.fixture()
def engine():
    os.environ.setdefault("CREDENTIAL_MASTER_KEY", "test-key-f-c01")
    os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
    os.environ["SKIP_FAILED_REVIVE"] = "1"
    eng = create_engine(DB_URL)
    _MADE.clear()
    yield eng
    # 清理本测试造的行（逐 id，不碰他人数据）
    with eng.connect() as conn:
        for tid in _MADE:
            conn.execute(text("DELETE FROM ozon_product_tasks WHERE id = :tid"),
                         {"tid": tid})
        conn.commit()


def _mk_task(eng, status: str) -> str:
    task_id = str(uuid.uuid4())  # id 列为 uuid 类型
    _MADE.append(task_id)
    with eng.connect() as conn:
        conn.execute(text("""
            INSERT INTO ozon_product_tasks (id, tenant_id, status, payload, created_at)
            VALUES (:tid, 'f-c01-test', :st, '{}'::jsonb, NOW())
        """), {"tid": task_id, "st": status})
        conn.commit()
    return task_id


def _get_status(eng, task_id: str) -> str:
    with eng.connect() as conn:
        return conn.execute(
            text("SELECT status FROM ozon_product_tasks WHERE id = :tid"),
            {"tid": task_id},
        ).scalar()


def _processor():
    from utils.task_processor import SupabaseTaskProcessor
    return SupabaseTaskProcessor(max_concurrent=1)


def test_running_row_accepts_terminal_write(engine):
    tid = _mk_task(engine, "running")
    ok = _processor()._write_terminal_status(
        tid, "completed", json.dumps({"ok": True}))
    assert ok is True
    assert _get_status(engine, tid) == "completed"


def test_reset_row_rejects_late_terminal_write(engine):
    """核心回归：任务被 stale 重置回 pending（旧 run 仍活着）→ 旧 run 的
    迟到终态写必须被拒（返回 False，行保持 pending 等新 run 认领）。"""
    tid = _mk_task(engine, "pending")
    ok = _processor()._write_terminal_status(
        tid, "failed", json.dumps({}), error_message="late write from old run")
    assert ok is False
    assert _get_status(engine, tid) == "pending"


def test_terminal_row_not_flipped_by_late_write(engine):
    """已终态（completed）的行不得被迟到 failed/rejected 写翻盘。"""
    tid = _mk_task(engine, "completed")
    ok = _processor()._write_terminal_status(
        tid, "failed", json.dumps({}), error_message="late")
    assert ok is False
    assert _get_status(engine, tid) == "completed"


def test_cancelled_row_not_flipped(engine):
    tid = _mk_task(engine, "cancelled")
    ok = _processor()._write_terminal_status(tid, "rejected", json.dumps({}))
    assert ok is False
    assert _get_status(engine, tid) == "cancelled"


def test_claim_sql_refreshes_updated_at():
    """认领 SQL 同步刷 updated_at——防刚认领的旧 pending 行即刻满足
    30min stale 条件被清理器误判（F-C02 触发源）。源码绊线锁定。"""
    import inspect

    from utils.task_processor import SupabaseTaskProcessor
    src = inspect.getsource(SupabaseTaskProcessor._claim_next_task)
    assert "updated_at = NOW()" in src, "认领应同步刷 updated_at"


def test_permanent_failure_exhausts_retry_count(engine):
    """F-C04 回归：永久性错误（OUT_OF_QUOTA 等）落 failed 时必须把
    retry_count 推满到 max_retries——否则 zombie_reset/failed 复活谓词
    （failed AND retry_count < max_retries）会把不可重试的任务复活重跑。"""
    tid = _mk_task(engine, "running")
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE ozon_product_tasks SET retry_count = 0, max_retries = 3
            WHERE id = :tid
        """), {"tid": tid})
        conn.commit()

    out = _processor()._handle_failure_sync(
        tid, "OUT_OF_QUOTA: 余额耗尽", permanent=True)

    assert out is not None and out["retried"] is False
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, retry_count, max_retries FROM ozon_product_tasks "
            "WHERE id = :tid"), {"tid": tid}).fetchone()
    assert row[0] == "failed"
    assert row[1] >= row[2], (
        f"永久错误必须把 retry_count 推满防复活: retry={row[1]}, max={row[2]}")


def test_permanent_failure_row_not_revivable(engine):
    """复活谓词视角：永久 failed 行不满足 'failed AND retry_count<max' 条件。"""
    tid = _mk_task(engine, "running")
    _processor()._handle_failure_sync(tid, "内容违规", permanent=True)
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM ozon_product_tasks "
            "WHERE id = :tid AND status = 'failed' AND retry_count < max_retries"),
            {"tid": tid}).scalar()
    assert n == 0

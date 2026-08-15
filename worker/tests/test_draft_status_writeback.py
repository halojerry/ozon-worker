"""M0.2 draft_submissions 状态写回工具 — 单测（mock engine，仿 test_delete_draft_guard.py 风格）。

覆盖：
- map_worker_status 六种映射（completed/failed/rejected/pending_moderation/未知透传）
- writeback_submission_status 生成正确 UPDATE 且按 submitted_task_id 键
- DB 异常被吞：engine 抛异常 → 返回 None 不 raise
- error_message=None 时 SQL 含 COALESCE 保留旧值

运行（无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_draft_status_writeback.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from utils import draft_status_writeback


class FakeRow:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        if self._engine.raise_on_execute:
            raise RuntimeError("db down")
        self._engine.calls.append((str(stmt), params))
        return FakeRow()

    def commit(self):
        pass


class FakeEngine:
    def __init__(self, raise_on_execute=False):
        self.raise_on_execute = raise_on_execute
        self.calls = []

    def connect(self):
        return FakeConn(self)


def _make_engine(monkeypatch, raise_on_execute=False):
    engine = FakeEngine(raise_on_execute=raise_on_execute)
    monkeypatch.setattr(draft_status_writeback, "get_engine", lambda: engine)
    return engine


TASK_ID = "3f2a1b0c-0000-4000-8000-000000000001"


# ── map_worker_status 六种映射 ──
@pytest.mark.parametrize(
    ("worker_status", "expected"),
    [
        ("completed", "published"),
        ("failed", "failed"),
        ("rejected", "rejected"),
        ("pending_moderation", "uploading"),
        ("running", "running"),
        ("unknown_custom", "unknown_custom"),
    ],
)
def test_map_worker_status(worker_status, expected):
    assert draft_status_writeback.map_worker_status(worker_status) == expected


# ── writeback 生成正确 UPDATE 且按 submitted_task_id 键 ──
def test_writeback_update_sql_and_params(monkeypatch):
    engine = _make_engine(monkeypatch)
    draft_status_writeback.writeback_submission_status(TASK_ID, "published", error_message="ok")
    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "UPDATE draft_submissions" in sql
    assert "submitted_task_id=:task_id" in sql
    assert params["task_id"] == TASK_ID
    assert params["s"] == "published"
    assert params["e"] == "ok"


# ── error_message=None 时 SQL 含 COALESCE（保留旧值）──
def test_writeback_none_error_uses_coalesce(monkeypatch):
    engine = _make_engine(monkeypatch)
    draft_status_writeback.writeback_submission_status(TASK_ID, "failed", error_message=None)
    sql, params = engine.calls[0]
    assert "COALESCE(:e, error_message)" in sql
    assert params["e"] is None


# ── DB 异常被吞：engine 抛异常 → 返回 None 不 raise ──
def test_writeback_db_exception_swallowed(monkeypatch):
    _make_engine(monkeypatch, raise_on_execute=True)
    # 不 raise 即通过；返回值必须为 None
    result = draft_status_writeback.writeback_submission_status(TASK_ID, "published")
    assert result is None

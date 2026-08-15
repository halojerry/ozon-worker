"""M0.4 草稿删除守卫 — 单测（mock engine，仿 test_drafts_api.py mock 风格）。

覆盖：
- running 任务 → 409（草稿存在进行中的上架任务）
- pending 任务 → 409
- 无任务 → 204（删除成功）
- completed 任务 → 204（删除成功，守卫只拦 pending/running）
- 跨租户 → 404（租户隔离先于守卫）

运行（无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_delete_draft_guard.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from fastapi import HTTPException

from services import draft_service


class FakeRow:
    def __init__(self, row=None, rowcount=1):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self._engine.calls.append((sql, params))
        if self._engine.pending_rows:
            return FakeRow(self._engine.pending_rows.pop(0))
        return FakeRow(None)


class FakeEngine:
    """pending_rows 按 SELECT 顺序吐出（每个 fetchone 消费一条），DELETE 默认 rowcount=1。"""

    def __init__(self, pending_rows=None):
        self.pending_rows = list(pending_rows or [])
        self.calls = []

    def begin(self):
        return FakeConn(self)


def _make_engine(monkeypatch, pending_rows=None):
    engine = FakeEngine(pending_rows)
    monkeypatch.setattr(draft_service, "get_engine", lambda: engine)
    return engine


DRAFT_ID = "3f2a1b0c-0000-4000-8000-000000000001"


def _guard_sql(calls):
    return [sql for sql, _ in calls if "draft_submissions" in sql and "ozon_product_tasks" in sql]


# ── running 任务 → 409 ──
def test_delete_running_task_409(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[(1,), (1,)])  # 归属命中 + 守卫命中
    with pytest.raises(HTTPException) as exc:
        draft_service.delete_draft("tenant-A", DRAFT_ID)
    assert exc.value.status_code == 409
    assert "进行中的上架任务" in exc.value.detail
    assert _guard_sql(engine.calls), "必须执行守卫查询"


# ── pending 任务 → 409 ──
def test_delete_pending_task_409(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[(1,), (1,)])
    with pytest.raises(HTTPException) as exc:
        draft_service.delete_draft("tenant-A", DRAFT_ID)
    assert exc.value.status_code == 409


# ── 无任务 → 204 ──
def test_delete_no_task_success(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[(1,), None])  # 归属命中 + 守卫未命中
    draft_service.delete_draft("tenant-A", DRAFT_ID)  # 无异常 = 204
    delete_sql = [sql for sql, _ in engine.calls if "DELETE FROM product_drafts" in sql]
    assert delete_sql, "守卫未命中后必须执行 DELETE"


# ── completed 任务 → 204 ──
def test_delete_completed_task_success(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[(1,), None])  # completed 不在 pending/running
    draft_service.delete_draft("tenant-A", DRAFT_ID)
    assert any("DELETE FROM product_drafts" in sql for sql, _ in engine.calls)


# ── 跨租户 → 404 ──
def test_delete_cross_tenant_404(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[None])  # 归属未命中
    with pytest.raises(HTTPException) as exc:
        draft_service.delete_draft("tenant-A", DRAFT_ID)
    assert exc.value.status_code == 404
    assert not _guard_sql(engine.calls), "跨租户不应执行守卫查询"
"""task_progress_service.emit 单测（E-1 seq 竞态守卫，2026-09-09 审计）。

锁定：emit 在同一事务内先取 pg_advisory_xact_lock(hashtext(task_id)) 再
MAX(seq)+1——并发 emit 同 task 不再产生重复 seq；异常仍静默降级返回 None。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services import task_progress_service as tps


class _FakeConn:
    """记录语句序列的假连接：SELECT 锁 → SELECT MAX → INSERT。"""

    def __init__(self, max_seq=0):
        self.max_seq = max_seq
        self.statements: list[str] = []
        self.inserts: list[dict] = []

    def execute(self, stmt, params=None):
        sql = str(stmt).strip()
        self.statements.append(sql)
        if "pg_advisory_xact_lock" in sql:
            return _Scalar(True)
        if "MAX(seq)" in sql:
            # 模拟 SQL 表达式 COALESCE(MAX(seq),0)+1 的返回值
            return _Scalar(self.max_seq + 1)
        if "INSERT INTO task_progress_events" in sql:
            self.inserts.append(params)
            return _Scalar(None)
        raise AssertionError(f"unexpected statement: {sql}")


class _Scalar:
    def __init__(self, v):
        self._v = v

    def scalar(self):
        return self._v


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def begin(self):
        return _Ctx(self._conn)


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *a):
        return False


def test_emit_takes_advisory_lock_before_max_seq():
    conn = _FakeConn(max_seq=0)
    with mock.patch.object(tps, "get_engine", return_value=_FakeEngine(conn)):
        seq = tps.emit("task-1", "auth_node", step="verify", status="progress", message="ok")
    assert seq == 1
    assert "pg_advisory_xact_lock" in conn.statements[0], "锁必须是事务内第一条语句"
    assert any("MAX(seq)" in s for s in conn.statements[1:2]), "MAX(seq) 紧随锁后（同事务）"
    assert conn.inserts and conn.inserts[0]["s"] == 1


def test_emit_seq_increments_from_max():
    conn = _FakeConn(max_seq=7)
    with mock.patch.object(tps, "get_engine", return_value=_FakeEngine(conn)):
        seq = tps.emit("task-1", "pricing_node")
    assert seq == 8


def test_emit_degrades_silently_on_error():
    class _BoomEngine:
        def begin(self):
            raise RuntimeError("db down")
    with mock.patch.object(tps, "get_engine", return_value=_BoomEngine()):
        assert tps.emit("task-1", "x") is None, "异常必须静默降级（不阻断任务管线）"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

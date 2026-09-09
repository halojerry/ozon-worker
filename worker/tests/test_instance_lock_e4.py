"""instance_lock 单测（E-4 后台循环单实例锁，2026-09-09 审计）。

锁协议：pg_try_advisory_lock 成功 → 持连接不还池；失败 → 关连接返回 False；
异常 → fail-open 返回 True；同 name 二次调用直接 True（不触 DB）。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import instance_lock


class _FakeConn:
    def __init__(self, got):
        self._got = got
        self.closed = False
        self.executed = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        class _R:
            def __init__(self, v):
                self._v = v
            def scalar(self):
                return self._v
        return _R(self._got)

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return self._conn


def _fresh():
    """清空模块级持有表，保证用例隔离。"""
    instance_lock._held.clear()


def test_acquire_success_holds_connection_open():
    _fresh()
    conn = _FakeConn(got=True)
    engine = _FakeEngine(conn)
    with mock.patch("storage.database.db.get_engine", return_value=engine):
        assert instance_lock.try_acquire_instance_lock("cleanup", instance_lock.PERIODIC_CLEANUP) is True
    assert not conn.closed, "持锁连接不得归还/关闭（还池即释放锁）"
    assert "pg_try_advisory_lock" in conn.executed[0][0]
    assert instance_lock.PERIODIC_CLEANUP == conn.executed[0][1]["k"]


def test_acquire_contended_returns_false_and_closes():
    _fresh()
    conn = _FakeConn(got=False)
    engine = _FakeEngine(conn)
    with mock.patch("storage.database.db.get_engine", return_value=engine):
        assert instance_lock.try_acquire_instance_lock("sync", instance_lock.STORE_SYNC_LEGACY) is False
    assert conn.closed, "未抢到锁必须立即关连接归还池"


def test_second_call_same_name_short_circuits():
    _fresh()
    conn = _FakeConn(got=True)
    engine = _FakeEngine(conn)
    with mock.patch("storage.database.db.get_engine", return_value=engine):
        assert instance_lock.try_acquire_instance_lock("metrics", instance_lock.METRICS_AGGREGATION) is True
        # 第二次：直接 True，不再触 DB
        assert instance_lock.try_acquire_instance_lock("metrics", instance_lock.METRICS_AGGREGATION) is True
    assert engine.connect_calls == 1


def test_engine_failure_fails_open():
    _fresh()
    def _boom():
        raise RuntimeError("pg down")
    with mock.patch("storage.database.db.get_engine", side_effect=_boom):
        assert instance_lock.try_acquire_instance_lock("cleanup", instance_lock.PERIODIC_CLEANUP) is True, \
            "PG 不可用必须 fail-open（宁可重复跑，不可停清理）"


def test_distinct_names_independent():
    _fresh()
    conns = {"a": _FakeConn(True), "b": _FakeConn(False)}
    engines = {k: _FakeEngine(v) for k, v in conns.items()}
    with mock.patch("storage.database.db.get_engine", side_effect=lambda: engines["a"]):
        assert instance_lock.try_acquire_instance_lock("a", 1) is True
    with mock.patch("storage.database.db.get_engine", side_effect=lambda: engines["b"]):
        assert instance_lock.try_acquire_instance_lock("b", 2) is False


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

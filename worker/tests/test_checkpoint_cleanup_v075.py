"""v0.75 C2: memory.checkpoint 三表清理（BL-25 Phase 1-2 漏项，纯 mock 无 PG 依赖）。

背景：A9 S9-06 任务表 30 天归档删除（main._periodic_task_cleanup）此前不清理
langgraph checkpoint——thread_id == ozon_product_tasks.id（task_processor.py:970
``configurable={"thread_id": task_id}`` 实证），任务行删除后三表行永久孤儿。

覆盖：
1. `_purge_checkpoints`：三表各一次 ``DELETE FROM memory.<t> WHERE thread_id = ANY(:ids)``，
   顺序 checkpoints → checkpoint_blobs → checkpoint_writes（本地 PG 探针实证三表无外键，
   语义父表先删）；bind 传 Python list[str]（psycopg2 自动数组化）；返回删除总行数 + debug 日志。
2. 空列表跳过（零 execute）。
3. `_periodic_task_cleanup` 单轮：收集将删 id 的 SELECT 先于三表 purge、purge 先于任务行 DELETE。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_checkpoint_cleanup_v075.py -q
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest import mock

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main  # noqa: E402

_TABLE_ORDER = ("memory.checkpoints", "memory.checkpoint_blobs", "memory.checkpoint_writes")


class _Res:
    """execute 结果：DELETE/UPDATE 用 rowcount；SELECT 用 fetchall。"""

    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _RoundConn:
    """记录 execute 序列的假 conn；按 SQL 片段路由预设结果。"""

    def __init__(self, due_ids):
        self.executed = []  # (sql_str, params)
        self._due_ids = due_ids

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = str(sql)
        self.executed.append((s, dict(params or {})))
        if "SELECT id::text FROM ozon_product_tasks" in s:
            return _Res(rows=[(i,) for i in self._due_ids])
        return _Res(rowcount=1)

    def commit(self):
        pass


def _captured_sql(conn):
    return [s for s, _ in conn.executed]


# ============================================================
# 1/2. _purge_checkpoints 单元
# ============================================================

def test_purge_deletes_three_tables_in_order_with_any_ids():
    """三表各一次 ANY(:ids) DELETE；父表 checkpoints 先删；返回总行数。"""
    conn = mock.MagicMock()
    conn.execute.side_effect = [_Res(rowcount=2), _Res(rowcount=5), _Res(rowcount=3)]
    total = main._purge_checkpoints(conn, ["id-a", "id-b"])
    assert total == 10
    stmts = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert len(stmts) == 3
    # 顺序即 zip 断言本身：stmts[k] 必须恰为第 k 张表（checkpoints → blobs → writes）
    for stmt, table in zip(stmts, _TABLE_ORDER):
        assert f"DELETE FROM {table}" in stmt, stmt
        assert "WHERE thread_id = ANY(:ids)" in stmt, stmt
    # bind 传 Python list[str]（psycopg2 自动数组化）
    for c in conn.execute.call_args_list:
        (params,) = c.args[1:]
        assert params == {"ids": ["id-a", "id-b"]}
        assert isinstance(params["ids"], list)


def test_purge_migrations_table_never_touched():
    """checkpoint_migrations 是 langgraph 自有版本表——绝不出现清理 SQL。"""
    conn = mock.MagicMock()
    conn.execute.side_effect = [_Res(rowcount=1)] * 3
    main._purge_checkpoints(conn, ["id-a"])
    assert not any("checkpoint_migrations" in str(c.args[0]) for c in conn.execute.call_args_list)


def test_purge_empty_ids_skips_execute():
    """空列表零 execute、返回 0（删除谓词与收集谓词同 WHERE，空集是常态）。"""
    conn = mock.MagicMock()
    assert main._purge_checkpoints(conn, []) == 0
    conn.execute.assert_not_called()


def test_purge_debug_log_records_rowcount(caplog):
    """返回删除总行数并记 debug 日志（运维可见但默认不刷屏）。"""
    import logging

    conn = mock.MagicMock()
    conn.execute.side_effect = [_Res(rowcount=7), _Res(rowcount=0), _Res(rowcount=2)]
    with caplog.at_level(logging.DEBUG, logger="main"):
        total = main._purge_checkpoints(conn, ["id-a"])
    assert total == 9
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("9" in m and "checkpoint" in m.lower() for m in debug_msgs), debug_msgs


# ============================================================
# 3. _periodic_task_cleanup 单轮顺序（收集 → purge → 任务 DELETE）
# ============================================================

class _StopLoop(Exception):
    pass


def _run_one_cleanup_round(due_ids, monkeypatch):
    """驱动 _periodic_task_cleanup 恰好跑一轮：第二次 asyncio.sleep 抛 _StopLoop。"""
    import storage.database.db as db_mod

    conn = _RoundConn(due_ids)
    engine = mock.MagicMock()
    engine.connect.return_value = conn
    monkeypatch.setattr(db_mod, "get_engine", lambda: engine)
    # 24h 节流钩子直接跳过（本轮聚焦任务/checkpoint 清理序）
    monkeypatch.setattr(main, "_LAST_CACHE_SWEEP", time.time())
    monkeypatch.setattr(main, "_LAST_FX_REFRESH", time.time())
    # 任务生图缓存清理 mock（避免其内部 get_engine 混入 execute 序列——本测试用独立假 conn）
    import utils.task_image_cache as tic
    monkeypatch.setattr(tic, "cleanup_old", lambda older_than_days=7: 0)

    calls = {"n": 0}

    async def _fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _StopLoop

    with mock.patch.object(main.asyncio, "sleep", _fake_sleep), \
            pytest.raises(_StopLoop):
        asyncio.run(main._periodic_task_cleanup(interval_seconds=60))
    return conn


def test_cleanup_collects_ids_before_checkpoint_purge_before_task_delete(monkeypatch):
    """单轮顺序：SELECT 将删 id → 三表 purge → 任务行 DELETE；purge 消费收集到的 id。"""
    conn = _run_one_cleanup_round(["task-1", "task-2"], monkeypatch)
    sqls = _captured_sql(conn)
    i_collect = next(i for i, s in enumerate(sqls)
                     if "SELECT id::text FROM ozon_product_tasks" in s)
    i_task_del = next(i for i, s in enumerate(sqls)
                      if "DELETE FROM ozon_product_tasks" in s)
    purge_idx = [i for i, s in enumerate(sqls)
                 if "DELETE FROM memory." in s and "ANY(:ids)" in s]
    assert len(purge_idx) == 3, sqls
    assert i_collect < min(purge_idx) < i_task_del, sqls
    # 三表顺序 + bind 为收集到的 id 列表
    for i, table in zip(purge_idx, _TABLE_ORDER):
        assert f"DELETE FROM {table}" in sqls[i]
        assert conn.executed[i][1] == {"ids": ["task-1", "task-2"]}


def test_cleanup_skips_purge_when_nothing_due(monkeypatch):
    """无到期任务 → 不执行任何 checkpoint DELETE（空列表跳过）。"""
    conn = _run_one_cleanup_round([], monkeypatch)
    assert not any("DELETE FROM memory." in s for s in _captured_sql(conn))

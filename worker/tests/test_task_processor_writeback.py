"""M0.3: draft_submissions 状态写回接入 task_processor 4 终态点（mock engine，无需 PG）。

覆盖（TDD RED→GREEN）：
1. completed 终态 → writeback 被调 status='published'
2. failed 终态 → writeback 被调 status='failed' + error_message=_harness_error
3. rejected 终态 → writeback 被调 status='rejected'
4. handle_task_failure 重试耗尽（永久失败）→ writeback status='failed' + error_message 参数
5. 写回在 conn.commit() 之后（调用顺序断言）
6. 写回抛异常 → 任务终态落库不受影响，不 raise

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_processor_writeback.py -q
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ============================================================
# fake engine（驱动 process_next_task / handle_task_failure）
# events 记录 commit / writeback 顺序，供断言"写回在 commit 之后"
# ============================================================

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row, engine):
        self._row = row
        self._engine = engine
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), dict(params or {})))
        return _FakeResult(self._row)

    def commit(self):
        self._engine.events.append("commit")


class _FakeEngine:
    def __init__(self, row, events):
        self._row = row
        self.events = events
        self.conns = []

    def connect(self):
        conn = _FakeConn(self._row, self)
        self.conns.append(conn)
        return conn


def _make_task_row(payload=None):
    """SELECT ... FOR UPDATE SKIP LOCKED 返回的行: (id, tenant_id, priority, payload, timeout_seconds, retry_count)"""
    return (
        "task-1",        # id
        "u1",            # tenant_id
        0,               # priority
        payload or {},   # payload (JSONB → dict)
        1800,            # timeout_seconds
        0,               # retry_count
    )


def _make_failure_row(retry_count=3, max_retries=3, payload=None):
    """handle_task_failure SELECT 返回的行: (retry_count, max_retries, payload)"""
    return (retry_count, max_retries, payload or {})


def _run_process_next(graph_result, payload=None, writeback_spy=None):
    """驱动 SupabaseTaskProcessor.process_next_task，mock 图执行 + writeback spy。

    返回 (engine, writeback_calls, events)。writeback_calls 为
    [(task_id, status, error_message)]；events 记录 commit/writeback 顺序。
    """
    import utils.task_processor as tp_mod
    from utils.task_processor import SupabaseTaskProcessor

    events = []
    engine = _FakeEngine(_make_task_row(payload), events)
    calls = []

    def _spy(task_id, status, error_message=None):
        events.append("writeback")
        calls.append((task_id, status, error_message))

    async def _fake_execute(payload, timeout):
        return graph_result

    with patch.object(tp_mod, "get_supabase_client", return_value=None), \
         patch.object(tp_mod, "get_engine", return_value=engine), \
         patch.object(tp_mod, "writeback_submission_status", side_effect=_spy):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        with patch.object(proc, "execute_graph_with_timeout", _fake_execute):
            asyncio.run(proc.process_next_task())
    return engine, calls, events


def _terminal_update(engine):
    """终态分支 UPDATE 语句: (SQL, params)。"""
    conn = engine.conns[1]
    return conn.executed[0]


def _run_handle_failure_permanent(error_message="boom"):
    """驱动 handle_task_failure 重试耗尽（永久失败）分支。"""
    import utils.task_processor as tp_mod
    from utils.task_processor import SupabaseTaskProcessor

    events = []
    engine = _FakeEngine(_make_failure_row(), events)
    calls = []

    def _spy(task_id, status, err=None):
        events.append("writeback")
        calls.append((task_id, status, err))

    with patch.object(tp_mod, "get_supabase_client", return_value=None), \
         patch.object(tp_mod, "get_engine", return_value=engine), \
         patch.object(tp_mod, "writeback_submission_status", side_effect=_spy):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        asyncio.run(proc.handle_task_failure("task-1", error_message))
    return engine, calls, events


# ============================================================
# 1. completed → writeback status='published'
# ============================================================

def test_completed_writeback_published():
    engine, calls, events = _run_process_next({
        "upload_status": "success",
        "moderation_status": "approved",
    })
    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql, "终态仍应落 completed"
    assert calls == [("task-1", "published", None)], f"writeback 应映射 completed→published: {calls}"
    # 写回在 commit 之后：最后一个 commit 事件先于 writeback
    assert events[-1] == "writeback"
    assert events.count("commit") >= 1


# ============================================================
# 2. failed → writeback status='failed' + error_message=_harness_error
# ============================================================

def test_failed_writeback_failed_with_error():
    engine, calls, events = _run_process_next({
        "upload_status": "failed",
        "error_message": "[OZON_VALIDATION_FAILED] 属性错误",
        "failed_stage": "ozon_status",
    })
    sql, _params = _terminal_update(engine)
    assert "status = 'failed'" in sql, "终态仍应落 failed"
    assert calls and calls[0][0] == "task-1"
    assert calls[0][1] == "failed", f"failed→failed 映射: {calls}"
    assert calls[0][2] == "[OZON_VALIDATION_FAILED] 属性错误", \
        f"error_message 应取 _harness_error: {calls[0][2]}"
    assert events[-1] == "writeback"


# ============================================================
# 3. rejected → writeback status='rejected'
# ============================================================

def test_rejected_writeback_rejected():
    engine, calls, events = _run_process_next({
        "upload_status": "rejected_unfixable",
        "error_code": "VARIANT_MODERATE_REJECTED",
        "failed_stage": "ozon_status",
    })
    sql, _params = _terminal_update(engine)
    assert "status = 'rejected'" in sql, "终态仍应落 rejected"
    assert calls and calls[0][1] == "rejected", f"rejected→rejected 映射: {calls}"
    assert events[-1] == "writeback"


# ============================================================
# 4. handle_task_failure 重试耗尽 → writeback status='failed'
# ============================================================

def test_handle_failure_permanent_writeback_failed():
    engine, calls, events = _run_handle_failure_permanent("permanent err")
    # 终态 SQL（conns[1] 是 UPDATE status='failed'）
    sql, params = engine.conns[1].executed[0]
    assert "status = 'failed'" in sql
    assert params["error_message"] == "permanent err"
    assert calls == [("task-1", "failed", "permanent err")], \
        f"handle_task_failure 应写回 failed + error_message: {calls}"
    assert events[-1] == "writeback", "写回应在 commit 之后"


# ============================================================
# 5. 写回在 commit 之后（顺序：commit → writeback）
# ============================================================

def test_writeback_after_commit_order():
    _, _, events = _run_process_next({
        "upload_status": "success",
        "moderation_status": "approved",
    })
    # events 形如 [commit(认领), commit(终态), writeback]
    last_commit = max(i for i, e in enumerate(events) if e == "commit")
    assert events.index("writeback") > last_commit, \
        f"写回必须发生在 conn.commit() 之后: {events}"


# ============================================================
# 6. 写回抛异常 → 任务终态落库不受影响，不 raise
# ============================================================

def test_writeback_exception_does_not_break_task():
    import utils.task_processor as tp_mod
    from utils.task_processor import SupabaseTaskProcessor

    events = []
    engine = _FakeEngine(_make_task_row(), events)

    async def _fake_execute(payload, timeout):
        return {"upload_status": "success", "moderation_status": "approved"}

    def _boom(task_id, status, error_message=None):
        events.append("writeback")
        raise RuntimeError("writeback db down")

    with patch.object(tp_mod, "get_supabase_client", return_value=None), \
         patch.object(tp_mod, "get_engine", return_value=engine), \
         patch.object(tp_mod, "writeback_submission_status", side_effect=_boom):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        with patch.object(proc, "execute_graph_with_timeout", _fake_execute):
            result = asyncio.run(proc.process_next_task())

    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql, "写回异常不应影响终态落库"
    assert result is not None and result.get("upload_status") == "success", \
        "写回异常不应吞掉任务结果 / 不应 raise"
    assert events.count("commit") == 2 and events.count("writeback") == 1, \
        f"commit 仍应先于 writeback 且流程完整: {events}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)

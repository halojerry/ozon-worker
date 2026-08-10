"""v0.34 C6: shop_usage_stats 店铺使用埋点单测（mock engine/conn，不依赖真实 PG）。

覆盖：
1. 新建一行（task_count=1, approved_delta 落参）
2. 同日 ON CONFLICT DO UPDATE（task_count/approved_count EXCLUDED 增量累加 → DB 层 task_count=2）
3. 跨日新增（stat_date 由 DB CURRENT_DATE 决定，不同日期不同行）
4. 失败路径 common_errors 累积（JSONB 保留最近 5 条，成功路径不增）
5. _moderation_status_deltas 判定（approved/validation_failed/其他）
6. process_next_task 成功/失败终态钩子调用传参
7. handle_task_failure 重试耗尽钩子调用传参 + 重试分支不写埋点
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from utils.task_processor import SupabaseTaskProcessor, _moderation_status_deltas, _upsert_shop_usage


class FakeRowResult:
    def __init__(self, row=None):
        self._row = row

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
            return FakeRowResult(self._engine.pending_rows.pop(0))
        return FakeRowResult(None)

    def commit(self):
        self._engine.commits += 1


class FakeEngine:
    """模拟 PG engine：pending_rows 按 SELECT 顺序吐出（每个 SELECT 消费一条），其余无返回行。"""

    def __init__(self, pending_rows=None):
        self.pending_rows = list(pending_rows or [])
        self.calls = []
        self.commits = 0

    def connect(self):
        return FakeConn(self)


def _spy_upsert(monkeypatch, target):
    """把 task_processor._upsert_shop_usage 换成 spy，返回调用记录 [(ozon_client_id, kwargs)]。"""
    calls = []
    monkeypatch.setattr(target, "_upsert_shop_usage",
                        lambda conn, oci, **kw: calls.append((oci, kw)))
    return calls


def _make_processor(monkeypatch, pending_rows=None, graph_result=None):
    import utils.task_processor as tp

    engine = FakeEngine(pending_rows)
    monkeypatch.setattr(tp, "get_engine", lambda: engine)
    monkeypatch.setattr(tp, "get_supabase_client", lambda: None)
    if graph_result is not None:
        async def _fake_execute(self, payload, timeout):
            return graph_result

        monkeypatch.setattr(SupabaseTaskProcessor, "execute_graph_with_timeout", _fake_execute)
    processor = SupabaseTaskProcessor(max_concurrent=1)
    return processor, engine


# ── 1. 新建一行 ──
def test_new_row_insert_params():
    engine = FakeEngine()
    with engine.connect() as conn:
        _upsert_shop_usage(conn, "5371047", task_delta=1, approved_delta=1)
        conn.commit()
    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "INSERT INTO shop_usage_stats" in sql
    assert "ON CONFLICT (ozon_client_id, stat_date) DO UPDATE" in sql
    assert params["ozon_client_id"] == "5371047"
    assert params["task_delta"] == 1
    assert params["approved_delta"] == 1
    assert params["validation_failed_delta"] == 0
    assert params["common_errors"] is None
    assert params["last_error"] is None


# ── 2. 同日 ON CONFLICT UPDATE ──
def test_same_day_conflict_accumulates():
    engine = FakeEngine()
    with engine.connect() as conn:
        _upsert_shop_usage(conn, "5371047", task_delta=1, approved_delta=1)
        _upsert_shop_usage(conn, "5371047", task_delta=1, approved_delta=1)
        conn.commit()
    assert len(engine.calls) == 2
    sql, _ = engine.calls[0]
    # 同日第二次走 DO UPDATE：计数用 EXCLUDED 增量累加（DB 层 task_count=2, approved_count=2）
    assert "task_count = shop_usage_stats.task_count + EXCLUDED.task_count" in sql
    assert "approved_count = shop_usage_stats.approved_count + EXCLUDED.approved_count" in sql
    assert all(c[1]["task_delta"] == 1 for c in engine.calls)
    assert all(c[1]["approved_delta"] == 1 for c in engine.calls)


# ── 3. 跨日新增 ──
def test_cross_day_new_row():
    engine = FakeEngine()
    with engine.connect() as conn:
        _upsert_shop_usage(conn, "5371047", task_delta=1)
    sql, _ = engine.calls[0]
    # stat_date 由 DB CURRENT_DATE 决定：不同日期命中不同行（UNIQUE(ozon_client_id, stat_date)）
    assert "CURRENT_DATE" in sql
    assert "ON CONFLICT (ozon_client_id, stat_date)" in sql


# ── 4. 失败路径 common_errors 累积（最近 5 条，成功路径不增）──
def test_failure_path_common_errors_accumulate():
    engine = FakeEngine()
    with engine.connect() as conn:
        _upsert_shop_usage(conn, "5371047", error_message="err1")
        _upsert_shop_usage(conn, "5371047", error_message="err2")
        conn.commit()
    sql, _ = engine.calls[0]
    assert "jsonb_array_elements" in sql
    assert "LIMIT 5" in sql
    assert engine.calls[0][1]["common_errors"] == json.dumps(["err1"])
    assert engine.calls[0][1]["last_error"] == "err1"
    assert engine.calls[1][1]["common_errors"] == json.dumps(["err2"])
    assert engine.calls[1][1]["last_error"] == "err2"


def test_failure_path_error_truncated_to_500():
    engine = FakeEngine()
    long_err = "x" * 600
    with engine.connect() as conn:
        _upsert_shop_usage(conn, "5371047", error_message=long_err)
    _, params = engine.calls[0]
    assert len(params["last_error"]) == 500
    assert params["common_errors"] == json.dumps(["x" * 500])


def test_success_path_common_errors_not_incremented():
    engine = FakeEngine()
    with engine.connect() as conn:
        _upsert_shop_usage(conn, "5371047", approved_delta=1, error_message=None)
    sql, params = engine.calls[0]
    assert params["common_errors"] is None
    assert params["last_error"] is None
    # DO UPDATE 分支：EXCLUDED.common_errors IS NULL → 保持现有数组不变
    assert "WHEN EXCLUDED.common_errors IS NULL THEN shop_usage_stats.common_errors" in sql


# ── 5. moderation_status 判定 ──
def test_moderation_status_deltas():
    assert _moderation_status_deltas({"moderation_status": "approved"}) == (1, 0)
    assert _moderation_status_deltas({"moderation_status": "validation_failed"}) == (0, 1)
    assert _moderation_status_deltas({}) == (0, 0)
    assert _moderation_status_deltas({"moderation_status": "pending_moderation"}) == (0, 0)


# ── 6. process_next_task 成功/失败终态钩子 ──
def test_process_next_task_success_hook(monkeypatch):
    import utils.task_processor as tp

    payload = {"ozon_client_id": "5371047", "envelope": {"draft": {}}}
    row = ("t1", "tenant1", 0, payload, 1800, 0)
    processor, engine = _make_processor(monkeypatch, [row],
                                        graph_result={"moderation_status": "approved"})
    calls = _spy_upsert(monkeypatch, tp)
    result = asyncio.run(processor.process_next_task())
    assert result.get("moderation_status") == "approved"
    assert len(calls) == 1
    oci, kw = calls[0]
    assert oci == "5371047"
    assert kw["task_delta"] == 1
    assert kw["approved_delta"] == 1
    assert kw["validation_failed_delta"] == 0
    assert kw["error_message"] is None
    assert any("status = 'completed'" in c[0] for c in engine.calls)


def test_process_next_task_failed_hook(monkeypatch):
    import utils.task_processor as tp

    payload = {"ozon_client_id": "5371047", "envelope": {"draft": {}}}
    row = ("t2", "tenant1", 0, payload, 1800, 0)
    processor, engine = _make_processor(monkeypatch, [row],
                                        graph_result={
                                            "upload_status": "failed",
                                            "notice": "上架失败 [BR_x]",
                                            "failed_stage": "ozon_upload",
                                            "moderation_status": "validation_failed",
                                        })
    calls = _spy_upsert(monkeypatch, tp)
    result = asyncio.run(processor.process_next_task())
    assert result["_harness_status"] == "failed"
    assert len(calls) == 1
    oci, kw = calls[0]
    assert oci == "5371047"
    assert kw["task_delta"] == 1
    assert kw["validation_failed_delta"] == 1
    assert kw["error_message"] == "上架失败 [BR_x]"
    assert any("status = 'failed'" in c[0] for c in engine.calls)


# ── 7. handle_task_failure 重试耗尽钩子 + 重试分支不写埋点 ──
def test_handle_task_failure_hook(monkeypatch):
    import utils.task_processor as tp

    payload = {"ozon_client_id": "5371047"}
    processor, engine = _make_processor(monkeypatch, [(2, 2, payload)])
    calls = _spy_upsert(monkeypatch, tp)
    asyncio.run(processor.handle_task_failure("t3", "boom error"))
    assert len(calls) == 1
    oci, kw = calls[0]
    assert oci == "5371047"
    assert kw["task_delta"] == 1
    assert kw["error_message"] == "boom error"
    assert any("status = 'failed'" in c[0] for c in engine.calls)


def test_handle_task_failure_payload_as_json_string(monkeypatch):
    import utils.task_processor as tp

    processor, _ = _make_processor(
        monkeypatch, [(2, 2, json.dumps({"ozon_client_id": "5371047"}))])
    calls = _spy_upsert(monkeypatch, tp)
    asyncio.run(processor.handle_task_failure("t4", "err"))
    assert calls[0][0] == "5371047"


def test_handle_task_failure_retry_no_hook(monkeypatch):
    import utils.task_processor as tp

    processor, _ = _make_processor(monkeypatch, [(1, 3, {"ozon_client_id": "5371047"})])
    calls = _spy_upsert(monkeypatch, tp)
    asyncio.run(processor.handle_task_failure("t5", "temporary"))
    assert calls == []  # 重试分支（failed→pending）不写埋点

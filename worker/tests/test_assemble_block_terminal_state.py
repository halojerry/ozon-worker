"""v0.69 阻断假 completed 修复测试（TDD RED→GREEN）。

生产取证：assemble 类目阻断出口只返回 error_message/assembly_retry_count（无
upload_status、error_message 无 "[" 前缀、无 failed_stage）→ task_processor
_is_failed 判 False → 落 completed 分支——任务表/draft_submissions 显示
「completed」但实际被拦（假成功）。listing_result_log 的 derive_final_status
已正确记 failed，终态分类与留存表互相矛盾。

修法：assemble 所有类目阻断出口统一带 failed_stage="category_match"——
_is_failed 的现有 (error_message 且 failed_stage) 条件自然命中 → failed 终态
+ draft_submissions 写回 failed。⚠️ _is_failed 判定逻辑本身不动（error_message
非空一律 failed 会误伤 pending 软成功路径）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_assemble_block_terminal_state.py -q
mock-only，无需 PG/GPU。
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ============================================================
# process_next_task fake engine（复用 test_moderation_rejected 模式）
# ============================================================

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), dict(params or {})))
        return _FakeResult(self._row)

    def commit(self):
        pass


class _FakeEngine:
    def __init__(self, row):
        self._row = row
        self.conns = []

    def connect(self):
        conn = _FakeConn(self._row)
        self.conns.append(conn)
        return conn


def _make_task_row(payload=None):
    """SELECT ... FOR UPDATE SKIP LOCKED 返回的行:
    (id, tenant_id, priority, payload, timeout_seconds, retry_count)"""
    return ("task-1", "u1", 0, payload or {}, 1800, 0)


def _run_process_next(graph_result):
    """驱动 SupabaseTaskProcessor.process_next_task，mock 图执行返回 graph_result。

    返回 (engine, writeback_calls)：writeback_calls 捕获 _writeback_status 调用。
    """
    from utils.task_processor import SupabaseTaskProcessor
    import utils.task_processor as tp_mod

    engine = _FakeEngine(_make_task_row())
    writeback_calls = []

    def _capture_writeback(task_id, status, error_message=None):
        writeback_calls.append((task_id, status, error_message))

    async def _fake_execute(payload, timeout):
        return graph_result

    with patch.object(tp_mod, "get_supabase_client", return_value=None), \
         patch.object(tp_mod, "get_engine", return_value=engine), \
         patch.object(tp_mod, "_writeback_status", side_effect=_capture_writeback):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        # 实例级 patch：plain function 非描述符，不会隐式绑定 self
        with patch.object(proc, "execute_graph_with_timeout", _fake_execute):
            asyncio.run(proc.process_next_task())
    return engine, writeback_calls


def _terminal_sql_params(engine):
    """终态分支 UPDATE 语句: (SQL, params)（conns[1] 首条执行）。"""
    conn = engine.conns[1]
    return conn.executed[0]


# ============================================================
# 1. assemble 阻断形状 → 任务分类 failed
# ============================================================

def test_assemble_block_shape_classified_failed():
    """assemble 阻断新形状（error_message + failed_stage=category_match）
    → 任务落 failed 终态（不再假 completed）。"""
    engine, _wb = _run_process_next({
        "error_message": "类目匹配失败：低置信/歧义类目经 LLM 确认后仍无可靠匹配"
                         "（需人工确认类目），阻断上架",
        "assembly_retry_count": 1,
        "match_confidence": 0.0,
        "failed_stage": "category_match",
    })
    sql, params = _terminal_sql_params(engine)
    assert "status = 'failed'" in sql, f"阻断必须落 failed 终态: {sql[:120]}"
    assert "类目匹配失败" in params["err"]


def test_old_block_shape_without_stage_is_documented_completed():
    """旧阻断形状（无 failed_stage）→ 判定不命中 → completed（修复前假成功的
    根因形状；此用例锁定 _is_failed 语义未被放宽——勿把 error_message 非空
    一律 failed）。"""
    engine, _wb = _run_process_next({
        "error_message": "类目匹配失败：低置信/歧义类目经 LLM 确认后仍无可靠匹配",
        "assembly_retry_count": 1,
        "match_confidence": 0.0,
    })
    sql, _params = _terminal_sql_params(engine)
    assert "status = 'completed'" in sql


# ============================================================
# 2. draft_submissions 写回 failed
# ============================================================

def test_draft_writeback_failed_on_assemble_block():
    """failed 终态分支必须把 draft_submissions 写回 failed（采集箱状态一致）。"""
    _engine, wb = _run_process_next({
        "error_message": "类目匹配失败：无候选类目",
        "assembly_retry_count": 1,
        "failed_stage": "category_match",
    })
    assert wb, "draft 写回未被调用"
    task_id, status, err = wb[0]
    assert task_id == "task-1"
    assert status == "failed", f"采集箱应写回 failed: {wb}"
    assert "类目匹配失败" in (err or "")


# ============================================================
# 3. assemble 所有类目阻断出口带 failed_stage（源级锁定）
# ============================================================

_BLOCK_MARKERS = [
    "产品标题为空，无法进行类目匹配",
    "类目匹配失败：无候选类目",
    "LLM fallback 无可靠结果",
    "jieba搜索+LLM均无可靠结果",
    "候选类目为敏感类目(成人用品",   # R1 veto 出口
    "低置信/歧义类目经 LLM 确认后仍无可靠匹配",  # R2b 出口
    "类目相似度低于接受门槛",
    "类目匹配失败：type_id 无效",
]


def test_all_category_block_exits_carry_failed_stage():
    """assemble 每个类目阻断 return 必须带 failed_stage="category_match"。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    for m in _BLOCK_MARKERS:
        i = src.index(m)
        region = src[i:i + 600]
        assert '"failed_stage": "category_match"' in region, \
            f"阻断出口缺 failed_stage: {m}"


# ============================================================
# 4. 软成功路径回归不破
# ============================================================

def test_pending_soft_success_regression():
    """pending 软成功（审核中）不得被误标 failed——仍是 completed。"""
    engine, wb = _run_process_next({
        "upload_status": "pending",
        "moderation_status": "pending",
        "product_id": "123456",
        "error_message": "",
    })
    sql, _params = _terminal_sql_params(engine)
    assert "status = 'completed'" in sql
    assert wb and wb[0][1] == "completed"


def test_warning_error_without_stage_not_failed():
    """无 failed_stage 的警告性 error_message（WARNING 级 Ozon 错误过滤）不算失败。"""
    engine, _wb = _run_process_next({
        "upload_status": "",
        "error_message": "WARNING 级错误已过滤，不影响上架",
    })
    sql, _params = _terminal_sql_params(engine)
    assert "status = 'completed'" in sql


# ============================================================
# 5. _graph_result_is_failed 收口语义（判定逻辑不变）
# ============================================================

def test_is_failed_helper_semantics():
    """判定收口为 _graph_result_is_failed（逻辑逐字保持，可单测）。"""
    from utils.task_processor import _graph_result_is_failed
    # assemble 阻断新形状 → failed
    assert _graph_result_is_failed({
        "error_message": "类目匹配失败：无候选类目",
        "failed_stage": "category_match"}) is True
    # 旧形状/软成功不误伤
    assert _graph_result_is_failed({"error_message": "类目匹配失败：无候选类目"}) is False
    assert _graph_result_is_failed({"upload_status": "pending"}) is False
    # 原三条件保持
    assert _graph_result_is_failed({"upload_status": "failed"}) is True
    assert _graph_result_is_failed({"error_message": "[OZON_VALIDATION_FAILED] x"}) is True
    # notice 优先（v0.28.5 C2 语义保持）
    assert _graph_result_is_failed({
        "notice": "类目匹配失败", "error_message": "",
        "failed_stage": "category_match"}) is True


def test_failed_stage_channel_reaches_graph_output():
    """failed_stage 通道：GlobalState（merge reducer）→ GraphOutput 透出在位。"""
    from graphs.state import GlobalState, GraphOutput
    assert "failed_stage" in GlobalState.model_fields
    assert "failed_stage" in GraphOutput.model_fields
    # assemble 阻断 dict 可直接并入 GlobalState（LangGraph 节点返回 merge）
    st = GlobalState.model_construct()
    merged = GlobalState.model_fields["failed_stage"].metadata
    assert merged, "GlobalState.failed_stage 必须带 merge reducer（operator.add）"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: 异常")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)

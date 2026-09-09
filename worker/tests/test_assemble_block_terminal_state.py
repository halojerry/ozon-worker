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
        # F-C01 终态守卫：rowcount 语义（默认 1 = 行在 running，写落成功）
        self.rowcount = 1

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


def test_old_block_shape_without_stage_now_failed_by_missing_product():
    """旧阻断形状（无 failed_stage）→ v0.69.2 T0.4 起 completed 需真实 Ozon 商品佐证：
    无 product_id → 走 failed（「宁可多判 failed 不可假 completed」）。_is_failed
    函数语义本身未放宽（见 test_is_failed_helper_semantics：error_message 非空
    一律 failed 仍被禁止，pending 软成功靠真实 product_id 区分）。"""
    engine, wb = _run_process_next({
        "error_message": "类目匹配失败：低置信/歧义类目经 LLM 确认后仍无可靠匹配",
        "assembly_retry_count": 1,
        "match_confidence": 0.0,
    })
    sql, params = _terminal_sql_params(engine)
    assert "status = 'failed'" in sql
    assert "未创建 Ozon 商品" in params["err"]
    assert wb and wb[0][1] == "failed"


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
    """assemble 每个类目阻断 return 必须带 failed_stage="category_match"。
    v0.69 T0.3: 阻断出口收敛 _blocked_exit 统一构造（终态字段 + 尽力入采集箱）——
    出口 region 命中内联字面或 _blocked_exit( 调用均达标；构造器本体必须内联
    字面（T2.2 语义收口点，入箱 notice 不得丢 failed_stage）。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    for m in _BLOCK_MARKERS:
        i = src.index(m)
        # 回看 300 字符：_blocked_exit( 调用名在 error_message 实参之前
        region = src[max(0, i - 300):i + 600]
        assert ('"failed_stage": "category_match"' in region
                or "_blocked_exit(" in region), \
            f"阻断出口缺 failed_stage: {m}"
    i_def = src.index("def _blocked_exit(")
    assert '"failed_stage": "category_match"' in src[i_def:i_def + 900], \
        "_blocked_exit 构造器缺 failed_stage=category_match（T2.2 收口被破坏）"


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
    """无 failed_stage 的警告性 error_message（WARNING 级 Ozon 错误过滤）不算失败。
    v0.69.2 T0.4: 上架真实成功必有 product_id——fixture 补 pid 锁定「警告不误伤」
    本意（无 pid 的 completed 已被 T0.4 商品佐证闸收口为 failed）。"""
    engine, _wb = _run_process_next({
        "upload_status": "",
        "product_id": "123456",
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


# ============================================================
# 6. v0.69.2 T0.4: completed 终态商品佐证闸（假 completed 第二窗口收口）
#    生产实证 task 3170fd33：17s completed、product_id 空、Ozon 侧查无此品。
# ============================================================

def test_completed_without_product_id_is_failed():
    """①无 product_id、无 upload_status、无 error → 任务判 failed（旧行为
    completed 是假成功窗口——图执行完 ≠ 商品创建）。"""
    engine, wb = _run_process_next({})
    sql, params = _terminal_sql_params(engine)
    assert "status = 'failed'" in sql, f"无商品佐证必须 failed: {sql[:120]}"
    assert "未创建 Ozon 商品" in params["err"]
    assert wb and wb[0][1] == "failed", "采集箱应写回 failed"


def test_real_product_evidence_helper_semantics():
    """商品佐证判定（纯函数）：pid 缺失/0/None → False；pid==import 任务 ID →
    False（upload 向后兼容 product_id=str(task_id) + phase1 超时残留）；
    uploaded_products 对上其他真实 pid → True（多 SKU 佐证通道）。"""
    from utils.task_processor import _has_real_product_evidence as ev
    assert ev({}) is False
    assert ev({"product_id": ""}) is False
    assert ev({"product_id": "0"}) is False
    assert ev({"product_id": "None"}) is False
    assert ev({"product_id": "123"}) is True
    # import task_id 假 pid（moderation pending 不是佐证——phase1 超时路径也写 pending）
    assert ev({"product_id": "735122001", "ozon_task_id": "735122001",
               "moderation_status": "pending"}) is False
    assert ev({"product_id": "735122001", "import_task_id": "735122001"}) is False
    # uploaded_products 有其他真实 pid → 佐证成立
    assert ev({"product_id": "735122001", "ozon_task_id": "735122001",
               "uploaded_products": [{"product_id": "999888777"}]}) is True
    # pid 与任务 ID 不同 → 真实
    assert ev({"product_id": "999", "ozon_task_id": "735122001",
               "moderation_status": ""}) is True


def test_import_task_id_as_product_id_is_failed():
    """③product_id == import task_id 且无佐证 → failed（Ozon 侧查无此品的
    假 completed 根因形状：upload 节点 product_id=str(task_id) 向后兼容 +
    ozon_status phase1 轮询超时残留）。"""
    engine, _wb = _run_process_next({
        "upload_status": "success",
        "product_id": "735122001",
        "ozon_task_id": "735122001",
        "moderation_status": "pending",
    })
    sql, params = _terminal_sql_params(engine)
    assert "status = 'failed'" in sql
    assert "未创建 Ozon 商品" in params["err"]


def test_import_task_id_with_real_uploaded_product_completed():
    """product_id 是任务 ID 但 uploaded_products 对上真实商品 → 仍 completed。"""
    engine, wb = _run_process_next({
        "upload_status": "success",
        "product_id": "735122001",
        "ozon_task_id": "735122001",
        "uploaded_products": [{"product_id": "999888777", "sku_id": "s1"}],
    })
    sql, _params = _terminal_sql_params(engine)
    assert "status = 'completed'" in sql
    assert wb and wb[0][1] == "completed"


# ============================================================
# 7. v0.69.2 T0.4b: auth 失败带 failed_stage（假 completed 第三窗口收口）
# ============================================================

def test_auth_failure_shape_classified_failed():
    """④auth 失败形状（error_message 非空 + failed_stage=auth）→ failed。
    修复前 AuthOutput 无 failed_stage，三项失败判定全不命中 → 假 completed。"""
    from utils.task_processor import _graph_result_is_failed
    assert _graph_result_is_failed({
        "error_message": "Token not found",
        "error_code": "AUTH_INVALID",
        "failed_stage": "auth",
    }) is True
    engine, wb = _run_process_next({
        "error_message": "Token not found",
        "error_code": "AUTH_INVALID",
        "failed_stage": "auth",
    })
    sql, params = _terminal_sql_params(engine)
    assert "status = 'failed'" in sql
    assert "Token not found" in params["err"]
    assert wb and wb[0][1] == "failed"


def test_auth_node_failure_exits_carry_failed_stage():
    """源级锁定：AuthOutput 契约含 failed_stage 字段；auth_node 失败出口必须
    带 failed_stage="auth"（成功出口恒空）。"""
    import inspect
    from graphs.state import AuthOutput
    from graphs.nodes import auth_node as auth_mod
    assert "failed_stage" in AuthOutput.model_fields
    src = inspect.getsource(auth_mod)
    # auth_node 失败出口共 10 处（token 缺失/supabase 非 200/token 不存在/
    # 无 user_id/用户查询失败/用户不存在/余额不足/MXOU 失败/HTTP 异常/兜底异常）
    assert src.count('failed_stage="auth"') == 10, \
        f"auth_node 失败出口 failed_stage=\"auth\" 数量应为 10: {src.count('failed_stage=\"auth\"')}"


def test_follow_update_with_product_id_completed():
    """⑤follow/UPDATE（import-by-sku 复制）product_id 为已存在商品 ID →
    天然过佐证闸 → completed（回归不误伤）。"""
    engine, wb = _run_process_next({
        "upload_status": "success",
        "moderation_status": "pending",
        "product_id": "3807171071",
    })
    sql, _params = _terminal_sql_params(engine)
    assert "status = 'completed'" in sql
    assert wb and wb[0][1] == "completed"


def test_approved_success_without_pid_now_failed_fixture_contract():
    """approved + upload success 但无 product_id → failed（旧 fixture 形状
    {"moderation_status":"approved"} 已不代表可 completed——真实 approved 必有
    商品 ID，无 ID 即假成功）。"""
    engine, _wb = _run_process_next({
        "upload_status": "success",
        "moderation_status": "approved",
    })
    sql, _params = _terminal_sql_params(engine)
    assert "status = 'failed'" in sql


def test_import_task_id_channels_reach_graph_output():
    """ozon_task_id/import_task_id 透传通道：GlobalState 既有 channel 加进
    GraphOutput 即透传（无需改节点），task_processor 佐证闸的数据前提。"""
    from graphs.state import GlobalState, GraphOutput
    assert "ozon_task_id" in GlobalState.model_fields
    assert "import_task_id" in GlobalState.model_fields
    assert "ozon_task_id" in GraphOutput.model_fields, \
        "GraphOutput 缺 ozon_task_id（佐证闸对照不到 import 任务 ID）"
    assert "import_task_id" in GraphOutput.model_fields, \
        "GraphOutput 缺 import_task_id（import-by-sku 任务 ID 对照缺失）"


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

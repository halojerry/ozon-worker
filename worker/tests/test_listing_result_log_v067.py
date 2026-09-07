"""v0.67: 上架结果留存分析表 + P1-6 审计关联修复测试。

覆盖（TDD RED→GREEN）：
1. writer 纯函数：pipeline_source 三态（graph/discover/follow）+ final_status 四态判定
2. writer DB 单测：真 PG insert → 断言行字段（1688 侧/Ozon 侧/归因）
3. 幂等：同 task_db_id 两次写 → 一行且更新（on_conflict_do_update）
4. GraphOutput/GlobalState/WrapperOutput errors/notice 透出字段存在
5. _log_match_attempt task_id：mock config thread_id → 写入值=thread_id（P1-6）
6. _log_match_attempt source_url 落库（draft.purchase_url）

运行:
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
      PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_listing_result_log_v067.py -q
    # PG 不可用时 DB 集成用例自动 skip（纯函数用例仍跑）
"""
import os
import sys
import uuid
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest


# ============================================================
# fixtures / helpers
# ============================================================

def _payload(pipeline="graph", draft_extra=None, extensions=None):
    draft = {
        "item_id": "1688-102939",
        "title": "透明硅胶手机壳",
        "sku_id": "sku-A1",
        "purchase_url": "https://detail.1688.com/offer/102939.html",
        "purchase_cost": 12.5,
        "supplier": "义乌市某硅胶厂",
        "source_category_path": "数码配件 > 手机配件 > 手机壳",
        "source_category_id": 12345,
        "weight": 180,
        "dimensions": {"length": 150, "width": 80, "height": 10},
        "variants": [],
    }
    if draft_extra:
        draft.update(draft_extra)
    if extensions is None:
        extensions = {}
    return {
        "token": "sk-test",
        "ozon_client_id": "5371047",
        "ozon_api_key": "key",
        "envelope": {"draft": draft, "source": {"purchase_url": draft["purchase_url"]},
                     "extensions": extensions},
    }


def _graph_result(upload_status="success", moderation_status="approved",
                  product_id="", errors=None, error_message="", pricing=None):
    pi = pricing if pricing is not None else {
        "price": 199.0, "old_price": 259.0, "promo_price": 159.0,
        "currency_code": "RUB", "margin_rate": 2.0,
    }
    return {
        "upload_status": upload_status,
        "moderation_status": moderation_status,
        "product_id": product_id or "123456789",
        "errors": errors or [],
        "error_message": error_message,
        "pricing_info": pi,
        "final_attributes": [{"attribute_id": 85}, {"attribute_id": 9048}],
    }


def _pg_reachable() -> bool:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(os.environ["PGDATABASE_URL"])
        with eng.connect() as c:
            c.execute(text("SELECT 1")).scalar()
        eng.dispose()
        return True
    except Exception:
        return False


_PG = _pg_reachable()


@pytest.fixture()
def listing_tbl():
    """确保 listing_result_log 表存在（真 PG 用例用），用毕清理测试残留。"""
    if not _PG:
        pytest.skip("本地 PG 不可用，跳过 DB 集成用例")
    from sqlalchemy import create_engine, text
    from storage.database.shared.model import Base
    eng = create_engine(os.environ["PGDATABASE_URL"])
    Base.metadata.create_all(bind=eng)
    test_ids = []

    def _cleanup():
        if test_ids:
            with eng.connect() as c:
                for tid in test_ids:
                    c.execute(text("DELETE FROM listing_result_log WHERE task_db_id=:id"), {"id": tid})
                c.commit()
        eng.dispose()

    yield eng, test_ids
    _cleanup()


def _insert_row(eng, payload, graph_result, task_db_id=None, tenant_id="u1"):
    from utils.listing_result_log import write_listing_result_log
    tid = task_db_id or str(uuid.uuid4())
    write_listing_result_log(
        payload=payload, graph_result=graph_result,
        task_db_id=tid, tenant_id=tenant_id, retry_count=2,
    )
    return tid


def _read_row(eng, task_db_id):
    """用 ORM 读回（JSONB 自动解码），返回 ListingResultLog 实例或 None。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from storage.database.shared.model import ListingResultLog
    with Session(eng) as s:
        return s.execute(
            select(ListingResultLog).where(ListingResultLog.task_db_id == task_db_id)
        ).scalar_one_or_none()


# ============================================================
# 1. 纯函数：pipeline_source 三态 + final_status 四态
# ============================================================

def test_pipeline_source_graph():
    from utils.listing_result_log import pipeline_source
    assert pipeline_source(_payload(pipeline="graph")) == "graph"


def test_pipeline_source_discover():
    """follow_sell=True 无 follow_type（skill discover 信封）→ discover"""
    from utils.listing_result_log import pipeline_source
    p = _payload(extensions={"follow_sell": True})
    assert pipeline_source(p) == "discover"


def test_pipeline_source_follow():
    """follow_sell=True + follow_type（真跟卖 hand/api）→ follow"""
    from utils.listing_result_log import pipeline_source
    p = _payload(extensions={"follow_sell": True, "follow_type": "hand"})
    assert pipeline_source(p) == "follow"


def test_final_status_approved():
    from utils.listing_result_log import derive_final_status
    assert derive_final_status(_graph_result(upload_status="success",
                                             moderation_status="approved")) == "approved"


def test_final_status_pending():
    """upload=success 但审核非 approved（imported/后台仍审）→ pending"""
    from utils.listing_result_log import derive_final_status
    assert derive_final_status(_graph_result(upload_status="success",
                                             moderation_status="pending")) == "pending"
    assert derive_final_status(_graph_result(upload_status="imported",
                                             moderation_status="")) == "pending"


def test_final_status_declined():
    """moderation=error 或 errors 非空 → declined（approved 优先，防修复后残留旧 errors 误判）"""
    from utils.listing_result_log import derive_final_status
    assert derive_final_status(_graph_result(moderation_status="error")) == "declined"
    assert derive_final_status(_graph_result(
        upload_status="failed", moderation_status="",
        errors=[{"code": "INVALID_ATTRIBUTE_VALUE", "attribute_id": 8229}])) == "declined"
    # rejected/declined moderation → declined（对齐 task_processor._mod_rejected）
    assert derive_final_status(_graph_result(
        upload_status="rejected_unfixable", moderation_status="rejected")) == "declined"
    assert derive_final_status(_graph_result(
        upload_status="success", moderation_status="declined")) == "declined"
    # approved 最高优先：即使上游残留旧 errors（修复成功路径），仍记 approved 防误判
    assert derive_final_status(_graph_result(
        upload_status="success", moderation_status="approved",
        errors=[{"code": "INVALID_ATTRIBUTE_VALUE"}])) == "approved"


def test_final_status_failed():
    from utils.listing_result_log import derive_final_status
    assert derive_final_status(_graph_result(upload_status="failed",
                                             moderation_status="")) == "failed"


def test_source_leaf_last_segment():
    from utils.listing_result_log import source_category_leaf_of
    assert source_category_leaf_of("数码配件 > 手机配件 > 手机壳") == "手机壳"
    assert source_category_leaf_of("") == ""


# ============================================================
# 2. writer DB 单测（真 PG）：双侧字段落库
# ============================================================

def test_writer_inserts_bilateral_fields(listing_tbl):
    eng, test_ids = listing_tbl
    p = _payload(extensions={"follow_sell": True, "follow_type": "hand"})
    gr = _graph_result(upload_status="success", moderation_status="approved",
                       errors=[{"code": "X", "attribute_id": 1}])
    tid = _insert_row(eng, p, gr, tenant_id="user_9")
    test_ids.append(tid)

    row = _read_row(eng, tid)
    assert row is not None
    # 1688 侧
    assert row.task_db_id == tid
    assert row.tenant_id == "user_9"
    assert row.ozon_client_id == "5371047"
    assert row.source_url == "https://detail.1688.com/offer/102939.html"
    assert row.source_item_id == "1688-102939"
    assert row.source_sku_id == "sku-A1"
    assert row.source_title_cn == "透明硅胶手机壳"
    assert row.supplier == "义乌市某硅胶厂"
    assert row.source_category_path == "数码配件 > 手机配件 > 手机壳"
    assert row.source_category_leaf == "手机壳"
    assert row.source_category_id == 12345
    assert row.purchase_cost == pytest.approx(12.5)
    # Ozon 侧
    assert row.ozon_product_id == "123456789"
    assert row.price == pytest.approx(199.0)
    assert row.old_price == pytest.approx(259.0)
    assert row.promo_price == pytest.approx(159.0)
    assert row.currency_code == "RUB"
    assert row.weight_g == 180
    assert row.dims_mm["length"] == 150
    # 归因
    assert row.final_status == "approved"
    assert row.moderation_status == "approved"
    assert row.pipeline_source == "follow"
    assert row.retry_count == 2
    assert row.errors == [{"code": "X", "attribute_id": 1}]
    assert row.completed_at is not None


def test_writer_declined_status_and_truncation(listing_tbl):
    eng, test_ids = listing_tbl
    p = _payload()  # graph pipeline
    long_msg = "x" * 5000
    gr = _graph_result(upload_status="failed", moderation_status="error",
                       error_message=long_msg,
                       errors=[{"code": "INVALID_ATTRIBUTE_VALUE", "attribute_id": 8229}])
    tid = _insert_row(eng, p, gr)
    test_ids.append(tid)
    row = _read_row(eng, tid)
    assert row.final_status == "declined"
    assert row.error_code is not None or row.error_message is not None
    # error_message 截 2000
    assert row.error_message == "x" * 2000
    assert row.pipeline_source == "graph"


# ============================================================
# 3. 幂等：同 task_db_id 两次写 → 一行且更新
# ============================================================

def test_writer_upsert_idempotent(listing_tbl):
    eng, test_ids = listing_tbl
    tid = str(uuid.uuid4())
    test_ids.append(tid)
    p1 = _payload(draft_extra={"title": "旧标题", "supplier": "老厂"})
    _insert_row(eng, p1, _graph_result(moderation_status="approved"), task_db_id=tid)
    p2 = _payload(draft_extra={"title": "新标题", "supplier": "新厂"})
    _insert_row(eng, p2, _graph_result(upload_status="failed", moderation_status="error",
                                       error_message="boom"), task_db_id=tid)

    from sqlalchemy import text
    with eng.connect() as c:
        cnt = c.execute(text(
            "SELECT COUNT(*) FROM listing_result_log WHERE task_db_id=:id"
        ), {"id": tid}).scalar()
    assert cnt == 1, f"同 task_db_id 两次写应只留一行: {cnt}"
    row = _read_row(eng, tid)
    assert row.source_title_cn == "新标题", "upsert 应更新最新事实"
    assert row.supplier == "新厂"
    assert row.final_status == "declined", "upsert 应更新终态归因"


# ============================================================
# 4. GraphOutput / GlobalState / Wrapper errors·notice 透出字段
# ============================================================

def test_graph_output_has_errors_notice():
    from graphs.state import GraphOutput
    assert "errors" in GraphOutput.model_fields
    assert "notice" in GraphOutput.model_fields


def test_global_state_has_notice_channel():
    from graphs.state import GlobalState
    assert "notice" in GlobalState.model_fields


def test_wrapper_output_has_errors_notice():
    from graphs.state import ValidationRetryWrapperOutput
    from graphs.validation_retry_loop import ValidationRetryLoopOutput as VRLOut
    assert "errors" in ValidationRetryWrapperOutput.model_fields
    assert "notice" in ValidationRetryWrapperOutput.model_fields
    assert "errors" in VRLOut.model_fields
    assert "notice" in VRLOut.model_fields
    # 构造一个带 errors 的子图输出（确认字段可用不被 pydantic 拒）
    o = VRLOut(is_valid=False, upload_status="failed", moderation_status="error",
               errors=[{"code": "INVALID_ATTRIBUTE_VALUE"}], notice="属性不正确:已修正")
    assert o.errors[0]["code"] == "INVALID_ATTRIBUTE_VALUE"
    assert o.notice == "属性不正确:已修正"


def test_wrapper_node_forwards_errors_notice():
    """validation_retry_wrapper_node 应把子图 errors/notice 透传到 WrapperOutput。"""
    import graphs.nodes.validation_retry_wrapper_node as vrn
    from graphs.state import ValidationRetryWrapperInput, ValidationRetryWrapperOutput

    sent = {"is_valid": False, "retry_count": 1, "upload_status": "failed",
            "moderation_status": "error", "product_id": "123",
            "ozon_payload": {}, "validation_errors": [],
            "error_message": "raw", "errors": [{"code": "INVALID_ATTRIBUTE_VALUE"}],
            "notice": "属性不正确:已按字典值修正",
            "description_category_id": "", "type_id": "", "final_attributes": [],
            "attributes_schema": [], "category_match_meta": {}}

    state = ValidationRetryWrapperInput()
    runtime = SimpleNamespace(context=SimpleNamespace())
    with mock.patch.object(vrn.validation_retry_loop, "invoke", return_value=sent):
        out = vrn.validation_retry_wrapper_node(state, {}, runtime)
    assert isinstance(out, ValidationRetryWrapperOutput)
    assert out.errors == [{"code": "INVALID_ATTRIBUTE_VALUE"}]
    assert out.notice == "属性不正确:已按字典值修正"


# ============================================================
# 5. P1-6: _log_match_attempt task_id = config thread_id + source_url
# ============================================================

class _FakeCur:
    def __init__(self):
        self.params = None
        self.sql = None

    def execute(self, sql, params):
        self.sql = str(sql)
        self.params = params


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCur()
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_log_match_attempt_uses_thread_id_and_source_url():
    from graphs.nodes.assemble_ozon_product_node import _log_match_attempt

    fake_conn = _FakeConn()
    state = SimpleNamespace(
        task_id="ingest-random-uuid-should-be-ignored",
        draft={"purchase_url": "https://detail.1688.com/offer/102939.html"},
    )
    config = {"configurable": {"thread_id": "task-db-uuid-0001"}}
    result = {"description_category_id": 17028959, "type_id": 96513}

    with mock.patch("psycopg2.connect", return_value=fake_conn):
        _log_match_attempt(state, "透明手机壳", "数码配件>手机壳", "手机壳 透明",
                           result, "L1", 0.7, [], config=config)

    assert "category_match_log" in fake_conn.cur.sql
    params = fake_conn.cur.params
    assert params[0] == "task-db-uuid-0001", \
        f"task_id 应为 config thread_id（P1-6）: {params[0]}"
    assert params[3] == "https://detail.1688.com/offer/102939.html", \
        f"source_url 应从 draft.purchase_url 落库: {params[3]}"
    assert fake_conn.commits == 1


def test_log_match_attempt_fallback_to_state_task_id():
    """无 config（老调用方/单测直调）→ 回退 state.task_id，不崩。"""
    from graphs.nodes.assemble_ozon_product_node import _log_match_attempt

    fake_conn = _FakeConn()
    state = SimpleNamespace(task_id="legacy-task-id", draft={"purchase_url": ""})
    result = {"description_category_id": 17028959, "type_id": 96513}
    with mock.patch("psycopg2.connect", return_value=fake_conn):
        _log_match_attempt(state, "t", "c", "kw", result, "Skill", 0.9, [], config=None)
    assert fake_conn.cur.params[0] == "legacy-task-id"
    assert fake_conn.cur.params[3] is None


# ============================================================
# 6. task_processor 挂点存在（回归：终态不因 writer 报错而失败）
# ============================================================

def test_task_processor_terminal_calls_listing_writer():
    """三终态分支经 _write_listing_result_log（非致命）。驱动 failed 分支断言调用。"""
    import asyncio
    import utils.task_processor as tp_mod
    from utils.task_processor import SupabaseTaskProcessor

    class _FakeResult:
        def fetchone(self):
            return (
                "task-1",        # id
                "u1",            # tenant_id
                0,               # priority
                {"envelope": {"draft": {"title": "测试"}, "extensions": {}},
                 "ozon_client_id": "1"},   # payload
                1800,            # timeout_seconds
                0,               # retry_count
            )

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            return _FakeResult()

        def commit(self):
            pass

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    calls = []

    async def _fake_execute(payload, timeout):
        return {"upload_status": "failed", "error_message": "[ERR] x",
                "failed_stage": "ozon_status"}

    with mock.patch.object(tp_mod, "get_supabase_client", return_value=None), \
         mock.patch.object(tp_mod, "get_engine", return_value=_FakeEngine()), \
         mock.patch.object(tp_mod, "_write_listing_result_log",
                           side_effect=lambda *a, **k: calls.append((a, k))):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        with mock.patch.object(proc, "execute_graph_with_timeout", _fake_execute):
            asyncio.run(proc.process_next_task())

    assert len(calls) == 1, f"failed 终态应调用 listing writer: {calls}"
    _args, _kwargs = calls[0]
    assert _args[2] == "task-1", f"task_db_id 应为任务行 uuid: {_args}"
    assert _args[3] == "u1", f"tenant_id 应为 u1: {_args}"


def test_task_processor_writer_exception_non_fatal():
    """writer 抛异常（PG 不可用等）→ 终态不 raise、任务照常返回。"""
    import asyncio
    import utils.task_processor as tp_mod
    from utils.task_processor import SupabaseTaskProcessor

    class _FakeResult:
        def fetchone(self):
            return (
                "task-1", "u1", 0,
                {"envelope": {"draft": {}, "extensions": {}}, "ozon_client_id": "1"},
                1800, 0,
            )

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            return _FakeResult()

        def commit(self):
            pass

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    async def _fake_execute(payload, timeout):
        return {"upload_status": "success", "moderation_status": "approved",
                "product_id": "123456"}  # v0.69.2 T0.4: completed 需真实商品佐证

    def _boom(*a, **k):
        raise RuntimeError("PG down")

    with mock.patch.object(tp_mod, "get_supabase_client", return_value=None), \
         mock.patch.object(tp_mod, "get_engine", return_value=_FakeEngine()), \
         mock.patch("utils.listing_result_log.write_listing_result_log", side_effect=_boom):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        with mock.patch.object(proc, "execute_graph_with_timeout", _fake_execute):
            result = asyncio.run(proc.process_next_task())

    assert result is not None and result.get("upload_status") == "success", \
        "writer 异常不应破坏任务终态"


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

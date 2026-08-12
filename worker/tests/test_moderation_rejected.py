"""P0-2: 审核被拒(rejected)状态透明化 + 重新提交端点（mock-only，无需 PG/GPU）。

1. 终态分支：graph_result.upload_status="rejected_unfixable" / moderation_status
   in ("rejected","declined") → 任务状态落 rejected（此前被埋没为 completed），
   result.moderation_rejected=True；upload_status="failed" 仍走 failed（无回归）；
   success/approved 仍走 completed（无回归）。
2. resubmit_task 端点：rejected/failed → 复制原载荷重新入队（parent_task_id +
   extensions.image_regen=True + sku_key 派生）；completed → 409
   TASK_NOT_RESUBMITTABLE；不存在 → 404 TASK_NOT_FOUND。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_moderation_rejected.py -q
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ============================================================
# process_next_task 用 fake engine（驱动终态分支）
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
    """SELECT ... FOR UPDATE SKIP LOCKED 返回的行: (id, tenant_id, priority, payload, timeout_seconds, retry_count)"""
    return (
        "task-1",        # id
        "u1",            # tenant_id
        0,               # priority
        payload or {},   # payload (JSONB → dict)
        1800,            # timeout_seconds
        0,               # retry_count
    )


def _run_process_next(graph_result):
    """驱动 SupabaseTaskProcessor.process_next_task，mock 图执行返回 graph_result。

    engine.connect() 被调用 2 次：conns[0]=认领任务(SELECT+UPDATE running)，
    conns[1]=终态分支(UPDATE terminal + shop_usage)。
    """
    from utils.task_processor import SupabaseTaskProcessor
    import utils.task_processor as tp_mod

    engine = _FakeEngine(_make_task_row())

    async def _fake_execute(payload, timeout):
        return graph_result

    with patch.object(tp_mod, "get_supabase_client", return_value=None), \
         patch.object(tp_mod, "get_engine", return_value=engine):
        proc = SupabaseTaskProcessor(max_concurrent=1)
        # 实例级 patch：plain function 非描述符，不会隐式绑定 self
        with patch.object(proc, "execute_graph_with_timeout", _fake_execute):
            asyncio.run(proc.process_next_task())
    return engine


def _terminal_update(engine):
    """终态分支 UPDATE 语句: (SQL, params)。"""
    conn = engine.conns[1]
    return conn.executed[0]


# ============================================================
# 终态分支：rejected
# ============================================================

def test_rejected_unfixable_marks_task_rejected():
    """upload_status=rejected_unfixable → status='rejected' + result.moderation_rejected=True。"""
    engine = _run_process_next({
        "upload_status": "rejected_unfixable",
        "error_code": "VARIANT_MODERATE_REJECTED",
        "failed_stage": "ozon_status",
    })
    sql, params = _terminal_update(engine)
    assert "status = 'rejected'" in sql
    result = json.loads(params["result_json"])
    assert result["moderation_rejected"] is True
    assert result["_harness_status"] == "rejected"
    assert params["err"], "被拒任务应落 error_message 供用户查看拒绝原因"


def test_moderation_status_rejected_marks_task_rejected():
    """moderation_status=rejected → status='rejected' + result.moderation_rejected=True。"""
    engine = _run_process_next({
        "moderation_status": "rejected",
        "upload_status": "error",
        "error_message": "商品被审核拒绝",
    })
    sql, params = _terminal_update(engine)
    assert "status = 'rejected'" in sql
    result = json.loads(params["result_json"])
    assert result["moderation_rejected"] is True


def test_moderation_status_declined_marks_task_rejected():
    """moderation_status=declined → 同样落 rejected。"""
    engine = _run_process_next({
        "moderation_status": "declined",
        "upload_status": "error",
    })
    sql, _params = _terminal_update(engine)
    assert "status = 'rejected'" in sql


# ============================================================
# 终态分支：无回归
# ============================================================

def test_upload_failed_still_failed():
    """upload_status=failed → 仍走 failed（无回归，rejected 判定不得吞掉真实失败）。"""
    engine = _run_process_next({
        "upload_status": "failed",
        "error_message": "[OZON_VALIDATION_FAILED] 属性错误",
        "failed_stage": "ozon_status",
    })
    sql, params = _terminal_update(engine)
    assert "status = 'failed'" in sql
    result = json.loads(params["result_json"])
    assert "moderation_rejected" not in result


def test_success_still_completed():
    """success/approved → 仍走 completed（无回归）。"""
    engine = _run_process_next({
        "upload_status": "success",
        "moderation_status": "approved",
    })
    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql


# ============================================================
# resubmit_task 端点
# ============================================================

class _FakeTaskStatusProcessor:
    """task_processor 替身：get_task_status + submit_task。"""

    def __init__(self, task_status):
        self.task_status = task_status
        self.submit_calls = []
        self.next_id = "task-resub-1"

    async def get_task_status(self, task_id):
        return self.task_status

    async def submit_task(self, **kwargs):
        self.submit_calls.append(kwargs)
        return self.next_id


class _ResubmitRequest:
    """携带 token 的重提交请求替身。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _resubmit_body(token="sk-tok123"):
    return {"token": token}


def _task_status(status, payload=None, tenant_id="u1"):
    return {
        "id": "task-old",
        "tenant_id": tenant_id,
        "status": status,
        "priority": 0,
        "payload": payload or {},
        "result": None,
        "error_message": None,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": None,
        "updated_at": None,
        "started_at": None,
        "completed_at": None,
        "timeout_seconds": 1800,
        "progress": None,
    }


def _original_payload():
    return {
        "token": "sk-tok123",
        "ozon_client_id": "c1",
        "ozon_api_key": "k1",
        "envelope": {
            "draft": {"item_id": "1688abc", "title": "测试商品"},
            "extensions": {"margin_rate": 0.25},
        },
    }


def test_resubmit_rejected_creates_new_pending():
    """rejected 任务 → 新任务入队：parent_task_id + image_regen=True + sku_key 派生。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("rejected", _original_payload()))
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp["ok"] is True
    assert resp["task_id"] == "task-resub-1"
    assert len(proc.submit_calls) == 1
    kwargs = proc.submit_calls[0]
    assert kwargs["tenant_id"] == "u1"
    new_payload = kwargs["payload"]
    assert new_payload["parent_task_id"] == "task-old"
    assert new_payload["envelope"]["extensions"]["image_regen"] is True
    assert kwargs["sku_key"] == "u1:c1:1688abc"


def test_resubmit_failed_creates_new_pending():
    """failed 任务 → 同样可重试（resubmit 是 rejected/failed 双态入口）。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("failed", _original_payload()))
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp["ok"] is True
    kwargs = proc.submit_calls[0]
    assert kwargs["payload"]["parent_task_id"] == "task-old"
    assert kwargs["payload"]["envelope"]["extensions"]["image_regen"] is True


def test_resubmit_completed_rejected_with_409():
    """completed 任务 → 409 + TASK_NOT_RESUBMITTABLE，不入队。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("completed", _original_payload()))
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["ok"] is False
    assert body["error_code"] == "TASK_NOT_RESUBMITTABLE"
    assert proc.submit_calls == []


def test_resubmit_not_found_404():
    """任务不存在 → 404 + TASK_NOT_FOUND。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(None)
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("ghost", _ResubmitRequest(_resubmit_body()))
        )

    assert resp.status_code == 404
    body = json.loads(resp.body)
    assert body["error_code"] == "TASK_NOT_FOUND"
    assert proc.submit_calls == []


def test_resubmit_cross_tenant_blocked():
    """v0.38.1 安全：调用者 token 归属租户 ≠ 任务 tenant_id → 404（不泄露任务存在性）。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("rejected", _original_payload(), tenant_id="victim"))
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="attacker"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp.status_code == 404
    body = json.loads(resp.body)
    assert body["error_code"] == "TASK_NOT_FOUND"
    assert proc.submit_calls == [], "跨租户重提交必须拒绝，不得复制受害者载荷重新入队"


def test_resubmit_missing_token_401():
    """v0.38.1 安全：请求体无 token → 鉴权拒绝 401，不入队。"""
    import pytest
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("rejected", _original_payload()))
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", side_effect=main_mod.HTTPException(401, "Token is required")
    ), pytest.raises(main_mod.HTTPException) as exc_info:
        asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest({}))
        )

    assert exc_info.value.status_code == 401
    assert proc.submit_calls == [], "未鉴权不得触发重提交"


def test_resubmit_sku_key_includes_store_dimension():
    """v0.38.1：sku_key 含店铺维度（与原载荷 ozon_client_id 一致）。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("rejected", _original_payload()))
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp["ok"] is True
    assert proc.submit_calls[0]["sku_key"] == "u1:c1:1688abc"


def test_resubmit_error_does_not_leak_internal_detail():
    """v0.38.1：内部异常不向客户端回传 str(e)（防路径/DB 细节泄露）。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("rejected", _original_payload()))

    async def _boom(**kwargs):
        raise RuntimeError("psycopg2 detail: /secret/db/path: relation does not exist")

    proc.submit_task = _boom
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp.status_code == 500
    body = json.loads(resp.body)
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "/secret/db/path" not in body["message"], "内部异常细节不得回传客户端"
    assert "psycopg2" not in body["message"]


def test_task_status_endpoint_rejected_progress():
    """task_status 端点对 rejected 终态返回归位进度 + 重提指引（不再显示内存残留阶段）。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_task_status("rejected"))
    with patch.object(main_mod, "task_processor", proc):
        resp = asyncio.run(main_mod.http_task_status("task-old"))

    assert resp["status"] == "rejected"
    assert resp["progress"]["stage"] == "rejected"
    assert resp["progress"]["percent"] == 100
    assert "resubmit_task" in resp["progress"]["message"]


def test_task_status_schema_accepts_rejected():
    """TaskStatus 枚举含 rejected — v1 端点(Pydantic)序列化 rejected 任务不会 500。"""
    from api.schemas import TaskStatus

    assert TaskStatus.REJECTED.value == "rejected"


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

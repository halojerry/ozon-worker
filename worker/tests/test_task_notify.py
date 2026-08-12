"""P1-4: 任务终态 webhook 通知（mock-only，无需 PG/GPU）。

TASK_NOTIFY_URL 环境变量（Server酱等任意 webhook）或 payload.notify=True
任一命中 → process_next_task 终态分支（failed/rejected/completed）落库后
调用 _send_task_notify 向 URL POST {task_id, status, product_summary,
error_message, product_id, ozon_client_id}。

覆盖：
1. 配置 TASK_NOTIFY_URL + completed → requests.post 带 task_id/status/product_summary
2. payload.notify=True + URL 配置 → 同样触发
3. payload.notify=True 但未配置 URL → 跳过（不 POST）+ warning 日志
4. 无 URL 无 notify → 不 POST（零额外行为）
5. webhook POST 抛异常 → 不传播（任务仍正常落终态）
6. rejected 终态 → 触发通知（status=rejected + error_message）
7. failed 终态 → 触发通知（status=failed + error_message）

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_notify.py -q
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

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


def _run_process_next(graph_result, payload=None, env=None):
    """驱动 SupabaseTaskProcessor.process_next_task，mock 图执行返回 graph_result。

    engine.connect() 被调用 2 次：conns[0]=认领任务(SELECT+UPDATE running)，
    conns[1]=终态分支(UPDATE terminal + shop_usage)。
    env 完全控制环境变量（clear=True），保证 TASK_NOTIFY_URL 确定性。
    """
    import utils.task_processor as tp_mod
    from utils.task_processor import SupabaseTaskProcessor

    engine = _FakeEngine(_make_task_row(payload))

    async def _fake_execute(payload, timeout):
        return graph_result

    with patch.dict(os.environ, env or {}, clear=True), \
         patch.object(tp_mod, "get_supabase_client", return_value=None), \
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
# 触发条件
# ============================================================

def test_env_url_fires_notify_on_completed():
    """配置 TASK_NOTIFY_URL + 终态 completed → requests.post 带 task_id/status/product_summary。"""
    with patch("requests.post", return_value=Mock()) as mock_post:
        engine = _run_process_next(
            {"upload_status": "success", "moderation_status": "approved", "product_id": "PID-1"},
            env={"TASK_NOTIFY_URL": "https://sctapi.ftqq.com/KEY/send"},
        )
    assert mock_post.call_count == 1
    url, kwargs = mock_post.call_args
    assert url[0] == "https://sctapi.ftqq.com/KEY/send"
    body = kwargs["json"]
    assert body["task_id"] == "task-1"
    assert body["status"] == "completed"
    assert body["product_id"] == "PID-1"
    assert body["product_summary"] and isinstance(body["product_summary"], list)
    assert kwargs.get("allow_redirects") is False, "v0.38.1: 禁重定向防 payload 转发到意外主机"
    # 通知是附加行为，终态落库不受影响
    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql


def test_payload_notify_flag_fires_when_url_set():
    """payload.notify=True + 配置 TASK_NOTIFY_URL → 同样触发（ozon_client_id/product_id 取自 payload/draft）。"""
    payload = {
        "token": "t1",
        "ozon_client_id": "c1",
        "ozon_api_key": "k1",
        "envelope": {"draft": {"item_id": "1688-1"}},
        "notify": True,
    }
    with patch("requests.post", return_value=Mock()) as mock_post:
        engine = _run_process_next(
            {"upload_status": "success", "moderation_status": "approved"},
            payload=payload,
            env={"TASK_NOTIFY_URL": "https://hook.example/send"},
        )
    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["json"]
    assert body["status"] == "completed"
    assert body["ozon_client_id"] == "c1"
    assert body["product_id"] == "1688-1"  # product_id 兜底到 draft.item_id


def test_notify_flag_without_url_skips():
    """payload.notify=True 但未配置 URL → 跳过（不 POST）+ warning 日志，不抛异常。"""
    import utils.task_processor as tp_mod

    with patch("requests.post") as mock_post, \
         patch.object(tp_mod.logger, "warning") as mock_warn:
        engine = _run_process_next(
            {"upload_status": "success", "moderation_status": "approved"},
            payload={"notify": True},
            env={},
        )
    assert mock_post.call_count == 0
    assert any("TASK_NOTIFY_URL" in str(a) for a in mock_warn.call_args)
    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql  # 任务本身不受影响


def test_no_url_no_notify_no_post():
    """无 URL 无 notify → 零额外行为（不 POST）。"""
    with patch("requests.post") as mock_post:
        engine = _run_process_next(
            {"upload_status": "success", "moderation_status": "approved"},
            env={},
        )
    assert mock_post.call_count == 0
    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql


# ============================================================
# 容错
# ============================================================

def test_notify_post_exception_never_propagates():
    """webhook POST 抛异常 → 不传播，任务仍正常落终态。"""
    import requests

    def _boom(*a, **k):
        raise requests.RequestException("webhook down")

    with patch("requests.post", side_effect=_boom):
        engine = _run_process_next(
            {"upload_status": "success", "moderation_status": "approved"},
            env={"TASK_NOTIFY_URL": "https://hook.example/send"},
        )
    sql, _params = _terminal_update(engine)
    assert "status = 'completed'" in sql


# ============================================================
# 各终态状态
# ============================================================

def test_notify_fires_on_rejected():
    """rejected 终态 → 触发通知（status=rejected + error_message 携带拒绝原因）。"""
    with patch("requests.post", return_value=Mock()) as mock_post:
        engine = _run_process_next(
            {"upload_status": "rejected_unfixable", "error_code": "VARIANT_MODERATE_REJECTED",
             "failed_stage": "ozon_status"},
            env={"TASK_NOTIFY_URL": "https://hook.example/send"},
        )
    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["json"]
    assert body["status"] == "rejected"
    assert body["error_message"]  # _harness_error 携带拒绝原因
    sql, _params = _terminal_update(engine)
    assert "status = 'rejected'" in sql


def test_notify_fires_on_failed():
    """failed 终态 → 触发通知（status=failed + error_message 携带失败原因）。"""
    with patch("requests.post", return_value=Mock()) as mock_post:
        engine = _run_process_next(
            {"upload_status": "failed", "error_message": "[OZON_VALIDATION_FAILED] 属性错误",
             "failed_stage": "ozon_status"},
            env={"TASK_NOTIFY_URL": "https://hook.example/send"},
        )
    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["json"]
    assert body["status"] == "failed"
    assert "OZON_VALIDATION_FAILED" in body["error_message"]
    sql, _params = _terminal_update(engine)
    assert "status = 'failed'" in sql


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

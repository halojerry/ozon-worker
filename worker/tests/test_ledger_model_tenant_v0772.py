"""v0.77.2 观测修复（任务一）：mxou_call_ledger 补 model 列 + tenant_id 写入透传。

背景（生产实数据）：mxou_call_ledger 列只有 id/called_at/tenant_id/token_fp/endpoint，
tenant_id 生产全空（写入点恒传 None），且无 model 列 → 无法按模型对账费用
（chat 1465 / image_gen:nano-banana-fast 325 / image_gen:gpt-image-2 72 /
image_gen:gpt-image-2.5 7 七天；model 信息编码在 endpoint 字符串里但无独立列）。

覆盖：
1. mxou_ledger_service.record_call 新写入 model 列（可空 + 截断）
2. mxou_api._record_mxou_call 的 model 解析：image_gen:<model> 从 endpoint 拆；
   chat 走显式 model 参数
3. tenant_id 逐级透传：显式参数 > 任务边界 ContextVar（logger._user_id，即
   task_processor.set_trace_context 写入的 tenant）；无上下文 → None（不落空串）
4. 红线不变：台账写失败绝不抛回业务（fire-and-forget 吞错）

运行（纯 mock，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest \
        tests/test_ledger_model_tenant_v0772.py -q
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import mxou_ledger_service as ml  # noqa: E402
import utils.mxou_api as mx  # noqa: E402
from utils.logger import clear_trace_context, set_trace_context  # noqa: E402


class _FakeResult:
    def fetchone(self):
        return None


class _FakeConn:
    """记录 executed (sql, params)；可注入失败片段。"""

    def __init__(self, executed, fail_on=None):
        self.executed = executed
        self._fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        sql_str = str(sql)
        if self._fail_on and self._fail_on in sql_str:
            raise RuntimeError(f"模拟 DB 失败: {self._fail_on}")
        self.executed.append((sql_str, dict(params or {})))
        return _FakeResult()

    def commit(self):
        pass


class _FakeEngine:
    def __init__(self, fail_on=None):
        self.executed = []
        self._fail_on = fail_on

    def _conn(self):
        return _FakeConn(self.executed, fail_on=self._fail_on)

    def connect(self):
        return self._conn()

    def begin(self):
        return self._conn()


# ============================================================
# 1. record_call：model 列写入
# ============================================================

def test_record_call_insert_includes_model_column():
    eng = _FakeEngine()
    with patch.object(ml, "get_engine", return_value=eng):
        ml.record_call(tenant_id="28", token_fp="a" * 64,
                       endpoint="image_gen:gpt-image-2.5", model="gpt-image-2.5")
    assert len(eng.executed) == 1
    sql_str, params = eng.executed[0]
    assert "model" in sql_str, "INSERT 必须包含 model 列"
    assert params["model"] == "gpt-image-2.5"


def test_record_call_model_none_ok_and_truncated():
    """model 可空（chat 未传/旧调用方）；超长 model 写入侧截断防列溢出。"""
    eng = _FakeEngine()
    with patch.object(ml, "get_engine", return_value=eng):
        ml.record_call(tenant_id=None, token_fp="fp", endpoint="chat", model=None)
    _, params = eng.executed[0]
    assert params["model"] is None

    eng2 = _FakeEngine()
    with patch.object(ml, "get_engine", return_value=eng2):
        ml.record_call(tenant_id=None, token_fp="fp", endpoint="chat", model="m" * 500)
    _, params2 = eng2.executed[0]
    assert len(params2["model"]) == 80, "model 列 VARCHAR(80)，写入侧截断"


# ============================================================
# 2. _record_mxou_call：model 解析（endpoint 拆分 / chat 参数）
# ============================================================

def _captured_record_call():
    """返回 (recorder, captured) —— 捕获 record_call 的 kwargs。"""
    captured = {}

    def _fake_record_call(**kwargs):
        captured.update(kwargs)

    return _fake_record_call, captured


def test_record_mxou_call_chat_uses_explicit_model():
    rec, captured = _captured_record_call()
    clear_trace_context()
    with patch("services.mxou_ledger_service.record_call", rec):
        mx._record_mxou_call("tok", "chat", model="deepseek-v4-flash-vision-exp")
    assert captured["endpoint"] == "chat"
    assert captured["model"] == "deepseek-v4-flash-vision-exp"


def test_record_mxou_call_image_model_split_from_endpoint():
    """image endpoint 内嵌 model —— 无显式 model 时从 'image_gen:<model>' 拆出。"""
    rec, captured = _captured_record_call()
    clear_trace_context()
    with patch("services.mxou_ledger_service.record_call", rec):
        mx._record_mxou_call("tok", "image_gen:nano-banana-fast")
    assert captured["endpoint"] == "image_gen:nano-banana-fast"
    assert captured["model"] == "nano-banana-fast"


def test_record_mxou_call_explicit_model_wins_over_endpoint_split():
    rec, captured = _captured_record_call()
    clear_trace_context()
    with patch("services.mxou_ledger_service.record_call", rec):
        mx._record_mxou_call("tok", "image_gen:gpt-image-2", model="gpt-image-2.5")
    assert captured["model"] == "gpt-image-2.5"


# ============================================================
# 3. tenant_id 透传：显式 > ContextVar（任务边界） > None
# ============================================================

def test_record_mxou_call_tenant_from_contextvar():
    """task_processor.set_trace_context(user_id=tenant) 后，节点内调用自动带租户。"""
    rec, captured = _captured_record_call()
    set_trace_context(user_id="28")
    try:
        with patch("services.mxou_ledger_service.record_call", rec):
            mx._record_mxou_call("tok", "chat", model="m")
    finally:
        clear_trace_context()
    assert captured["tenant_id"] == "28"


def test_record_mxou_call_explicit_tenant_wins():
    rec, captured = _captured_record_call()
    set_trace_context(user_id="28")
    try:
        with patch("services.mxou_ledger_service.record_call", rec):
            mx._record_mxou_call("tok", "chat", model="m", tenant_id="99")
    finally:
        clear_trace_context()
    assert captured["tenant_id"] == "99"


def test_record_mxou_call_tenant_none_when_no_context():
    """无任务上下文（空 ContextVar）→ None，绝不落空串。"""
    rec, captured = _captured_record_call()
    clear_trace_context()
    with patch("services.mxou_ledger_service.record_call", rec):
        mx._record_mxou_call("tok", "chat", model="m")
    assert captured["tenant_id"] is None


# ============================================================
# 4. 红线：台账写失败吞错（不阻断业务）
# ============================================================

def test_record_call_swallow_db_error_with_model():
    eng = _FakeEngine(fail_on="INSERT INTO mxou_call_ledger")
    with patch.object(ml, "get_engine", return_value=eng):
        ml.record_call(tenant_id="28", token_fp="fp", endpoint="chat", model="m")  # 不抛即过
    with patch.object(ml, "get_engine", side_effect=RuntimeError("no engine")):
        ml.record_call(tenant_id="28", token_fp="fp", endpoint="chat", model="m")

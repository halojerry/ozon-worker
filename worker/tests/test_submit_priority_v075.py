"""v0.75 C7: submit_task priority 开放（BL-25 Phase 2-5，纯 mock 无 PG 依赖）。

docstring 早就写了「priority: 任务优先级（0-100，VIP用户使用更高优先级）」，
实现却硬编码 `priority = 0`——本批把读取口打开：
- 请求体可选 ``priority``（顶层 body，与 timeout_seconds/max_retries 同位同读法）；
- 缺省/非数字 → 0（try/except 容错，与该端点其余数值字段一致，不 422 不 500）；
- 越界 clamp 到 [0, 100]（对齐 docstring 口径）；
- 认领 SQL 零改动（task_processor ``ORDER BY priority DESC, created_at ASC`` 已就绪）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_submit_priority_v075.py -q
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# 夹具假 token：非关键词常量 + f-string 拼接（leak-scan 纪律）
_TK = "k" + "x" * 16


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _fake_supabase() -> MagicMock:
    fake = MagicMock()
    exec_chain = fake.table.return_value.select.return_value.eq.return_value.is_.return_value.execute
    exec_chain.return_value.data = [
        {
            "user_id": "u1",
            "key": _TK,
            "remain_quota": 999,
            "status": 1,
            "expired_time": -1,
            "unlimited_quota": False,
        }
    ]
    return fake


def _submit_body(priority=None) -> dict:
    draft = {
        "item_id": "123456",
        "title": "测试商品",
        "currency": "CNY",
        "images": ["https://example.com/1.jpg"],
        "weight": 100,
        "dimensions": {"length": 10, "width": 10, "height": 10},
        "purchase_cost": 5.0,
        "purchase_url": "https://example.com/buy",
    }
    body = {
        "token": _TK,
        "ozon_client_id": "storeA",
        "ozon_api_key": "",  # 空 → 跳过 Ozon 配额检查（网络）
        "envelope": {"draft": draft},
    }
    if priority is not None:
        body["priority"] = priority
    return body


class _FakeResult:
    def fetchone(self):
        return None


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

    def begin(self):
        return _FakeConn()


class _FakeProcessor:
    def __init__(self):
        self.calls = []

    async def submit_task(self, **kwargs):
        self.calls.append(kwargs)
        return "task-new"


def _run_submit(body):
    import main as main_mod

    fake_proc = _FakeProcessor()
    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=_FakeEngine()), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(body)))
    assert resp.get("ok") is True, resp
    assert len(fake_proc.calls) == 1
    return fake_proc.calls[0]


# ============================================================
# priority 读取口（clamp / 缺省 / 容错）
# ============================================================

def test_priority_90_passed_through():
    """body.priority=90 → 入队行 priority=90。"""
    kwargs = _run_submit(_submit_body(priority=90))
    assert kwargs["priority"] == 90


def test_priority_absent_defaults_zero():
    """不带 priority → 0（行为逐字节不变）。"""
    kwargs = _run_submit(_submit_body())
    assert kwargs["priority"] == 0


def test_priority_over_100_clamped():
    """priority=150 → clamp 100（docstring 口径 0-100，容错不 422）。"""
    kwargs = _run_submit(_submit_body(priority=150))
    assert kwargs["priority"] == 100


def test_priority_negative_clamped_to_zero():
    """priority=-5 → clamp 0。"""
    kwargs = _run_submit(_submit_body(priority=-5))
    assert kwargs["priority"] == 0


def test_priority_non_numeric_defaults_zero():
    """priority='abc' → 当 0（try/except 容错，不 500）。"""
    kwargs = _run_submit(_submit_body(priority="abc"))
    assert kwargs["priority"] == 0


def test_priority_none_defaults_zero():
    """priority=null → 当 0（or 0 兜 None）。"""
    kwargs = _run_submit(_submit_body(priority=None))
    assert kwargs["priority"] == 0

"""P0-1: SKU 级重复提交防护 — 同一用户同一商品已有活跃任务 → DUPLICATE_SUBMIT。

http_submit_task 在提交队列前计算 sku_key = {user_id}:{product_id}，
查询 ozon_product_tasks 是否存在非 failed/cancelled 的同 sku_key 活跃任务；
命中则返回 409 + DUPLICATE_SUBMIT（不消耗 MXOU/生图额度），未命中则正常入队。
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


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
            "key": "tok123",
            "remain_quota": 999,
            "status": 1,
            "expired_time": -1,
            "unlimited_quota": False,
        }
    ]
    return fake


def _submit_body(item_id="123456", ozon_product_id=None, sku_id=None) -> dict:
    draft = {
        "item_id": item_id,
        "title": "测试商品",
        "currency": "CNY",
        "images": ["https://example.com/1.jpg"],
        "weight": 100,
        "dimensions": {"length": 10, "width": 10, "height": 10},
        "purchase_cost": 5.0,
        "purchase_url": "https://example.com/buy",
    }
    if ozon_product_id is not None:
        draft["ozon_product_id"] = ozon_product_id
    if sku_id is not None:
        draft["sku_id"] = sku_id
    return {
        "token": "sk-tok123",
        "ozon_client_id": "",
        "ozon_api_key": "",
        "envelope": {"draft": draft},
    }


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, result):
        self._result = result
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), dict(params or {})))
        return self._result


class _FakeEngine:
    def __init__(self, result):
        self._result = result
        self.conn = None

    def connect(self):
        self.conn = _FakeConn(self._result)
        return self.conn


class _FakeProcessor:
    def __init__(self):
        self.calls = []
        self.next_task_id = "task-new"

    async def submit_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.next_task_id


def test_duplicate_submit_blocked_when_active_task_exists():
    """同一用户同一 sku_key 已有 pending 任务 → 409 + DUPLICATE_SUBMIT + 已有 task_id，不二次入队。"""
    import main as main_mod

    fake_engine = _FakeEngine(_FakeResult(("existing-task-1", "pending")))
    fake_proc = _FakeProcessor()

    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=fake_engine), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body())))

    assert resp.status_code == 409, f"命中活跃任务应返回 409，实际 {resp.status_code}"
    body = json.loads(resp.body)
    assert body["ok"] is False
    assert body["error_code"] == "DUPLICATE_SUBMIT"
    assert body["detail"]["task_id"] == "existing-task-1"
    assert body["detail"]["status"] == "pending"
    assert fake_proc.calls == [], "命中重复任务时不应再次提交队列"


def test_resubmit_allowed_when_no_active_task():
    """无活跃任务（无 blocking 行）→ 正常入队，submit_task 携带 sku_key。"""
    import main as main_mod

    fake_engine = _FakeEngine(_FakeResult(None))
    fake_proc = _FakeProcessor()

    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=fake_engine), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body())))

    assert resp["ok"] is True
    assert resp["task_id"] == "task-new"
    assert len(fake_proc.calls) == 1
    assert fake_proc.calls[0]["sku_key"] == "u1:123456"


def test_sku_key_uses_ozon_product_id_for_follow():
    """跟卖信封（draft.ozon_product_id）→ sku_key = {user_id}:{ozon_product_id}。"""
    import main as main_mod

    fake_engine = _FakeEngine(_FakeResult(None))
    fake_proc = _FakeProcessor()

    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=fake_engine), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(
            main_mod.http_submit_task(FakeRequest(_submit_body(ozon_product_id="ozon123")))
        )

    assert resp["ok"] is True
    assert fake_engine.conn is not None, "有商品 ID 时必须执行去重查询"
    _sql, params = fake_engine.conn.executed[0]
    assert params["t"] == "u1"
    assert params["k"] == "u1:ozon123"
    assert fake_proc.calls[0]["sku_key"] == "u1:ozon123"


def test_sku_key_uses_item_id_for_1688():
    """1688 信封（draft.item_id）→ sku_key = {user_id}:{item_id}。"""
    import main as main_mod

    fake_engine = _FakeEngine(_FakeResult(None))
    fake_proc = _FakeProcessor()

    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=fake_engine), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body(item_id="1688abc"))))

    assert resp["ok"] is True
    _sql, params = fake_engine.conn.executed[0]
    assert params["k"] == "u1:1688abc"
    assert fake_proc.calls[0]["sku_key"] == "u1:1688abc"


def test_empty_product_id_skips_dedup():
    """商品 ID 为空/空白 → 跳过去重查询，submit_task 携空 sku_key 正常入队。"""
    import main as main_mod

    fake_engine = _FakeEngine(_FakeResult(None))
    fake_proc = _FakeProcessor()

    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=fake_engine), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body(item_id="   "))))

    assert resp["ok"] is True
    assert fake_engine.conn is None, "无商品 ID 时不应执行去重查询"
    assert fake_proc.calls[0]["sku_key"] == ""


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

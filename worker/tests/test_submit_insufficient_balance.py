"""P3: submit_task 余额不足必须返回统一错误响应（HTTP 402 + INSUFFICIENT_BALANCE）。

此前裸 raise HTTPException(402, ...) → 响应体是 {"detail": ...}，
Skill 端无法按 error_code 分流；必须改走 error_response 统一格式。
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


def _submit_body() -> dict:
    return {
        "token": "sk-tok123",
        "ozon_client_id": "",
        "ozon_api_key": "",
        "envelope": {
            "draft": {
                "item_id": "123456",
                "title": "测试商品",
                "currency": "CNY",
                "images": ["https://example.com/1.jpg"],
                "weight": 100,
                "dimensions": {"length": 10, "width": 10, "height": 10},
                "purchase_cost": 5.0,
                "purchase_url": "https://example.com/buy",
            },
        },
    }


class _FakeProcessor:
    async def submit_task(self, **kwargs):
        return "task-123"


def test_submit_task_insufficient_balance_returns_unified_402():
    """余额不足 → 统一错误响应：HTTP 402 + ok=false + INSUFFICIENT_BALANCE。"""
    import main as main_mod

    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(0.0, False)
    ), patch.object(main_mod, "task_processor", _FakeProcessor()):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body())))

    assert resp.status_code == 402, f"余额不足应返回 402，实际 {resp.status_code}"
    body = json.loads(resp.body)
    assert body["ok"] is False
    assert body["error_code"] == "INSUFFICIENT_BALANCE"
    assert "MXOU 余额不足" in body["message"]

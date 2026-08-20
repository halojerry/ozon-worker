"""P3: tokens 表 SELECT 必须带 key 列（否则 _check_mxou_balance 的 MXOU 实查分支是死代码）。

背景：auth_verify / submit_task 两个端点 SELECT tokens 表时都漏了 key 列，
导致 `token_record.get("key")` 恒为空 → MXOU 真实余额从未被查询，
永远降级到陈旧的 Supabase users.quota（issue P3: 用户欠费不被拦截）。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

EXPECTED_SELECT = "user_id, key, remain_quota, status, expired_time, unlimited_quota"


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _fake_supabase(record: dict) -> MagicMock:
    """返回 token 记录并允许事后断言 select() 的列参。"""
    fake = MagicMock()
    exec_chain = fake.table.return_value.select.return_value.eq.return_value.is_.return_value.execute
    exec_chain.return_value.data = [record]
    return fake


def _select_arg(fake: MagicMock) -> str:
    return fake.table.return_value.select.call_args.args[0]


def _valid_token_record() -> dict:
    return {
        "user_id": "u1",
        "key": "tok123",
        "remain_quota": 999,
        "status": 1,
        "expired_time": -1,
        "unlimited_quota": False,
    }


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


def test_auth_verify_select_includes_key_and_passes_key_to_balance():
    """auth_verify: tokens SELECT 带 key 列，且 key 实际传给 _check_mxou_balance。"""
    import main as main_mod

    fake = _fake_supabase(_valid_token_record())
    captured = {}

    def fake_balance(record):
        captured["record"] = record
        return 100.0, True

    with patch("main.get_supabase_client", return_value=fake), patch(
        "main._check_mxou_balance", side_effect=fake_balance
    ):
        resp = asyncio.run(main_mod.auth_verify(FakeRequest({"token": "sk-tok123"})))

    assert _select_arg(fake) == EXPECTED_SELECT, "auth_verify SELECT 必须包含 key 列"
    assert captured["record"].get("key") == "tok123", "MXOU 实查分支需要 key，否则是死代码"
    assert resp["valid"] is True


def test_submit_task_derives_tenant_and_passes_key_to_balance():
    """submit_task (v0.56 key 派生租户): 不再 SELECT tokens 表，_check_mxou_balance 收 key+user_id。"""
    import main as main_mod

    captured = {}

    def fake_balance(record):
        captured["record"] = record
        return 100.0, True

    with patch("main._check_mxou_balance", side_effect=fake_balance), patch.object(
        main_mod, "task_processor", _FakeProcessor()
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body())))

    assert captured["record"].get("key") == "tok123", "MXOU 实查分支需要 key，否则是死代码"
    assert captured["record"].get("user_id") == main_mod._key_user_id("tok123"), \
        "submit_task 按 key 派生租户（不再查 tokens 表）"
    assert resp["ok"] is True

"""P3 修复回归: auth_node 余额计算与 main.py `_check_mxou_balance` 对齐。

main.py:1094 用 `users.quota` 判定余额; auth_node 旧代码 `balance = quota - used_quota`
(used_quota 是历史累计, 判定不参与 — AGENTS.md 约定)。本测试锁定 balance=quota。

Mock 方式与 tests/test_full_pipeline_mock_images.py 一致: patch session + MXOU + 外部调用。
"""
from unittest.mock import MagicMock, patch

from graphs.nodes.auth_node import auth_node
from graphs.state import AuthInput


def _fake_resp(status: int = 200, data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data if data is not None else []
    resp.text = text
    return resp


def _make_input() -> AuthInput:
    return AuthInput(
        token="sk-test123",
        ozon_client_id="1",
        ozon_api_key="2",
        envelope=None,
    )


def test_balance_uses_quota_not_quota_minus_used_quota(monkeypatch):
    """Given: users 表 quota=100, used_quota=90; MXOU 查询不可用(None)。

    When: auth_node 正常路径走完。
    Then: balance==100(旧代码会算出 10), error_code=="".
    """
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    tok_resp = _fake_resp(200, [{"key": "test123", "user_id": "u1", "id": "t1"}])
    user_resp = _fake_resp(200, [{"quota": 100, "used_quota": 90}])
    fake_session = MagicMock()
    fake_session.get.side_effect = [tok_resp, user_resp]

    with patch("graphs.nodes.auth_node.session", fake_session), \
         patch("utils.mxou_api.get_mxou_balance", return_value=None), \
         patch("graphs.nodes.auth_node._verify_mxou_token", return_value=(True, "")), \
         patch("graphs.nodes.auth_node.query_ozon_seller_info", return_value={"currency_code": ""}):
        out = auth_node(_make_input(), config={}, runtime=None)

    assert out.balance == 100
    assert out.error_code == ""

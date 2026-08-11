"""P3 修复回归: Supabase 降级分支(fail-open)先探 MXOU 真实余额。

旧代码: Supabase 不可达 / HTTP>=500 时直接 balance=999.0 放行, 零余额用户漏拦截。
新代码: 默认 999.0 前先 get_mxou_balance(clean_token)(请求 token 即 MXOU key);
MXOU 命中 → 用真实余额(负/零 → 后续 AUTH_EXHAUSTED 语义保留); MXOU 也返回 None
(本地开发环境) → 才回退 999.0。
"""
import requests
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


def _run_failopen(monkeypatch, fake_session, mxou_balance):
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.setattr("time.sleep", lambda _s: None)  # 3 次重试的 2s 退避跳过
    with patch("graphs.nodes.auth_node.session", fake_session), \
         patch("utils.mxou_api.get_mxou_balance", return_value=mxou_balance), \
         patch("graphs.nodes.auth_node._verify_mxou_token", return_value=(True, "")), \
         patch("graphs.nodes.auth_node.query_ozon_seller_info", return_value={"currency_code": ""}):
        return auth_node(_make_input(), config={}, runtime=None)


def test_failopen_supabase_unreachable_probes_mxou(monkeypatch):
    """Given: Supabase 3 次重试全部连接失败(不可达); MXOU 返回 50。

    When: auth_node 走 supabase_unreachable fail-open 分支。
    Then: balance==50.0(MXOU 真实余额), error_code==""(决策继续)。
    """
    fake_session = MagicMock()
    fake_session.get.side_effect = requests.ConnectionError("supabase down")
    out = _run_failopen(monkeypatch, fake_session, mxou_balance=50.0)

    assert out.balance == 50.0
    assert out.error_code == ""


def test_failopen_supabase_5xx_probes_mxou(monkeypatch):
    """Given: Supabase 返回 HTTP 500; MXOU 返回 50。

    When: auth_node 走 status_code>=500 fail-open 分支。
    Then: balance==50.0, error_code==""。
    """
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_resp(500, [], "boom")
    out = _run_failopen(monkeypatch, fake_session, mxou_balance=50.0)

    assert out.balance == 50.0
    assert out.error_code == ""


def test_failopen_mxou_none_falls_back_999(monkeypatch):
    """Given: Supabase 不可达; MXOU 也返回 None(本地开发环境)。

    When: auth_node 走 supabase_unreachable fail-open 分支。
    Then: balance==999.0(保留本地 fail-open), error_code==""。
    """
    fake_session = MagicMock()
    fake_session.get.side_effect = requests.ConnectionError("supabase down")
    out = _run_failopen(monkeypatch, fake_session, mxou_balance=None)

    assert out.balance == 999.0
    assert out.error_code == ""

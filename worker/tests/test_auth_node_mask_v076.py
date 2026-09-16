"""v0.76 T5(crypto-L1): auth_node 日志只出 Api-Key 尾 4 位掩码。

背景：query_ozon_seller_info 原样打 Api-Key 前 10 位 + 完整响应体切片，
日志泄露凭证面收窄为「*** + 尾 4 位」；响应结构打印收敛为只出 keys。

终审 Fix-3 追加：Supabase token 查询 URL（tokens?key=eq.<key>）明文 key
经连接异常字符串化落日志 + 错误 response.text 整段落日志，两处收敛。
"""
import logging
from unittest.mock import MagicMock, patch

import requests

from graphs.nodes import auth_node as an


def test_mask_api_key_shape():
    assert an.mask_api_key("AKIAIOSFODNN7EXAMPLE") == "***MPLE"
    assert an.mask_api_key("abc") == "***"
    assert an.mask_api_key("") == "***"


def test_log_line_never_contains_key_prefix(caplog):
    key = "AKIAIOSFODNN7EXAMPLE"
    caplog.set_level(logging.INFO)
    an.logger.info("鉴权校验: Api-Key=%s", an.mask_api_key(key))
    assert key[:10] not in caplog.text
    assert "***MPLE" in caplog.text


def test_query_ozon_seller_info_log_masks_key_and_collapses_response(monkeypatch, caplog):
    """真实日志点验证：Api-Key 只出掩码；响应体值不落日志（只出 keys）。"""
    key = "AKIAIOSFODNN7EXAMPLE"
    marker_value = "TOPSECRETVENDORNAME"
    monkeypatch.setattr(
        an,
        "ozon_post",
        lambda *a, **k: {"company": {"currency": "RUB", "name": marker_value}},
    )
    with caplog.at_level(logging.INFO, logger="graphs.nodes.auth_node"):
        result = an.query_ozon_seller_info("12345", key)
    assert result == {"currency_code": "RUB"}
    assert key[:10] not in caplog.text
    assert key not in caplog.text
    assert "***MPLE" in caplog.text
    # 响应值不落日志：只允许出结构 keys
    assert marker_value not in caplog.text
    assert "company" in caplog.text


# ============================================================
# 终审 Fix-3: Supabase 查询失败路径凭证落日志收敛
# ============================================================

def _fake_resp(status: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = []
    resp.text = text
    return resp


def _run_auth(monkeypatch, fake_session):
    """驱动 auth_node 走 Supabase 查询路径（外部依赖全 mock）。"""
    from graphs.nodes.auth_node import auth_node
    from graphs.state import AuthInput

    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example.co")
    monkeypatch.setenv("SUPABASE_KEY", "k-test-supabase")
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with patch("graphs.nodes.auth_node.session", fake_session), \
         patch("utils.mxou_api.get_mxou_balance", return_value=None), \
         patch("graphs.nodes.auth_node._verify_mxou_token", return_value=(True, "")), \
         patch("graphs.nodes.auth_node.query_ozon_seller_info", return_value={"currency_code": ""}):
        return auth_node(
            AuthInput(token="sk-test123", ozon_client_id="1", ozon_api_key="2",
                      envelope=None),
            config={}, runtime=None,
        )


def test_supabase_conn_fail_log_never_contains_token(monkeypatch, caplog):
    """Fix-3①: 连接异常字符串含完整 URL（query 带明文 token）→ 日志剥 URL。

    Supabase 完全不可达走 fail-open 分支；降级 warning 原样拼 {retry_err}
    会把 tokens?key=eq.test123 整段落日志。修复后：明文 token 与 URL 不落，
    错误类别（连接失败）保留保排障可用性。
    """
    token_url = "https://supabase.example.co/rest/v1/tokens?key=eq.test123&select=*"
    fake_session = MagicMock()
    fake_session.get.side_effect = requests.ConnectionError(
        "HTTPSConnectionPool(host='supabase.example.co', port=443): "
        f"Max retries exceeded with url: {token_url} "
        "(Caused by NameResolutionError('Failed to resolve'))"
    )
    with caplog.at_level(logging.WARNING, logger="graphs.nodes.auth_node"):
        out = _run_auth(monkeypatch, fake_session)

    assert out.error_code == ""  # fail-open 语义不变
    assert "test123" not in caplog.text   # 明文 token 不落日志
    assert "key=eq." not in caplog.text   # 完整 URL 不落日志
    assert "Supabase连接失败" in caplog.text  # 错误类别保留


def test_supabase_token_query_error_log_truncates_response(monkeypatch, caplog):
    """Fix-3②: 非 200 错误 response.text 不再整段落日志——截断+状态码。"""
    long_body = "ERRBODY-" + "y" * 500
    fake_session = MagicMock()
    fake_session.get.return_value = _fake_resp(400, long_body)
    with caplog.at_level(logging.WARNING, logger="graphs.nodes.auth_node"):
        out = _run_auth(monkeypatch, fake_session)

    assert out.error_code == "AUTH_INVALID"
    assert "y" * 300 not in caplog.text   # 截断生效（500 字符尾部不落）
    assert "ERRBODY-" in caplog.text      # 头部保留（排障可用性）
    assert "400" in caplog.text           # 状态码保留

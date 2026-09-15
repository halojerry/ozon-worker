"""v0.76 T5(crypto-L1): auth_node 日志只出 Api-Key 尾 4 位掩码。

背景：query_ozon_seller_info 原样打 Api-Key 前 10 位 + 完整响应体切片，
日志泄露凭证面收窄为「*** + 尾 4 位」；响应结构打印收敛为只出 keys。
"""
import logging

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

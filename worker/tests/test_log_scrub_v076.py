"""v0.76 安全修复 T2：/run 系日志不落 body 原文；错误响应不回显 body/traceback（crypto-C1/api-M1）。"""
import logging
from fastapi.testclient import TestClient


def test_run_receipt_log_has_no_body(monkeypatch, caplog):
    from main import _log_request_receipt
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "stub", None, None)
    monkeypatch.setattr(logging.Logger, "handle", lambda self, r: None)
    captured = {}
    monkeypatch.setattr(logging.Logger, "info", lambda self, msg, *a, **k: captured.update(msg=msg))
    import asyncio
    class FakeReq:  # query_params 误打误撞只用于摘要
        query_params = {}
    body = b'{"token":"sk-secret123","ozon_api_key":"AK-xyz"}'  # brief 写 49，实为 48 字节——按字面量实算
    _log_request_receipt("/run", "rid-1", FakeReq(), body)
    assert "sk-secret123" not in captured["msg"]
    assert "AK-xyz" not in captured["msg"]
    assert f"body_bytes={len(body)}" in captured["msg"]


def test_invalid_json_detail_has_no_body_echo(client_factory=None):
    from main import app
    client = TestClient(app, raise_server_exceptions=False)
    # brief 原文 body 是合法 UTF-8 但残缺 JSON——会先被 _extract_token_from_body 判空 token
    # 走 401，到不了 400 分支；这里用非法 UTF-8 字节命中 /run decode-failure 400
    # （正是此前回显 body_text+traceback 的泄漏点），两种断言才能对修复前后都有效。
    r = client.post("/run", content=b'{"token":"sk-leaky-key","bad":"\xff"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert "sk-leaky-key" not in r.text
    assert "Traceback" not in r.text


def test_stream_run_and_node_run_400_no_body_echo(client_factory=None):
    """同形态泄漏点 /stream_run（原 997）/node_run（原 1086）一并锁定。"""
    from main import app
    client = TestClient(app, raise_server_exceptions=False)
    for url in ("/stream_run", "/node_run/demo"):
        r = client.post(url, content=b'{"token":"sk-leaky-key","bad":"\xff"}',
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400, url
        assert "sk-leaky-key" not in r.text, url
        assert "Traceback" not in r.text, url

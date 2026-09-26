"""v0.76 安全修复 T2：/run 系日志不落 body 原文；错误响应不回显 body/traceback（crypto-C1/api-M1）。"""
import logging
from fastapi.testclient import TestClient


def test_run_receipt_log_has_no_body(monkeypatch, caplog):
    from main import _log_request_receipt
    monkeypatch.setattr(logging.Logger, "handle", lambda self, r: None)
    captured = {}
    monkeypatch.setattr(logging.Logger, "info", lambda self, msg, *a, **k: captured.update(msg=msg))

    class FakeReq:  # query_params 误打误撞只用于摘要
        query_params = {}
    body = b'{"token":"sk-secret123","ozon_api_key":"AK-xyz"}'  # brief 写 49，实为 48 字节——按字面量实算
    _log_request_receipt("/run", "rid-1", FakeReq(), body)
    assert "sk-secret123" not in captured["msg"]
    assert "AK-xyz" not in captured["msg"]
    assert f"body_bytes={len(body)}" in captured["msg"]


def test_invalid_json_detail_has_no_body_echo():
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


def test_stream_run_and_node_run_400_no_body_echo():
    """同形态泄漏点 /stream_run（原 997）/node_run（原 1086）一并锁定。"""
    from main import app
    client = TestClient(app, raise_server_exceptions=False)
    for url in ("/stream_run", "/node_run/demo"):
        r = client.post(url, content=b'{"token":"sk-leaky-key","bad":"\xff"}',
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400, url
        assert "sk-leaky-key" not in r.text, url
        assert "Traceback" not in r.text, url


def test_receipt_log_extra_field(monkeypatch):
    """补①：extra 诊断键值对以 k=v 拼进日志行；不传则形态与基线一致。"""
    from main import _log_request_receipt
    monkeypatch.setattr(logging.Logger, "handle", lambda self, r: None)
    captured = {}
    monkeypatch.setattr(logging.Logger, "info", lambda self, msg, *a, **k: captured.update(msg=msg))

    class FakeReq:
        query_params = {}

    _log_request_receipt("/stream_run", "rid-2", FakeReq(), b"{}",
                         extra={"is_agent_project": True})
    assert "is_agent_project=True" in captured["msg"]
    _log_request_receipt("/run", "rid-3", FakeReq(), b"{}")
    assert captured["msg"].endswith("body_bytes=2")
    assert "is_agent_project" not in captured["msg"]


def test_async_storage_503_no_exception_text(monkeypatch):
    """补②：/async_run 的 503 不回显 AsyncTaskStorageError 原文。

    /task/{id} 已于 2026-09-23 退役为 410 墓碑（原实现调 async_runtime.get——真实
    AsyncTaskRuntime 早已无该方法，100% 500 死代码；旧测试用 _FakeRuntime 模拟了
    过时接口形状掩盖漂移），其 503 路径随之移除。
    """
    import main as main_mod
    from main import app
    from runtime.async_tasks import AsyncTaskStorageError
    client = TestClient(app, raise_server_exceptions=False)

    class _FakeRuntime:
        async def submit(self, **kwargs):
            raise AsyncTaskStorageError("secret-bucket-internal")

    monkeypatch.setattr(main_mod, "async_runtime", _FakeRuntime())
    monkeypatch.setattr("services.tenant_service.resolve_tenant", lambda t: "28")
    # 无 lifespan 时 async_task_config 是 stub（缺 RECURSION_LIMIT），补上避免无关 500
    monkeypatch.setattr(main_mod.async_task_config, "RECURSION_LIMIT", 25, raising=False)
    # v0.81 安全收尾（fix/sec-closeout-v081）：/async_run 补 T3 鉴权门——匿名提交
    # 已 401（test_sec_closeout_v081 锁定），本用例锁 503 形态须带 token 过门。
    r = client.post("/async_run", content=b'{"foo": "bar", "token": "sk-probe-scrub"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 503
    assert "secret-bucket-internal" not in r.text


def test_legacy_task_endpoint_returns_410():
    """/task/{id} 墓碑语义：恒 410 + 迁移指引，不触任何运行时（退役收口断言）。"""
    from main import app
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/task/anything-dead-beef")
    assert r.status_code == 410
    assert "task_status" in r.text


def test_health_503_no_exception_text(monkeypatch):
    """补②：/health 503 的 message 不回显 str(e)（可能含连接串），degraded 语义保留。"""
    from main import app
    client = TestClient(app, raise_server_exceptions=False)

    def _boom():
        raise RuntimeError("postgres://user:pass@internal-host:5432/ozon")

    monkeypatch.setattr("storage.database.db.get_engine", _boom)
    r = client.get("/api/v1/health")
    assert r.status_code == 503
    assert "internal-host" not in r.text
    assert "degraded" in r.text

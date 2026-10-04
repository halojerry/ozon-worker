"""v0.76 安全修复 T2（残留用例）：错误出口不回显敏感内容 + 历史端点墓碑语义。

platform-compat 调试面（/async_run /run /stream_run /cancel/{run_id}
/node_run /v1/chat/completions）已于 2026-10 整体退役删除——原属本文件的
/run 系回执日志脱敏（``_log_request_receipt``）与 400/503 不回显用例随端点
删除（脱敏链仅服务该层，无活端点消费）；保留两条不依赖被删层的用例：
- ``/task/{task_id}`` 410 墓碑：恒 410 + 迁移指引，不触任何运行时；
- ``GET /api/v1/health`` 503：message 不回显 str(e)（可能含连接串），degraded 语义保留。
"""
from fastapi.testclient import TestClient


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

"""v0.76 T18（cicd-H1）：8902 任务网关鉴权 + CORS 收敛 + 参数白名单 + body 上限。

覆盖：
- 纯函数：_check_auth（Bearer 精确匹配/大小写/时序安全比较语义）、
  _filter_params（白名单外丢弃）、_body_size_cap（>1MB 拒）
- _build_argv 白名单兜底闸：白名单内命令丢弃未知 flag + stderr 提示；
  白名单字典外命令（MCP 既有面）不受影响
- 端到端（真起 ThreadingHTTPServer，127.0.0.1 随机端口，manager 用 stub）：
  无 token 401 / 带 token 201 且 params 被过滤 / GET /tasks 401 /
  /health 免鉴权 200 / OPTIONS 204 无 CORS 头 / 超限 body 413 / 未知 kind 400

不依赖真实 skill subprocess / Chrome / fastmcp。
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from pounding_mcp import tasks_server
from pounding_mcp.skill_runner import _build_argv

# ── 纯函数：_check_auth ──────────────────────────────────────────────


def test_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(tasks_server, "_TASKS_TOKEN", "right-token")
    assert tasks_server._check_auth({}) is False
    assert tasks_server._check_auth({"Authorization": "Bearer wrong"}) is False
    assert tasks_server._check_auth({"Authorization": "Bearer right-token"}) is True


def test_auth_requires_bearer_prefix_and_exact_match(monkeypatch):
    monkeypatch.setattr(tasks_server, "_TASKS_TOKEN", "right-token")
    assert tasks_server._check_auth({"Authorization": "right-token"}) is False  # 缺 Bearer 前缀
    assert tasks_server._check_auth({"Authorization": "bearer right-token"}) is False  # 前缀大小写敏感
    assert tasks_server._check_auth({"Authorization": "Bearer right-token "}) is False  # 尾随空格


def test_auth_header_name_case_insensitive(monkeypatch):
    monkeypatch.setattr(tasks_server, "_TASKS_TOKEN", "right-token")
    assert tasks_server._check_auth({"authorization": "Bearer right-token"}) is True
    assert tasks_server._check_auth({"AUTHORIZATION": "Bearer right-token"}) is True


# ── 纯函数：_filter_params / _body_size_ok ───────────────────────────


def test_params_whitelist_drops_unknown():
    allow = {"url", "target_count", "auto_submit"}
    out = tasks_server._filter_params(
        {"url": "u", "auto_submit": True, "evil_flag": "x"}, allow)
    assert out == {"url": "u", "auto_submit": True}


def test_params_whitelist_none_params_returns_empty():
    assert tasks_server._filter_params(None, {"a"}) == {}
    assert tasks_server._filter_params([("a", 1)], {"a"}) == {}  # 非 dict 防御


def test_body_size_cap():
    assert tasks_server._body_size_ok(1_000_001) is False
    assert tasks_server._body_size_ok(1_000_000) is True  # 上限本身放行
    assert tasks_server._body_size_ok(999) is True


# ── _build_argv 白名单兜底闸 ─────────────────────────────────────────


def test_build_argv_drops_unknown_flags_with_stderr_hint(capsys):
    """白名单内命令：未知 flag 丢弃 + stderr 提示；白名单内 flag 原样保留。"""
    argv = _build_argv("discover_task", (),
                       {"keyword": "手套", "auto_submit": True, "evil_flag": "x"})
    assert "--keyword" in argv and "手套" in argv
    assert "--auto-submit" in argv
    assert "--evil-flag" not in argv
    err = capsys.readouterr().err
    assert "evil_flag" in err and "discover_task" in err


def test_build_argv_unknown_command_passthrough():
    """白名单字典外的命令（MCP 既有面 set_store 等）不过滤——闸不误伤 MCP 工具。"""
    argv = _build_argv("set_store", (), {"name": "n", "client_id": "c"})
    assert "--name" in argv and "--client-id" in argv


# ── 端到端：真起 server（127.0.0.1 随机端口）────────────────────────


class _StubManager:
    """get_manager 替身：记录 create 调用（可断言 params 过滤），不起子进程。"""

    def __init__(self) -> None:
        self.created: list[tuple] = []

    def create(self, kind: str, params: dict, source: str = "manual") -> str:
        self.created.append((kind, params, source))
        return "e2etask0001"

    def list(self, limit: int = 50) -> list[dict]:
        return []

    def get(self, task_id: str) -> dict | None:
        return None

    def log_tail(self, task_id: str, n: int = 40) -> str:
        return ""

    def cancel(self, task_id: str) -> bool:
        return False


@pytest.fixture()
def server(monkeypatch):
    stub = _StubManager()
    monkeypatch.setattr(tasks_server, "get_manager", lambda: stub)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), tasks_server.TaskHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, stub
    srv.shutdown()
    srv.server_close()


def _token() -> str:
    return tasks_server._TASKS_TOKEN


def _request(method: str, port: int, path: str, body: dict | None = None,
             token: str | None = "valid") -> tuple[int, dict, http.client.HTTPResponse]:
    """默认带合法 token（valid 哨兵 → 真值）；token=None 显式不带头。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        real = _token() if token == "valid" else token
        headers["Authorization"] = f"Bearer {real}"
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        parsed = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        parsed = {}
    return resp.status, parsed, resp


def test_e2e_post_tasks_without_token_401(server):
    srv, stub = server
    status, body, _ = _request(
        "POST", srv.server_port, "/tasks",
        {"kind": "search", "params": {"query": "杯子"}}, token=None)
    assert status == 401
    assert body == {"error": "unauthorized"}
    assert stub.created == []  # 未鉴权绝不创建任务


def test_e2e_post_tasks_wrong_token_401(server):
    srv, stub = server
    status, body, _ = _request(
        "POST", srv.server_port, "/tasks",
        {"kind": "search", "params": {"query": "杯子"}}, token="wrong")
    assert status == 401
    assert body == {"error": "unauthorized"}  # 401 体不回显 token 信息
    assert stub.created == []


def test_e2e_post_tasks_with_token_creates_and_filters_params(server):
    """带 token 201；params 白名单外键（evil_flag）在创建前被丢弃。"""
    srv, stub = server
    status, body, _ = _request(
        "POST", srv.server_port, "/tasks",
        {"kind": "search", "params": {"query": "杯子", "evil_flag": "x"}})
    assert status == 201
    assert body["task_id"] == "e2etask0001"
    assert len(stub.created) == 1
    kind, params, source = stub.created[0]
    assert kind == "search"
    assert params == {"query": "杯子"}  # evil_flag 已被白名单滤掉
    assert source == "manual"


def test_e2e_post_tasks_params_non_dict_400(server):
    srv, _stub = server
    status, body, _ = _request(
        "POST", srv.server_port, "/tasks",
        {"kind": "search", "params": ["not", "a", "dict"]})
    assert status == 400


def test_e2e_post_tasks_unknown_kind_400(server):
    srv, stub = server
    status, body, _ = _request(
        "POST", srv.server_port, "/tasks",
        {"kind": "no_such_kind", "params": {}})
    assert status == 400
    assert "no_such_kind" in body["error"]
    assert stub.created == []


def test_e2e_get_tasks_requires_auth(server):
    srv, _stub = server
    status, body, _ = _request("GET", srv.server_port, "/tasks", token=None)
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_e2e_get_tasks_with_token_200(server):
    srv, _stub = server
    status, body, _ = _request("GET", srv.server_port, "/tasks")
    assert status == 200
    assert "items" in body


def test_e2e_health_free_of_auth(server):
    """/health 存活探测免鉴权，只回 {"ok": true}。"""
    srv, _stub = server
    status, body, resp = _request("GET", srv.server_port, "/health", token=None)
    assert status == 200
    assert body == {"ok": True}
    # CORS 已移除：不再有 Access-Control-Allow-Origin: *
    assert resp.getheader("Access-Control-Allow-Origin") is None


def test_e2e_options_204_no_cors_headers(server):
    """OPTIONS 预检：204 空体，无任何 Access-Control-* 头。"""
    srv, _stub = server
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    conn.request("OPTIONS", "/tasks")
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    assert resp.status == 204
    assert data == b""
    assert resp.getheader("Access-Control-Allow-Origin") is None
    assert resp.getheader("Access-Control-Allow-Methods") is None


def test_e2e_post_oversize_body_413(server):
    """Content-Length 超 1MB → 413（body 上限闸，读体前拒绝；不创建任务）。

    注：只声明大 Content-Length 不真发 1MB——服务端按头拒绝且不读体，
    真发 1MB 会在服务端关闭连接时触发客户端 broken pipe（预期行为，
    非 bug；闸的机制就是 Content-Length 头判定）。"""
    srv, stub = server
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    conn.request("POST", "/tasks", b'{"kind":"search"}',
                 {"Content-Length": str(1_000_001),
                  "Authorization": f"Bearer {_token()}"})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    assert resp.status == 413
    assert json.loads(data) == {"error": "body too large"}
    assert stub.created == []


def test_e2e_post_ask_without_token_401(server):
    """/ask 同受鉴权保护（401 先于路由执行）。"""
    srv, stub = server
    status, body, _ = _request(
        "POST", srv.server_port, "/ask", {"text": "检查一下环境"}, token=None)
    assert status == 401
    assert stub.created == []

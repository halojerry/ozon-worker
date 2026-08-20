"""意图路由层 + /ask REST 端点测试（v1 纯规则路由）。

覆盖 docs/PLAN-conversation-entry-v1.md Phase 1 验收：
- route_intent：URL → A/B/C、图片 → D1、意图词 → C/D/E、多 URL → F、歧义 → unknown 追问
- tasks_server /ask：正常路由 / 澄清 / 确认 / 长时后台 / 500 不回显 / CORS 预检

不依赖真实 skill subprocess / Chrome / fastmcp：monkeypatch run_skill_command 与 get_manager。
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from pounding_mcp import tasks_server
from pounding_mcp.router import ROUTER_VERSION, normalize_intent, route_intent

# ── route_intent 纯函数 ──────────────────────────────────────────────

def test_router_version():
    """ROUTER_VERSION 常量存在（防漂移锚点，后续 LLM 消歧层同接口）。"""
    assert ROUTER_VERSION == "v1"


def test_router_1688_url_to_pipeline_a():
    """1688 商品页 URL → A + graph --url（明确 URL 自动执行，无需确认）。"""
    r = route_intent("帮我把这个 1688 链接上架 https://detail.1688.com/offer/980815374096.html")
    assert r["pipeline"] == "A"
    assert r["command"] == "graph"
    assert "--url" in r["args"]
    assert r["needs_confirmation"] is False
    assert r["needs_clarification"] is False


def test_router_ozon_product_url_to_pipeline_b():
    """Ozon 商品页 URL → B + follow --ozon-url。"""
    r = route_intent("跟卖这个商品 https://www.ozon.ru/product/123456789/")
    assert r["pipeline"] == "B"
    assert r["command"] == "follow"
    assert "--ozon-url" in r["args"]


def test_router_ozon_list_url_to_pipeline_c():
    """Ozon 搜索页/类目页 URL → C + discover --url。"""
    r = route_intent("采集这个搜索页 https://www.ozon.ru/search/?text=поилка")
    assert r["pipeline"] == "C"
    assert r["command"] == "discover"
    assert "--url" in r["args"]


def test_router_image_intent_to_pipeline_d1():
    """图片意图词（无 URL）→ D1 + image_search + needs_confirmation（图搜结果须确认再 graph）。"""
    r = route_intent("帮我把这个图片找同款")
    assert r["pipeline"] == "D1"
    assert r["command"] == "image_search"
    assert r["needs_confirmation"] is True


def test_router_listing_to_pipeline_d():
    """「上架」→ D + discover --auto-submit + needs_confirmation=True（写类命令必须确认）。"""
    r = route_intent("帮我上架宠物用品")
    assert r["pipeline"] == "D"
    assert r["command"] == "discover"
    assert "--auto-submit" in r["args"]
    assert "--keyword" in r["args"]
    assert "宠物用品" in r["args"]  # 衬词剔除不得吞掉类目后缀（"用"不是衬词）
    assert r["needs_confirmation"] is True


def test_router_trend_requires_clarification():
    """「趋势/热卖」无品类 → E + needs_clarification + questions 非空（命令层无 trend，须 web_search 先分析）。"""
    r = route_intent("有什么热卖趋势")
    assert r["pipeline"] == "E"
    assert r["needs_clarification"] is True
    assert r["questions"]


def test_router_empty_unknown():
    """空输入 → unknown + needs_clarification + questions 非空。"""
    r = route_intent("")
    assert r["pipeline"] == "unknown"
    assert r["needs_clarification"] is True
    assert r["questions"]


def test_router_no_object_unknown():
    """无对象输入（指代不清）→ unknown + 追问，禁止猜测执行。"""
    r = route_intent("帮我弄一下这个")
    assert r["pipeline"] == "unknown"
    assert r["needs_clarification"] is True
    assert r["questions"]


def test_router_multi_url_to_pipeline_f():
    """多个 URL → F + batch_test.py + needs_confirmation=True。"""
    r = route_intent("https://detail.1688.com/offer/111.html https://detail.1688.com/offer/222.html")
    assert r["pipeline"] == "F"
    assert r["command"] == "batch_test.py"
    assert "--urls-file" in r["args"]
    assert r["needs_confirmation"] is True


def test_router_category():
    """「查一下类目 护手霜」→ category 位置参数。"""
    r = route_intent("查一下类目 护手霜")
    assert r["command"] == "category"
    assert "护手霜" in r["args"]


def test_normalize_intent():
    """中文标点归一 + strip。"""
    assert normalize_intent("  帮我，选品；宠物、用品。  ") == "帮我 选品 宠物 用品"


# ── /ask REST handler ────────────────────────────────────────────────

class _StubManager:
    """get_manager 替身：只记录 create 调用，不真实起子进程。"""

    def __init__(self) -> None:
        self.created = []

    def create(self, kind: str, params: dict, source: str = "manual") -> str:
        self.created.append((kind, params, source))
        return "testtask123"

    def list(self, limit: int = 50) -> list[dict]:
        return []


@pytest.fixture()
def server(monkeypatch):
    """在随机端口起真实 ThreadingHTTPServer，返回 server 对象（每测试独立）。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), tasks_server.TaskHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _request(method: str, port: int, path: str, body: dict | None = None) -> tuple[int, dict, http.client.HTTPResponse]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        parsed = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        parsed = {}
    return resp.status, parsed, resp


def test_ask_direct_command(server, monkeypatch):
    """check/category/search 直接同步执行 → 200 + output（不真实起子进程）。"""
    captured = {}

    def fake_run(cmd, *pos, **kw):
        captured["cmd"] = cmd
        captured["pos"] = pos
        captured["kw"] = kw
        return {"ok": True, "raw": "环境正常"}

    monkeypatch.setattr(tasks_server, "run_skill_command", fake_run)
    status, body, _resp = _request("POST", server.server_port, "/ask", {"text": "检查一下环境"})
    assert status == 200
    assert body["ok"] is True
    assert body["output"] == {"ok": True, "raw": "环境正常"}
    assert captured["cmd"] == "check"


def test_ask_long_command_to_background(server, monkeypatch):
    """graph/follow/discover 长时命令 → 后台 get_manager().create() → 200 + task_id。"""
    stub = _StubManager()
    monkeypatch.setattr(tasks_server, "get_manager", lambda: stub)
    status, body, _resp = _request(
        "POST", server.server_port, "/ask", {"text": "把 https://detail.1688.com/offer/980815374096.html 采集下来"}
    )
    assert status == 200
    assert body["ok"] is True
    assert body["task_id"] == "testtask123"
    assert body["command"] == "graph"
    assert stub.created and stub.created[0][0] == "graph"


def test_ask_clarification(server, monkeypatch):
    """趋势无品类 → 200 {ok: False, questions, pipeline unknown}，不执行。"""
    status, body, _resp = _request("POST", server.server_port, "/ask", {"text": "有什么热卖趋势"})
    assert status == 200
    assert body["ok"] is False
    assert body["pipeline"] == "unknown"
    assert body["questions"]


def test_ask_needs_confirmation(server, monkeypatch):
    """上架 → 200 {ok: True, needs_confirmation, command/args/pipeline}，不执行。"""
    stub = _StubManager()
    monkeypatch.setattr(tasks_server, "get_manager", lambda: stub)
    status, body, _resp = _request("POST", server.server_port, "/ask", {"text": "帮我上架宠物用品"})
    assert status == 200
    assert body["ok"] is True
    assert body["needs_confirmation"] is True
    assert body["command"] == "discover"
    assert "--auto-submit" in body["args"]
    assert body["pipeline"] == "D"
    assert stub.created == []  # 确认前绝不执行


def test_ask_500_no_internal_leak(server, monkeypatch):
    """skill 调用异常 → 500 {ok: False, error: 'route failed'}，不回显内部异常。"""

    def boom(*a, **kw):
        raise RuntimeError("secret internal traceback detail")

    monkeypatch.setattr(tasks_server, "run_skill_command", boom)
    status, body, _resp = _request("POST", server.server_port, "/ask", {"text": "检查一下环境"})
    assert status == 500
    assert body["ok"] is False
    assert body["error"] == "route failed"
    assert "secret" not in json.dumps(body)


def test_options_preflight_cors(server, monkeypatch):
    """OPTIONS 预检 → 200 + 全量 CORS 头。"""
    status, _, resp = _request("OPTIONS", server.server_port, "/ask")
    assert status == 200
    assert resp.getheader("Access-Control-Allow-Origin") == "*"
    assert "POST" in (resp.getheader("Access-Control-Allow-Methods") or "")
    assert resp.getheader("Access-Control-Allow-Headers") == "Content-Type"
    assert resp.getheader("Access-Control-Max-Age") == "86400"


def test_health_has_cors(server, monkeypatch):
    """GET /health 正常且带 CORS 头。"""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    assert resp.status == 200
    assert json.loads(data) == {"ok": True}
    assert resp.getheader("Access-Control-Allow-Origin") == "*"

"""意图路由层 + /ask REST 端点测试（v2 纯规则路由）。

覆盖 docs/PLAN-conversation-entry-v1.md Phase 1 验收：
- route_intent：URL → A/B/C、图片 → D1、意图词 → C/D/E、多 URL → F、歧义 → unknown 追问
- v2：查进度意图 → query（带/缺 task_id 两态）；A/B 强意图直提腿带 --wait；
  「上传图片」被图片分支截住不漏 D（G1 评估结论现状锁定）
- tasks_server /ask：正常路由 / 澄清 / 确认 / 长时后台 / 500 不回显 / OPTIONS 204 无 CORS
- v0.76 T18 起 8902 网关强制 Bearer 鉴权：本文件所有请求默认带合法 token
  （401/免鉴权语义专项在 test_tasks_server_auth.py）

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
    """ROUTER_VERSION 常量存在（防漂移锚点，后续 LLM 消歧层同接口）。

    v2：新增 query 意图类 + A/B 强意图腿补 --wait（接口形态变化必须升版本号）。"""
    assert ROUTER_VERSION == "v2"


def test_router_1688_url_to_pipeline_a():
    """1688 商品页 URL → A + graph --url --wait（明确 URL 自动执行，无需确认）。

    v2：直提腿带 --wait（闸排队+提交后轮询终态，failed exit 3），
    对齐 SKILL.md §1「graph --url <URL> --wait」与 §3 二分法口径。"""
    r = route_intent("帮我把这个 1688 链接上架 https://detail.1688.com/offer/980815374096.html")
    assert r["pipeline"] == "A"
    assert r["command"] == "graph"
    assert "--url" in r["args"]
    assert "--wait" in r["args"]
    assert r["needs_confirmation"] is False
    assert r["needs_clarification"] is False


def test_router_ozon_product_url_to_pipeline_b():
    """Ozon 商品页 URL → B + follow --ozon-url --auto-submit --wait（强意图直提）。

    缺省 follow 只展示不提交，--auto-submit 由 router 直带（arch-findings 修复）；
    v2 再补 --wait，逐字对齐 SKILL.md §1「follow --ozon-url <URL> --auto-submit --wait」。"""
    r = route_intent("跟卖这个商品 https://www.ozon.ru/product/123456789/")
    assert r["pipeline"] == "B"
    assert r["command"] == "follow"
    assert "--ozon-url" in r["args"]
    assert "--auto-submit" in r["args"]
    assert "--wait" in r["args"]


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


def test_router_auto_task_to_pipeline_c2():
    """「自动采集/无人值守」→ C2 + discover_task（漏斗 v2 任务式；dry_run 缺省零副作用无确认）。

    v0.70 目标驱动：未说数量 → 追问目标（不直接执行）。"""
    r = route_intent("帮我自动采集宠物饮水机")
    assert r["pipeline"] == "C2"
    assert r["command"] == "discover_task"
    assert "--keyword" in r["args"]
    assert "宠物饮水机" in r["args"]
    assert r["needs_confirmation"] is False
    assert r["needs_clarification"] is True
    assert any("多少个" in q for q in r["questions"])


def test_router_auto_task_with_count_runs_direct():
    """「自动选品 30个 宠物用品」→ 数量词转 --target-count 且不污染关键词，直接执行。"""
    r = route_intent("自动选品 30个 宠物用品")
    assert r["pipeline"] == "C2"
    assert r["command"] == "discover_task"
    assert r["needs_clarification"] is False
    assert "--target-count" in r["args"]
    assert r["args"][r["args"].index("--target-count") + 1] == "30"
    kw = r["args"][r["args"].index("--keyword") + 1]
    assert "宠物用品" in kw and "30" not in kw and "个" not in kw


def test_router_auto_task_no_object_clarifies():
    """「无人值守」无品类 → C2 + 追问（无可提取关键词；目标+品类都问）。"""
    r = route_intent("无人值守")
    assert r["pipeline"] == "C2"
    assert r["command"] == "discover_task"
    assert r["needs_clarification"] is True
    assert r["questions"]


def test_router_discover_with_count_maps_max_products():
    """「选品 50个 宠物用品」→ C + discover --max-products 50（关键词不带数量词）。"""
    r = route_intent("选品 50个 宠物用品")
    assert r["pipeline"] == "C"
    assert r["command"] == "discover"
    assert r["needs_clarification"] is False
    assert "--max-products" in r["args"]
    assert r["args"][r["args"].index("--max-products") + 1] == "50"
    kw = r["args"][r["args"].index("--keyword") + 1]
    assert "宠物用品" in kw and "50" not in kw


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


# ── v2 query 意图（查任务进度）──────────────────────────────────────

def test_router_query_with_uuid_id():
    """「查任务进度 <uuid>」→ query + args=[uuid]（只读秒级，无确认无澄清）。"""
    tid = "550e8400-e29b-41d4-a716-446655440000"
    r = route_intent(f"帮我查一下任务 {tid} 跑到哪了")
    assert r["pipeline"] == "query"
    assert r["command"] == "query"
    assert r["args"] == [tid]
    assert r["needs_confirmation"] is False
    assert r["needs_clarification"] is False


def test_router_query_with_local_hex_id():
    """本地采集箱短任务 ID（uuid4().hex[:12]，含 a-f 字母）→ query args=[id]。"""
    r = route_intent("任务 a1b2c3d4e5f6 处理完了吗")
    assert r["pipeline"] == "query"
    assert r["args"] == ["a1b2c3d4e5f6"]


def test_router_query_with_numeric_id():
    """纯数字串 + 进度词 → query（数字兜底只在进度词在场时被采用）。"""
    r = route_intent("123456789 好了吗")
    assert r["pipeline"] == "query"
    assert r["args"] == ["123456789"]


def test_router_query_without_id_clarifies():
    """进度问句缺 task_id → needs_clarification + 追问 task_id（禁止猜测执行）。"""
    r = route_intent("查进度")
    assert r["pipeline"] == "query"
    assert r["command"] == "query"
    assert r["needs_clarification"] is True
    assert r["questions"]
    assert r["args"] == []


def test_router_query_bare_id_token():
    """裸任务凭证（12 位 hex 含字母）无进度词也按 query 处理。"""
    r = route_intent("a1b2c3d4e5f6")
    assert r["pipeline"] == "query"
    assert r["args"] == ["a1b2c3d4e5f6"]


def test_router_numeric_item_id_without_query_word_stays_search():
    """纯数字串（1688 item_id 形态）无进度词 → 不误入 query（让位 search/unknown）。"""
    r = route_intent("搜一下 980815374096")
    assert r["pipeline"] == "search"
    assert "980815374096" in r["args"]
    assert route_intent("980815374096")["pipeline"] == "unknown"


# ── v2「上传」词现状锁定（G1 评估结论）─────────────────────────────

def test_router_upload_image_locked_to_d1():
    """「上传图片」不漏到 D：图片意图分支（③）先于 D 词表判定，现状即正确。

    G1 曾担心 _LIST_WORDS 的「上传」会把「上传图片」误路由到 discover——实测
    被分支顺序正确截住（router 无需改词表），本测试锁定该现状防回归。"""
    r = route_intent("上传图片找同款")
    assert r["pipeline"] == "D1"
    assert r["command"] == "image_search"
    assert r["needs_confirmation"] is True
    assert route_intent("上传一张图片")["pipeline"] == "D1"


def test_router_upload_goods_still_discover():
    """「上传」词对货品类话术保持 D 腿不变（评估不动词表的前提）。"""
    r = route_intent("上传宠物用品")
    assert r["pipeline"] == "D"
    assert r["command"] == "discover"
    assert "--auto-submit" in r["args"]


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
    # v0.76 T18：8902 网关强制 Bearer 鉴权，所有请求默认带合法 token
    headers = {"Authorization": f"Bearer {tasks_server._TASKS_TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
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


def test_ask_query_direct_command(server, monkeypatch):
    """/ask 查进度（带 task_id）→ query 同步执行组，task_id 走位置参数（非 --query 伪 flag）。"""
    captured = {}

    def fake_run(cmd, *pos, **kw):
        captured["cmd"] = cmd
        captured["pos"] = pos
        captured["kw"] = kw
        return {"ok": True, "raw": "running (35%)"}

    monkeypatch.setattr(tasks_server, "run_skill_command", fake_run)
    tid = "550e8400-e29b-41d4-a716-446655440000"
    status, body, _resp = _request("POST", server.server_port, "/ask",
                                   {"text": f"任务 {tid} 好了吗"})
    assert status == 200
    assert body["ok"] is True
    assert body["command"] == "query"
    assert body["output"] == {"ok": True, "raw": "running (35%)"}
    assert captured["cmd"] == "query"
    assert captured["pos"] == (tid,)      # CLI 位置参数 task_id
    assert "--query" not in str(captured) # 不许落成伪 flag


def test_ask_query_without_id_clarifies(server, monkeypatch):
    """/ask 查进度缺 task_id → 200 {ok: False, questions}，不执行任何命令。"""
    stub = _StubManager()
    monkeypatch.setattr(tasks_server, "get_manager", lambda: stub)

    def fake_run(cmd, *pos, **kw):  # pragma: no cover — 澄清路径绝不执行
        raise AssertionError("澄清分支不得执行 skill 命令")

    monkeypatch.setattr(tasks_server, "run_skill_command", fake_run)
    status, body, _resp = _request("POST", server.server_port, "/ask", {"text": "查进度"})
    assert status == 200
    assert body["ok"] is False
    assert body["questions"]
    assert stub.created == []


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


def test_options_preflight_no_cors(server, monkeypatch):
    """OPTIONS 预检 → 204 空体，无任何 Access-Control-* 头（v0.76 T18 CORS 收敛）。"""
    status, _, resp = _request("OPTIONS", server.server_port, "/ask")
    assert status == 204
    assert resp.getheader("Access-Control-Allow-Origin") is None
    assert resp.getheader("Access-Control-Allow-Methods") is None
    assert resp.getheader("Access-Control-Allow-Headers") is None


def test_health_free_of_auth_no_cors(server, monkeypatch):
    """GET /health 免鉴权 200 {"ok": true}，且无 CORS 头（存活探测不泄漏信息）。"""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    assert resp.status == 200
    assert json.loads(data) == {"ok": True}
    assert resp.getheader("Access-Control-Allow-Origin") is None

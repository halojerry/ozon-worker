"""v0.67.0 批次 1: worker 内置远程 MCP 服务测试（src/mcp_server.py）。

三层覆盖：
1. 工具逻辑（fastmcp in-memory Client + monkeypatch _call/_current_token）：
   参数整形/token 注入/大字段裁剪/结构化错误。
2. Bearer 鉴权中间件（httpx ASGITransport 直打 mcp_app）：无 token 401、
   有效 token 通过并写租户上下文。
3. 挂载面（main.app，MCP_ENABLED 开）：/mcp 无凭据 401、工具清单注册齐全。

全程无真实网络/DB：REST 回调经 _call monkeypatch，鉴权经 monkeypatch
main._authenticate_token（中间件内延迟导入，patch 生效）。
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import mcp_server  # noqa: E402
from fastmcp import Client  # noqa: E402

pytestmark = pytest.mark.asyncio


# ── 工具 ────────────────────────────────────────────────────────

async def _call_tool(name: str, args: dict | None = None):
    """fastmcp in-memory 直连：返回工具的结构化返回值（dict）。"""
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool(name, args or {})
    data = getattr(result, "data", None)
    if data is None:
        data = getattr(result, "structured_content", None)
    return data


def _patch_call(monkeypatch, response=None, capture=None):
    """monkeypatch mcp_server._call：记录 (method, path, body, params) 并返回固定响应。"""
    calls: list[dict] = []

    async def fake_call(method, path, *, body=None, params=None):
        calls.append({"method": method, "path": path, "body": body, "params": params})
        if capture is not None:
            capture.append((method, path, body, params))
        return response if response is not None else {"ok": True}

    monkeypatch.setattr(mcp_server, "_call", fake_call)
    return calls


def _auth(monkeypatch, token="sk-test-key-123", tenant="user_abc"):
    monkeypatch.setattr(mcp_server, "_current_token", lambda: token)
    monkeypatch.setattr(mcp_server, "_current_tenant", lambda: tenant)


async def test_all_14_tools_registered():
    async with Client(mcp_server.mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert set(mcp_server.TOOLS) <= names, f"缺工具: {set(mcp_server.TOOLS) - names}"
    assert len(names) == 17


async def test_submit_task_injects_token_into_body(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"task_id": "t1"})
    envelope = {"draft": {"item_id": "x", "title": "t"}}
    out = await _call_tool("submit_task", {
        "envelope": envelope, "ozon_client_id": "cid", "ozon_api_key": "akey"})
    assert out == {"task_id": "t1"}
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "/submit_task"
    assert calls[0]["body"]["token"] == "sk-test-key-123"
    assert calls[0]["body"]["envelope"] == envelope
    assert calls[0]["body"]["ozon_client_id"] == "cid"


async def test_submit_task_without_token_returns_error(monkeypatch):
    _auth(monkeypatch, token="")
    calls = _patch_call(monkeypatch)
    out = await _call_tool("submit_task", {
        "envelope": {"draft": {}}, "ozon_client_id": "c", "ozon_api_key": "a"})
    assert "error" in out
    assert calls == []  # 未授权不触达 REST


async def test_list_drafts_trims_payload(monkeypatch):
    _auth(monkeypatch)
    _patch_call(monkeypatch, [{
        "id": "d-1", "payload": {"draft": {"title": "宠物饮水机", "item_id": "1688:1",
                                            "purchase_cost": 12.5, "price": 399,
                                            "purchase_url": "https://1688.com/x",
                                            "stock": 5}},
        "submission_status": "pending", "source": "skill", "version": 3,
        "updated_at": "2026-09-05T00:00:00Z",
        "payload_keep_out": "应被裁剪",
    }])
    out = await _call_tool("list_drafts")
    assert out["count"] == 1
    d = out["drafts"][0]
    assert d["title"] == "宠物饮水机" and d["submission_status"] == "pending"
    assert "payload" not in d and "payload_keep_out" not in d


async def test_error_passthrough_structured(monkeypatch):
    _auth(monkeypatch)
    _patch_call(monkeypatch, {"error": {"status": 409, "detail": "该草稿已在上架中"}})
    out = await _call_tool("get_task_status", {"task_id": "t-9"})
    assert out["error"]["status"] == 409
    assert "上架中" in out["error"]["detail"]


async def test_get_task_status_get_path(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"status": "completed"})
    await _call_tool("get_task_status", {"task_id": "tid"})
    assert calls[0]["method"] == "GET" and calls[0]["path"] == "/task_status/tid"


async def test_cancel_task_post_path(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"status": "success"})
    await _call_tool("cancel_task", {"task_id": "tid"})
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "/cancel_task/tid"


async def test_task_statistics_scopes_to_tenant(monkeypatch):
    _auth(monkeypatch, tenant="user_abc")
    calls = _patch_call(monkeypatch, {"status": "success"})
    await _call_tool("get_task_statistics")
    assert calls[0]["params"] == {"tenant_id": "user_abc"}


async def test_task_statistics_requires_tenant(monkeypatch):
    _auth(monkeypatch, tenant="")
    calls = _patch_call(monkeypatch)
    out = await _call_tool("get_task_statistics")
    assert "error" in out and calls == []


async def test_submit_draft_body_shape(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"task_id": "t2"})
    await _call_tool("submit_draft", {"draft_id": "d1", "credential_id": "c1",
                                      "template_id": "tpl-7"})
    assert calls[0]["path"] == "/api/v1/drafts/d1/submit"
    assert calls[0]["body"] == {"token": "sk-test-key-123", "credential_id": "c1",
                                "template_id": "tpl-7"}


async def test_batch_submit_body_shape(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"submitted": [], "skipped": [], "failed": []})
    await _call_tool("batch_submit_drafts", {"ids": ["a", "b"], "credential_id": "c1"})
    assert calls[0]["path"] == "/api/v1/drafts/batch-submit"
    assert calls[0]["body"]["ids"] == ["a", "b"]
    assert calls[0]["body"]["token"] == "sk-test-key-123"


async def test_run_store_action_merges_operation_and_params(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"ok": True})
    await _call_tool("run_store_action", {
        "credential_id": "c1", "operation": "bulk_update_prices",
        "params": {"items": [{"offer_id": "o1", "price": "100"}]}})
    assert calls[0]["path"] == "/api/v1/stores/c1/actions"
    assert calls[0]["body"]["operation"] == "bulk_update_prices"
    assert calls[0]["body"]["items"] == [{"offer_id": "o1", "price": "100"}]


async def test_quote_logistics_conditional_fields(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"logistics_cost_cny": 6.0})
    await _call_tool("quote_logistics", {
        "weight_g": 500, "depth_cm": 20, "width_cm": 15, "height_cm": 10})
    body = calls[0]["body"]
    assert body["weight_g"] == 500 and "ozon_client_id" not in body
    await _call_tool("quote_logistics", {
        "weight_g": 500, "depth_cm": 20, "width_cm": 15, "height_cm": 10,
        "ozon_client_id": "cid", "ozon_api_key": "akey"})
    assert calls[1]["body"]["ozon_client_id"] == "cid"
    # token 透传（logistics_quote 走 body token 鉴权）
    assert calls[0]["body"]["token"] == "sk-test-key-123"


async def test_lookup_query_tools_params(monkeypatch):
    _auth(monkeypatch)
    calls = _patch_call(monkeypatch, {"found": False})
    await _call_tool("lookup_commission", {"category_id": 12345})
    assert calls[0]["path"] == "/api/v1/commissions/lookup"
    assert calls[0]["params"] == {"category_id": "12345"}
    await _call_tool("lookup_mapping", {"keyword": "宠物饮水机"})
    assert calls[1]["params"] == {"keyword": "宠物饮水机"}
    await _call_tool("get_seo_keywords", {"q": "поилкa", "limit": 5})
    assert calls[2]["params"] == {"q": "поилкa", "limit": "5"}


# ── Bearer 鉴权中间件（ASGI 直打 mcp_app）────────────────────────

def _mcp_asgi_transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=mcp_server.mcp_app)


async def test_middleware_rejects_missing_token():
    transport = _mcp_asgi_transport()
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    assert "error" in resp.json()


async def test_middleware_rejects_invalid_token(monkeypatch):
    def fake_auth(token):  # 与真实 _authenticate_token 同为 sync
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Token is revoked")

    import main as _main
    monkeypatch.setattr(_main, "_authenticate_token", fake_auth)
    transport = _mcp_asgi_transport()
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 401


async def test_middleware_passes_valid_token_to_mcp_app(monkeypatch):
    import main as _main

    def fake_auth(token):
        assert token == "sk-good"
        return "user_ok"

    monkeypatch.setattr(_main, "_authenticate_token", fake_auth)
    # 直打 wrapper：真实挂载时 Mount 已剥掉 /mcp 前缀（root_path=/mcp, path=/），故 POST /；
    # fastmcp session manager 需要 lifespan 在跑（真实挂载由 main._root_lifespan 提供）。
    init_body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"}}}
    async with mcp_server.mcp_app.app.lifespan(mcp_server.mcp_app.app):
        async with httpx.AsyncClient(transport=_mcp_asgi_transport(),
                                     base_url="http://mcp") as client:
            resp = await client.post(
                "/", json=init_body,
                headers={"Authorization": "Bearer sk-good",
                         "Accept": "application/json, text/event-stream"})
    assert resp.status_code == 200


# ── 挂载面（main.app，MCP_ENABLED 默认开）───────────────────────

async def test_main_app_mcp_mount_rejects_anonymous():
    """裸 /mcp 与 /mcp/ 都必须 401（裸路径不允许 307 绕过鉴权重定向）。"""
    import main as _main

    transport = httpx.ASGITransport(app=_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://w",
                                 follow_redirects=False) as client:
        for path in ("/mcp", "/mcp/"):
            resp = await client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            assert resp.status_code == 401, f"{path} → {resp.status_code}"


async def test_main_app_has_mcp_mount():
    """MCP_ENABLED 默认开 → main.app 必须挂载 /mcp（MCP_ENABLED=0 时 main 顶层跳过挂载）。"""
    import main as _main

    mounts = [r for r in _main.app.routes if getattr(r, "path", "") == "/mcp"]
    assert mounts, "/mcp 未挂载到 main.app"

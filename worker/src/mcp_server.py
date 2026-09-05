"""worker 内置远程 MCP 服务（streamable HTTP）— v0.67.0 批次 1。

对标竞品 linkfox 的远程 MCP 网关模式：任何支持远程 MCP 的平台（dsh / Claude Code /
Cursor / Cherry Studio…）用 `Authorization: Bearer <mxou key>` 连 `https://<host>/mcp`
即可调用 worker 云端能力。与本地 pounding-mcp（stdio，采集执行面）分工见 instructions。

设计纪律（批次 1 拍板，见 docs/PLAN-harness-mcp-adoption-v1.md）：
- 零新业务逻辑：全部工具经进程内 httpx ASGITransport 回调现有 FastAPI 路由，
  鉴权/租户隔离/校验/错误码/后台任务与 REST 完全同源（无双写漂移风险）。
- 鉴权复用 `main._authenticate_token`（限流 RateLimiter / 租户解析 / 吊销全复用）；
  middleware 在 MCP 协议层先挡 401/429，工具内层路由再走各自 Bearer 鉴权
  （同一 token，限流计数一次调用记 2 次——比 REST 更保守，可接受）。
- 工具名用业务语义；返回精简 JSON（大字段如 draft payload 不回传）；
  错误返回结构化 {"error": {...}} 不 raise，agent 可读。
- token/租户经 ContextVar 从 middleware 传递到工具（HTTP 面经 Bearer header；
  fastmcp in-memory Client 测试面直接 set contextvar）。
"""

from __future__ import annotations

import contextvars
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP(
    "pounding-worker",
    instructions=(
        "Pounding Ozon 云端运营 MCP（上架/采集箱/店铺/查询）。\n"
        "与本地 pounding MCP 的分工：\n"
        "- 1688/Ozon 页面采集类操作（graph 1688 抓取、follow 跟卖采集、discover 选品、"
        "image_search 以图搜款、check 环境诊断）依赖用户本机 Chrome 登录态，"
        "必须走本地 pounding MCP（stdio）。\n"
        "- 本服务（远程）只做云端能力：提交上架任务/查进度、采集箱草稿、店铺凭证与分析、"
        "批量改价/库存/归档/促销、佣金/物流/类目映射/SEO 关键词查询。\n"
        "典型编排：本地采集组装信封 → 本服务 submit_task 上架；或本地 discover → "
        "结果入采集箱 → 本服务 submit_draft 提交。\n"
        "鉴权：Authorization: Bearer <mxou key>（与 worker REST 同一 token 体系，"
        "多租户按 key 自动隔离）。"
    ),
)

# ── 请求上下文（middleware → 工具）───────────────────────────────
_mcp_token: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_token", default="")
_mcp_tenant: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_tenant", default="")


def _current_token() -> str:
    return _mcp_token.get()


def _current_tenant() -> str:
    return _mcp_tenant.get()


class _BearerAuthMiddleware:
    """纯 ASGI 中间件：MCP 协议层统一 Bearer 鉴权（401/429 先挡，不进 MCP 会话）。

    复用 main._authenticate_token（限流/租户解析/吊销/Supabase 全同源）；
    校验通过后把 token 与租户写 ContextVar 供工具层取用。
    """

    def __init__(self, app):
        self.app = app
        # 透传内层 FastMCP ASGI app 的 lifespan：main._root_lifespan 要靠它把
        # streamable-http session manager 并进主 lifespan（否则握手报 task group 未初始化）
        self.lifespan = getattr(app, "lifespan", None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        try:
            from main import _authenticate_token

            tenant = _authenticate_token(token)
        except Exception as exc:
            status = getattr(exc, "status_code", 401)
            detail = getattr(exc, "detail", "unauthorized")
            from starlette.responses import JSONResponse

            resp = JSONResponse({"error": {"status": status, "detail": str(detail)}}, status_code=status)
            await resp(scope, receive, send)
            return
        _mcp_token.set(token)
        _mcp_tenant.set(str(tenant))
        await self.app(scope, receive, send)


mcp_app = _BearerAuthMiddleware(mcp.http_app(path="/"))

# ── 进程内回调现有 REST（零业务逻辑复用）────────────────────────
_inner_client = None


def _get_inner_client():
    global _inner_client
    if _inner_client is None:
        import main as _main  # 延迟导入防循环（routes 同款模式）
        from httpx import ASGITransport, AsyncClient

        _inner_client = AsyncClient(
            transport=ASGITransport(app=_main.app),
            base_url="http://mcp-internal",
            timeout=120,
        )
    return _inner_client


async def _call(method: str, path: str, *, body: dict | None = None,
                params: dict | None = None) -> dict:
    """带 Bearer 调本进程 REST；≥400 → 结构化 error dict（不 raise）。"""
    headers = {}
    token = _current_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await _get_inner_client().request(method, path, json=body, params=params, headers=headers)
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:300]
        return {"error": {"status": resp.status_code, "detail": detail}}
    if resp.status_code == 204 or not resp.content:
        return {"ok": True}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:2000]}


def _err(message: str, **kw) -> dict:
    return {"error": {"status": 0, "detail": message}, **kw}


def _trim_draft(d: dict) -> dict:
    """采集箱列表回传精简：剔除 payload（envelope 大字段），抽 title 供 agent 识别。"""
    payload = d.get("payload") or {}
    draft = payload.get("draft") or {}
    return {
        "id": str(d.get("id") or ""),
        "title": draft.get("title"),
        "item_id": draft.get("item_id"),
        "price": draft.get("price"),
        "purchase_cost": draft.get("purchase_cost"),
        "purchase_url": draft.get("purchase_url"),
        "stock": draft.get("stock"),
        "submission_status": d.get("submission_status"),
        "image_mirror_state": d.get("image_mirror_state"),
        "source": d.get("source"),
        "version": d.get("version"),
        "updated_at": str(d.get("updated_at") or ""),
    }


# ── 任务 ────────────────────────────────────────────────────────

@mcp.tool()
async def submit_task(envelope: dict, ozon_client_id: str, ozon_api_key: str) -> dict:
    """提交上架任务（云端管线：类目→定价→属性→生图→校验→上传 Ozon）。

    envelope 为 GraphInput 信封 {draft, source, extensions}（由本地采集/信封组装产出，
    必填 draft.item_id/title/images/weight/dimensions）。写操作：会真实消耗任务额度并上架。
    """
    token = _current_token()
    if not token:
        return _err("未授权：缺少 Bearer token")
    return await _call(
        "POST", "/submit_task",
        body={"token": token, "ozon_client_id": ozon_client_id,
              "ozon_api_key": ozon_api_key, "envelope": envelope},
    )


@mcp.tool()
async def get_task_status(task_id: str) -> dict:
    """查询上架任务状态与进度（status/progress/result/error_message）。只读。"""
    return await _call("GET", f"/task_status/{task_id}")


@mcp.tool()
async def cancel_task(task_id: str) -> dict:
    """取消任务（仅 pending 可取消）。写操作。"""
    return await _call("POST", f"/cancel_task/{task_id}")


@mcp.tool()
async def get_task_statistics() -> dict:
    """获取本租户任务统计（总数/成功率/平均耗时）。只读。"""
    tenant = _current_tenant()
    if not tenant:
        return _err("未授权：缺少租户上下文")
    return await _call("GET", "/task_statistics", params={"tenant_id": tenant})


# ── 采集箱（草稿）───────────────────────────────────────────────

@mcp.tool()
async def list_drafts() -> dict:
    """列出采集箱草稿（本租户，精简字段：id/标题/价格/最新提交状态）。只读。"""
    result = await _call("GET", "/api/v1/drafts")
    if isinstance(result, list):
        return {"drafts": [_trim_draft(d) for d in result], "count": len(result)}
    return result


@mcp.tool()
async def submit_draft(draft_id: str, credential_id: str, template_id: str | None = None) -> dict:
    """提交采集箱草稿上架到指定店铺。写操作：真实上架。

    credential_id 来自 list_stores；template_id 可选（上架配置模板）。
    """
    token = _current_token()
    if not token:
        return _err("未授权：缺少 Bearer token")
    body: dict[str, Any] = {"token": token, "credential_id": credential_id}
    if template_id:
        body["template_id"] = template_id
    return await _call("POST", f"/api/v1/drafts/{draft_id}/submit", body=body)


@mcp.tool()
async def batch_submit_drafts(ids: list[str], credential_id: str) -> dict:
    """批量提交采集箱草稿（≤50 条；逐条进行中守卫）。写操作：真实上架。

    返回 {submitted[], skipped[], failed[]} 明细。
    """
    token = _current_token()
    if not token:
        return _err("未授权：缺少 Bearer token")
    return await _call("POST", "/api/v1/drafts/batch-submit",
                       body={"ids": ids, "token": token, "credential_id": credential_id})


# ── 店铺（凭证/分析/执行）───────────────────────────────────────

@mcp.tool()
async def list_stores() -> dict:
    """列出已绑定店铺凭证（api_key 仅掩码）。只读。返回项含 credential_id 供后续工具使用。"""
    return await _call("GET", "/api/v1/credentials")


@mcp.tool()
async def analyze_store(credential_id: str) -> dict:
    """整店分析：summary/利润趋势/低利润/缺货/可报名活动清单。只读。无成本商品不填 profit_rate。"""
    return await _call("GET", f"/api/v1/stores/{credential_id}/analysis")


@mcp.tool()
async def run_store_action(credential_id: str, operation: str, params: dict | None = None) -> dict:
    """店铺执行写操作（危险：真实改店铺数据，需用户明确确认后调用）。

    operation ∈ bulk_update_prices / bulk_update_stocks / bulk_archive /
    actions_register / seller_action_discount；params 按端点契约传对应字段
    （如 bulk_update_prices: {"items": [{"offer_id","price","old_price"}...]}）。
    """
    body = dict(params or {})
    body["operation"] = operation
    return await _call("POST", f"/api/v1/stores/{credential_id}/actions", body=body)


# ── 查询（选品决策高频）─────────────────────────────────────────

@mcp.tool()
async def lookup_commission(category_id: int) -> dict:
    """按 Ozon 类目 ID 查佣金分段（FBS/FBO × 价格段）。只读。未命中返回 found=false。"""
    return await _call("GET", "/api/v1/commissions/lookup",
                       params={"category_id": str(category_id)})


@mcp.tool()
async def quote_logistics(weight_g: float, depth_cm: float, width_cm: float, height_cm: float,
                          ozon_client_id: str = "", ozon_api_key: str = "") -> dict:
    """物流运费报价（CNY）：按重量+尺寸查费率表。只读。传店铺凭证可自动探测 3PL 渠道。"""
    token = _current_token()
    body = {"weight_g": weight_g, "depth_cm": depth_cm, "width_cm": width_cm, "height_cm": height_cm}
    if token:
        body["token"] = token
    if ozon_client_id and ozon_api_key:
        body["ozon_client_id"] = ozon_client_id
        body["ozon_api_key"] = ozon_api_key
    return await _call("POST", "/api/v1/logistics/quote", body=body)


@mcp.tool()
async def lookup_mapping(keyword: str) -> dict:
    """按 1688 中文类目关键词查已学习的 Ozon 类目映射（L0 学习表）。只读。"""
    return await _call("GET", "/api/v1/mappings/lookup", params={"keyword": keyword})


@mcp.tool()
async def get_seo_keywords(q: str, limit: int = 20) -> dict:
    """查 Ozon 蓝海 SEO 流量关键词（标题/hashtag 用）。只读。limit ≤50。"""
    return await _call("GET", "/api/v1/seo/keywords", params={"q": q, "limit": str(limit)})


TOOLS = [
    "submit_task", "get_task_status", "cancel_task", "get_task_statistics",
    "list_drafts", "submit_draft", "batch_submit_drafts",
    "list_stores", "analyze_store", "run_store_action",
    "lookup_commission", "quote_logistics", "lookup_mapping", "get_seo_keywords",
]

"""FastMCP 工厂 —— 把 skill CLI 的 19 个命令注册为 MCP 工具。

设计原则（见 docs/ozonharness/MCP-TOOLS.md）：
- 薄封装：每个工具 = 参数映射 CLI flag + run_skill_command，业务逻辑留在 skill
- 不做审批：审批/安全门控在 dsh 侧 `tools/pre-execute` 钩子（本 server 是独立进程，无 ctx.approval）
- 参数 1:1 映射 CLI：下划线转连字符（page_size → --page-size）

工具在 dsh 中可见为 `mcp__pounding__<toolName>`（由 dsh-mcp-client 加前缀）。
"""

from __future__ import annotations

from fastmcp import FastMCP

from .skill_runner import run_skill_command
from .tasks import get_manager
from .worker_http import analyze_store as _analyze_store
from .worker_http import run_store_action as _run_store_action

mcp = FastMCP("pounding")


# ── 只读 / 诊断 ────────────────────────────────────────────────

@mcp.tool()
def check() -> dict:
    """诊断前置条件（Chrome / 凭证 / Worker / Ozon API 是否就绪）。只读。"""
    return run_skill_command("check")


@mcp.tool()
def list_stores() -> dict:
    """列出所有已配置的 Ozon 店铺。只读。"""
    return run_skill_command("list_stores")


# ── 配置 / 凭证（write，dsh 侧审批）──────────────────────────────

@mcp.tool()
def set_store(name: str, client_id: str, api_key: str, currency: str = "") -> dict:
    """配置 Ozon 店铺凭证。写操作（敏感）。"""
    return run_skill_command("set_store", name=name, client_id=client_id,
                             api_key=api_key, currency=currency)


@mcp.tool()
def set_token(token: str) -> dict:
    """设置 MXOU 平台 token。写操作（敏感）。"""
    return run_skill_command("set_token", token=token)


@mcp.tool()
def set_ak(ak: str) -> dict:
    """手动设置 1688 Access Key。写操作（敏感）。"""
    return run_skill_command("set_ak", ak=ak)


@mcp.tool()
def get_ak(timeout: int = 300) -> dict:
    """浏览器自动获取 1688 AK。写操作（需本地 Chrome）。"""
    return get_manager().run_and_record("get_ak", {"timeout": timeout}, source="agent")


# ── 采集 / 选品（只读为主，提交类 flag 触发 dsh 侧审批）───────────

@mcp.tool()
def search(query: str, page_size: int = 5, sort: str = "",
           rules: str = "", store: str = "", auto_submit: bool = False) -> dict:
    """搜索 1688 商品。只读；auto_submit=True 时批量提交上架（dsh 侧审批）。"""
    return get_manager().run_and_record("search",
        {"query": query, "page_size": page_size, "sort": sort, "rules": rules, "store": store, "auto_submit": auto_submit},
        source="agent")


@mcp.tool()
def probe(url: str, timeout: int = 30) -> dict:
    """CDP 探针抓取 1688 商品详情页。只读。"""
    return get_manager().run_and_record("probe", {"url": url, "timeout": timeout}, source="agent")


@mcp.tool()
def image_search(image: str, limit: int = 10, sort: str = "", source: str = "aibuy") -> dict:
    """以图搜款（上传图片找 1688 同款）。只读。source: aibuy/ak/cdp。"""
    return get_manager().run_and_record("image_search",
        {"image": image, "limit": limit, "sort": sort, "source": source}, source="agent")


@mcp.tool()
def category(query: str, lang: str = "ZH_HANS", max: int = 5, store: str = "") -> dict:
    """查询 Ozon 类目（关键词 → 候选类目）。只读。lang: ZH_HANS/EN/RU。"""
    return run_skill_command("category", query, lang=lang, max=max, store=store)


@mcp.tool()
def follow(ozon_url: str, auto_submit: bool = False, to_box: bool = False,
           store: str = "", review: bool = False, notify: bool = False) -> dict:
    """跟卖 Ozon 商品（竞品 → 找 1688 同款 → 上架）。auto_submit/to_box 触发 dsh 侧审批。"""
    return get_manager().run_and_record("follow",
        {"ozon_url": ozon_url, "auto_submit": auto_submit, "to_box": to_box,
         "store": store, "review": review, "notify": notify}, source="agent")


@mcp.tool()
def discover(url: str = "", keyword: str = "", local: bool = False,
             max_products: int = 50, min_margin: float = 15.0,
             store: str = "", auto_submit: bool = False, to_box: bool = False,
             fission: bool = False, max_depth: int = 2,
             rules: str = "", review: bool = False, notify: bool = False) -> dict:
    """Ozon 选品 v2（采集 → 分析 → 挑货）。只读；auto_submit/to_box/fission 触发 dsh 侧审批。
    更多参数（fx_rate / min_price / max_price / brand_filter / export / blue-ocean 等）见 skill CLI discover --help。"""
    return get_manager().run_and_record("discover",
        {"url": url, "keyword": keyword, "local": local,
         "max_products": max_products, "min_margin": min_margin, "store": store,
         "auto_submit": auto_submit, "to_box": to_box, "fission": fission,
         "max_depth": max_depth, "rules": rules, "review": review, "notify": notify},
        source="agent")


@mcp.tool()
def discover_multi(keywords: str, max_each: int = 30, local: bool = False,
                   min_margin: float = 15.0, store: str = "",
                   auto_submit: bool = False, to_box: bool = False) -> dict:
    """多关键词批量选品。keywords 逗号分隔。auto_submit/to_box 触发 dsh 侧审批。"""
    return get_manager().run_and_record("discover_multi",
        {"keywords": keywords, "max_each": max_each, "local": local,
         "min_margin": min_margin, "store": store, "auto_submit": auto_submit, "to_box": to_box},
        source="agent")


@mcp.tool()
def seller(seller_id: str, max_products: int = 60, max_skus: int = 30) -> dict:
    """卖家店铺全产品运营分析（跟卖前 20 名卖家 → 店铺选品）。只读。"""
    return get_manager().run_and_record("seller",
        {"seller_id": seller_id, "max_products": max_products, "max_skus": max_skus}, source="agent")


@mcp.tool()
def queries(type: str, keyword: str = "", sku: str = "", category_id: str = "",
            price_min: float | None = None, price_max: float | None = None) -> dict:
    """what-to-sell 榜单查询。type: all-queries/ozon-bestsellers/market-bestsellers。只读。"""
    return get_manager().run_and_record("queries",
        {"type": type, "keyword": keyword, "sku": sku, "category_id": category_id,
         "price_min": price_min, "price_max": price_max}, source="agent")


# ── 上架组装 / 提交 ────────────────────────────────────────────

@mcp.tool()
def graph(item_id: str = "", url: str = "", category_query: str = "",
          retries: int = 3, store: str = "", no_submit: bool = False,
          to_box: bool = False, ozon_ref_url: str = "",
          template_id: str = "", notify: bool = False) -> dict:
    """组装 GraphInput 信封并提交上架。默认直接提交（dsh 侧 pre-execute 审批）；
    no_submit=True 只组装；to_box=True 入采集箱。"""
    return run_skill_command(
        "graph", item_id=item_id, url=url, category_query=category_query,
        retries=retries, store=store, no_submit=no_submit, to_box=to_box,
        ozon_ref_url=ozon_ref_url, template_id=template_id, notify=notify,
    )


@mcp.tool()
def query(task_id: str, watch: bool = False, timeout: int = 900) -> dict:
    """查询 Worker 任务状态。只读。watch=True 轮询直到终态。"""
    return run_skill_command("query", task_id, watch=watch, timeout=timeout)


# ── 维护 ──────────────────────────────────────────────────────

@mcp.tool()
def update() -> dict:
    """检查并应用 skill 自动更新。写操作（维护）。"""
    return run_skill_command("update")


@mcp.tool()
def cleanup() -> dict:
    """清理缓存/临时数据。默认预演（--all --dry-run）不真删；破坏性操作（dsh 侧双重确认）。"""
    return run_skill_command("cleanup", all=True, dry_run=True)


# ── 店铺分析 / 执行（直接 HTTP 调 worker，非 skill CLI subprocess）──────────

@mcp.tool()
def analyze_store(store_id: str) -> dict:
    """整店分析（读）：利润率/库存/候选清单（summary + profit_trend + 三组清单）。只读。

    直接 HTTP 调 worker `GET /api/v1/stores/{store_id}/analysis`（非 skill CLI）。
    返回结构化 JSON；工作不可达/失败返回 error dict（不 raise）。"""
    return _analyze_store(store_id)


@mcp.tool()
def run_store_action(store_id: str, operation: str, payload: dict | None = None) -> dict:
    """单店执行（写，dsh 侧审批）：改价/stocks/归档/活动报名/自建促销。

    直接 HTTP 调 worker `POST /api/v1/stores/{store_id}/actions`（非 skill CLI）。
    operation ∈ {bulk_update_prices, bulk_update_stocks, bulk_archive,
                 actions_register, seller_action_discount}。
    payload 为 operation 请求体字段（如 prices/stocks/product_ids/action_id）。
    本工具只负责触发并返回执行结果（含 store_operation_log），不做自动执行决策。"""
    return _run_store_action(store_id, operation, payload)


def main() -> None:
    """MCP stdio 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()

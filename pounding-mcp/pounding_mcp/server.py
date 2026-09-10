"""FastMCP 工厂 —— 注册 MCP 工具（v0.74 批：21 个 skill CLI 封装 + 5 个 worker REST 直调
+ 4 个后台 job_* 监控 = 30 个）。

设计原则（见 docs/ozonharness/MCP-TOOLS.md）：
- 薄封装：每个工具 = 参数映射 CLI flag + run_skill_command，业务逻辑留在 skill
- 不做审批：审批/安全门控在 dsh 侧 `tools/pre-execute` 钩子（本 server 是独立进程，无 ctx.approval）
- 参数 1:1 映射 CLI：下划线转连字符（page_size → --page-size）

v0.70 后台化：慢命令（discover/discover_multi/discover_task/follow/seller/queries/
graph）加 `background=true` → CLI 进程脱离会话独立运行（输出落盘），工具 <1s 返回
task dict，agent 用 job_status/job_result 轮询；dsh 会话关闭任务照跑，重开会话
job_list 找回。缺省 background=False 行为与旧版逐字一致。

v0.74 会话代管（A6 P2 #6 / BL-13）：session_sync 封装 skill CLI session-sync，
worker 409 session_expired 后对话内自愈；输出经脱敏层才返回（绝不回显 cookie 值）。

工具在 dsh 中可见为 `mcp__pounding__<toolName>`（由 dsh-mcp-client 加前缀）。
"""

from __future__ import annotations

import re

from fastmcp import FastMCP

from .skill_runner import run_skill_command
from .skill_runner import run_skill_command_capture
from .tasks import get_manager
from .worker_http import analyze_store as _analyze_store
from .worker_http import run_store_action as _run_store_action
from .worker_http import report_issue as _report_issue
from .worker_http import list_error_reports as _list_error_reports
from .worker_http import get_task_forensics as _get_task_forensics

mcp = FastMCP("pounding")


def _run_or_background(kind: str, params: dict, background: bool,
                       force: bool = False) -> dict:
    """同步执行（缺省，兼容旧流程）或后台启动（background=true 立即返回）。"""
    if not background:
        return get_manager().run_and_record(kind, params, source="agent")
    return get_manager().start_background(kind, params, source="agent", force=force)


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
           rules: str = "", store: str = "", auto_submit: bool = False,
           to_box: bool = False) -> dict:
    """搜索 1688 商品。双出口二选一：auto_submit=True 直接批量上架（dsh 审批）；
    to_box=True 逐个入采集箱（WebUI 认领后再上架）。都不传=只搜索。"""
    return get_manager().run_and_record("search",
        {"query": query, "page_size": page_size, "sort": sort, "rules": rules, "store": store,
         "auto_submit": auto_submit, "to_box": to_box},
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
           store: str = "", review: bool = False, notify: bool = False,
           background: bool = False, force: bool = False) -> dict:
    """跟卖 Ozon 商品（竞品 → 找 1688 同款 → 上架）。auto_submit/to_box 触发 dsh 侧审批。
    background=true 后台跑立即返回 task_id（job_status 轮询）；force 强制越过单飞闸。"""
    return _run_or_background("follow",
        {"ozon_url": ozon_url, "auto_submit": auto_submit, "to_box": to_box,
         "store": store, "review": review, "notify": notify},
        background, force)


@mcp.tool()
def discover(url: str = "", keyword: str = "", local: bool = False,
             max_products: int = 50, min_margin: float = 15.0,
             store: str = "", auto_submit: bool = False, to_box: bool = False,
             note: str | None = None,
             fission: bool = False, max_depth: int = 2,
             rules: str = "", review: bool = False, notify: bool = False,
             export: str = "", output: str = "",
             background: bool = False, force: bool = False) -> dict:
    """Ozon 选品 v2（采集 → 分析 → 挑货）。只读；auto_submit/to_box/fission 触发 dsh 侧审批。
    export="csv|json|both" + output=路径 落盘全量+选中结果（后台跑完 CSV 可复核）。
    note=采集箱备注（to_box=True 入箱时随草稿存储，≤2000 字；运营态，不进上架信封）。
    更多参数（fx_rate / min_price / max_price / brand_filter / blue-ocean 等）见 skill CLI discover --help。
    background=true 后台跑立即返回 task_id（分钟级任务必用，别阻塞对话）。"""
    return _run_or_background("discover",
        {"url": url, "keyword": keyword, "local": local,
         "max_products": max_products, "min_margin": min_margin, "store": store,
         "auto_submit": auto_submit, "to_box": to_box, "note": note,
         "fission": fission, "max_depth": max_depth, "rules": rules,
         "review": review, "notify": notify,
         "export": export, "output": output},
        background, force)


@mcp.tool()
def discover_multi(keywords: str, max_each: int = 30, local: bool = False,
                   min_margin: float = 15.0, store: str = "",
                   auto_submit: bool = False, to_box: bool = False,
                   background: bool = False, force: bool = False) -> dict:
    """多关键词批量选品。keywords 逗号分隔。auto_submit/to_box 触发 dsh 侧审批。
    background=true 后台跑立即返回 task_id。"""
    return _run_or_background("discover_multi",
        {"keywords": keywords, "max_each": max_each, "local": local,
         "min_margin": min_margin, "store": store, "auto_submit": auto_submit,
         "to_box": to_box},
        background, force)


@mcp.tool()
def discover_task(url: str = "", keyword: str = "", target_count: int = 50,
                  min_margin: float = 15.0, match_limit: int | None = None,
                  match_concurrency: int = 1, store: str = "",
                  to_box: bool = False, dry_run: bool = True,
                  resume: bool = False, max_scan: int = 300,
                  expend_shop: int | None = None,
                  export: str = "", auto_submit: bool = False,
                  background: bool = False, force: bool = False) -> dict:
    """任务式全自动目标驱动选品（漏斗 v2，v0.70 语义翻转）：--max-scan 上限采集
    （默认 300，深滚动）→ ai 粗筛 → 自动 1688 匹配 → profitable 达到 target_count
    即停（达标数，护图搜配额；匹配池按达标可能性降序）。match_limit 缺省=目标×3。
    expend_shop=N=拓店模式（v0.74）：url 须为 Ozon 商品页唯一种子，裂变展开竞品
    卖家→店铺产品（N=期望产出规模，与 keyword 互斥；0/缺省=关闭）。
    双出口二选一（互斥）：to_box=True 入采集箱（POST /drafts，可逆，dsh 审批）；
    auto_submit=True 直接提交 Worker 上架（submit_task，真实创建商品，必须确认）。
    dry_run=True（默认）零副作用；export=CSV 路径落盘全量候选（含状态/利润率列）。
    resume 续跑同入口最近任务（跳过已处理 pid 不重烧图搜）；粗筛池耗尽仍未达标
    会如实报告缺口。结果尾部输出结构化 summary。
    其余筛选参数（filters/filter_profile/min_price/max_price/brand_filter 等）见
    skill CLI discover-task --help。
    background=true 后台跑立即返回 task_id——本命令分钟级，长任务必用。"""
    return _run_or_background("discover_task",
        {"url": url, "keyword": keyword, "target_count": target_count,
         "min_margin": min_margin, "match_limit": match_limit,
         "match_concurrency": match_concurrency, "store": store,
         "to_box": to_box, "auto_submit": auto_submit, "dry_run": dry_run,
         "resume": resume, "max_scan": max_scan, "expend_shop": expend_shop,
         "export": export},
        background, force)


@mcp.tool()
def seller(seller_id: str, max_products: int = 60, max_skus: int = 30,
           background: bool = False, force: bool = False) -> dict:
    """卖家店铺全产品运营分析（跟卖前 20 名卖家 → 店铺选品）。只读。
    background=true 后台跑立即返回 task_id。"""
    return _run_or_background("seller",
        {"seller_id": seller_id, "max_products": max_products, "max_skus": max_skus},
        background, force)


@mcp.tool()
def queries(type: str, keyword: str = "", sku: str = "", category_id: str = "",
            price_min: float | None = None, price_max: float | None = None,
            background: bool = False, force: bool = False) -> dict:
    """what-to-sell 榜单查询。type: all-queries/ozon-bestsellers/market-bestsellers。只读。
    background=true 后台跑立即返回 task_id。"""
    return _run_or_background("queries",
        {"type": type, "keyword": keyword, "sku": sku, "category_id": category_id,
         "price_min": price_min, "price_max": price_max},
        background, force)


# ── 上架组装 / 提交 ────────────────────────────────────────────

@mcp.tool()
def graph(item_id: str = "", url: str = "", category_query: str = "",
          category_id: int | None = None, type_id: int | None = None,
          min_density: float | None = None,
          retries: int = 3, store: str = "", no_submit: bool = False,
          to_box: bool = False, ozon_ref_url: str = "",
          template_id: str = "", notify: bool = False,
          background: bool = False, force: bool = False) -> dict:
    """组装 GraphInput 信封并提交上架。默认直接提交（dsh 侧 pre-execute 审批）；
    no_submit=True 只组装；to_box=True 入采集箱。
    category_id+type_id 同时提供=manual 权威类目直传（绕过自动匹配，v0.69 白名单
    通道；类目 ID 用 category 工具查询）；min_density=g/cm³ 密度下限拦截（如 0.1，
    缺省不拦截仅告警）。
    background=true 后台跑立即返回 task_id——CDP+图搜分钟级，长任务必用；
    完成后 job_status 的 worker_task_ids 可直接喂给 query 查云任务。"""
    return _run_or_background("graph",
        {"item_id": item_id, "url": url, "category_query": category_query,
         "category_id": category_id, "type_id": type_id,
         "min_density": min_density,
         "retries": retries, "store": store, "no_submit": no_submit,
         "to_box": to_box, "ozon_ref_url": ozon_ref_url,
         "template_id": template_id, "notify": notify},
        background, force)


@mcp.tool()
def query(task_id: str, watch: bool = False, timeout: int = 900) -> dict:
    """查询 Worker 任务状态。只读。watch=True 轮询直到终态。"""
    return run_skill_command("query", task_id, watch=watch, timeout=timeout)


# ── 后台任务监控（v0.70：配 background=true 使用）──────────────────

@mcp.tool()
def job_list(limit: int = 20) -> dict:
    """列出本机采集/选品/上架任务（含后台任务与实时进度）。只读。

    会话关闭后任务仍在跑（后台进程独立于会话）；重开会话先 job_list 找回。
    返回 items[]：id/kind/label/status(running|completed|failed|cancelled|
    interrupted)/progress{current,total}/stage/summary/error。"""
    return {"items": get_manager().list(limit)}


@mcp.tool()
def job_status(task_id: str, log_tail: int = 40) -> dict:
    """查单个任务详情：状态/阶段/进度/摘要/错误 + 日志尾 + 关联 worker task_id。只读。

    后台任务（background=true 提交）的进度看 progress/stage 字段；卡住时看
    log_tail 最后几行。完成后 worker_task_ids 给 query 工具查云端任务；
    job_result 取完整结果。"""
    t = get_manager().get(task_id)
    if not t:
        return {"error": f"任务不存在: {task_id}（job_list 可列出全部）"}
    t["log_tail"] = get_manager().log_tail(task_id, log_tail)
    t["worker_task_ids"] = get_manager().extract_worker_task_ids(task_id)
    return t


@mcp.tool()
def job_result(task_id: str) -> dict:
    """取任务完整结果 JSON（任务完成后调用；大结果单独取，不塞进 job_status）。只读。"""
    result, err = get_manager().read_result(task_id)
    if result is None:
        return {"error": err or f"任务 {task_id} 尚无结果（job_status 查状态）"}
    return result


@mcp.tool()
def job_cancel(task_id: str) -> dict:
    """取消运行中的任务（终止子进程/进程组；后台任务同样可取消）。写操作。"""
    ok = get_manager().cancel(task_id)
    return {"ok": ok, "task_id": task_id,
            "hint": "" if ok else "任务不存在或已非 running（job_list 核对）"}


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


# ── 问题反馈（错误报告模板化通道，v0.69）─────────────────────────────

@mcp.tool()
def report_issue(
    title: str,
    severity: str = "medium",
    category: str = "other",
    description: str = "",
    steps: list[str] | None = None,
    command: str = "",
    expect: str = "",
    actual: str = "",
    task_ids: list[str] | None = None,
    item_id: str = "",
    error_codes: list[str] | None = None,
    extra_evidence: dict | None = None,
) -> dict:
    """用户问题反馈 → 错误报告入 worker 跟踪队列（模板化，v0.69）。

    用户报问题时调用：先用 check_task_status 等工具收集证据，再填本模板提交。
    worker 会按 task_ids 自动附加任务快照（状态/错误/时间线/product_id），报告自足可复现。
    - title 必填一句话概括；severity ∈ {high,medium,low}；
      category ∈ {upload_failed,category_wrong,attribute_error,image_error,pricing,cli_bug,other}
    - 复现方式：steps（逐步）/ command（实际命令）/ expect vs actual（期望 vs 实际）
    - 证据：task_ids（必给，触发自动快照）/ item_id（1688 offer）/ error_codes（Ozon 拒单码原样）
    模板契约：worker 仓库 docs/ERROR-REPORT-TEMPLATE.md；agent 侧使用纪律
    （何时报/怎么报/红线）见 skill/references/error-report.md。提交成功返回 report_id。"""
    reproduction: dict = {}
    if steps:
        reproduction["steps"] = steps
    if command:
        reproduction["command"] = command
    if expect:
        reproduction["expect"] = expect
    if actual:
        reproduction["actual"] = actual
    evidence: dict = dict(extra_evidence or {})
    if task_ids:
        evidence["task_ids"] = task_ids
    if item_id:
        evidence["item_id"] = item_id
    if error_codes:
        evidence["error_codes"] = error_codes
    return _report_issue(title, severity, category, description,
                         reproduction or None, evidence or None)


@mcp.tool()
def list_error_reports(status: str = "", limit: int = 50, report_id: str = "") -> dict:
    """查看本租户已提交的错误报告（列表或单条详情，只读）。

    status ∈ {new,triaging,fixed,wontfix} 可筛；report_id 非空返回单条详情
    （含 worker 自动附加的任务快照 auto_context）。"""
    return _list_error_reports(status, limit, report_id)


@mcp.tool()
def get_task_forensics(task_id: str) -> dict:
    """任务取证一站式只读聚合（v0.70）：任务快照 + 上架留存(listing_result_log)
    + 类目/属性匹配审计四路事实。

    排查「为什么失败 / 为什么这么上架」首选——先 get_task_forensics 取证，
    仍无结论再按模板 report_issue（task_ids 自动附快照）。
    跨租户/不存在的任务返回 404。"""
    return _get_task_forensics(task_id)


# ── 会话代管（v0.74 批次 C / A6 P2 #6 / BL-13：worker 409 session_expired 自愈）──
# 红线：cookie 明文绝不进本工具返回值。CLI 自身已脱敏（只打 cookie 名单+状态+
# harvested_at），本层再兜一道保守打码：key 带 cookie/token/session/auth/bearer
# 字样的 k[:=]v 对值一律打码；≥48 连续密钥形态字符兜底打码——宁可误杀不可漏杀。

_SECRET_KV_RE = re.compile(
    r"(?i)\b([a-z0-9_-]{0,20}(?:cookie|token|session|auth|bearer)[a-z0-9_.-]{0,24})"
    r"(\s*[:=]\s*)([^\n]*)"
)
_LONG_SECRET_RE = re.compile(r"(?<![\w/.:-])[A-Za-z0-9+/_=.-]{48,}(?![\w/.=-])")
_REDACTED = "[已打码]"


def _redact_secrets(text: str) -> str:
    """文本脱敏：secret 形态的 k[:=]v 值与超长密钥串打码（保守，宁可误杀）。

    注意「cookie 名单: sc_company_id, ...」这类名单行不被打码——键后跟中文
    （如「名单」）不满足 k[:=]v 形态，且名单本就是 CLI 有意暴露的脱敏信息。"""
    text = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    return _LONG_SECRET_RE.sub(_REDACTED, text)


def _redact_deep(obj):
    """递归脱敏 str/dict/list（_parse_output 产物可能是 JSON dict 或 {"raw": 文本}）。"""
    if isinstance(obj, str):
        return _redact_secrets(obj)
    if isinstance(obj, dict):
        return {k: _redact_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_deep(v) for v in obj]
    return obj


@mcp.tool()
def session_sync(credential_id: str, worker_url: str = "") -> dict:
    """收割本机 Chrome 的 seller.ozon.ru 会话上传 worker 代管（AES-GCM 加密，脱敏）。

    对话内自愈通道：worker 返回 409 session_expired / check 提示会话过期 /
    首次启用店铺会话代管时调用。前置：让用户在工具 Chrome 打开一次
    seller.ozon.ru 卖家后台（确保登录），成功后重试原请求即可。
    credential_id = worker 店铺凭证 ID（与 analyze_store/run_store_action 的
    store_id 同源；WebUI 店铺管理页可见）。worker_url 仅本地调试指定。
    成功返回脱敏摘要（cookie 名单 + 状态 + harvested_at）；未登录或缺核心
    cookie sc_company_id 时 CLI fail-fast 拒传（exit 2），返回补救提示。
    红线：绝不回显 cookie 值——CLI 输出过脱敏层后才返回。"""
    parsed, proc = run_skill_command_capture(
        "session_sync", credential_id=credential_id, worker_url=worker_url)
    redacted = _redact_deep(parsed)
    out: dict = {"ok": proc.returncode == 0, "credential_id": credential_id,
                 "exit_code": proc.returncode}
    if proc.returncode == 2:
        out["hint"] = ("本机 Chrome 未登录 seller.ozon.ru 或未加载过卖家后台"
                       "（缺核心 cookie sc_company_id，CLI 拒传半截会话）——"
                       "请在工具 Chrome 打开一次卖家后台后重试本工具")
    elif proc.returncode != 0:
        out["hint"] = ("worker 不可达 / 鉴权失败 / 凭证不存在——核对 worker_url 与 "
                       "credential_id（WebUI 店铺管理页可见）")
    if isinstance(redacted, dict):
        out.update(redacted)
    else:  # pragma: no cover — _parse_output 恒返回 dict
        out["raw"] = redacted
    return out


def main() -> None:
    """MCP stdio 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()

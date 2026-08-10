"""Ozon Seller 后台运营指标采集 — 跨 Tab 借道（参考 maozi-plugin 实现）。

Ozon 公开页面（www.ozon.ru）不提供月销量/销售增长率/广告占比/上架日期等
运营数据；这些数据位于卖家后台 seller.ozon.ru 的分析接口：

    POST /api/site/seller-analytics/what_to_sell/data/v3   （畅销榜商品数据）
    POST /api/site/searchteam/Stats/queries/search/v2      （all-queries 关键词蓝海）

本模块通过 CDP 打开 seller.ozon.ru（用户浏览器已登录，登录态保留），
在页面上下文内 fetch 同源接口，携带 x-o3-company-id（cookie sc_company_id）
与 zh-Hans 语言头。任何失败 → 返回 {}（或空 list），调用方降级为公开替代指标，
绝不阻断主流程。

what-to-sell SPA 三页真实端点（2026-08-10 CDP 探测证据，见
`.omo/evidence/sentry-attribute-fixes/task-5-c4a.endpoints.json`）：

- all-queries:      POST /api/site/searchteam/Stats/queries/search/v2
                    body: {"text","limit","offset","sort_by","sort_dir","period"}
                    响应 data.data[]：query/count/ca/avgCaRub/uniqSellers/ord/gmv/...
- ozon-bestsellers: POST /api/site/seller-analytics/what_to_sell/data/v3
                    filter: {stock, period: weekly, categories: []}
                    sort: {key: "session_count_search_desc"}（无 sku → Ozon 畅销榜）
- market-bestsellers: 同上端点，filter 加 platform: "PLATFORM_ALL" + 可选
                    categories/[minPrice,maxPrice]（跨平台畅销榜）
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

SELLER_URL = "https://seller.ozon.ru/"

# 每 SKU 一次调用（filter.sku 单值），间隔防反爬
DEFAULT_BATCH_DELAY = 1.0
EVALUATE_TIMEOUT = 30

# what_to_sell 请求模板。__COMPANY_ID__ / __SKU__ 由 Python 侧替换。
# ✅ v0.26（参考 maozi CROSS_TAB）：credentials:"include" — 同源 fetch 默认 same-origin
# 也会带 cookie，但显式 include 覆盖 __Secure-* 分区 cookie / CHIPS 差异，零成本。
_SELLER_ANALYTICS_JS = r'''(async () => {
    try {
        const resp = await fetch('/api/site/seller-analytics/what_to_sell/data/v3', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-o3-company-id': __COMPANY_ID__,
                'x-o3-language': 'zh-Hans',
            },
            credentials: 'include',
            body: JSON.stringify({
                limit: "50",
                offset: "0",
                filter: {stock: "any_stock", period: "monthly", categories: [], sku: "__SKU__"},
                sort: {key: "sum_gmv_desc"}
            }),
        });
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); }
        catch (e) { return JSON.stringify({error: "非JSON响应 status=" + resp.status + " body=" + text.slice(0, 200)}); }
        if (!resp.ok) { return JSON.stringify({error: "HTTP " + resp.status + " " + text.slice(0, 200)}); }
        return JSON.stringify(data);
    } catch (e) {
        return JSON.stringify({error: String((e && e.message) || e)});
    }
})()'''

# 读 sc_company_id cookie（HttpOnly 读不到时返回空，由 Python 侧走 CDP 网络域兜底）
_GET_COMPANY_ID_JS = r'''(() => {
    const m = document.cookie.match(/(?:^|;\s*)sc_company_id=([^;]+)/);
    return m ? m[1] : '';
})()'''

# ── what-to-sell SPA 三页独立模板（v0.33.2，CDP 探测真实端点，勿合并进上面旧模板）──

# ① all-queries 关键词蓝海查询（CDP 探测：POST /api/site/searchteam/Stats/queries/search/v2）
# 响应 data.data[]：query/count/ca/avgCaRub/uniqSellers/ord/gmv/uniqQueriesWCa/searchUsersToOrdUsers
_QUERIES_SEARCH_JS = r'''(async () => {
    try {
        const resp = await fetch('/api/site/searchteam/Stats/queries/search/v2', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-o3-company-id': __COMPANY_ID__,
                'x-o3-language': 'zh-Hans',
            },
            credentials: 'include',
            body: JSON.stringify({
                text: __KEYWORD__,
                limit: "50",
                offset: "0",
                sort_by: "count",
                sort_dir: "desc",
                period: "days_7"
            }),
        });
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); }
        catch (e) { return JSON.stringify({error: "非JSON响应 status=" + resp.status + " body=" + text.slice(0, 200)}); }
        if (!resp.ok) { return JSON.stringify({error: "HTTP " + resp.status + " " + text.slice(0, 200)}); }
        return JSON.stringify(data);
    } catch (e) {
        return JSON.stringify({error: String((e && e.message) || e)});
    }
})()'''

# ② ozon-bestsellers Ozon 畅销榜（CDP 探测：POST what_to_sell/data/v3，period=weekly，
# sort=session_count_search_desc，无 sku。sku_or_id 传入时按单 SKU 过滤）
_QUERIES_OZON_BESTSELLERS_JS = r'''(async () => {
    try {
        const filter = {stock: "any_stock", period: "weekly", categories: []};
        if (__SKU__) { filter.sku = __SKU__; }
        const resp = await fetch('/api/site/seller-analytics/what_to_sell/data/v3', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-o3-company-id': __COMPANY_ID__,
                'x-o3-language': 'zh-Hans',
            },
            credentials: 'include',
            body: JSON.stringify({
                limit: "50",
                offset: "0",
                filter: filter,
                sort: {key: "session_count_search_desc"}
            }),
        });
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); }
        catch (e) { return JSON.stringify({error: "非JSON响应 status=" + resp.status + " body=" + text.slice(0, 200)}); }
        if (!resp.ok) { return JSON.stringify({error: "HTTP " + resp.status + " " + text.slice(0, 200)}); }
        return JSON.stringify(data);
    } catch (e) {
        return JSON.stringify({error: String((e && e.message) || e)});
    }
})()'''

# ③ market-bestsellers 跨平台畅销榜（CDP 探测：同上端点 + platform=PLATFORM_ALL +
# 可选 categories/minPrice/maxPrice。__CATEGORIES__ 形如 ["286"]，__PRICE_FILTER__ 形如
# ',"minPrice":"500","maxPrice":"2000"' 或空串——注意保留合法 JSON 拼装）
_QUERIES_MARKET_BESTSELLERS_JS = r'''(async () => {
    try {
        const resp = await fetch('/api/site/seller-analytics/what_to_sell/data/v3', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-o3-company-id': __COMPANY_ID__,
                'x-o3-language': 'zh-Hans',
            },
            credentials: 'include',
            body: JSON.stringify({
                limit: "50",
                offset: "0",
                filter: {stock: "any_stock", period: "weekly", categories: __CATEGORIES__, platform: "PLATFORM_ALL" __PRICE_FILTER__},
                sort: {key: "session_count_search_desc"}
            }),
        });
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); }
        catch (e) { return JSON.stringify({error: "非JSON响应 status=" + resp.status + " body=" + text.slice(0, 200)}); }
        if (!resp.ok) { return JSON.stringify({error: "HTTP " + resp.status + " " + text.slice(0, 200)}); }
        return JSON.stringify(data);
    } catch (e) {
        return JSON.stringify({error: String((e && e.message) || e)});
    }
})()'''

# ✅ v0.26 premium 解锁注入脚本（学习上品帮 ozon_min.js 的 XHR/fetch 深度拦截机制，
# 精简重写：去掉弹窗/页面跳转/模糊单元格检测等 UI hack，只保留数据接口解锁）。
# 背景：what_to_sell / analytics 图表数据对非 premium 卖家受限（上品帮实证），
# 拦截 premium/status 与 graphs 相关请求，返回伪造的 PREMIUM_PLUS 全量权限响应。
# 幂等（__OZON_PREMIUM_UNLOCK__ 防重复安装）；不匹配的请求原样放行；
# 仅本地自用（用户已登录的 seller.ozon.ru 页面内注入），不对外分发。
_PREMIUM_UNLOCK_JS = r'''(() => {
    if (window.__OZON_PREMIUM_UNLOCK__) return;
    window.__OZON_PREMIUM_UNLOCK__ = true;
    const STATUS_RX = /\/premium\/status|\/get-seller-premium-status/i;
    const GRAPH_RX = /\/analytics\/graphs|\/graph\/data|\/statistics\/data/i;
    const makeStatus = () => ({
        status: "grace_good",
        is_premium: true,
        isPremiumPlus: true,
        isAnalyst: true,
        subscription: {current: "PREMIUM_PLUS", available: ["PREMIUM_PLUS"],
                       grace_period_end_at: new Date(Date.now() + 48384e3).toISOString()},
        features: {analytics: "full", marketing: "full", api: "full_access",
                   graphs: "full", reports: "full", statistics: "full",
                   recommendations: "full"},
        hasAccess: true,
        accessLevel: "FULL",
        dataPoints: Array.from({length: 15}, (_, i) => ({
            id: "metric_" + i, value: Math.floor(616 * Math.random()),
            trend: Math.random() > .5 ? "up" : "down", change: Math.floor(36 * Math.random())
        }))
    });
    const makeGraph = () => ({is_premium: true, isPremiumPlus: true, graphsAccess: true,
        dataSets: ["sales", "traffic", "conversion"], timeRanges: ["day", "week", "month"]});
    const fake = (url) => STATUS_RX.test(url) ? makeStatus() : makeGraph();
    // XHR 深度拦截（上品帮机制）：伪造 responseText/status/readyState
    const XHR = window.XMLHttpRequest;
    class UnlockXHR extends XHR {
        constructor() { super(); this._ozonUrl = ""; this._ozonHit = false; }
        open(method, url) { this._ozonUrl = url || ""; return super.open(method, url); }
        send(body) {
            if (STATUS_RX.test(this._ozonUrl) || GRAPH_RX.test(this._ozonUrl)) {
                this._ozonHit = true;
                const text = JSON.stringify(fake(this._ozonUrl));
                Object.defineProperties(this, {
                    responseText: {value: text}, response: {value: text},
                    status: {value: 200}, statusText: {value: "OK"},
                    readyState: {get: () => this._ozonHit ? 4 : super.readyState}
                });
                Promise.resolve().then(() => {
                    if (typeof this.onreadystatechange === "function") this.onreadystatechange(new Event("readystatechange"));
                    if (typeof this.onload === "function") this.onload(new Event("load"));
                    this.dispatchEvent(new Event("load"));
                    this.dispatchEvent(new Event("loadend"));
                });
                return;
            }
            return super.send(body);
        }
    }
    try { window.XMLHttpRequest = UnlockXHR; } catch (e) {}
    // fetch 深度拦截（上品帮机制）：返回伪造 Response
    const origFetch = window.fetch;
    window.fetch = (input, init) => {
        const url = (input instanceof Request ? input.url : input) || "";
        if (STATUS_RX.test(url) || GRAPH_RX.test(url)) {
            return Promise.resolve(new Response(JSON.stringify(fake(url)),
                {status: 200, headers: {"Content-Type": "application/json", "X-Intercepted": "true"}}));
        }
        return origFetch(input, init);
    };
})()'''


def _to_int(v: Any) -> int:
    try:
        return int(float(str(v).replace(" ", "").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> float:
    try:
        return float(str(v).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _to_rate(v: Any) -> float:
    """佣金率解析（v0.26 修 maozi 实测根因）。

    毛子实测：what_to_sell 响应的 fbp_rate / rfbs_rate 是**分段对象**
    {fbp_leq_1500, fbp_leq_5000, fbp_gt_5000}，旧代码 _to_float 对 dict
    直接 str() → 恒 0。取中间段（5000 内）代表佣金；标量直接转。
    """
    if isinstance(v, dict):
        for k in ("fbp_leq_5000", "rfbs_leq_5000", "fbp_leq_1500", "fbp_gt_5000"):
            if v.get(k) not in (None, ""):
                return _to_float(v.get(k))
        return 0.0
    return _to_float(v)


def _compute_return_rate(redemption: Any) -> float | None:
    """退货率 = 100 - nullableRedemptionRate（0-100）；无数据/0 → None。"""
    try:
        r = float(str(redemption).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return (100.0 - r) if r > 0 else None


def _first(item: dict, *keys: str, default: Any = 0) -> Any:
    """取第一个非空候选 key 的值。"""
    for k in keys:
        v = item.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _extract_metrics(item: dict) -> dict[str, Any]:
    """从 what_to_sell 的单个 item 中防御式提取运营指标。

    字段名参考 maozi-plugin（what_to_sell 响应透传），camelCase 与 snake_case
    双兼容。27+ 字段覆盖：销量/销售额/日销/广告/促销/流量/转化/跟卖/货币等，
    重量(4497)与尺寸(9454/9455/9456)在 attributes 数组里。
    """
    metrics: dict[str, Any] = {
        "sku": str(_first(item, "sku", "variantId", default="")),
        "brand": str(_first(item, "brand", default="")),
        "sold_count": _to_int(_first(item, "soldCount", "sold_count", "ordersCount")),
        "gmv_sum": _to_float(_first(item, "gmvSum", "gmv_sum", "soldSum")),
        "sold_sum": _to_float(_first(item, "soldSum", "sold_sum", "gmvSum")),
        "sales_dynamics": _to_float(_first(item, "salesDynamics", "sales_dynamics")),
        # 日销/日GMV（毛子 avgOrdersOnAccDays / avgGmvOnAccDays）
        "avg_orders_on_acc_days": _to_int(_first(item, "avgOrdersOnAccDays", "avg_orders_on_acc_days")),
        "avg_gmv_on_acc_days": _to_float(_first(item, "avgGmvOnAccDays", "avg_gmv_on_acc_days")),
        "drr": _to_float(_first(item, "drr", "adShare", "ad_share")),
        # 促销参与
        "days_in_promo": _to_int(_first(item, "daysInPromo", "days_in_promo")),
        "discount": _to_float(_first(item, "discount", "promoDiscount")),
        "promo_revenue_share": _to_float(_first(item, "promoRevenueShare", "promo_revenue_share")),
        "days_with_trafarets": _to_int(_first(item, "daysWithTrafarets", "days_with_trafarets")),
        # 流量/转化
        "qty_view_pdp": _to_int(_first(item, "qtyViewPdp", "qty_view_pdp")),
        "conv_to_cart_pdp": _to_float(_first(item, "convToCartPdp", "conv_to_cart_pdp")),
        "session_count_search": _to_int(_first(item, "sessionCountSearch", "session_count_search")),
        "conv_to_cart_search": _to_float(_first(item, "convToCartSearch", "conv_to_cart_search")),
        "conv_view_to_order": _to_float(_first(item, "convViewToOrder", "conv_view_to_order")),
        "custom_click_rate": _to_float(_first(item, "customClickRate", "custom_click_rate")),
        # 发货模式 / 退货取消率（return_rate = 100 - nullableRedemptionRate）
        "sales_schema": str(_first(item, "salesSchema", "sales_schema", default="")),
        "nullable_redemption_rate": _to_float(item.get("nullableRedemptionRate")),
        # 体积/重量（custom_volume 为尺寸串，custom_weight 为克）
        "custom_volume": str(_first(item, "customVolume", "custom_volume", default="")),
        "custom_weight": _to_float(_first(item, "customWeight", "custom_weight")),
        "create_days": _to_int(_first(item, "createDays", "upTimeDays", "upTime")),
        "create_date": str(_first(item, "nullableCreateDate", "createDate", default="")),
        "nullable_create_date": str(_first(item, "nullableCreateDate", "createDate", default="")),
        # 跟卖（otherOffersFromSellers 同源）
        "follow_info": _first(item, "followInfo", "follow_info", default=[]),
        "follow_min_price": _to_float(_first(item, "followMinPrice", "follow_min_price")),
        "follow_max_price": _to_float(_first(item, "followMaxPrice", "follow_max_price")),
        # 货币双通道（soldSumCny / soldSumRub）
        "sold_sum_cny": _to_float(_first(item, "soldSumCny", "sold_sum_cny")),
        "sold_sum_rub": _to_float(_first(item, "soldSumRub", "sold_sum_rub")),
        "return_rate": _compute_return_rate(item.get("nullableRedemptionRate")),
        "rating": _to_float(_first(item, "rating", "avgRating")),
        "review_count": _to_int(_first(item, "reviewsCount", "review_count")),
        "commission_fbp": _to_rate(_first(item, "fbp_rate", "commissionFbp", "fbpRate")),
        "commission_rfbs": _to_rate(_first(item, "rfbs_rate", "commissionRfbs", "rfbsRate")),
        # ✅ v0.26 权威类目（Seller 空间，wave2 眉笔类目错配根因修复）：
        # what_to_sell 返回 category1Id/category2Id/category3Id —— category2Id 即
        # Seller 树的 description_category_id(dc)、category3Id 即叶子 type_id。
        # 实测验证：眉笔 dc=17028990 + type=93418 → schema API 200 有效。
        # skill 抓竞品页面拿到的只是 Widget 空间面包屑 ID（如 dc=6522），
        # worker 得靠 pg_trgm 猜（sim=0.353 误匹配 → DESCRIPTION_DECLINE 类目不符）。
        # 用权威 Seller 类目直接覆盖 draft.ozon_category → worker 数字直查命中，不再猜。
        "category1_id": _to_int(item.get("category1Id")),
        "category2_id": _to_int(item.get("category2Id")),
        "category3_id": _to_int(item.get("category3Id")),
    }

    # 重量/尺寸在 attributes（毛子: 4497 重量, 9454/9455/9456 长/宽/高, 单位 mm）
    attrs = item.get("attributes") or item.get("characteristics") or []
    for a in attrs:
        try:
            aid = int(a.get("id") or a.get("attribute_id") or 0)
        except (TypeError, ValueError):
            continue
        if aid == 4497:
            metrics["weight_g"] = _to_int(a.get("value"))
        elif aid == 9454:
            metrics["length_mm"] = _to_float(a.get("value"))
        elif aid == 9455:
            metrics["width_mm"] = _to_float(a.get("value"))
        elif aid == 9456:
            metrics["height_mm"] = _to_float(a.get("value"))

    # 标记是否拿到了有效销量（区别于"接口通了但无数据"）
    metrics["has_sales_data"] = bool(metrics.get("sold_count") or metrics.get("gmv_sum"))
    return metrics


def _parse_response(data: dict) -> dict[str, Any]:
    """从 what_to_sell 响应中取第一个 item，防御式兼容 result.items / items 两种结构。"""
    result = data.get("result") or {}
    items = result.get("items") or data.get("items") or []
    if items:
        try:
            return _extract_metrics(items[0])
        except Exception as exc:  # 防御：字段结构异常不阻断
            logger.debug("parse what_to_sell item failed: %s", exc)
    return {}


def _tab_for_seller(cdp) -> tuple:
    """获取 seller.ozon.ru 的可用 Tab —— ✅ v0.26 参考 maozi CROSS_TAB 借道。

    优先复用用户已打开的 seller.ozon.ru Tab（登录态天然在 cookie 里），
    而不是新建 Tab（新 Tab 打开根路径会被重定向到 /app/ 或登录页，
    sc_company_id 拿不到 → 0/1 SKUs have data 头号根因）。

    Returns:
        (tab, reused)：reused=True 表示复用用户 Tab（调用方必须 release +
        close(close_remote=False) 防误关用户标签页）；False 表示新建（可正常 close）。
    """
    try:
        reused = cdp.find_tab("seller.ozon.ru")
        if reused is not None:
            logger.info("seller.ozon.ru: 复用用户已登录 seller Tab（跨 Tab 借道）")
            # ✅ v0.26 premium 解锁：已加载页面无法 add_init_script → 运行时注入
            _install_premium_unlock(reused, reused=True)
            return reused, True
    except Exception as exc:
        logger.debug("find_tab seller.ozon.ru 失败（降级新建）: %s", exc)
    logger.info("seller.ozon.ru: 未找到已打开 seller Tab，新建（可能需重新登录）")
    tab = cdp.new_tab()
    # ✅ v0.26 premium 解锁：新建场景导航前预注入（上品帮 addScriptToEvaluateOnNewDocument 时机）
    try:
        tab.add_init_script(_PREMIUM_UNLOCK_JS)
    except Exception as exc:
        logger.debug("premium unlock 预注入失败（继续）: %s", exc)
    tab.navigate(SELLER_URL, wait_until="domcontentloaded", timeout=30)
    time.sleep(3)  # 等登录态/SPA 初始化
    return tab, False


def _install_premium_unlock(tab, reused: bool = False) -> None:
    """在 seller tab 上安装 premium 解锁（幂等，失败不阻断主流程）。

    学习上品帮 ozon_min.js：伪造 premium/status 与 graphs 接口响应，解锁
    what_to_sell / 图表数据对非 premium 账号的限制。仅本地自用。
    """
    try:
        if reused:
            tab.evaluate(_PREMIUM_UNLOCK_JS, timeout=10)
        # 新建场景已在 navigate 前 add_init_script 预注入，无需重复
        logger.debug("premium unlock 注入完成 (reused=%s)", reused)
    except Exception as exc:
        logger.debug("premium unlock 注入失败（不影响主流程）: %s", exc)


def _close_seller_tab(cdp, tab, reused: bool) -> None:
    """关闭/归还 seller Tab。reused=True 时只关 WS 不关远程（防误关用户标签页）。"""
    if tab is None:
        return
    try:
        if reused:
            cdp.release(tab)
            tab.close(close_remote=False)
        else:
            tab.close()
    except Exception:
        pass


def check_seller_login(cdp) -> bool:
    """检测 seller.ozon.ru 卖家后台登录态（运营数据可用性）。

    登录成功 → sc_company_id cookie 存在。返回 True/False。
    优先复用用户已打开的 seller Tab；否则新建检测。
    """
    tab, reused = None, False
    try:
        tab, reused = _tab_for_seller(cdp)
        company_id = ""
        try:
            company_id = str(tab.evaluate(_GET_COMPANY_ID_JS, timeout=10) or "")
        except Exception:
            pass
        if not company_id:
            try:
                cookies = tab._send("Network.getCookies", {"urls": [SELLER_URL]})
                for c in (cookies.get("cookies") or []):
                    if c.get("name") == "sc_company_id":
                        company_id = str(c.get("value") or "")
                        break
            except Exception:
                pass
        return bool(company_id)
    except Exception:
        return False
    finally:
        _close_seller_tab(cdp, tab, reused)


def fetch_sales_analytics(
    cdp,
    skus: list[str],
    batch_delay: float = DEFAULT_BATCH_DELAY,
    max_skus: int = 200,
    lang: str = "zh-Hans",
) -> dict[str, dict]:
    """批量获取 SKU 的 seller.ozon.ru 运营指标。

    Args:
        cdp: CdpConnection（复用调用方的连接，不自行 close）
        skus: Ozon SKU（商品 ID 数字串）列表
        batch_delay: 每次调用间隔（秒），防反爬
        max_skus: 单次最多查询数量保护
        lang: 请求语言（what_to_sell 用 zh-Hans）；缓存 key 含语言维度，
            防固化错误货币/语言数据

    Returns:
        {sku: metrics_dict}；未登录/接口失败 → 空 dict（调用方降级）。
        metrics_dict 字段见 _extract_metrics()。
    """
    if not skus:
        return {}

    skus = [str(s) for s in skus[:max_skus]]

    # ✅ v0.36 磁盘缓存（昂贵 CDP fetch，6h 复用）。key = sorted skus hash + lang，
    # 语言维度防跨语言固化错误货币数据。只缓存有结果的（失败/未登录不缓存，可重试）。
    from scripts.lib.cache import cache_get, cache_set
    cache_key = f"{','.join(sorted(skus))}|{lang}"
    cached = cache_get("seller_analytics", cache_key)
    if cached is not None:
        return cached

    tab, reused = None, False
    try:
        tab, reused = _tab_for_seller(cdp)

        # 1. 取 sc_company_id cookie（document.cookie 优先，HttpOnly 走 CDP 网络域兜底）
        company_id = ""
        try:
            company_id = str(tab.evaluate(_GET_COMPANY_ID_JS, timeout=10) or "")
        except Exception as exc:
            logger.debug("read sc_company_id via JS failed: %s", exc)
        if not company_id:
            try:
                cookies = tab._send("Network.getCookies", {"urls": [SELLER_URL]})
                for c in (cookies.get("cookies") or []):
                    if c.get("name") == "sc_company_id":
                        company_id = str(c.get("value") or "")
                        break
            except Exception as exc:
                logger.debug("read sc_company_id via CDP cookies failed: %s", exc)

        if not company_id:
            logger.warning("seller.ozon.ru 未登录（无 sc_company_id），运营指标降级为公开数据")
            return {}

        # 2. 逐 SKU 查询
        results: dict[str, dict] = {}
        for i, sku in enumerate(skus):
            try:
                js = _SELLER_ANALYTICS_JS.replace("__COMPANY_ID__", json.dumps(company_id)) \
                                         .replace("__SKU__", sku)  # 模板已带引号，裸值替换（防双重引号）
                raw = tab.evaluate(js, await_promise=True, timeout=EVALUATE_TIMEOUT)
                if raw:
                    data = json.loads(raw)
                    if data.get("error"):
                        # ✅ v0.26: 失败日志升级 — 旧 debug 把 401/403/429/空 items 全吞掉，
                        # 无法定位（毛子对照后改进，保留原始错误正文前 200 字）
                        logger.warning("sku %s analytics error: %s", sku, str(data["error"])[:200])
                    else:
                        metrics = _parse_response(data)
                        if metrics:
                            results[sku] = metrics
                        else:
                            logger.warning("sku %s analytics 响应无可用 item（可能接口结构变化）: %s",
                                           sku, str(raw)[:200])
            except Exception as exc:
                logger.warning("sku %s analytics fetch failed: %s", sku, str(exc)[:200])

            if i < len(skus) - 1:
                time.sleep(batch_delay)

        logger.info("seller analytics: %d/%d SKUs have data", len(results), len(skus))
        if results:
            cache_set("seller_analytics", cache_key, results, ttl=21600)
        return results

    except Exception as exc:
        logger.warning("seller.ozon.ru analytics 整体失败，降级: %s", exc)
        return {}
    finally:
        _close_seller_tab(cdp, tab, reused)


# ═══════════════════════════════════════════════════════════════════════════
# what-to-sell SPA 三页查询（v0.33.2）— 独立 fetch，失败返回 []，绝不抛异常
# ═══════════════════════════════════════════════════════════════════════════


def _parse_query_items(data: dict) -> list[dict]:
    """从 all-queries 响应中提取关键词蓝海列表。

    响应结构：{result: {items: [...]}} 或 {data: {data: [...]}}（CDP 实测
    data.data[] 数组）。每项含 query/count/ca/avgCaRub/uniqSellers/ord/gmv/
    uniqQueriesWCa/searchUsersToOrdUsers 等（maozi what_to_sell 同族字段）。
    """
    result = data.get("result") or {}
    items = result.get("items") or data.get("data") or data.get("items") or []
    if isinstance(items, dict):
        items = items.get("data") or items.get("items") or []
    rows: list[dict] = []
    for item in items or []:
        try:
            if not isinstance(item, dict):
                continue
            rows.append({
                "query": str(_first(item, "query", default="")),
                "count": _to_int(_first(item, "count", "queryCount")),
                "ca": _to_float(_first(item, "ca", "conversion")),
                "avg_ca_rub": _to_float(_first(item, "avgCaRub", "avg_ca_rub")),
                "uniq_sellers": _to_int(_first(item, "uniqSellers", "uniq_sellers")),
                "ordering_amount": _to_int(_first(item, "ord", "orderingAmount", "ordering_amount")),
                "daily_avg": _to_int(_first(item, "dailyAvg", "daily_avg")),
                "gmv": _to_float(_first(item, "gmv", "gmvSum")),
                "uniq_queries_w_ca": _to_int(_first(item, "uniqQueriesWCa", "uniq_queries_w_ca")),
                "search_users_to_ord_users": _to_float(
                    _first(item, "searchUsersToOrdUsers", "search_users_to_ord_users")),
            })
        except Exception as exc:
            logger.debug("parse query item failed: %s", exc)
    return rows


def _parse_bestseller_items(data: dict) -> list[dict]:
    """从 data/v3 响应中提取畅销榜商品列表。

    响应结构：{result: {items: [...]}} 或 {data: {items: [...]}}（CDP 实测
    data.items[]）。每项含 sku/name/brand/soldCount/gmvSum/salesDynamics/
    sessionCountSearch/convToCartSearch/drr/category1Id/2/3/attributes 等。
    """
    result = data.get("result") or data.get("data") or {}
    items = result.get("items") or data.get("items") or []
    rows: list[dict] = []
    for item in items or []:
        try:
            if not isinstance(item, dict):
                continue
            m = _extract_metrics(item)
            m.update({
                "sku": str(_first(item, "sku", "variantId", default="")),
                "name": str(_first(item, "name", "skuName", default="")),
                "brand": str(_first(item, "brand", default="")),
                "category1": str(_first(item, "category1", default="")),
                "category3": str(_first(item, "category3", default="")),
                "link": str(_first(item, "link", default="")),
                "avg_price": _to_float(_first(item, "avgPrice", "avg_price")),
                "session_count_search": _to_int(
                    _first(item, "sessionCountSearch", "session_count_search")),
                "conv_to_cart_search": _to_float(
                    _first(item, "convToCartSearch", "conv_to_cart_search")),
                "views": _to_int(_first(item, "views", "qtyViewPdp")),
                "category1_id": _to_int(item.get("category1Id")),
                "category2_id": _to_int(item.get("category2Id")),
                "category3_id": _to_int(item.get("category3Id")),
            })
            rows.append(m)
        except Exception as exc:
            logger.debug("parse bestseller item failed: %s", exc)
    return rows


def _eval_seller_fetch(tab, js: str) -> tuple[dict, bool]:
    """执行页面内 fetch 模板，返回 (data, ok)。data 含 error 键视为失败。"""
    try:
        raw = tab.evaluate(js, await_promise=True, timeout=EVALUATE_TIMEOUT)
    except Exception as exc:
        logger.warning("seller fetch evaluate failed: %s", str(exc)[:200])
        return {}, False
    if not raw:
        return {}, False
    try:
        data = json.loads(raw)
    except Exception:
        return {}, False
    if isinstance(data, dict) and data.get("error"):
        logger.warning("seller fetch error: %s", str(data["error"])[:200])
        return {}, False
    return data, True


def _read_company_id(tab) -> str:
    """读 sc_company_id：document.cookie 优先，HttpOnly 走 CDP 网络域兜底。"""
    company_id = ""
    try:
        company_id = str(tab.evaluate(_GET_COMPANY_ID_JS, timeout=10) or "")
    except Exception as exc:
        logger.debug("read sc_company_id via JS failed: %s", exc)
    if not company_id:
        try:
            msg_id = tab._send("Network.getCookies", {"urls": [SELLER_URL]})
            resp = tab._recv_until_id(msg_id, timeout=10) or {}
            for c in (resp.get("result", {}).get("cookies") or []):
                if c.get("name") == "sc_company_id":
                    company_id = str(c.get("value") or "")
                    break
        except Exception as exc:
            logger.debug("read sc_company_id via CDP cookies failed: %s", exc)
    return company_id


def fetch_all_queries(cdp, keyword: str | None = None, company_id: str | None = None) -> list[dict]:
    """all-queries 关键词蓝海查询（what-to-sell SPA）。

    CDP 探测端点：POST /api/site/searchteam/Stats/queries/search/v2
    body: {text, limit:50, offset:0, sort_by:count, sort_dir:desc, period:days_7}
    返回 list[dict]（query/count/ca/avg_ca_rub/uniq_sellers/ordering_amount/
    gmv/...）；未登录/失败 → []，绝不抛异常。
    """
    tab, reused = None, False
    try:
        tab, reused = _tab_for_seller(cdp)
        cid = company_id or _read_company_id(tab)
        if not cid:
            logger.warning("seller.ozon.ru 未登录（无 sc_company_id），all-queries 降级为空")
            return []
        js = _QUERIES_SEARCH_JS.replace("__COMPANY_ID__", json.dumps(cid)) \
                               .replace("__KEYWORD__", json.dumps(keyword or ""))
        data, ok = _eval_seller_fetch(tab, js)
        return _parse_query_items(data) if ok else []
    except Exception as exc:
        logger.warning("fetch_all_queries 整体失败，降级: %s", exc)
        return []
    finally:
        _close_seller_tab(cdp, tab, reused)


def fetch_ozon_bestsellers(cdp, sku_or_id: str | None = None,
                           company_id: str | None = None) -> list[dict]:
    """ozon-bestsellers Ozon 畅销榜（what-to-sell SPA）。

    CDP 探测端点：POST /api/site/seller-analytics/what_to_sell/data/v3
    filter: {stock: any_stock, period: weekly, categories: [], sku?}
    sort: {key: session_count_search_desc}
    sku_or_id 传入时按单 SKU 过滤；None 返回全榜。
    返回 list[dict]（sku/name/brand/sold_count/gmv_sum/sales_dynamics/...）；
    未登录/失败 → []。
    """
    tab, reused = None, False
    try:
        tab, reused = _tab_for_seller(cdp)
        cid = company_id or _read_company_id(tab)
        if not cid:
            logger.warning("seller.ozon.ru 未登录（无 sc_company_id），ozon-bestsellers 降级为空")
            return []
        js = _QUERIES_OZON_BESTSELLERS_JS.replace("__COMPANY_ID__", json.dumps(cid))
        if sku_or_id:
            js = js.replace("__SKU__", json.dumps(str(sku_or_id)))
        else:
            js = js.replace("__SKU__", '""')
        data, ok = _eval_seller_fetch(tab, js)
        return _parse_bestseller_items(data) if ok else []
    except Exception as exc:
        logger.warning("fetch_ozon_bestsellers 整体失败，降级: %s", exc)
        return []
    finally:
        _close_seller_tab(cdp, tab, reused)


def fetch_market_bestsellers(cdp, category_id: str | int | None = None,
                             price_rub_min: int | float | None = None,
                             price_rub_max: int | float | None = None,
                             company_id: str | None = None) -> list[dict]:
    """market-bestsellers 跨平台畅销榜（what-to-sell SPA）。

    CDP 探测端点：POST /api/site/seller-analytics/what_to_sell/data/v3
    filter 含 platform: "PLATFORM_ALL"，可叠加 categories=[category_id] 与
    minPrice/maxPrice（RUB 字符串）。
    返回 list[dict]；未登录/失败 → []。
    """
    tab, reused = None, False
    try:
        tab, reused = _tab_for_seller(cdp)
        cid = company_id or _read_company_id(tab)
        if not cid:
            logger.warning("seller.ozon.ru 未登录（无 sc_company_id），market-bestsellers 降级为空")
            return []
        categories = json.dumps([str(category_id)]) if category_id is not None else "[]"
        price_filter = ""
        if price_rub_min is not None or price_rub_max is not None:
            parts = []
            if price_rub_min is not None:
                parts.append(f'"minPrice":"{int(price_rub_min)}"')
            if price_rub_max is not None:
                parts.append(f'"maxPrice":"{int(price_rub_max)}"')
            price_filter = "," + ",".join(parts)
        js = _QUERIES_MARKET_BESTSELLERS_JS.replace("__COMPANY_ID__", json.dumps(cid)) \
                                           .replace("__CATEGORIES__", categories) \
                                           .replace("__PRICE_FILTER__", price_filter)
        data, ok = _eval_seller_fetch(tab, js)
        return _parse_bestseller_items(data) if ok else []
    except Exception as exc:
        logger.warning("fetch_market_bestsellers 整体失败，降级: %s", exc)
        return []
    finally:
        _close_seller_tab(cdp, tab, reused)


def apply_analytics_to_candidate(candidate, metrics: dict) -> bool:
    """把运营指标写入 ProductCandidate（存在即覆盖，不存在的字段保留兜底）。

    Returns: 是否拿到有效指标。
    """
    if not metrics:
        return False
    try:
        if metrics.get("sold_count"):
            candidate.monthly_sales = int(metrics["sold_count"])
        if metrics.get("gmv_sum"):
            candidate.monthly_revenue = float(metrics["gmv_sum"])
        if metrics.get("sales_dynamics"):
            candidate.sales_growth = float(metrics["sales_dynamics"])
        if metrics.get("drr"):
            candidate.drr = float(metrics["drr"])
        if metrics.get("create_days"):
            candidate.create_days = int(metrics["create_days"])
        if metrics.get("weight_g"):
            candidate.weight_g = int(metrics["weight_g"])
        if metrics.get("length_mm") or metrics.get("width_mm") or metrics.get("height_mm"):
            candidate.dimensions_mm = {
                "length": int(metrics.get("length_mm") or 0),
                "width": int(metrics.get("width_mm") or 0),
                "height": int(metrics.get("height_mm") or 0),
            }
        if metrics.get("commission_fbp"):
            candidate.commission_fbp = float(metrics["commission_fbp"])
        if metrics.get("commission_rfbs"):
            candidate.commission_rfbs = float(metrics["commission_rfbs"])
        cat2 = metrics.get("category2_id") or 0
        if cat2:
            candidate.category = str(cat2)
        candidate.has_analytics = bool(
            metrics.get("has_sales_data")
            or metrics.get("sales_dynamics")
            or metrics.get("drr")
            or metrics.get("create_days")
        )
        return True
    except Exception as exc:
        logger.debug("apply analytics failed: %s", exc)
        return False

"""Ozon Seller 后台运营指标采集 — 跨 Tab 借道（参考 maozi-plugin 实现）。

Ozon 公开页面（www.ozon.ru）不提供月销量/销售增长率/广告占比/上架日期等
运营数据；这些数据位于卖家后台 seller.ozon.ru 的分析接口：

    POST /api/site/seller-analytics/what_to_sell/data/v3

本模块通过 CDP 打开 seller.ozon.ru（用户浏览器已登录，登录态保留），
在页面上下文内 fetch 同源接口，携带 x-o3-company-id（cookie sc_company_id）
与 zh-Hans 语言头。任何失败 → 返回 {}，调用方降级为公开替代指标，
绝不阻断主流程。
"""
from __future__ import annotations

import json
import logging
import re
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


def _first(item: dict, *keys: str, default: Any = 0) -> Any:
    """取第一个非空候选 key 的值。"""
    for k in keys:
        v = item.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _extract_metrics(item: dict) -> dict[str, Any]:
    """从 what_to_sell 的单个 item 中防御式提取运营指标。

    字段名参考 maozi-plugin（what_to_sell 响应透传）：
    soldCount/gmvSum/salesDynamics/drr/createDays/...，
    重量(4497)与尺寸(9454/9455/9456)在 attributes 数组里。
    """
    metrics: dict[str, Any] = {
        "sold_count": _to_int(_first(item, "soldCount", "sold_count", "ordersCount")),
        "gmv_sum": _to_float(_first(item, "gmvSum", "gmv_sum", "soldSum")),
        "sales_dynamics": _to_float(_first(item, "salesDynamics", "sales_dynamics")),
        "drr": _to_float(_first(item, "drr", "adShare", "ad_share")),
        "create_days": _to_int(_first(item, "createDays", "upTimeDays", "upTime")),
        "create_date": str(_first(item, "nullableCreateDate", "createDate", default="")),
        "conv_to_cart_search": _to_float(_first(item, "convToCartSearch", "conv_to_cart_search")),
        "session_count_search": _to_int(_first(item, "sessionCountSearch", "session_count_search")),
        "rating": _to_float(_first(item, "rating", "avgRating")),
        "review_count": _to_int(_first(item, "reviewsCount", "review_count")),
        "commission_fbp": _to_rate(_first(item, "fbp_rate", "commissionFbp", "fbpRate")),
        "commission_rfbs": _to_rate(_first(item, "rfbs_rate", "commissionRfbs", "rfbsRate")),
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
            return reused, True
    except Exception as exc:
        logger.debug("find_tab seller.ozon.ru 失败（降级新建）: %s", exc)
    logger.info("seller.ozon.ru: 未找到已打开 seller Tab，新建（可能需重新登录）")
    tab = cdp.new_tab()
    tab.navigate(SELLER_URL, wait_until="domcontentloaded", timeout=30)
    time.sleep(3)  # 等登录态/SPA 初始化
    return tab, False


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
) -> dict[str, dict]:
    """批量获取 SKU 的 seller.ozon.ru 运营指标。

    Args:
        cdp: CdpConnection（复用调用方的连接，不自行 close）
        skus: Ozon SKU（商品 ID 数字串）列表
        batch_delay: 每次调用间隔（秒），防反爬
        max_skus: 单次最多查询数量保护

    Returns:
        {sku: metrics_dict}；未登录/接口失败 → 空 dict（调用方降级）。
        metrics_dict 字段见 _extract_metrics()。
    """
    if not skus:
        return {}

    skus = [str(s) for s in skus[:max_skus]]

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
                                         .replace("__SKU__", json.dumps(sku))
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
        return results

    except Exception as exc:
        logger.warning("seller.ozon.ru analytics 整体失败，降级: %s", exc)
        return {}
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

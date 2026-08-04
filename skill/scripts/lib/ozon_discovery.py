"""Ozon product discovery -- Discover v2（先全量采集 → 表格分析 → 挑完再找货源）。

Flow (v2, 2026-08-01):
1. 采集修正：真实搜索页 /search/?text= 或指定 URL，结果容器限定
   （.tile-root），逐屏滚动 + 懒加载等待 + 翻页 + 去重（PRD-discover-v2）
2. 全量数据：widget API（价格/标题/图/品牌/评分/评论数）+ 跟卖数/最低价
   + seller.ozon.ru 运营指标（月销量/增长率/广告占比/上架天数，可降级）
3. 表格分析（CLI 层）：全量展示 + 人工/规则挑选 —— 此时不花 1688 配额
4. 批量货源：只对选中候选 1688 识图 → 利润计算 → 蓝海评分 → 提交
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DISCOVERY_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "discovery"

# Ozon China goods highlight page
CHINA_HIGHLIGHT_URL = "https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/"

# Default thresholds
DEFAULT_FX_RATE = 0.075          # RUB -> CNY
DEFAULT_LOGISTICS_CNY = 15.0     # rough per-kg logistics cost
DEFAULT_COMMISSION_PCT = 0.10    # Ozon commission ~10%
DEFAULT_MIN_MARGIN_PCT = 15.0    # minimum profit margin %
DEFAULT_MAX_COMPETITORS = 50     # skip products with too many sellers
LOGISTICS_PER_KG_CNY = 40.0      # 跨境物流按重量估算 CNY/kg（保底 8 CNY）

# ⚠️ v0.22: 知名品牌黑名单（discover 直接过滤，避免浪费图搜/1688 匹配/生图资源）。
# 只放知名品牌（跟卖会侵权/被拒）；1688 白牌/小厂牌（fansen 等）不在此列。
KNOWN_BRAND_PATTERNS: list[str] = [
    "nike", "adidas", "puma", "reebok", "new balance", "converse", "vans",
    "apple", "samsung", "huawei", "honor", "oppo", "vivo", "xiaomi", "redmi",
    "oneplus", "lenovo", "dell", "hp", "asus", "msi", "acer", "gigabyte",
    "philips", "bosch", "dewalt", "makita", "stanley", "black+decker", "black decker",
    "miele", "dyson", "braun", "panasonic", "sony", "jbl", "anker", "baseus",
    "remax", "ugreen", "logitech", "razer", "steelseries", "corsair",
    "canon", "nikon", "fujifilm", "gopro", "insta360", "dji",
    "lego", "hape", "gucci", "louis vuitton", "chanel", "dior", "armani",
    "zara", "h&m", "uniqlo", "prada", "versace",
    "tefal", "moulinex", "rowenta", "kenwood", "kitchenaid", "vitamix", "zwilling",
    "wmf", "tescoma", "pyrex", "tupperware", "vileda", "scotch-brite",
    "gorenje", "indesit", "beko", "electrolux", "lg",
]


def _is_known_brand(brand: str) -> bool:
    """判断品牌是否为知名品牌（黑名单命中）。空/白牌返回 False。"""
    b = str(brand or "").strip().lower()
    if not b or b in ("无品牌", "no brand", "нет бренда"):
        return False
    return any(pat in b for pat in KNOWN_BRAND_PATTERNS)


def _is_branded(brand: str) -> bool:
    """判断产品是否带品牌（参考 maozi 插件：brand 非空且非"без бренда"即品牌）。

    Ozon 品牌字段是英/俄文（如 "Nike"、"fansen"、"без бренда"），中文认不出来。
    """
    b = str(brand or "").strip()
    if not b:
        return False
    if b.lower() in (
        "без бренда", "no brand", "no name", "无品牌", "нет бренда", "бренд отсутствует"
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ProductCandidate:
    """A discovered product with profit analysis."""

    ozon_product_id: str
    ozon_title: str
    ozon_price: float  # RUB
    ozon_images: list[str] = field(default_factory=list)
    ozon_url: str = ""
    competing_sellers: int = 0
    min_competing_price: float = 0.0

    # 1688 match
    match_1688_url: str = ""
    match_1688_title: str = ""
    match_1688_price: float = 0.0  # CNY
    match_1688_images: list[str] = field(default_factory=list)

    # Profit estimates
    estimated_logistics_cny: float = 0.0
    estimated_commission: float = 0.0
    estimated_profit_cny: float = 0.0
    profit_margin: float = 0.0  # percentage

    # Seller API data
    category: str = ''
    brand: str = ''
    commission_fbp: float = 0.0   # 百分数（10 = 10%）
    commission_rfbs: float = 0.0  # 百分数（10 = 10%）
    monthly_sales: int = 0
    monthly_revenue: float = 0.0
    weight_g: int = 0
    dimensions_mm: dict = field(default_factory=dict)

    # 公开页指标（v2 新增）
    rating: float = 0.0           # 评分
    review_count: int = 0         # 评论数

    # seller.ozon.ru 运营指标（v2 新增，可降级）
    sales_growth: float = 0.0     # 月销售动态 %
    drr: float = 0.0              # 广告费占比 %
    create_days: int = 0          # 上架天数
    has_analytics: bool = False   # 是否拿到后台运营数据

    # Ozon 类目（面包屑/候选品数据，供提交）
    ozon_category: dict = field(default_factory=dict)

    # Blue ocean
    blue_ocean_score: int = 0

    # Status: pending, ok, uncertain, matched, profitable, rejected, no_match, error
    status: str = "pending"
    error: str = ""

    def __post_init__(self):
        if self.dimensions_mm is None:
            self.dimensions_mm = {}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def discover_from_highlight(
    cdp_url: str,
    max_products: int = 20,
    fx_rate: float = DEFAULT_FX_RATE,
    min_margin_pct: float = DEFAULT_MIN_MARGIN_PCT,
    max_competitors: int = DEFAULT_MAX_COMPETITORS,
    logistics_cny: float = DEFAULT_LOGISTICS_CNY,
    keyword: str = "",
    progress_callback=None,
) -> list[ProductCandidate]:
    """兼容壳（v1 入口）：走 Discover v2 管线，返回 profitable 候选。

    Args 同 v1。注意：v2 中 max_competitors 仅作为 1688 匹配阶段的
    预过滤（跟卖过多的候选不浪费识图配额），不再硬丢弃。
    """
    candidates = collect_and_analyze(
        cdp_url,
        keyword=keyword,
        max_products=max_products,
        progress_callback=progress_callback,
    )
    to_match = [c for c in candidates if c.status in ("ok", "uncertain")
                and c.competing_sellers <= max_competitors]
    match_selected(
        to_match,
        cdp_url,
        fx_rate=fx_rate,
        min_margin_pct=min_margin_pct,
        logistics_cny=logistics_cny,
    )

    profitable = [c for c in candidates if c.status == "profitable"]
    profitable.sort(key=lambda c: c.profit_margin, reverse=True)
    logger.info(
        "Discovery complete: %d total, %d profitable",
        len(candidates), len(profitable),
    )
    return profitable


# ---------------------------------------------------------------------------
# Discover v2 主流程：采集 → 全量数据(+运营指标) → 挑选 → 批量货源
# ---------------------------------------------------------------------------

# 结果容器限定选择器（参考上品帮/毛子：只收搜索结果卡片，不混推荐位）
_COLLECT_ROOT_SEL = (
    '#contentScrollPaginator .tile-root, '
    '#paginatorContent .tile-root, '
    '[data-widget="skuGrid"] .tile-root'
)

# 采集容器内的产品 ID（去重）
# ⚠️ 用 :is() 包裹多选择器列表——直接拼后缀会把逗号分隔的选择器列表拆坏
# （前几个选择器变成选 .tile-root 本身，href 为空，采集恒为空）
_COLLECT_URLS_JS = r'''(() => {
    const links = document.querySelectorAll(':is(__ROOT_SEL__) a[href*="/product/"]');
    const seen = new Set();
    const out = [];
    for (const a of links) {
        const href = (a.href || '').split('?')[0];
        const m = href.match(/\/product\/(?:[^\/]+-)?(\d{5,})\/?$/);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        out.push(m[1]);
    }
    return JSON.stringify(out);
})()'''

# 尝试点击分页器最后一页链接（类目页有 #paginator，搜索页多为无限滚动）
_TRY_NEXT_PAGE_JS = r'''(() => {
    const pager = document.querySelector('#paginator, #paginatorContent');
    if (!pager) return false;
    const links = pager.querySelectorAll('a[href*="page="]');
    if (!links.length) return false;
    links[links.length - 1].click();
    return true;
})()'''


def _lazy_collect_urls(tab: Any, max_products: int,
                       max_scrolls: int = 60, stall_limit: int = 3) -> list[str]:
    """逐屏滚动采集结果容器内的产品 ID（懒加载等待 + 翻页兜底）。

    - 每屏滚动 85% 视口高度
    - 轮询 .tile-root 数量增长（每 0.5s，单屏最多 10s）确认新卡渲染完成
    - 连续 stall_limit 屏无新卡 → 尝试翻页 → 仍无 → 结束
    """
    js = _COLLECT_URLS_JS.replace("__ROOT_SEL__", _COLLECT_ROOT_SEL)
    pids: list[str] = []
    stall = 0
    prev_tiles = 0

    for _ in range(max_scrolls):
        # 1. 采集当前 DOM（容器限定）
        try:
            raw = tab.evaluate(js, timeout=10)
            found = json.loads(raw) if raw else []
        except Exception:
            found = []
        for pid in found:
            if pid not in pids:
                pids.append(pid)
        if len(pids) >= max_products:
            break

        # 2. 滚动到接近底部触发懒加载（85% 视口步长太小，搜索页需滚多次
        #    才接近底部触发加载——实测 4 次滚动才 +8 卡片；直接滚到
        #    底部上方 1 屏，上品帮同款思路，1-2 次滚动即触发）
        try:
            tab.evaluate(
                "window.scrollTo(0, document.body.scrollHeight - window.innerHeight * 1.1)")
        except Exception:
            pass

        # 3. 等新卡片渲染（轮询 tile 数量增长）
        # 注意: 用反引号包裹选择器（内含双引号 [data-widget="skuGrid"]，单引号会破坏 JS）
        grew = False
        for _ in range(20):  # 最多 10s
            time.sleep(0.5)
            try:
                tiles = int(tab.evaluate(
                    f"document.querySelectorAll(`{_COLLECT_ROOT_SEL}`).length") or 0)
            except Exception:
                tiles = 0
            if tiles > prev_tiles:
                prev_tiles = tiles
                grew = True
                break
        if grew:
            stall = 0
            continue

        # 4. 无新卡片：尝试翻页
        stall += 1
        if stall >= stall_limit:
            try:
                clicked = bool(tab.evaluate(_TRY_NEXT_PAGE_JS, timeout=10))
                if clicked:
                    time.sleep(4)  # 等新页加载
                    stall = 0
                    prev_tiles = 0
                    continue
            except Exception:
                pass
            logger.debug("Lazy collect: no new tiles and no next page, stopping")
            break

    return pids[:max_products]


def _analyze_product(cdp_url: str, cdp: Any, pid: str) -> ProductCandidate:
    """单产品全量数据（widget API）：标题/价格/图/品牌/评分/评论数 + 跟卖。"""
    from scripts.lib.ozon_widget import (
        fetch_competing_sellers,
        fetch_product_info,
    )
    from scripts.lib.utils import parse_price as _parse_price

    url = f"https://www.ozon.ru/product/{pid}"
    candidate = ProductCandidate(
        ozon_product_id=pid,
        ozon_title="",
        ozon_price=0.0,
        ozon_images=[],
        ozon_url=url,
    )

    try:
        info = fetch_product_info(cdp_url, pid, cdp=cdp)
        candidate.ozon_title = info.get("title", "")
        # webPrice 结构 price 可能为空，fallback cardPrice（实测部分商品 price 字段为空）
        candidate.ozon_price = _parse_price(
            info.get("price", "") or info.get("cardPrice", ""))
        candidate.ozon_images = info.get("images", [])
        candidate.brand = info.get("brand", "")
        candidate.rating = float(info.get("rating", 0) or 0)
        candidate.review_count = int(info.get("reviewCount", 0) or 0)

        if not candidate.ozon_title:
            candidate.status = "error"
            candidate.error = "no title returned"
            return candidate

        # 跟卖数/最低跟卖价
        sellers = fetch_competing_sellers(cdp_url, pid, cdp=cdp)
        candidate.competing_sellers = sellers.get("count", 0)
        candidate.min_competing_price = sellers.get("min_price", 0)
        candidate.status = "ok"
    except Exception as exc:
        candidate.status = "error"
        candidate.error = str(exc)
        logger.warning("Product %s analysis failed: %s", pid, exc)

    return candidate


def collect_and_analyze(
    cdp_url: str,
    url: str = "",
    keyword: str = "",
    max_products: int = 50,
    use_analytics: bool = True,
    min_price: float = 0,
    max_price: float = 0,
    brand_filter: str = "nobrand",
    progress_callback=None,
) -> list[ProductCandidate]:
    """Discover v2 阶段①+②：采集 + 全量数据 + seller.ozon.ru 运营指标。

    - url 优先；否则 keyword 构造真实搜索页；否则中国站 highlight 页
    - min_price/max_price（RUB，0=不限）：价格区间外的候选标记 filtered，
      跳过运营指标查询（省调用），不参与挑选
    - brand_filter（参考 maozi 插件 brand_option）：nobrand=只要无品牌/白牌（默认，
      规避品牌侵权）；known=只过滤知名品牌黑名单；all=不过滤
    - 返回全部候选（status: ok/uncertain/filtered/error），**不做 1688 匹配**（阶段④）
    - 关键词校验：标题含中文但无关键词 → uncertain（表格标黄，仍可选）
    """
    from scripts.lib.cdp_client import CdpConnection

    if url:
        target_url = url
    elif keyword and keyword.strip():
        import urllib.parse
        target_url = f"https://www.ozon.ru/search/?text={urllib.parse.quote(keyword.strip())}"
        logger.info("Opening Ozon search page: %s", target_url)
    else:
        target_url = CHINA_HIGHLIGHT_URL
        logger.info("Opening Ozon China highlight page: %s", target_url)

    candidates: list[ProductCandidate] = []

    with CdpConnection(cdp_url) as cdp:
        # ── 阶段① 采集 ──
        tab = cdp.new_tab(target_url)
        try:
            time.sleep(5)  # 初始加载
            pids = _lazy_collect_urls(tab, max_products)
            logger.info("Collected %d product IDs", len(pids))
        finally:
            try:
                tab.close()
            except Exception:
                pass

        # ── 阶段② 全量数据 ──
        for i, pid in enumerate(pids):
            candidate = _analyze_product(cdp_url, cdp, pid)

            # ⚠️ v0.22: 品牌过滤（参考 maozi：brand 字段布尔判断 + 配置开关）
            if candidate.status == "ok" and candidate.brand:
                if brand_filter == "nobrand" and _is_branded(candidate.brand):
                    candidate.status = "filtered"
                    candidate.error = f"品牌产品（{candidate.brand}），自动跳过"
                    logger.info("品牌过滤(nobrand): %s（%s）", candidate.ozon_title[:40], candidate.brand)
                elif brand_filter == "known" and _is_known_brand(candidate.brand):
                    candidate.status = "filtered"
                    candidate.error = f"知名品牌产品（{candidate.brand}），自动跳过"
                    logger.info("品牌过滤(known): %s（%s）", candidate.ozon_title[:40], candidate.brand)

            # 关键词相关性校验（仅对含中文的标题有效，俄语标题无法判断）
            if (keyword and candidate.status == "ok"
                    and re.search(r"[\u4e00-\u9fff]", candidate.ozon_title)
                    and keyword.strip() not in candidate.ozon_title):
                candidate.status = "uncertain"
                candidate.error = "标题不含关键词，可能夹带推荐/广告商品"

            # 价格区间过滤（RUB）：区间外标记 filtered，跳过 analytics
            if candidate.status == "ok" and (min_price > 0 or max_price > 0):
                if candidate.ozon_price <= 0:
                    candidate.status = "filtered"
                    candidate.error = "无有效价格"
                elif min_price > 0 and candidate.ozon_price < min_price:
                    candidate.status = "filtered"
                    candidate.error = f"价格低于下限 {min_price:.0f}₽"
                elif max_price > 0 and candidate.ozon_price > max_price:
                    candidate.status = "filtered"
                    candidate.error = f"价格高于上限 {max_price:.0f}₽"

            candidates.append(candidate)
            if progress_callback:
                progress_callback(i + 1, len(pids), candidate)
            time.sleep(0.5)

        # ── 阶段②b 运营指标（借道 seller.ozon.ru，失败自动降级）──
        # 只对价格区间内的候选查询（filtered 跳过，省 API 调用）
        if use_analytics:
            to_enrich = [c for c in candidates if c.status in ("ok", "uncertain")]
            if to_enrich:
                from scripts.lib.ozon_seller_analytics import (
                    apply_analytics_to_candidate,
                    fetch_sales_analytics,
                )
                metrics_map = fetch_sales_analytics(
                    cdp, [c.ozon_product_id for c in to_enrich])
                for c in to_enrich:
                    apply_analytics_to_candidate(
                        c, metrics_map.get(c.ozon_product_id, {}))
            else:
                logger.info("无价格区间内的候选，跳过运营指标查询")

    # 全量落盘（含 error/uncertain/filtered，供表格与审计）
    _save_discovery_log(candidates)
    return candidates


def match_selected(
    candidates: list[ProductCandidate],
    cdp_url: str,
    fx_rate: float = DEFAULT_FX_RATE,
    min_margin_pct: float = DEFAULT_MIN_MARGIN_PCT,
    logistics_cny: float = DEFAULT_LOGISTICS_CNY,
    commission_rate: float = 0,
    progress_callback=None,
) -> list[ProductCandidate]:
    """Discover v2 阶段④：对选中候选批量 1688 识图 + 利润 + 蓝海评分。

    就地更新候选状态：matched / profitable / rejected / no_match。
    仅处理 status in (ok, uncertain) 的候选（error 跳过）。
    """
    from scripts.lib.config_store import get_store_profile

    store_profile = get_store_profile()
    if commission_rate <= 0:
        commission_rate = float(store_profile.get("commission_rate", 0) or 0)

    selected = [c for c in candidates if c.status in ("ok", "uncertain")]
    stats = {"matched": 0, "rejected": 0, "no_match": 0, "error": 0}
    # ⚠️ v0.14 E6: 批量 1688 识图复用同一 CDP 连接（旧代码每候选新建 CdpConnection+CdpTab）
    from scripts.lib.cdp_client import CdpConnection
    import contextlib
    with contextlib.closing(CdpConnection(cdp_url)) as shared_cdp:
        for i, candidate in enumerate(selected):
            try:
                match = _search_1688_source(
                    cdp_url, candidate.ozon_images, candidate.ozon_title, conn=shared_cdp)
                if match:
                    candidate.match_1688_url = match.get("url", "")
                    candidate.match_1688_title = match.get("title", "")
                    candidate.match_1688_price = float(match.get("price", 0))
                    candidate.match_1688_images = match.get("images", [])
                    candidate.status = "matched"

                    _calculate_profit(
                        candidate,
                        fx_rate=fx_rate,
                        logistics_cny=logistics_cny,
                        commission_rate=commission_rate,
                    )
                    candidate.blue_ocean_score = calculate_blue_ocean_score(candidate)

                    if candidate.profit_margin >= min_margin_pct:
                        candidate.status = "profitable"
                    else:
                        candidate.status = "rejected"
                        candidate.error = (f"margin too low "
                                           f"({candidate.profit_margin:.1f}% < {min_margin_pct}%)")
                else:
                    candidate.status = "no_match"
            except Exception as exc:
                candidate.status = "error"
                candidate.error = str(exc)
                logger.warning("1688 match failed for %s: %s",
                               candidate.ozon_product_id, exc)

        stats[candidate.status if candidate.status in stats else "error"] += 1

        if progress_callback:
            progress_callback(i + 1, len(selected), candidate)
        time.sleep(0.5)

    logger.info("1688 匹配统计: %s", stats)
    # 匹配结果落盘（collect_and_analyze 保存的是匹配前的全量数据，
    # 这里覆盖保存最终版本，含 1688 匹配/利润/蓝海评分）
    try:
        _save_discovery_log(candidates)
    except Exception as exc:
        logger.debug("保存匹配后日志失败: %s", exc)
    return candidates


# ---------------------------------------------------------------------------
# 挑选规则（阶段③ 自动筛选）
# ---------------------------------------------------------------------------

_SELECTION_FIELDS: dict[str, Any] = {
    "monthly_sales": lambda c: c.monthly_sales,
    "gmv": lambda c: c.monthly_revenue,
    "drr": lambda c: c.drr,
    "seller_count": lambda c: c.competing_sellers,
    "margin": lambda c: c.profit_margin,
    "price": lambda c: c.ozon_price,
    "create_days": lambda c: c.create_days,
    "sales_growth": lambda c: c.sales_growth,
    "rating": lambda c: c.rating,
}


def _check_rule(actual: Any, op: str, expected: float) -> bool:
    try:
        actual = float(actual or 0)
    except (TypeError, ValueError):
        return False
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    if op == "<":
        return actual < expected
    return actual == expected


def apply_selection_rules(candidates: list[ProductCandidate], rules: str) -> list[ProductCandidate]:
    """按规则字符串筛选候选。

    格式: "monthly_sales>=200,drr<=30,seller_count<=20"
    支持字段: monthly_sales/gmv/drr/seller_count/margin/price/create_days/sales_growth/rating
    比较符: >= / <= / > / < / =
    返回满足全部规则的候选；rules 为空返回原列表。
    """
    if not rules or not rules.strip():
        return candidates

    parsed = []
    for part in rules.split(","):
        part = part.strip()
        m = re.match(r"^([a-z_]+)\s*(>=|<=|>|<|=)\s*([\d.]+)$", part, re.IGNORECASE)
        if not m:
            raise ValueError(
                f"无法解析规则: {part!r}（格式: field>=100,field2<=50）")
        field_name, op, val = m.group(1).lower(), m.group(2), float(m.group(3))
        if field_name not in _SELECTION_FIELDS:
            raise ValueError(
                f"未知规则字段: {field_name}（支持: {', '.join(_SELECTION_FIELDS)}）")
        parsed.append((field_name, op, val))

    result = []
    for c in candidates:
        if c.status in ("error",):
            continue
        if all(_check_rule(_SELECTION_FIELDS[f](c), op, val) for f, op, val in parsed):
            result.append(c)
    return result


# ---------------------------------------------------------------------------
# Generic page discovery
# ---------------------------------------------------------------------------


def discover_from_url(cdp_url: str, url: str, max_products: int = 50) -> list[str]:
    """Discover product URLs from any Ozon page.

    Supports:
    - Highlight pages: https://www.ozon.ru/highlight/...
    - Search results: https://www.ozon.ru/search/?text=...
    - Category pages: https://www.ozon.ru/category/...
    - Brand pages: https://www.ozon.ru/brand/...
    - Sale pages: https://www.ozon.ru/sale/...

    Returns list of product URLs (deduplicated).
    """
    from scripts.lib.cdp_client import CdpConnection

    with CdpConnection(cdp_url) as cdp:
        tab = cdp.new_tab(url)
        try:
            time.sleep(6)

            # Scroll to load products
            prev_count = 0
            for _ in range(max_products // 10 + 5):
                tab.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                time.sleep(2)

                # Check if new products loaded
                count = tab.evaluate('document.querySelectorAll(".tile-root").length')
                if count == prev_count:
                    break  # no new products
                prev_count = count

                if count >= max_products:
                    break

            # Extract product URLs
            urls = tab.evaluate(r'''(() => {
                return [...new Set(
                    [...document.querySelectorAll('.tile-root a[href*="/product/"]')]
                        .map(a => a.href.split('?')[0])
                        .filter(h => h.match(/-\d{5,}\/?$/) || h.match(/\/product\/\d{5,}\/?$/))
                )];
            })()''')
        finally:
            tab.close()

    return (urls or [])[:max_products]


def discover_from_keyword(cdp_url: str, keyword: str, max_products: int = 50) -> list[str]:
    """Search Ozon by keyword and discover products.

    Constructs search URL and calls discover_from_url().
    """
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"https://www.ozon.ru/search/?text={encoded}"
    return discover_from_url(cdp_url, url, max_products)


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def export_to_csv(candidates: list[ProductCandidate], filepath: str) -> str:
    """Export candidates to CSV file.

    Columns: product_id, title, price_rub, category, brand,
    commission_fbp, commission_rfbs, monthly_sales, monthly_revenue,
    competing_sellers, min_competitor_price, weight_g, dimensions,
    match_1688_url, match_1688_price, profit_margin, blue_ocean_score, verdict
    """
    import csv
    from pathlib import Path

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        'product_id', 'title', 'price_rub', 'category', 'brand',
        'commission_fbp', 'commission_rfbs', 'monthly_sales', 'monthly_revenue',
        'sales_growth', 'drr', 'create_days', 'rating', 'review_count',
        'has_analytics',
        'competing_sellers', 'min_competitor_price', 'weight_g', 'dimensions',
        'match_1688_url', 'match_1688_price', 'profit_margin', 'blue_ocean_score', 'verdict'
    ]

    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for c in candidates:
            dims = c.dimensions_mm or {}
            writer.writerow({
                'product_id': c.ozon_product_id,
                'title': c.ozon_title,
                'price_rub': c.ozon_price,
                'category': getattr(c, 'category', ''),
                'brand': getattr(c, 'brand', ''),
                'commission_fbp': getattr(c, 'commission_fbp', 0),
                'commission_rfbs': getattr(c, 'commission_rfbs', 0),
                'monthly_sales': getattr(c, 'monthly_sales', 0),
                'monthly_revenue': getattr(c, 'monthly_revenue', 0),
                'sales_growth': getattr(c, 'sales_growth', 0),
                'drr': getattr(c, 'drr', 0),
                'create_days': getattr(c, 'create_days', 0),
                'rating': getattr(c, 'rating', 0),
                'review_count': getattr(c, 'review_count', 0),
                'has_analytics': getattr(c, 'has_analytics', False),
                'competing_sellers': c.competing_sellers,
                'min_competitor_price': c.min_competing_price,
                'weight_g': getattr(c, 'weight_g', 0),
                'dimensions': f"{dims.get('length',0)}x{dims.get('width',0)}x{dims.get('height',0)}",
                'match_1688_url': c.match_1688_url,
                'match_1688_price': c.match_1688_price,
                'profit_margin': round(c.profit_margin, 1),
                'blue_ocean_score': getattr(c, 'blue_ocean_score', 0),
                'verdict': c.status,
            })

    return str(path)


def export_to_json(candidates: list[ProductCandidate], filepath: str) -> str:
    """Export candidates to JSON file."""
    from pathlib import Path

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = [asdict(c) for c in candidates]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


from scripts.lib.utils import parse_price as _parse_price


# 俄语产品词 → 中文关键词（1688 标题相关性校验用，覆盖常见品类）
# badge 为空时俄语 Ozon 标题无法直接与中文 1688 标题重叠校验，
# 用该映射把标题中的俄语产品词翻译后做包含匹配。
_RU_ZH_PRODUCT_WORDS: dict[str, list[str]] = {
    # 宠物
    "шлейка": ["胸背", "牵引", "遛狗", "背带", "胸带"],
    "пояс": ["腰带", "背带", "尿不湿", "纸尿裤", "裤"],   # впитывающие пояса=吸收腰带(尿裤)
    "ошейник": ["项圈"],
    "поводок": ["牵引绳", "牵狗", "遛狗"],
    "памперс": ["尿不湿", "纸尿裤", "尿裤", "拉拉裤"],
    "подгузник": ["尿不湿", "纸尿裤"],
    "игрушк": ["玩具"],
    "корм": ["粮", "饲料", "食品"],
    "миска": ["碗", "食盆", "喂食"],
    "шапка": ["帽"],
    "одежд": ["衣服", "服装", "衣"],
    "костюм": ["衣服", "服装", "套装"],
    "ботинк": ["鞋", "靴"],
    "туфл": ["鞋"],
    "носок": ["袜"],
    "перчатк": ["手套"],
    "шарф": ["围巾"],
    "ремень": ["腰带", "皮带"],
    "когтеточк": ["猫抓", "抓板"],
    "домик": ["窝", "屋", "房子"],
    "переноск": ["航空箱", "包", "笼"],
    "лоток": ["猫砂盆", "便盆"],
    "наполнитель": ["猫砂", "垫料"],
    "амуниция": ["背带", "牵引"],
    "дрессировк": ["训练", "训犬"],
    # 家居日用
    "сумка": ["包", "挎包", "手袋"],
    "рюкзак": ["背包", "双肩包"],
    "чехол": ["壳", "套", "保护套"],
    "коврик": ["垫", "地垫"],
    "подушка": ["枕", "靠垫", "抱枕"],
    "одеяло": ["被子", "毯子"],
    "полотенц": ["毛巾"],
    "стакан": ["杯", "水杯"],
    "кружка": ["杯", "马克杯"],
    "тарелк": ["盘", "餐盘"],
    "ложк": ["勺"],
    "вилк": ["叉"],
    "нож": ["刀"],
    "кастрюл": ["锅", "汤锅"],
    "сковород": ["锅", "煎锅"],
    "вешалк": ["衣架", "挂钩"],
    "полк": ["置物架", "架子"],
    "корзин": ["收纳篮", "篮子"],
    "ламп": ["灯"],
    "зеркал": ["镜子"],
    "щетк": ["刷"],
    "расческ": ["梳"],
    "фен": ["吹风"],
    "пауэрбанк": ["充电宝"],
    "наушник": ["耳机"],
    "чехол-книжк": ["翻盖", "壳"],
    "зарядк": ["充电"],
    # 五金工具（v0.19：棘轮扳手误拒补充）
    "ключ": ["扳手", "钥匙"],
    "трещоточн": ["棘轮"],
    "шарнирн": ["活动头", "万向", "铰"],
    "комбинированн": ["两用", "梅花", "组合"],
    "гаечн": ["扳手"],
    "отвёртк": ["螺丝刀", "起子"],
    "отвертк": ["螺丝刀", "起子"],
    "молоток": ["锤"],
    "плоскогубц": ["钳", "尖嘴"],
    "пассатиж": ["钳"],
    "дрель": ["电钻", "钻"],
    "шуруповерт": ["电动螺丝刀", "起子机", "电钻"],
    "пил": ["锯"],
    "стамеск": ["凿"],
    # 健身器材（v0.19：甩脂机误拒补充）
    "виброплатформ": ["甩脂机", "抖抖机", "律动机"],
    "вибро": ["甩脂", "抖抖", "震动"],
    "похудени": ["减肥", "瘦身", "减脂"],
    "музык": ["音乐"],
    "фитнес": ["健身"],
    "тренажер": ["健身", "器械", "甩脂", "踏步"],
    # v0.22: 五金工具扩充（套筒/撬棍/水平仪/钳工）
    "головк": ["套筒", "批头"],
    "монтировк": ["撬棍", "撬棒"],
    "уровень": ["水平仪", "水平尺"],
    "лобзик": ["曲线锯", "线锯"],
    "ножниц": ["剪刀", "剪"],
    "тиск": ["台钳", "虎钳"],
    "напильник": ["锉"],
    "сверл": ["钻头", "钻"],
    "шланг": ["水管", "软管"],
    "насос": ["泵", "打气筒"],
    # v0.22: 电器/风扇/照明
    "вентилятор": ["风扇", "扇"],
    "фонар": ["手电", "灯"],
    "лампа": ["灯泡", "灯"],
    "розетк": ["插座", "排插"],
    "удлинитель": ["延长线", "插排"],
    "термокружк": ["保温杯", "杯"],
    "электрочайник": ["烧水壶", "电水壶"],
    "блендер": ["榨汁机", "搅拌机"],
    "пылесос": ["吸尘器"],
    # v0.22: 通用套装/材质词
    "набор": ["套装", "套", "组合"],
    "настольн": ["桌面", "台"],
    "портативн": ["便携", "迷你"],
    "складн": ["折叠"],
    "регулируем": ["可调", "调节"],
    "съемник": ["拆卸", "拉马", "提取器"],
    # v0.22: 玩具/户外
    "головоломк": ["拼图", "魔方", "智力"],
    "конструктор": ["积木", "拼装"],
    "мяч": ["球"],
    "палатк": ["帐篷"],
    "спальник": ["睡袋"],
    "коврик-пенк": ["防潮垫", "瑜伽垫"],
    "походн": ["户外", "露营", "登山"],
}


def _ru_zh_title_overlap(ozon_title: str, cn_title: str) -> float:
    """俄语标题 vs 中文标题相关性：命中产品词数 / 标题中出现的产品词数，多词命中加权。

    v0.22: 单词命中（words=1）rate 再高也只给 ~0.67 上限（避免偶然单映射高分），
    多词命中（≥3 个产品词）才接近 1.0——无徽章环境靠多词证据提准确率。
    """
    lower = ozon_title.lower()
    words = 0
    hits = 0
    for ru, zh_list in _RU_ZH_PRODUCT_WORDS.items():
        if ru in lower:
            words += 1
            for zh in zh_list:
                if zh in cn_title:
                    hits += 1
                    break
    if not words:
        return 0.0
    rate = hits / words
    # 词数加权：1 词 → ×0.70；2 词 → ×0.85；≥3 词 → ×1.0
    weight = min(1.0, 0.55 + words * 0.15)
    return rate * weight


def _badge_effectiveness(badge_str: str) -> float:
    """badge 匹配有效性 0-1（区别于 _get_badge_score 的原始数值分）：
    '全部符合'（matchBadgeFull）→ 1.0；'符合N/M个条件' → N/M；
    '匹配度xx%' → xx/100；'精准/较高' → 0.9/0.7。"""
    if not badge_str:
        return 0.0
    if "全部符合" in badge_str or "满足所有" in badge_str or "符合全部" in badge_str:
        return 1.0
    m = re.search(r"符合\s*(\d+)\s*/\s*(\d+)\s*个条件", badge_str)
    if m:
        n, total = int(m.group(1)), int(m.group(2))
        return n / total if total > 0 else 0.0
    m = re.search(r"(匹配度|相似度)[\s:：]*(\d+)", badge_str, re.IGNORECASE)
    if m:
        return min(int(m.group(2)), 100) / 100.0
    for k, v in {"精准": 0.9, "较高": 0.7, "高": 0.6}.items():
        if k in badge_str:
            return v
    return 0.0


def _pick_best_match(results: list[dict[str, Any]], ozon_title: str) -> dict[str, Any] | None:
    """从图搜结果中挑选最相关的匹配。

    1688 图搜列表第一张卡片往往是不相关商品（badge 标"符合 0/N 个条件"），
    直接取 results[0] 会拿到错误匹配（实测 ¥1-2 错误价格根因）。

    排序策略（分 = badge 有效性×40 + 标题相关性×30）：
    1. badge "符合 0/N"（明确 0 匹配）→ 跳过
    2. badge 匹配有效性为主排序（1688 官方匹配度最可信）
    3. 标题相关性作辅排序：中文标题走关键词重叠；俄语标题走 RU→ZH
       产品词映射包含匹配（badge 为空时唯一可用的相关性信号）
    4. 相关性护栏：badge 无明确匹配（<0.6）且标题相关性弱（conf < 0.5）
       → 拒绝匹配（宁缺毋滥，不把不相关商品当货源）
    """
    from scripts.lib.ozon_image_search import _get_badge_score

    is_ru_title = bool(re.search(r"[а-яёА-ЯЁ]", ozon_title or ""))

    scored: list[tuple[float, dict[str, Any]]] = []
    for r in results:
        badge_str = r.get("badge", "") or ""
        # 跳过明确 0 匹配的（"符合0/N个条件"）
        if re.search(r"符合\s*0\s*/\s*\d+\s*个条件", badge_str):
            continue
        # 无价格候选跳过：价格是利润计算核心，拿不到价格再高匹配也没用
        # （实测 badge 2/3 的垃圾袋收纳袋价格区未解析 → ¥0，跳过选有价格的）
        try:
            if not (float(r.get("price", 0) or 0) > 0):
                continue
        except (TypeError, ValueError):
            continue
        badge_eff = _badge_effectiveness(badge_str)
        # 标题相关性
        conf = 0.0
        r_title = r.get("title", "") or ""
        if r_title and re.search(r"[\u4e00-\u9fff]", r_title):
            if is_ru_title:
                conf = _ru_zh_title_overlap(ozon_title, r_title)
            else:
                v = verify_1688_match(ozon_title, r_title)
                conf = v.get("confidence", 0.0)
        scored.append((badge_eff * 40 + conf * 30, r))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    # 相关性护栏：badge 完全无匹配分（含空/0匹配）且标题相关性弱 → 拒绝
    # （宁缺毋滥，实测"花插 ¥1"当遛狗带货源的错误由此拦截）。
    # ⚠️ v0.14 E5 增强: badge 轻微匹配（<0.5，如"符合1/3"）但标题相关性极弱（conf < 0.3）
    # → 也拒绝。1688 图搜偶发把不同产品误标轻微匹配（实测"水龙头"被标符合1/3），
    # 仅凭 badge 放行会组装错产品；标题重叠是更可靠的证据。
    badge_eff_of_best = _badge_effectiveness(best.get("badge", "") or "")
    _conf_of_best = 0.0
    _bt = best.get("title", "") or ""
    if _bt and re.search(r"[\u4e00-\u9fff]", _bt):
        _conf_of_best = _ru_zh_title_overlap(ozon_title, _bt) if is_ru_title else verify_1688_match(ozon_title, _bt).get("confidence", 0.0)

    # ✅ v0.19: 1688 官方"全部符合"（matchBadgeFull）直接放行——最强信号，
    # 不再被标题相关性否决（修复棘轮扳手/创可贴卷误拒）
    if badge_eff_of_best >= 1.0:
        logger.info("图搜徽标全部符合（matchBadgeFull）直接放行: %s", best.get("title", "")[:40])
        return best

    # ✅ v0.19/v0.22: 无徽标降级（未登录 1688 / 页面未渲染徽标）：整页无任何有效徽标时，
    # 按标题相关性取最优，conf ≥ 0.3 即放行（用户确认可接受牺牲一点准确度）
    any_badge = any(
        _badge_effectiveness(r.get("badge", "") or "") > 0 for r in results
    )
    if not any_badge:
        if _conf_of_best >= 0.3:
            logger.info("图搜无徽标（badge-less），标题相关性 conf=%.2f 放行: %s",
                        _conf_of_best, best.get("title", "")[:40])
            return best
        logger.debug("图搜无徽标且标题相关性弱（conf=%.2f），拒绝: %s",
                     _conf_of_best, best.get("title", "")[:40])
        return None

    if badge_eff_of_best <= 0 and best_score < 15:
        logger.debug("图搜候选相关性过低（badge=%s, score=%.1f），拒绝匹配: %s",
                     best.get("badge", ""), best_score, best.get("title", "")[:40])
        return None
    if badge_eff_of_best < 0.5 and _conf_of_best < 0.3:
        logger.warning("图搜候选 badge 轻微匹配但标题相关性弱（badge=%s, conf=%.2f），拒绝: %s",
                       best.get("badge", ""), _conf_of_best, best.get("title", "")[:40])
        return None
    return best


def _search_1688_source(
    cdp_url: str,
    images: list[str],
    title: str,
    max_retries: int = 1,
    conn=None,
) -> dict[str, Any] | None:
    """Search 1688 for a matching source product.

    Strategy:
    1. Try CDP-based image search (best quality, uses YOLO crop regions)
    2. Fall back to AK API image search
    3. Fall back to AK API keyword search using the Ozon title

    Retry 机制（偶发失败容错）：
    - CDP 图搜空结果（页面加载失败/超时等偶发）→ 重试 max_retries 次
      （间隔 3s），仍空才降级 AK
    - CDP 图搜有结果但被相关性护栏拒绝 → 不重试（结果确定），直接降级
    - AK 图搜/关键词同样重试（API 快，成本低）

    Returns dict with: url, title, price, images  (or None if no match).
    """
    # --- Strategy 1: CDP image search ---
    if images:
        for attempt in range(max_retries + 1):
            try:
                from scripts.lib.ozon_image_search import search_by_image_cdp

                logger.debug("1688 CDP image search (attempt %d/%d) with: %s",
                             attempt + 1, max_retries + 1, images[0][:80])
                results = search_by_image_cdp(images[0], cdp_url=cdp_url, conn=conn)
                best = _pick_best_match(results, title) if results else None
                if best:
                    price = best.get("price", 0)
                    if isinstance(price, str):
                        price = _parse_price(price)
                    return {
                        "url": best.get("detail_url", "")
                            or f"https://detail.1688.com/offer/{best.get('id', '')}.html"
                            if best.get("id") else "",
                        "title": best.get("title", ""),
                        "price": float(price) if price else 0,
                        "images": [best.get("image", "")] if best.get("image") else [],
                    }
                if not results and attempt < max_retries:
                    logger.info("CDP 图搜空结果（偶发），重试 %d/%d",
                                attempt + 1, max_retries)
                    time.sleep(3)
                    continue
                # 有结果但被护栏拒绝 → 结果确定，不重试，降级 AK
                break
            except Exception as exc:
                logger.debug("CDP image search failed (attempt %d): %s",
                             attempt + 1, exc)
                if attempt < max_retries:
                    time.sleep(3)
                    continue
                break

    # --- Strategy 2: AK API image search ---
    if images:
        for attempt in range(2):
            try:
                from scripts.lib.ak_1688_client import search_by_image

                logger.debug("1688 AK image search (attempt %d/2) with: %s",
                             attempt + 1, images[0][:80])
                results = search_by_image(image_url=images[0], page_size=5, score_level="high")
                # ⚠️ AK 结果同样不能无条件取 results[0]（实测第一条是"活体羊驼
                # ¥2000"，第三条才是相关商品），与 CDP 路径共用 _pick_best_match
                best = _pick_best_match(results, title) if results else None
                if best:
                    return {
                        "url": best.get("detail_url", ""),
                        "title": best.get("title", ""),
                        "price": float(best.get("price", 0) or 0),
                        "images": [best.get("image_url", "")] if best.get("image_url") else [],
                    }
                if not results and attempt < 1:
                    logger.info("AK 图搜空结果，重试 %d/2", attempt + 1)
                    time.sleep(1)
                    continue
                break
            except Exception as exc:
                logger.debug("AK image search failed (attempt %d): %s", attempt + 1, exc)
                if attempt < 1:
                    time.sleep(1)
                    continue
                break

    # --- Strategy 3: AK API keyword search (fallback) ---
    if title:
        for attempt in range(2):
            try:
                from scripts.lib.ak_1688_client import search_products

                # Simplify title: remove brand, keep core keywords
                keywords = _extract_search_keywords(title)
                if keywords:
                    logger.debug("1688 keyword search (attempt %d/2): %s",
                                 attempt + 1, keywords)
                    results = search_products(keywords, page_size=5)
                    if results:
                        best = results[0]
                        return {
                            "url": best.get("detail_url", ""),
                            "title": best.get("title", ""),
                            "price": float(best.get("price", 0) or 0),
                            "images": [best.get("image_url", "")] if best.get("image_url") else [],
                        }
                if attempt < 1:
                    time.sleep(1)
                    continue
                break
            except Exception as exc:
                logger.debug("AK keyword search failed (attempt %d): %s", attempt + 1, exc)
                if attempt < 1:
                    time.sleep(1)
                    continue
                break

    return None


def _extract_search_keywords(title: str) -> str:
    """Extract core search keywords from an Ozon product title.

    Removes noise (brand names, marketing phrases, numbers-only tokens)
    and returns a simplified Chinese/English keyword string suitable for
    1688 search.
    """
    # Common noise words to strip from Ozon titles
    noise = [
        "Ozon", "ozon", "Premium", "premium", "Хит", "хит",
        "Скидка", "скидка", "Акция", "акция", "Бесплатная доставка",
        "Россия", "россия", "Склад", "склад",
    ]
    text = str(title)
    for word in noise:
        text = text.replace(word, "")

    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # If title has Chinese characters, use them directly (likely from Chinese seller)
    cn_chars = re.findall(r"[\u4e00-\u9fff]+", text)
    if cn_chars and len("".join(cn_chars)) >= 4:
        return " ".join(cn_chars[:5])

    # Otherwise, take the first 5 significant words
    words = [w for w in text.split() if len(w) >= 3]
    return " ".join(words[:6])


def _calculate_profit(
    candidate: ProductCandidate,
    fx_rate: float = DEFAULT_FX_RATE,
    logistics_cny: float = DEFAULT_LOGISTICS_CNY,
    commission_rate: float = 0,
) -> None:
    """Calculate profit margin for a candidate.

    Updates candidate fields in-place:
      estimated_logistics_cny, estimated_commission,
      estimated_profit_cny, profit_margin

    佣金优先级：commission_rate（小数）> 真实 commission_fbp/rfbs（百分数）
    > DEFAULT_COMMISSION_PCT。物流：有真实重量按 kg 估算，否则用固定值。
    """
    if not candidate.match_1688_price or not candidate.ozon_price:
        return

    effective_commission = commission_rate
    if effective_commission <= 0:
        real_comm = (candidate.commission_fbp or candidate.commission_rfbs or 0)
        effective_commission = real_comm / 100 if real_comm > 0 else DEFAULT_COMMISSION_PCT

    cost_cny = candidate.match_1688_price
    revenue_cny = candidate.ozon_price * fx_rate

    if revenue_cny <= 0:
        return

    # 物流估算：有真实重量按重量（40 CNY/kg，保底 8），否则固定值
    if candidate.weight_g > 0:
        candidate.estimated_logistics_cny = max(
            8.0, candidate.weight_g / 1000.0 * LOGISTICS_PER_KG_CNY)
    else:
        candidate.estimated_logistics_cny = logistics_cny

    # Commission
    candidate.estimated_commission = revenue_cny * effective_commission

    total_cost = cost_cny + candidate.estimated_logistics_cny + candidate.estimated_commission
    candidate.estimated_profit_cny = revenue_cny - total_cost
    candidate.profit_margin = (candidate.estimated_profit_cny / revenue_cny) * 100.0


def calculate_blue_ocean_score(candidate: ProductCandidate) -> int:
    """Calculate blue ocean score 0-100.

    Factors（v3 增强，2026-08-01）:
    - competing_sellers (weight 30): <5 → 30, <10 → 27, <50 → 18, <200 → 9, >200 → 3
    - profit_margin (weight 30): >40% → 30, >30% → 25.5, >20% → 21, >10% → 12, <10% → 4.5
    - monthly_sales (weight 10 有 analytics / 20 无): 1-50 → 10/16 (niche),
      50-200 → 8/12, 200-1000 → 5/8, >1000 → 2/4, 0 → 10/10 (unknown)
    - sales_growth (weight 5, 需 analytics): >30% → 5, 10-30% → 4, 0-10% → 2, <0 → 0
    - drr 广告占比 (weight 5, 需 analytics): <10% → 5, 10-25% → 3, 25-50% → 1, >50% → 0
    - price_range (weight 10): 500-5000 RUB → 10, 100-500 → 7, >5000 → 5, <100 → 3
    - commission_rate (weight 10): <10% → 10, <15% → 7, <20% → 4, >20% → 2

    无 analytics（seller.ozon.ru 未登录降级）时增长/广告因子为 0，
    monthly_sales 权重回 20——两套评分上限一致（100），可比。
    """
    score = 0.0
    has_analytics = bool(getattr(candidate, 'has_analytics', False))

    # Competing sellers (30%)
    sellers = candidate.competing_sellers
    if sellers < 5: score += 30
    elif sellers < 10: score += 27
    elif sellers < 50: score += 18
    elif sellers < 200: score += 9
    else: score += 3

    # Profit margin (30%)
    margin = candidate.profit_margin
    if margin > 40: score += 30
    elif margin > 30: score += 25.5
    elif margin > 20: score += 21
    elif margin > 10: score += 12
    else: score += 4.5

    # Monthly sales (10% 有 analytics / 20% 无)
    sales = getattr(candidate, 'monthly_sales', 0)
    if has_analytics:
        if 1 <= sales <= 50: score += 10
        elif 50 < sales <= 200: score += 8
        elif 200 < sales <= 1000: score += 5
        elif sales > 1000: score += 2
        else: score += 10  # 接口通但无销量数据 → 未知
    else:
        if 1 <= sales <= 50: score += 16
        elif 50 < sales <= 200: score += 12
        elif 200 < sales <= 1000: score += 8
        elif sales > 1000: score += 4
        else: score += 10  # unknown

    # Sales growth (5%, 需 analytics) — 需求上升信号
    if has_analytics:
        growth = float(getattr(candidate, 'sales_growth', 0) or 0)
        if growth > 30: score += 5
        elif growth > 10: score += 4
        elif growth >= 0: score += 2
        # growth < 0 → 0 分（需求下滑）

    # drr 广告占比 (5%, 需 analytics) — 低广告占比 = 自然流量/低竞争
    if has_analytics:
        drr = float(getattr(candidate, 'drr', 0) or 0)
        if drr < 10: score += 5
        elif drr < 25: score += 3
        elif drr < 50: score += 1
        # drr >= 50 → 0 分（重度依赖广告）

    # Price range (10%)
    price = candidate.ozon_price
    if 500 <= price <= 5000: score += 10
    elif 100 <= price < 500: score += 7
    elif price > 5000: score += 5
    else: score += 3

    # Commission rate (10%)
    comm = getattr(candidate, 'commission_fbp', 0) or getattr(candidate, 'commission_rfbs', 0)
    if comm < 10: score += 10
    elif comm < 15: score += 7
    elif comm < 20: score += 4
    else: score += 2

    return min(100, max(0, int(round(score))))


def verify_1688_match(ozon_title: str, match_1688_title: str, match_1688_url: str = "") -> dict:
    """Verify that the 1688 match is a similar product.

    Uses keyword extraction + substring matching for Chinese text.
    Returns: {"verified": bool, "confidence": float, "reason": str}
    """
    if not match_1688_title:
        return {"verified": False, "confidence": 0, "reason": "no match found"}

    def extract_keywords(text):
        """Extract keywords: split by whitespace/punctuation, also extract Chinese 2-grams."""
        noise = {'的', '了', '是', '在', '有', '和', '与', '及', '或', '等',
                 'for', 'the', 'a', 'an', 'and', 'or', 'is', 'in', 'on', 'at',
                 '男', '女', '款', '新', '大', '小', '中', '件', '个', '只', '条'}
        words = set()
        # Split by whitespace/punctuation
        for w in re.split(r'[\s\-_,./\\()（）]+', text.lower()):
            w = w.strip()
            if len(w) > 1 and w not in noise:
                words.add(w)
        # Extract Chinese 2-grams for substring matching
        cn_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in cn_chars:
            for i in range(len(seg) - 1):
                bigram = seg[i:i+2]
                if bigram not in noise:
                    words.add(bigram)
        return words

    ozon_kw = extract_keywords(ozon_title)
    match_kw = extract_keywords(match_1688_title)

    if not ozon_kw or not match_kw:
        return {"verified": False, "confidence": 0, "reason": "empty keywords"}

    # Direct keyword overlap
    overlap = ozon_kw & match_kw

    # Substring matching: check if any ozon keyword contains a match keyword or vice versa
    substring_hits = set()
    for ok in ozon_kw:
        for mk in match_kw:
            if len(ok) >= 2 and len(mk) >= 2 and (ok in mk or mk in ok):
                substring_hits.add(f"{ok}~{mk}")

    total_matches = len(overlap) + len(substring_hits)
    confidence = min(1.0, total_matches / max(1, min(len(ozon_kw), len(match_kw))))

    if confidence >= 0.3:
        reasons = list(overlap)[:3] + list(substring_hits)[:3]
        return {"verified": True, "confidence": round(confidence, 2),
                "reason": f"keywords overlap: {', '.join(reasons[:5])}"}
    else:
        return {"verified": False, "confidence": round(confidence, 2),
                "reason": f"low overlap ({total_matches}/{min(len(ozon_kw), len(match_kw))})"}


def _save_discovery_log(candidates: list[ProductCandidate]) -> Path | None:
    """Save discovery results to a timestamped JSON cache file.

    Returns the path to the saved file, or None on failure.
    """
    if not candidates:
        return None

    try:
        DISCOVERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = DISCOVERY_CACHE_DIR / f"discovery_{ts}.json"

        data = []
        for c in candidates:
            entry = asdict(c)
            data.append(entry)

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Discovery log saved: %s (%d products)", path, len(data))
        return path
    except Exception as exc:
        logger.error("Failed to save discovery log: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Convenience: load latest discovery cache
# ---------------------------------------------------------------------------


def load_latest_discovery() -> list[dict[str, Any]]:
    """Load the most recent discovery cache file.

    Returns list of product dicts, or empty list if no cache exists.
    """
    if not DISCOVERY_CACHE_DIR.exists():
        return []

    files = sorted(DISCOVERY_CACHE_DIR.glob("discovery_*.json"), reverse=True)
    if not files:
        return []

    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load discovery cache %s: %s", files[0], exc)
        return []


def load_discovery_by_date(date_str: str) -> list[dict[str, Any]]:
    """Load discovery cache for a specific date (YYYYMMDD format).

    Returns list of product dicts from the first matching file.
    """
    if not DISCOVERY_CACHE_DIR.exists():
        return []

    pattern = f"discovery_{date_str}*.json"
    files = sorted(DISCOVERY_CACHE_DIR.glob(pattern), reverse=True)
    if not files:
        return []

    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load discovery cache %s: %s", files[0], exc)
        return []

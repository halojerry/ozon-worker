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
_COLLECT_URLS_JS = r'''(() => {
    const links = document.querySelectorAll('__ROOT_SEL__ a[href*="/product/"]');
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

        # 2. 逐屏滚动触发懒加载
        try:
            tab.evaluate("window.scrollBy(0, window.innerHeight * 0.85)")
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
        candidate.ozon_price = _parse_price(info.get("price", ""))
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
    progress_callback=None,
) -> list[ProductCandidate]:
    """Discover v2 阶段①+②：采集 + 全量数据 + seller.ozon.ru 运营指标。

    - url 优先；否则 keyword 构造真实搜索页；否则中国站 highlight 页
    - 返回全部候选（status: ok/uncertain/error），**不做 1688 匹配**（阶段④）
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

            # 关键词相关性校验（仅对含中文的标题有效，俄语标题无法判断）
            if (keyword and candidate.status == "ok"
                    and re.search(r"[\u4e00-\u9fff]", candidate.ozon_title)
                    and keyword.strip() not in candidate.ozon_title):
                candidate.status = "uncertain"
                candidate.error = "标题不含关键词，可能夹带推荐/广告商品"

            candidates.append(candidate)
            if progress_callback:
                progress_callback(i + 1, len(pids), candidate)
            time.sleep(0.5)

        # ── 阶段②b 运营指标（借道 seller.ozon.ru，失败自动降级）──
        if use_analytics and candidates:
            from scripts.lib.ozon_seller_analytics import (
                apply_analytics_to_candidate,
                fetch_sales_analytics,
            )
            metrics_map = fetch_sales_analytics(
                cdp, [c.ozon_product_id for c in candidates])
            for c in candidates:
                apply_analytics_to_candidate(
                    c, metrics_map.get(c.ozon_product_id, {}))

    # 全量落盘（含 error/uncertain，供表格与审计）
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
    for i, candidate in enumerate(selected):
        try:
            match = _search_1688_source(
                cdp_url, candidate.ozon_images, candidate.ozon_title)
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

        if progress_callback:
            progress_callback(i + 1, len(selected), candidate)
        time.sleep(0.5)

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


def _search_1688_source(
    cdp_url: str,
    images: list[str],
    title: str,
) -> dict[str, Any] | None:
    """Search 1688 for a matching source product.

    Strategy:
    1. Try CDP-based image search (best quality, uses YOLO crop regions)
    2. Fall back to AK API image search
    3. Fall back to AK API keyword search using the Ozon title

    Returns dict with: url, title, price, images  (or None if no match).
    """
    # --- Strategy 1: CDP image search ---
    if images:
        try:
            from scripts.lib.ozon_image_search import search_by_image_cdp

            logger.debug("1688 CDP image search with: %s", images[0][:80])
            results = search_by_image_cdp(images[0], cdp_url=cdp_url)
            if results:
                best = results[0]
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
        except Exception as exc:
            logger.debug("CDP image search failed: %s", exc)

    # --- Strategy 2: AK API image search ---
    if images:
        try:
            from scripts.lib.ak_1688_client import search_by_image

            logger.debug("1688 AK image search with: %s", images[0][:80])
            results = search_by_image(image_url=images[0], page_size=5, score_level="high")
            if results:
                best = results[0]
                return {
                    "url": best.get("detail_url", ""),
                    "title": best.get("title", ""),
                    "price": float(best.get("price", 0) or 0),
                    "images": [best.get("image_url", "")] if best.get("image_url") else [],
                }
        except Exception as exc:
            logger.debug("AK image search failed: %s", exc)

    # --- Strategy 3: AK API keyword search (fallback) ---
    if title:
        try:
            from scripts.lib.ak_1688_client import search_products

            # Simplify title: remove brand, keep core keywords
            keywords = _extract_search_keywords(title)
            if keywords:
                logger.debug("1688 keyword search: %s", keywords)
                results = search_products(keywords, page_size=5)
                if results:
                    best = results[0]
                    return {
                        "url": best.get("detail_url", ""),
                        "title": best.get("title", ""),
                        "price": float(best.get("price", 0) or 0),
                        "images": [best.get("image_url", "")] if best.get("image_url") else [],
                    }
        except Exception as exc:
            logger.debug("AK keyword search failed: %s", exc)

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

    Factors:
    - competing_sellers (weight 30): <5 → 100, <10 → 90, <50 → 60, <200 → 30, >200 → 10
    - profit_margin (weight 30): >40% → 100, >30% → 85, >20% → 70, >10% → 40, <10% → 15
    - monthly_sales (weight 20): 1-50 → 80 (niche), 50-200 → 60 (growing), 200-1000 → 40 (competitive), >1000 → 20 (saturated), 0 → 50 (unknown)
    - price_range (weight 10): 500-5000 RUB → 100 (sweet spot), 100-500 → 70, >5000 → 50, <100 → 30
    - commission_rate (weight 10): <10% → 100, <15% → 70, <20% → 40, >20% → 20
    """
    score = 0.0

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

    # Monthly sales (20%)
    sales = getattr(candidate, 'monthly_sales', 0)
    if 1 <= sales <= 50: score += 16
    elif 50 < sales <= 200: score += 12
    elif 200 < sales <= 1000: score += 8
    elif sales > 1000: score += 4
    else: score += 10  # unknown

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

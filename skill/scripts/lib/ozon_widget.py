"""Ozon Widget API client -- product info + competing sellers.

Uses Ozon's public /api/entrypoint-api.bx/page/json/v2 endpoint
(no authentication required, works via CDP browser context).

Based on research from the "上品帮" browser extension approach:
  - Widget API returns structured JSON for any product page
  - widgetStates keys map to named UI widgets (webProductHeading, webPrice, etc.)
  - Parsing is done client-side from the JSON blobs
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:  # 仅注解用（运行时延迟导入，规避 Chrome 未装环境硬依赖）
    from scripts.lib.cdp_client import CdpConnection, CdpTab

logger = logging.getLogger(__name__)

OZON_BASE = "https://www.ozon.ru"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_product_id(url: str) -> str:
    """Extract numeric product ID from an Ozon URL.

    Handles formats like:
      /product/slug-123456789/
      /product/123456789/
      https://www.ozon.ru/product/slug-123456789/
      /product/slug-123456789/?query=params
    """
    clean = str(url).split("?")[0].split("#")[0]  # strip query and hash
    # Try slug-123456789 pattern first (most common Ozon URL format)
    m = re.search(r"-(\d{5,})/?$", clean)
    if m:
        return m.group(1)
    # Fallback: /product/123456789/ (bare numeric ID)
    m = re.search(r"/(\d{5,})/?$", clean)
    return m.group(1) if m else ""


def _is_geo_redirect(url: str) -> bool:
    """Detect Ozon geo-redirect (CN IP → search page): from_global=true / /search/."""
    return bool(url) and ("from_global=true" in url or "/search/" in url)


def _apply_geo_redirect_retry(tab: CdpTab, target_url: str) -> None:
    """Product 页导航后检测地理重定向，用 ?lang=ru&country=RU 重试一次。

    Ozon 对中国区 IP 把 /product/{id} 302 到搜索页（from_global=true），
    页面无 JSON-LD 产品数据、widget API 返回搜索页状态 → CDP 抓不到数据。
    """
    if not target_url or "/product/" not in target_url:
        return
    try:
        current = str(tab.url or "")
    except Exception:
        current = ""
    if not _is_geo_redirect(current):
        return
    product_id = extract_product_id(target_url)
    if not product_id:
        return
    logger.warning(
        "Ozon 地理重定向到搜索页（%s）→ 用 ?lang=ru&country=RU 重试 product %s",
        current, product_id,
    )
    retry_url = f"{OZON_BASE}/product/{product_id}/?lang=ru&country=RU"
    try:
        tab.navigate(retry_url, timeout=25)
        time.sleep(3)
    except Exception:
        pass


def _ensure_ozon_tab(cdp: CdpConnection, target_url: str = "",
                     force_new_tab: bool = False) -> CdpTab:
    """Find an existing ozon.ru tab or create a new one, navigated to target_url.

    Reuses an existing tab to preserve cookies/session state (avoid DataDome),
    then navigates it to target_url — Ozon API 按页面会话校验，在无关页面
    （如用户打开的别的商品页）上 fetch 会被 DataDome 拦。
    ⚠️ v0.31: find_tab 可能返回刚被 fetch_seller_products 关闭的 stale tab
    （Chrome 延迟清理 /json 列表），复用前验证存活，失败则新建。

    force_new_tab=True（P2）: 跳过 find_tab，直接 new_tab 开全新 tab。用于
    多线程并发（discover 每线程独立连接）——find_tab 会命中用户第一个
    ozon.ru tab，并发 worker 抢同一 tab 是 v0.31.1 教训；每 worker 开自己的
    tab，连接关闭时一并收走（new_tab 的 tab 归本连接所有，无需 release）。
    """
    if not force_new_tab:
        tab = cdp.find_tab("ozon.ru")
        if tab is not None:
            try:
                tab.evaluate("1", timeout=5)
            except Exception:
                tab = None
            else:
                # PR-4: 命中用户已有 tab → 立即 release，防止 conn.close() 远程关闭用户标签页
                cdp.release(tab)
                tab.set_bypass_csp()  # CSP 剥除（v4.2）：为页面上下文注入 fetch 铺路（批 6 variant 链）
                if target_url:
                    try:
                        tab.navigate(target_url, timeout=25)
                        time.sleep(3)
                        _apply_geo_redirect_retry(tab, target_url)
                    except Exception:
                        pass
                return tab
    if target_url:
        tab = cdp.new_tab(target_url)
        tab.set_bypass_csp()  # CSP 剥除（v4.2）：新建路径同样剥除
        _apply_geo_redirect_retry(tab, target_url)
        return tab
    tab = cdp.new_tab(f"{OZON_BASE}/")
    tab.set_bypass_csp()  # CSP 剥除（v4.2）：默认路径
    return tab


def _safe_json_parse(text: str) -> dict[str, Any]:
    """Parse JSON string, returning empty dict on failure."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def _normalize_price_to_rub(result: dict[str, Any]) -> dict[str, Any]:
    """把 widget 返回的价格统一归一化为 RUB（唯一真源，v0.36）。

    webPrice.currency.code 为 CNY 时按 CNY_TO_RUB 汇率换算 price/cardPrice/
    originalPrice；RUB 或未知货币原样 parse_price。所有价格归一化为数字串，
    result 恒带 currency="RUB"。
    """
    from scripts.lib.utils import normalize_ozon_price

    currency_code = str(result.get("currency") or "").strip().upper()
    for field in ("price", "cardPrice", "originalPrice"):
        if result.get(field):
            result[field] = str(normalize_ozon_price(str(result[field]), currency_code))
    result["currency"] = "RUB"
    return result


def _find_widget(widget_states: dict[str, str], substring: str) -> dict[str, Any]:
    """Find and parse the first widgetStates entry whose key contains *substring*."""
    for key, value in widget_states.items():
        if substring in key:
            return _safe_json_parse(value)
    return {}


def extract_layout_tracking_info(
    widget_states: dict[str, str],
) -> dict[str, Any] | None:
    """递归扫描 widgetStates 找 ``layoutTrackingInfo``（goldminer 同款）。

    页面结构化类目真值：不猜 widget key，逐 widget 值（JSON 字符串）parse 后
    深度优先走 dict/list 找 key ``layoutTrackingInfo``，返回首个命中的白名单
    三键 ``{categoryId, category_path, breadcrumbs}``（列表缺失容忍为空，
    categoryId 按载荷原样透传）。任一处都找不到 → None（调用方省略键）。
    """
    def _walk(node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            info = node.get("layoutTrackingInfo")
            if isinstance(info, dict):
                return {
                    "categoryId": info.get("categoryId"),
                    "category_path": info.get("categoryPath") or [],
                    "breadcrumbs": info.get("breadcrumbs") or [],
                }
            for value in node.values():
                found = _walk(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _walk(item)
                if found is not None:
                    return found
        return None

    for value in (widget_states or {}).values():
        parsed = _safe_json_parse(value) if isinstance(value, str) else value
        found = _walk(parsed)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# JS snippets evaluated via CDP
# ---------------------------------------------------------------------------

_FETCH_PRODUCT_JS = r'''(() => {
    return new Promise(async (resolve) => {
        try {
            const origin = window.location.origin;
            const url = origin + '/api/entrypoint-api.bx/page/json/v2?url='
                + encodeURIComponent('/product/__PRODUCT_ID__');
            const resp = await fetch(url, {
                method: 'get',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await resp.json();
            const ws = data.widgetStates || {};

            const result = {};

            // Title from webProductHeading
            const headingKey = Object.keys(ws).find(k => k.includes('webProductHeading'));
            if (headingKey) {
                try {
                    const h = JSON.parse(ws[headingKey]);
                    result.title = h.title || '';
                } catch(e) {}
            }

            // Price from webPrice
            // ⚠️ 必须匹配 "webPrice-" 前缀：k.includes('webPrice') 会先命中
            // webPricePerStars（Ozon 金融广告 widget，无 price 字段）导致价格恒空
            const priceKey = Object.keys(ws).find(k => k.includes('webPrice-'));
            if (priceKey) {
                try {
                    const p = JSON.parse(ws[priceKey]);
                    result.price = p.price || '';
                    result.cardPrice = (p.cardPrice && p.cardPrice.price) || '';
                    result.originalPrice = p.originalPrice || '';
                    // ✅ v0.36 货币识别：webPrice.currency.code（RUB/CNY/...），
                    // Python 侧据此归一化为 RUB（唯一真源）
                    result.currency = (p.currency && p.currency.code) || '';
                } catch(e) {}
            }

            // Images from webGallery
            const galleryKey = Object.keys(ws).find(k => k.includes('webGallery'));
            if (galleryKey) {
                try {
                    const g = JSON.parse(ws[galleryKey]);
                    result.images = (g.images || []).map(i => i.src || '').filter(Boolean);
                    result.primaryImage = g.cover || result.images[0] || '';
                } catch(e) {}
            }

            // Description from webCharacteristics or webDescription
            const descKey = Object.keys(ws).find(k =>
                k.includes('webCharacteristics') || k.includes('webDescription'));
            if (descKey) {
                try {
                    const d = JSON.parse(ws[descKey]);
                    result.description = d.description || d.text || '';
                    if (d.characteristics) {
                        result.characteristics = d.characteristics;
                    }
                } catch(e) {}
            }

            // SKU variants from webAspects
            const aspectKey = Object.keys(ws).find(k => k.includes('webAspects'));
            if (aspectKey) {
                try {
                    const a = JSON.parse(ws[aspectKey]);
                    result.aspects = a.aspects || a.variants || [];
                } catch(e) {}
            }

            // Brand from webBrand
            const brandKey = Object.keys(ws).find(k => k.includes('webBrand'));
            if (brandKey) {
                try {
                    const b = JSON.parse(ws[brandKey]);
                    result.brand = b.brand || b.title || '';
                } catch(e) {}
            }

            // Rating & reviews from webReviewProductScore（评分 widget 的真实 key）
            const revKey = Object.keys(ws).find(k => k.includes('webReviewProductScore'));
            if (revKey) {
                try {
                    const r = JSON.parse(ws[revKey]);
                    result.rating = r.score || r.totalScore || 0;
                    result.reviewCount = r.reviewsCount || 0;
                } catch(e) {}
            }

            // ✅ v0.71 类目真值：breadCrumbs widget（与 ozon_scraper 同款解析）——
            // discover 采集阶段即带竞品页面类目路径，不再单纯依赖 what_to_sell 登录态
            const crumbKey = Object.keys(ws).find(k => k.includes('breadCrumbs'));
            if (crumbKey) {
                try {
                    const parsedCrumbs = JSON.parse(ws[crumbKey]);
                    result.breadcrumbs = (parsedCrumbs.breadcrumbs || []).map(b => ({
                        text: b.text || '',
                        link: b.link || '',
                        crumbType: b.crumbType || ''
                    }));
                } catch(e) {}
            }

            // ✅ data-pool parity: 透传完整 widgetStates——Python 侧
            // extract_layout_tracking_info 递归扫描结构化类目真值用；
            // 扫描后即从 result 弹出，不进缓存不进信封
            result.widgetStates = ws;

            resolve(JSON.stringify(result));
        } catch(e) {
            resolve(JSON.stringify({error: e.message}));
        }
    });
})()'''

_FETCH_SELLERS_JS = r'''(() => {
    return new Promise(async (resolve) => {
        try {
            const origin = window.location.origin;
            const url = origin + '/api/entrypoint-api.bx/page/json/v2?url='
                + encodeURIComponent('/modal/otherOffersFromSellers?product_id=__PRODUCT_ID__&page_changed=true');
            const resp = await fetch(url, {
                method: 'get',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await resp.json();
            const ws = data.widgetStates || {};
            const sellerKey = Object.keys(ws).find(k => k.includes('webSellerList'));

            if (!sellerKey || !ws[sellerKey]) {
                resolve(JSON.stringify({count: 0, min_price: 0, sellers: []}));
                return;
            }

            const sellers = JSON.parse(ws[sellerKey]).sellers || [];
            sellers.forEach(s => {
                let p = (s.price && s.price.cardPrice && s.price.cardPrice.price)
                    || (s.price && s.price.price) || '';
                p = p.replace(/,/g, '.').replace(/[^\d.]/g, '');
                s.priceNum = parseFloat(p) || 0;
            });
            sellers.sort((a, b) => a.priceNum - b.priceNum);

            const mapSeller = s => ({
                sku: s.sku || '',
                price: s.priceNum,
                seller_name: s.name || s.sellerName || s.seller || '',
                seller_id: s.id || s.sellerId || '',
                seller_url: s.link || s.sellerUrl || (s.id ? '/seller/' + s.id : ''),
                // 评分真值 = rating.totalScore（webSellerList 实证结构，shopbang
                // 同源字段）；多数卖家 rating 为 null（稀疏），此处如实透出 0
                rating: (s.rating && (s.rating.totalScore ?? s.rating.value ?? s.rating.rating)) || 0,
                review_count: (s.rating && (s.rating.reviewsCount ?? s.rating.count)) || 0
            });
            const mapped = sellers.map(mapSeller);
            // 头部 20（最便宜，原语义逐字节不变）+ 尾部全部有评级卖家：按价截断
            // 会把稀疏的有评级卖家挤掉，拓店评分过滤就无米下锅——追加到尾部，
            // 下游不做评级过滤时只取前 20，行为与旧版一致
            const result = mapped.slice(0, 20).concat(mapped.slice(20).filter(s => s.rating > 0));

            resolve(JSON.stringify({
                count: sellers.length,
                min_price: sellers[0] ? sellers[0].priceNum : 0,
                // ⚠️ v0.31: 字段名修正（maozi 插件实证 webSellerList 真实结构）
                // 真实字段: id(卖家ID) / link(/seller/{id}/) / name，而非 sellerId/sellerUrl
                sellers: result
            }));
        } catch(e) {
            resolve(JSON.stringify({count: 0, min_price: 0, sellers: [], error: e.message}));
        }
    });
})()'''

_FETCH_VARIANTS_JS = r'''(() => {
    return new Promise(async (resolve) => {
        try {
            const origin = window.location.origin;
            const url = origin + '/api/entrypoint-api.bx/page/json/v2?url='
                + encodeURIComponent('/modal/aspectsNew?product_id=__PRODUCT_ID__');
            const resp = await fetch(url, {
                method: 'get',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await resp.json();
            const ws = data.widgetStates || {};
            const aspectKey = Object.keys(ws).find(k => k.includes('webAspects'));

            if (!aspectKey || !ws[aspectKey]) {
                resolve(JSON.stringify([]));
                return;
            }

            const parsed = JSON.parse(ws[aspectKey]);
            const variants = parsed.variants || parsed.aspects || [];
            resolve(JSON.stringify(variants.map(v => ({
                sku: v.sku || v.id || '',
                title: v.title || v.name || '',
                image: v.image || v.img || '',
                price: v.price || '',
                available: v.available !== false,
            }))));
        } catch(e) {
            resolve(JSON.stringify([]));
        }
    });
})()'''


# ---------------------------------------------------------------------------
# Public API -- CDP path
# ---------------------------------------------------------------------------


def fetch_product_info(cdp_url: str, product_id: str, *, cdp=None, lang: str = "ru",
                       force_new_tab: bool = False, shared_tab=None) -> dict[str, Any]:
    """Fetch product info via CDP using Ozon widget API.

    If *cdp* is provided, reuse the existing CdpConnection (caller owns it).
    Otherwise, create a temporary connection.

    force_new_tab=True: 跳过 find_tab（多线程并发场景每线程开自己的 tab，
    避免并发 worker 抢用户第一个 ozon.ru tab）。

    shared_tab（漏斗 v2 收尾·静默优先）: 调用方（collect_and_analyze）传入
    滚动采集保留的页面上下文，直接页内 fetch widget API——零导航零新 tab
    （shopbang 同款；实测 highlight 页上下文 fetch 其他商品的 widget 返回
    200 + 全量 widgetStates，逐商品导航不必要）。

    Returns dict with keys: title, price, cardPrice, originalPrice,
    images, primaryImage, description, characteristics, aspects, brand.
    Missing keys default to empty string / empty list.

    ⚠️ v0.36: 价格统一归一化为 RUB（webPrice.currency.code 识别 CNY → 汇率换算），
    result 恒带 ``currency: "RUB"``。缓存 key 含语言维度（防固化错误货币数据）。
    """
    from scripts.lib.cache import cache_get, cache_set
    from scripts.lib.cdp_client import CdpConnection

    cache_key = f"{product_id}:{lang}"
    cached = cache_get("ozon", cache_key)
    if cached is not None:
        return cached

    js = _FETCH_PRODUCT_JS.replace("__PRODUCT_ID__", product_id)

    result: dict[str, Any] = {
        "title": "",
        "price": "",
        "cardPrice": "",
        "originalPrice": "",
        "images": [],
        "primaryImage": "",
        "description": "",
        "brand": "",
        "currency": "RUB",
    }

    try:
        if shared_tab is not None:
            raw = shared_tab.evaluate(js, await_promise=True, timeout=20)
            parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
            if parsed.get("error"):
                logger.warning("Widget API error for product %s: %s",
                               product_id, parsed["error"])
            result.update(parsed)
            _normalize_price_to_rub(result)
        elif cdp is not None:
            tab = _ensure_ozon_tab(cdp, f"{OZON_BASE}/product/{product_id}/",
                                   force_new_tab=force_new_tab)
            raw = tab.evaluate(js, await_promise=True, timeout=20)
            parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
            if parsed.get("error"):
                logger.warning("Widget API error for product %s: %s",
                               product_id, parsed["error"])
            result.update(parsed)
            _normalize_price_to_rub(result)
        else:
            with CdpConnection(cdp_url) as _cdp:
                tab = _ensure_ozon_tab(_cdp, f"{OZON_BASE}/product/{product_id}/",
                                       force_new_tab=force_new_tab)
                raw = tab.evaluate(js, await_promise=True, timeout=20)
                parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
                if parsed.get("error"):
                    logger.warning("Widget API error for product %s: %s",
                                   product_id, parsed["error"])
                result.update(parsed)
                _normalize_price_to_rub(result)
    except Exception as exc:
        logger.error("fetch_product_info(%s) CDP failed: %s", product_id, exc)
        # Fallback: try direct HTTP (may fail due to geo/cookies)
        result = _fetch_product_info_http(product_id, result)

    # ⚠️ 只缓存有效数据（标题 + 价格都有），避免残缺数据（如限流时
    # price 为空）被缓存 1 小时污染后续运行（降级数据不缓存）
    # ✅ v0.71 类目真值派生（helper 可单测）
    _derive_category_from_breadcrumbs(result)
    # ✅ data-pool parity: layoutTrackingInfo 结构化类目真值（goldminer 同款
    # 递归扫描）；None 则省略键——信封省略纪律。widgetStates 由 JS 透传，
    # 此处扫描后立即弹出，不进缓存不进下游。
    widget_states = result.pop("widgetStates", {}) or {}
    layout_tracking = extract_layout_tracking_info(widget_states) or None
    if layout_tracking is not None:
        result["layout_tracking"] = layout_tracking
    if result.get("title") and (result.get("price") or result.get("cardPrice")):
        cache_set("ozon", cache_key, result, ttl=3600)
    return result


def _derive_category_from_breadcrumbs(result: dict[str, Any]) -> None:
    """面包屑 → category_path/web_category_id/breadcrumb_language（v0.71，原地写）。

    category_id 从链接抠（/category/xxx-14500/ → 14500）、品牌段排除，与
    ozon_scraper 同源 helper——discover 采集阶段即带竞品页面类目，不再单纯
    依赖 what_to_sell 登录态（Web 前台 ID ≠ Seller 树 dc/tp，仅作 worker
    路径精配/文本先验）。无 breadcrumbs 键时静默跳过。
    """
    crumbs = result.get("breadcrumbs")
    if not isinstance(crumbs, list) or not crumbs:
        return
    try:
        import re as _re_crumbs
        for _c in crumbs:
            if not isinstance(_c, dict):
                continue
            _link = str(_c.get("link") or "")
            _m = _re_crumbs.search(r"-(\d+)/?$", _link) if _link else None
            _c["category_id"] = _m.group(1) if _m else ""
        from scripts.lib.ozon_scraper import (
            _pick_category_from_crumbs,
            _category_path_from_crumbs,
        )
        _best = _pick_category_from_crumbs(crumbs)
        if _best is not None and _best.get("category_id"):
            result["web_category_id"] = str(_best["category_id"])
        _path = _category_path_from_crumbs(crumbs)
        if _path:
            result["category_path"] = _path
            if any("\u4e00" <= ch <= "\u9fff" for ch in _path):
                result["breadcrumb_language"] = "ZH_HANS"
            elif any("\u0400" <= ch <= "\u04FF" for ch in _path):
                result["breadcrumb_language"] = "RU"
    except Exception:
        pass


def _fetch_product_info_http(
    product_id: str, defaults: dict[str, Any]
) -> dict[str, Any]:
    """HTTP fallback for fetch_product_info (no CDP).

    Tries a plain GET to the widget endpoint.  Usually blocked by
    geo-check or missing cookies, but worth trying.
    """
    try:
        url = (
            f"{OZON_BASE}/api/entrypoint-api.bx/page/json/v2"
            f"?url={requests.utils.quote(f'/product/{product_id}')}"
        )
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })
        resp.raise_for_status()
        data = resp.json()
        ws = data.get("widgetStates", {})

        title_data = _find_widget(ws, "webProductHeading")
        if title_data:
            defaults["title"] = title_data.get("title", defaults["title"])

        price_data = _find_widget(ws, "webPrice")
        if price_data:
            defaults["price"] = price_data.get("price", defaults["price"])
            card = price_data.get("cardPrice")
            if isinstance(card, dict):
                defaults["cardPrice"] = card.get("price", defaults["cardPrice"])
            defaults["originalPrice"] = price_data.get(
                "originalPrice", defaults["originalPrice"]
            )
            currency = price_data.get("currency")
            if isinstance(currency, dict):
                defaults["currency"] = currency.get("code", defaults.get("currency"))

        gallery_data = _find_widget(ws, "webGallery")
        if gallery_data:
            imgs = [i.get("src", "") for i in gallery_data.get("images", []) if i.get("src")]
            if imgs:
                defaults["images"] = imgs
                defaults["primaryImage"] = gallery_data.get("cover", imgs[0])

        _normalize_price_to_rub(defaults)

        # ✅ data-pool parity: HTTP 回退路径同样扫 layoutTrackingInfo
        # （此处 ws 是原生 dict；None 则省略键）
        layout_tracking = extract_layout_tracking_info(ws) or None
        if layout_tracking is not None:
            defaults["layout_tracking"] = layout_tracking

    except Exception as exc:
        logger.debug("HTTP fallback for product %s also failed: %s", product_id, exc)

    return defaults


def fetch_competing_sellers(cdp_url: str, product_id: str, *, cdp=None,
                            lang: str = "ru", force_new_tab: bool = False,
                            shared_tab=None) -> dict[str, Any]:
    """Fetch competing sellers data for a product.

    If *cdp* is provided, reuse the existing CdpConnection (caller owns it).
    Otherwise, create a temporary connection.

    force_new_tab=True: 跳过 find_tab（多线程并发场景每线程开自己的 tab，
    避免并发 worker 抢用户第一个 ozon.ru tab）。

    shared_tab: 静默快路径（同 fetch_product_info——零导航零新 tab）。

    Returns::

        {
            "count": int,           # number of competing sellers
            "min_price": float,     # lowest price among sellers (RUB)
            "sellers": [            # top 10, sorted by price ascending
                {"sku": str, "price": float, "seller_name": str},
                ...
            ],
        }

    ⚠️ v0.36 磁盘缓存（6h，namespace ozon_sellers）：key = ``{pid}:{lang}``
    含语言维度（防固化错误货币数据）；只缓存有结果的（sellers 非空）。
    """
    from scripts.lib.cache import cache_get, cache_set
    from scripts.lib.cdp_client import CdpConnection

    cache_key = f"{product_id}:{lang}"
    cached = cache_get("ozon_sellers", cache_key)
    if cached is not None:
        return cached

    js = _FETCH_SELLERS_JS.replace("__PRODUCT_ID__", product_id)

    result: dict[str, Any] = {"count": 0, "min_price": 0, "sellers": []}

    try:
        if shared_tab is not None:
            raw = shared_tab.evaluate(js, await_promise=True, timeout=20)
            parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
            if parsed.get("error"):
                logger.warning("Seller API error for product %s: %s",
                               product_id, parsed["error"])
            result.update({
                "count": parsed.get("count", 0),
                "min_price": parsed.get("min_price", 0),
                "sellers": parsed.get("sellers", []),
            })
        elif cdp is not None:
            tab = _ensure_ozon_tab(cdp, f"{OZON_BASE}/product/{product_id}/",
                                   force_new_tab=force_new_tab)
            raw = tab.evaluate(js, await_promise=True, timeout=20)
            parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
            if parsed.get("error"):
                logger.warning("Seller API error for product %s: %s",
                               product_id, parsed["error"])
            result.update({
                "count": parsed.get("count", 0),
                "min_price": parsed.get("min_price", 0),
                "sellers": parsed.get("sellers", []),
            })
        else:
            with CdpConnection(cdp_url) as _cdp:
                tab = _ensure_ozon_tab(_cdp, f"{OZON_BASE}/product/{product_id}/",
                                       force_new_tab=force_new_tab)
                raw = tab.evaluate(js, await_promise=True, timeout=20)
                parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
                if parsed.get("error"):
                    logger.warning("Seller API error for product %s: %s",
                                   product_id, parsed["error"])
                result.update({
                    "count": parsed.get("count", 0),
                    "min_price": parsed.get("min_price", 0),
                    "sellers": parsed.get("sellers", []),
                })
    except Exception as exc:
        logger.error("fetch_competing_sellers(%s) failed: %s", product_id, exc)

    if result.get("sellers"):
        cache_set("ozon_sellers", cache_key, result, ttl=21600)
    return result


def fetch_sku_variants(cdp_url: str, product_id: str) -> list[dict[str, Any]]:
    """Fetch all SKU variants (aspects) for a product.

    Returns list of variant dicts::

        [{"sku": str, "title": str, "image": str, "price": str, "available": bool}, ...]
    """
    from scripts.lib.cdp_client import CdpConnection

    js = _FETCH_VARIANTS_JS.replace("__PRODUCT_ID__", product_id)

    variants: list[dict[str, Any]] = []

    try:
        with CdpConnection(cdp_url) as cdp:
            tab = _ensure_ozon_tab(cdp, f"{OZON_BASE}/product/{product_id}/")
            raw = tab.evaluate(js, await_promise=True, timeout=20)
            parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or [])
            if isinstance(parsed, list):
                variants = parsed
    except Exception as exc:
        logger.error("fetch_sku_variants(%s) failed: %s", product_id, exc)

    return variants


def extract_commissions_from_info(product_info: dict) -> dict:
    """Extract commission rates from Widget API product info response.

    Some Widget API responses include embedded commission data in the
    characteristics or in a dedicated widget.  This function searches
    common locations for commission info.

    Returns::

        {
            "sales_percent": float,  # Ozon sales commission %
            "fbo_fee": float,        # FBO fulfillment fee (RUB)
            "fbs_fee": float,        # FBS fulfillment fee (RUB)
        }

    Missing values default to 0.0.
    """
    result: dict[str, float] = {
        "sales_percent": 0.0,
        "fbo_fee": 0.0,
        "fbs_fee": 0.0,
    }

    # Try extracting from characteristics (some responses embed it)
    for char_group in product_info.get("characteristics", []):
        if not isinstance(char_group, dict):
            continue
        for item in char_group.get("items", []):
            if not isinstance(item, dict):
                continue
            key = (item.get("key") or item.get("name") or "").lower()
            val = item.get("value") or item.get("text") or ""
            if not val:
                continue
            try:
                val_str = str(val).replace(",", ".").replace("%", "").strip()
                val_num = float(re.sub(r"[^\d.]", "", val_str) or 0)
            except (ValueError, TypeError):
                continue

            if "commission" in key or "sales_percent" in key:
                result["sales_percent"] = val_num
            elif "fbo" in key and ("fee" in key or "fulfillment" in key or "logistics" in key):
                result["fbo_fee"] = val_num
            elif "fbs" in key and ("fee" in key or "fulfillment" in key or "logistics" in key):
                result["fbs_fee"] = val_num

    # Also check direct keys at the top level (some responses flatten this)
    for key, val in product_info.items():
        kl = key.lower()
        try:
            val_str = str(val).replace(",", ".").replace("%", "").strip()
            val_num = float(re.sub(r"[^\d.]", "", val_str) or 0)
        except (ValueError, TypeError):
            continue

        if "commission" in kl or "sales_percent" in kl:
            result["sales_percent"] = val_num
        elif "fbo" in kl and ("fee" in kl or "fulfillment" in kl):
            result["fbo_fee"] = val_num
        elif "fbs" in kl and ("fee" in kl or "fulfillment" in kl):
            result["fbs_fee"] = val_num

    return result


def fetch_all_product_data(
    cdp_url: str, product_id: str
) -> dict[str, Any]:
    """Convenience: fetch product info + competing sellers in one call.

    Returns a merged dict with all product data including seller info.
    """
    info = fetch_product_info(cdp_url, product_id)
    sellers = fetch_competing_sellers(cdp_url, product_id)
    info["competing_sellers"] = sellers
    return info


# ---------------------------------------------------------------------------
# variant_v2 重量真值链（毛子移植，PLAN-data-pool-parity-v1 Task 6.3）
# ---------------------------------------------------------------------------
# ⚠️ 下方端点/请求体/响应路径全部逐字取证自 maozi-plugin-3.2.6
# content-scripts/content.js 的跨 Tab API 函数 AE（2026-09-10 grep 提取，
# 禁凭记忆改——改前重跑 bundle 取证）：
#   ① search-sku-base → POST https://seller.ozon.ru/api/v1/search
#      body = {"company_id": cid, "need_total": true,
#              "filter": {"children_nodes": {"children_nodes": [
#                  {"input_leaf": {"sku": {"values": [<sku>]}}}],
#                  "operator": "AND"}},
#              "pagination": {"limit": "50"}, "is_copy_allowed": false}
#      响应 → data.variants[0].variant_id
#   ② variant_v2 → POST https://seller.ozon.ru/api/site/seller-prototype/
#      create-bundle-by-variant-id
#      body = {"company_id": cid, "variant_id": <vid>,
#              "source": "SOURCE_UI_COPY_MERGED"}
#      响应 → data.item.{weight(克) / depth / width / height(毫米)}
#      （毛子 UI `${w}g`、`${depth} x ${width} x ${height}mm` 即读此处）
#   两请求 headers 均 Content-Type + x-o3-company-id + x-o3-language:RU，
#   credentials:"include"；cid = seller.ozon.ru 的 sc_company_id cookie
#   （毛子 Yy()：cookie/localStorage 二源）。
OZON_SELLER_BASE = "https://seller.ozon.ru"

_VARIANT_SEARCH_JS = r'''(async () => {
    try {
        const m = document.cookie.match(/(?:^|;\s*)sc_company_id=([^;]+)/);
        const cid = m ? m[1] : '';
        if (!cid) { return JSON.stringify({error: "no sc_company_id cookie (seller not logged in)"}); }
        const resp = await fetch('https://seller.ozon.ru/api/v1/search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'x-o3-company-id': cid, 'x-o3-language': 'RU'},
            credentials: 'include',
            body: JSON.stringify({
                company_id: cid,
                need_total: true,
                filter: {children_nodes: {children_nodes: [{input_leaf: {sku: {values: ['__SKU__']}}}], operator: 'AND'}},
                pagination: {limit: '50'},
                is_copy_allowed: false,
            }),
        });
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); }
        catch (e) { return JSON.stringify({error: 'non-JSON status=' + resp.status}); }
        if (!resp.ok) { return JSON.stringify({error: 'HTTP ' + resp.status + ' ' + text.slice(0, 200)}); }
        const v = (data && data.variants && data.variants[0]) || null;
        const vid = v ? (v.variant_id != null ? v.variant_id : v.variantId) : null;
        if (!vid) { return JSON.stringify({error: 'no variant_id', body: text.slice(0, 200)}); }
        return JSON.stringify({company_id: cid, variant_id: vid});
    } catch (e) { return JSON.stringify({error: String((e && e.message) || e)}); }
})()'''

_VARIANT_BUNDLE_JS = r'''(async () => {
    try {
        const resp = await fetch('https://seller.ozon.ru/api/site/seller-prototype/create-bundle-by-variant-id', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'x-o3-company-id': '__COMPANY_ID__', 'x-o3-language': 'RU'},
            credentials: 'include',
            body: JSON.stringify({company_id: '__COMPANY_ID__', variant_id: __VARIANT_ID__, source: 'SOURCE_UI_COPY_MERGED'}),
        });
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); }
        catch (e) { return JSON.stringify({error: 'non-JSON status=' + resp.status}); }
        if (!resp.ok) { return JSON.stringify({error: 'HTTP ' + resp.status + ' ' + text.slice(0, 200)}); }
        const item = data && data.item;
        if (!item) { return JSON.stringify({error: 'no item', body: text.slice(0, 200)}); }
        return JSON.stringify({weight: item.weight, depth: item.depth, width: item.width, height: item.height});
    } catch (e) { return JSON.stringify({error: String((e && e.message) || e)}); }
})()'''


def _as_int(value: Any) -> int:
    """宽松转 int（"1840"/1840.0/1840 → 1840）；不可转 → 0。"""
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return 0


def _tab_for_variant_truth(cdp: CdpConnection) -> CdpTab:
    """variant 真值采集用 seller tab（对齐 ozon_seller_analytics._tab_for_seller 纪律）。

    不走 _ensure_ozon_tab：它会 find_tab("ozon.ru") 命中任意 www 商品 tab 并
    导航走——discover 流程中把用户/采集中的 www 页抢去开 seller 后台是破坏性的；
    而 bundle 端点在 seller.ozon.ru 源内才带得动 sc_company_id 会话 cookie
    （毛子 CROSS_TAB 同款：优先借已登录 seller tab，无则新建）。
    """
    tab = cdp.find_tab("seller.ozon.ru")
    if tab is not None:
        tab.set_bypass_csp()  # CSP 剥除（v4.2 铺路）：页内注入 fetch 不被 connect-src 拦
        return tab
    tab = cdp.new_tab(f"{OZON_SELLER_BASE}/")
    tab.set_bypass_csp()
    return tab


def fetch_variant_truth(cdp_url: str, sku: str, *,
                        cdp: CdpConnection | None = None) -> dict[str, Any] | None:
    """跨卖家 variant 重量/尺寸真值（seller 内部 composer API，毛子移植）。

    流程（seller.ozon.ru 页内两次 fetch，见上方取证注释）：
      search-sku-base（sku → variants[0].variant_id）
      → create-bundle-by-variant-id（variant_id → item 重量/尺寸）。

    Returns:
        {"weight_g": int, "dims_mm": [length, width, height]}（length=depth，
        毛子 UI 同序）——weight 须为正才算收获成功；seller 未登录（无
        sc_company_id）/任何失败 → None（绝不 raise，调用方零兜底负担）。
    传入 cdp 时复用调用方连接（tab 不关不远程收），否则临时建连自收。
    """
    from scripts.lib.cdp_client import CdpConnection

    own_connection = cdp is None
    if own_connection:
        try:
            cdp = CdpConnection(cdp_url)
        except Exception as exc:
            logger.info("variant 真值采集失败（忽略）: sku=%s %s", sku, str(exc)[:160])
            return None
    try:
        return _fetch_variant_truth_via(cdp, sku)
    except Exception as exc:
        logger.info("variant 真值采集失败（忽略）: sku=%s %s", sku, str(exc)[:160])
        return None
    finally:
        if own_connection:
            try:
                cdp.close()  # type: ignore[union-attr]
            except Exception:
                pass


def _fetch_variant_truth_via(cdp: CdpConnection, sku: str) -> dict[str, Any] | None:
    """fetch_variant_truth 主体（连接已就绪；异常上抛由调用方吞）。"""
    tab = _tab_for_variant_truth(cdp)
    js1 = _VARIANT_SEARCH_JS.replace("__SKU__", str(sku))
    raw1 = tab.evaluate(js1, await_promise=True, timeout=20)
    d1 = _safe_json_parse(raw1) if isinstance(raw1, str) else (raw1 or {})
    if not isinstance(d1, dict) or d1.get("error") or not d1.get("variant_id"):
        logger.info("variant 真值: sku %s 无 variant_id（%s）",
                    sku, str((d1 or {}).get("error", "empty"))[:120])
        return None
    vid = str(d1["variant_id"])
    vid_token = vid if vid.isdigit() else json.dumps(vid)
    js2 = (_VARIANT_BUNDLE_JS
           .replace("__COMPANY_ID__", json.dumps(str(d1["company_id"])))
           .replace("__VARIANT_ID__", vid_token))
    raw2 = tab.evaluate(js2, await_promise=True, timeout=20)
    d2 = _safe_json_parse(raw2) if isinstance(raw2, str) else (raw2 or {})
    if not isinstance(d2, dict) or d2.get("error"):
        logger.info("variant 真值: sku %s bundle 无 item（%s）",
                    sku, str((d2 or {}).get("error", "empty"))[:120])
        return None
    weight_g = _as_int(d2.get("weight"))
    if weight_g <= 0:
        return None
    return {
        "weight_g": weight_g,
        "dims_mm": [_as_int(d2.get("depth")), _as_int(d2.get("width")),
                    _as_int(d2.get("height"))],
    }

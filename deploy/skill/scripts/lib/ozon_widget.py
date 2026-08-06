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
from typing import Any

import requests

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


def _ensure_ozon_tab(cdp: "CdpConnection") -> "CdpTab":
    """Find an existing ozon.ru tab or create a new one.

    Reuses an existing tab to preserve cookies/session state.
    """
    tab = cdp.find_tab("ozon.ru")
    if tab is not None:
        return tab
    # Create a new tab pointing at Ozon homepage (sets cookies)
    return cdp.new_tab(f"{OZON_BASE}/")


def _safe_json_parse(text: str) -> dict[str, Any]:
    """Parse JSON string, returning empty dict on failure."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def _find_widget(widget_states: dict[str, str], substring: str) -> dict[str, Any]:
    """Find and parse the first widgetStates entry whose key contains *substring*."""
    for key, value in widget_states.items():
        if substring in key:
            return _safe_json_parse(value)
    return {}


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

            resolve(JSON.stringify({
                count: sellers.length,
                min_price: sellers[0] ? sellers[0].priceNum : 0,
                sellers: sellers.slice(0, 10).map(s => ({
                    sku: s.sku || '',
                    price: s.priceNum,
                    seller_name: s.sellerName || s.seller || ''
                }))
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


def fetch_product_info(cdp_url: str, product_id: str, *, cdp=None) -> dict[str, Any]:
    """Fetch product info via CDP using Ozon widget API.

    If *cdp* is provided, reuse the existing CdpConnection (caller owns it).
    Otherwise, create a temporary connection.

    Returns dict with keys: title, price, cardPrice, originalPrice,
    images, primaryImage, description, characteristics, aspects, brand.
    Missing keys default to empty string / empty list.
    """
    from scripts.lib.cache import cache_get, cache_set
    from scripts.lib.cdp_client import CdpConnection

    cached = cache_get("ozon", product_id)
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
    }

    try:
        if cdp is not None:
            tab = _ensure_ozon_tab(cdp)
            raw = tab.evaluate(js, await_promise=True, timeout=20)
            parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
            if parsed.get("error"):
                logger.warning("Widget API error for product %s: %s",
                               product_id, parsed["error"])
            result.update(parsed)
        else:
            with CdpConnection(cdp_url) as _cdp:
                tab = _ensure_ozon_tab(_cdp)
                raw = tab.evaluate(js, await_promise=True, timeout=20)
                parsed = _safe_json_parse(raw) if isinstance(raw, str) else (raw or {})
                if parsed.get("error"):
                    logger.warning("Widget API error for product %s: %s",
                                   product_id, parsed["error"])
                result.update(parsed)
    except Exception as exc:
        logger.error("fetch_product_info(%s) CDP failed: %s", product_id, exc)
        # Fallback: try direct HTTP (may fail due to geo/cookies)
        result = _fetch_product_info_http(product_id, result)

    # ⚠️ 只缓存有效数据（标题 + 价格都有），避免残缺数据（如限流时
    # price 为空）被缓存 1 小时污染后续运行（降级数据不缓存）
    if result.get("title") and (result.get("price") or result.get("cardPrice")):
        cache_set("ozon", product_id, result, ttl=3600)
    return result


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

        gallery_data = _find_widget(ws, "webGallery")
        if gallery_data:
            imgs = [i.get("src", "") for i in gallery_data.get("images", []) if i.get("src")]
            if imgs:
                defaults["images"] = imgs
                defaults["primaryImage"] = gallery_data.get("cover", imgs[0])

    except Exception as exc:
        logger.debug("HTTP fallback for product %s also failed: %s", product_id, exc)

    return defaults


def fetch_competing_sellers(cdp_url: str, product_id: str, *, cdp=None) -> dict[str, Any]:
    """Fetch competing sellers data for a product.

    If *cdp* is provided, reuse the existing CdpConnection (caller owns it).
    Otherwise, create a temporary connection.

    Returns::

        {
            "count": int,           # number of competing sellers
            "min_price": float,     # lowest price among sellers (RUB)
            "sellers": [            # top 10, sorted by price ascending
                {"sku": str, "price": float, "seller_name": str},
                ...
            ],
        }
    """
    from scripts.lib.cdp_client import CdpConnection

    js = _FETCH_SELLERS_JS.replace("__PRODUCT_ID__", product_id)

    result: dict[str, Any] = {"count": 0, "min_price": 0, "sellers": []}

    try:
        if cdp is not None:
            tab = _ensure_ozon_tab(cdp)
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
                tab = _ensure_ozon_tab(_cdp)
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
            tab = _ensure_ozon_tab(cdp)
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

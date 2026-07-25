"""Ozon Seller API client -- product details, commission, analytics.

Uses the official Ozon Seller API (api-seller.ozon.ru) with Client-Id/Api-Key.
Also provides Premium analytics spoofing via CDP.
"""
from __future__ import annotations
import json, logging, time, re
from typing import Any
import requests

logger = logging.getLogger(__name__)

OZON_SELLER_BASE = "https://api-seller.ozon.ru"


def _seller_post(client_id: str, api_key: str, path: str, body: dict, timeout: int = 30) -> dict:
    """POST to Ozon Seller API. Returns response dict. Raises on auth failures."""
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(f"{OZON_SELLER_BASE}{path}", json=body, headers=headers, timeout=timeout)
        if resp.status_code in (401, 403):
            logger.warning("Seller API auth failed (%s): %s", resp.status_code, path)
            raise requests.exceptions.HTTPError(response=resp)  # permanent, don't retry
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        logger.warning("Seller API connection failed: %s", path)
        return {}  # transient, can retry
    except requests.exceptions.Timeout:
        logger.warning("Seller API timeout: %s", path)
        return {}  # transient, can retry
    except requests.exceptions.HTTPError:
        raise  # re-raise 401/403 and other HTTP errors
    except Exception as e:
        logger.warning("Seller API unexpected error: %s: %s", path, e)
        return {}


def _seller_post_with_retry(client_id: str, api_key: str, path: str, body: dict, timeout: int = 30, retries: int = 2) -> dict:
    """POST with retry for transient failures only."""
    for attempt in range(retries + 1):
        try:
            result = _seller_post(client_id, api_key, path, body, timeout)
            return result  # success or transient failure (empty dict)
        except requests.exceptions.HTTPError:
            raise  # auth failure -- don't retry
        except requests.exceptions.ConnectionError:
            if attempt < retries:
                logger.warning("Seller API connection failed (attempt %d/%d), retrying...", attempt + 1, retries)
                time.sleep(2 ** attempt)
                continue
            return {}
        except requests.exceptions.Timeout:
            if attempt < retries:
                logger.warning("Seller API timeout (attempt %d/%d), retrying...", attempt + 1, retries)
                time.sleep(1)
                continue
            return {}
    return {}


def fetch_product_commissions(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """Fetch commission rates for products via /v5/product/info/prices.

    Returns dict mapping product_id -> {
        "sales_percent_fbo": float,  # FBO sales commission %
        "sales_percent_fbs": float,  # FBS sales commission %
        "sales_percent_rfbs": float,  # RFBS sales commission %
        "sales_percent_fbp": float,  # FBP sales commission %
        "fbo_fulfillment": float,  # FBO logistics fee (max)
        "fbs_fulfillment": float,  # FBS logistics fee (max)
    }
    """
    result = {}
    # Batch in groups of 100 using v5/product/info/prices
    for i in range(0, len(product_ids), 100):
        batch = product_ids[i:i+100]
        try:
            data = _seller_post(client_id, api_key, "/v5/product/info/prices", {
                "filter": {"product_id": [int(pid) for pid in batch]},
                "limit": len(batch),
                "last_id": "",
            })
            for item in data.get("items", []):
                pid = str(item.get("product_id", ""))
                comm = item.get("commissions", {})
                if comm:
                    result[pid] = {
                        "sales_percent_fbo": float(comm.get("sales_percent_fbo", 0) or 0),
                        "sales_percent_fbs": float(comm.get("sales_percent_fbs", 0) or 0),
                        "sales_percent_rfbs": float(comm.get("sales_percent_rfbs", 0) or 0),
                        "sales_percent_fbp": float(comm.get("sales_percent_fbp", 0) or 0),
                        "fbo_fulfillment": float(comm.get("fbo_direct_flow_trans_max_amount", 0) or 0),
                        "fbs_fulfillment": float(comm.get("fbs_direct_flow_trans_max_amount", 0) or 0),
                    }
        except requests.exceptions.HTTPError as e:
            logger.warning("fetch_product_commissions auth failed (%s), skipping remaining batches",
                           e.response.status_code if e.response else "unknown")
            break  # auth failure -- no point trying more batches
        except Exception as e:
            logger.warning("fetch_product_commissions batch failed: %s", e)
    return result


def fetch_product_attributes(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """Fetch product attributes (weight, dimensions, brand, category).

    Uses /v3/product/info/list for basic info and /v5/product/info/prices
    for volume weight. Note: detailed attributes (brand, category, dimensions)
    are only available for the seller's own products via the Seller API.

    Returns dict mapping product_id -> {
        "weight_g": int,
        "dimensions_mm": {"length": int, "width": int, "height": int},
        "brand": str,
        "category": str,
        "name": str,
        "offer_id": str,
        "price": str,
        "volume_weight": float,
    }
    """
    result = {}

    # Step 1: Get basic product info from /v3/product/info/list
    for i in range(0, len(product_ids), 100):
        batch = product_ids[i:i+100]
        try:
            data = _seller_post(client_id, api_key, "/v3/product/info/list", {
                "product_id": [int(pid) for pid in batch],
            })
            for item in data.get("items", []):
                pid = str(item.get("id", ""))
                result[pid] = {
                    "weight_g": 0,
                    "dimensions_mm": {"length": 0, "width": 0, "height": 0},
                    "brand": "",
                    "category": "",
                    "name": item.get("name", ""),
                    "offer_id": item.get("offer_id", ""),
                    "price": item.get("price", ""),
                    "volume_weight": float(item.get("volume_weight", 0) or 0),
                    "description_category_id": item.get("description_category_id", 0),
                    "type_id": item.get("type_id", 0),
                }
        except requests.exceptions.HTTPError as e:
            logger.warning("fetch_product_attributes auth failed (%s), skipping remaining batches",
                           e.response.status_code if e.response else "unknown")
            break  # auth failure -- no point trying more batches
        except Exception as e:
            logger.warning("fetch_product_attributes batch (info/list) failed: %s", e)

    # Step 2: Enrich with volume_weight from /v5/product/info/prices
    for i in range(0, len(product_ids), 100):
        batch = product_ids[i:i+100]
        try:
            data = _seller_post(client_id, api_key, "/v5/product/info/prices", {
                "filter": {"product_id": [int(pid) for pid in batch]},
                "limit": len(batch),
                "last_id": "",
            })
            for item in data.get("items", []):
                pid = str(item.get("product_id", ""))
                if pid not in result:
                    result[pid] = {
                        "weight_g": 0,
                        "dimensions_mm": {"length": 0, "width": 0, "height": 0},
                        "brand": "",
                        "category": "",
                        "name": "",
                        "offer_id": "",
                        "price": "",
                        "volume_weight": 0,
                    }
                result[pid]["volume_weight"] = float(item.get("volume_weight", 0) or 0)
        except requests.exceptions.HTTPError as e:
            logger.warning("fetch_product_attributes auth failed (%s), skipping remaining batches",
                           e.response.status_code if e.response else "unknown")
            break  # auth failure -- no point trying more batches
        except Exception as e:
            logger.warning("fetch_product_attributes batch (prices) failed: %s", e)

    return result


def fetch_analytics_via_premium_spoof(cdp_url: str, product_ids: list[str]) -> dict[str, dict]:
    """Fetch analytics data by spoofing Ozon Premium via CDP.

    This injects JS into seller.ozon.ru that intercepts /premium/status
    and returns fake Premium Plus status, unlocking analytics data.

    Returns dict mapping product_id -> {
        "monthly_sales": int,
        "monthly_revenue": float,
        "daily_sales": float,
        "conversion_rate": float,
        "search_views": int,
        "product_views": int,
    }
    """
    from scripts.lib.cdp_client import CdpConnection

    # Premium spoofing JS (from shangpinbang ozon_min.js)
    SPOOF_JS = r'''
    (() => {
        // Intercept fetch
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {
            const url = (args[0]?.url || args[0] || '').toString();
            if (/\/premium\/status|seller-analytics\/premium|get-seller-premium-status/i.test(url)) {
                return new Response(JSON.stringify({
                    is_premium: true, isPremiumPlus: true, isAnalyst: true,
                    subscription: "PREMIUM_PLUS",
                    features: { analytics: "full", statistics: "full", graphs: "full" }
                }), { status: 200, headers: { 'Content-Type': 'application/json' } });
            }
            return originalFetch.apply(this, args);
        };

        // Intercept XMLHttpRequest
        const originalOpen = XMLHttpRequest.prototype.open;
        const originalSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {
            this._url = url;
            return originalOpen.call(this, method, url, ...rest);
        };
        XMLHttpRequest.prototype.send = function(...args) {
            if (this._url && /\/premium\/status|seller-analytics\/premium/i.test(this._url)) {
                Object.defineProperty(this, 'responseText', {
                    get: () => JSON.stringify({
                        is_premium: true, isPremiumPlus: true, isAnalyst: true,
                        subscription: "PREMIUM_PLUS"
                    })
                });
                Object.defineProperty(this, 'status', { get: () => 200 });
                setTimeout(() => {
                    if (this.onload) this.onload();
                    if (this.onreadystatechange) this.onreadystatechange();
                }, 10);
                return;
            }
            return originalSend.call(this, ...args);
        };
    })()
    '''

    result = {}

    with CdpConnection(cdp_url) as cdp:
        tab = cdp.find_tab("seller.ozon.ru")
        if not tab:
            logger.warning("No seller.ozon.ru tab found. User must be logged in to seller panel.")
            return result

        # Inject Premium spoof
        tab.add_init_script(SPOOF_JS)
        logger.info("Premium spoof injected")

        # Navigate to analytics page
        tab.navigate("https://seller.ozon.ru/app/analytics/graphs", wait_until="load", timeout=30)
        time.sleep(5)

        # Try to read analytics data from the page
        data = tab.evaluate(r'''(() => {
            // The analytics page loads data via API calls that are now Premium-unlocked
            // Try to extract from the page's data stores
            const result = {};

            // Check for analytics data in page state
            if (window.__INITIAL_STATE__) {
                return JSON.stringify(window.__INITIAL_STATE__);
            }

            // Check for data in Vue/React component state
            const appEl = document.querySelector('#app') || document.querySelector('#root');
            if (appEl && appEl.__vue_app__) {
                try {
                    const store = appEl.__vue_app__.config.globalProperties.$store;
                    if (store) return JSON.stringify(store.state);
                } catch(e) {}
            }

            // Fallback: read visible table data
            const rows = document.querySelectorAll('table tr, [class*="analytics"] [class*="row"]');
            result.table_rows = Array.from(rows).slice(0, 20).map(r => r.innerText.trim());

            return JSON.stringify(result);
        })()''')

        try:
            parsed = json.loads(data) if data else {}
            logger.info("Analytics page data keys: %s", list(parsed.keys())[:10])

            # Extract analytics data for each product
            for pid in product_ids:
                product_data = parsed.get(pid, parsed.get(str(pid), {}))
                if product_data:
                    result[pid] = {
                        "monthly_sales": int(product_data.get("monthly_sales", 0) or 0),
                        "monthly_revenue": float(product_data.get("monthly_revenue", 0) or 0),
                        "daily_sales": float(product_data.get("daily_sales", 0) or 0),
                        "conversion_rate": float(product_data.get("conversion_rate", 0) or 0),
                        "search_views": int(product_data.get("search_views", 0) or 0),
                        "product_views": int(product_data.get("product_views", 0) or 0),
                    }
                else:
                    # Try to extract from table rows if structured data not available
                    result[pid] = {
                        "monthly_sales": 0,
                        "monthly_revenue": 0.0,
                        "daily_sales": 0.0,
                        "conversion_rate": 0.0,
                        "search_views": 0,
                        "product_views": 0,
                    }

            # If no per-product data found, store raw parsed data for debugging
            if not result and parsed:
                logger.info("Raw analytics data available but no per-product mapping found")
                result["_raw"] = parsed

        except json.JSONDecodeError:
            logger.warning("Could not parse analytics page data")

    return result


def fetch_full_product_data(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """Fetch all available data for products (commissions + attributes).

    Combines Seller API calls into a single result per product.
    Returns dict mapping product_id -> merged data dict.
    """
    commissions = fetch_product_commissions(client_id, api_key, product_ids)
    attributes = fetch_product_attributes(client_id, api_key, product_ids)

    result = {}
    for pid in set(list(commissions.keys()) + list(attributes.keys())):
        merged = {}
        if pid in commissions:
            merged.update(commissions[pid])
        if pid in attributes:
            merged.update(attributes[pid])
        result[pid] = merged

    return result

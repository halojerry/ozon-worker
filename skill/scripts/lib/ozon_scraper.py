#!/usr/bin/env python3
"""
Ozon Product Page Scraper — 抓取 Ozon 商品页公开数据（图片/标题/类目）

反检测策略:
  1. curl-cffi 模拟 Chrome TLS/JA3 指纹
  2. 俄罗斯 Accept-Language / 时区头
  3. 可选代理（俄罗斯住宅代理）

用法:
  from scripts.lib.ozon_scraper import scrape_ozon_product
  data = scrape_ozon_product("https://www.ozon.ru/product/xxx-12345/")

部署到 Worker 后，通过 /api/v1/scrape_ozon 端点暴露给 Skill 调用。
用户本地不需要代理——Worker 服务器配置代理即可。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OZON_PRODUCT_URL_RE = re.compile(
    r"ozon\.ru/product/(.+?)-(\d{6,20})", re.IGNORECASE
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------


def _parse_product_id_from_url(url: str) -> str | None:
    """Extract product_id from Ozon URL."""
    m = OZON_PRODUCT_URL_RE.search(url)
    return m.group(2) if m else None


def _extract_from_json_ld(html: str) -> dict[str, Any]:
    """Extract product data from JSON-LD structured data in page source."""
    result: dict[str, Any] = {
        "images": [],
        "title": "",
        "category": "",
        "price": "",
        "currency": "RUB",
        "sku": "",
    }

    # Ozon uses: <script nonce="" type="application/ld+json">
    json_ld_matches = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL
    )

    for ld_str in json_ld_matches:
        try:
            data = json.loads(ld_str)
            if not isinstance(data, dict):
                continue

            # Product data
            if data.get("@type") == "Product":
                result["title"] = data.get("name", "")
                result["sku"] = data.get("sku", "")

                # Images
                images = data.get("image", [])
                if isinstance(images, str):
                    images = [images]
                result["images"] = [img for img in images if isinstance(img, str)]

                # Price
                offers = data.get("offers", {})
                if isinstance(offers, dict):
                    result["price"] = str(offers.get("price", ""))
                    result["currency"] = offers.get("priceCurrency", "RUB")

            # Breadcrumb (category)
            elif data.get("@type") == "BreadcrumbList":
                items = data.get("itemListElement", [])
                categories = []
                for item in items:
                    if isinstance(item, dict) and "item" in item:
                        name = item["item"].get("name", "")
                        if name:
                            categories.append(name)
                result["category"] = " > ".join(categories)

        except (json.JSONDecodeError, TypeError):
            continue

    return result


def _extract_from_ssr_state(html: str) -> dict[str, Any]:
    """Extract product images from SSR state (Nuxt/Vue SSR pattern)."""
    result: dict[str, Any] = {"images": [], "title": ""}

    # Pattern 1: __NUXT__ state (Ozon uses this)
    nuxt_match = re.search(r"window\.__NUXT__\s*=\s*\{.*?\};?\s*window\.__NUXT__\.state\s*=\s*'({.*?})'", html)
    if not nuxt_match:
        nuxt_match = re.search(r"window\.__NUXT__\.state\s*=\s*'({.*?})'", html)
    if not nuxt_match:
        nuxt_match = re.search(r"window\.__NUXT__\s*=\s*(\{.*?\});\s*</script>", html, re.DOTALL)

    if nuxt_match:
        try:
            state_str = nuxt_match.group(1)
            state = json.loads(state_str)
            # Try common paths for product data
            product = _deep_get(state, ["product", "product"])
            if not product:
                product = _deep_get(state, ["currentProduct"])
            if product:
                imgs = product.get("images", [])
                if isinstance(imgs, list):
                    result["images"] = [img if isinstance(img, str) else img.get("url", "") for img in imgs]
                result["title"] = product.get("name", product.get("title", ""))
        except (json.JSONDecodeError, TypeError):
            pass

    return result


def _extract_images_from_html(html: str) -> list[str]:
    """Fallback: extract product image URLs from HTML (ir.ozone.ru CDN only, skip logos)."""
    patterns = [
        r'https://ir\.ozone\.ru/s3/multimedia[^\"\'\\s<>]*?\.(?:jpg|jpeg|png|webp)',
        r'https://cdn\.ozon\.ru/multimedia[^\"\'\\s<>]*?\.(?:jpg|jpeg|png|webp)',
    ]

    images: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for img in re.findall(pat, html, re.IGNORECASE):
            # Skip logos, icons, banners
            if any(s in img.lower() for s in ['logo', 'icon', 'banner', 'cms/']):
                continue
            if img not in seen:
                seen.add(img)
                images.append(img)

    return images


def _deep_get(d: dict, keys: list[str], default=None):
    """Safely traverse nested dict."""
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key, {})
        else:
            return default
    return d if d != {} else default


def _fetch_page(url: str, proxy: str = "", timeout: int = 30) -> tuple[int, str]:
    """Fetch Ozon product page with anti-detection measures.

    Returns (status_code, html_text).
    """
    import random as _random

    try:
        from curl_cffi import requests
    except ImportError:
        logger.error("curl_cffi not installed. Run: pip install curl_cffi")
        raise RuntimeError("curl_cffi is required for Ozon scraping")

    headers = dict(BASE_HEADERS)
    headers["User-Agent"] = _random.choice(USER_AGENTS)

    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": timeout,
        "impersonate": "chrome131",
        "allow_redirects": True,
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}

    try:
        resp = requests.get(url, **kwargs)
        return resp.status_code, resp.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        raise


def scrape_ozon_product(
    url: str,
    proxy: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Scrape an Ozon product page for images, title, category.

    Args:
        url: Ozon product page URL (e.g., https://www.ozon.ru/product/xxx-12345/)
        proxy: Optional proxy URL (e.g., "http://user:pass@proxy.ru:8080")
        timeout: Request timeout in seconds

    Returns:
        {
            "success": bool,
            "product_id": str,
            "slug": str,
            "images": [str, ...],       # Full-resolution image URLs
            "title": str,                # Product title (Russian)
            "category": str,             # Category breadcrumb
            "price": str,                # Display price
            "error": str | None,
        }
    """
    product_id = _parse_product_id_from_url(url)
    if not product_id:
        return {"success": False, "error": f"无法解析 Ozon URL: {url}"}

    # Extract slug
    m = re.search(r"/product/(.+?)-(\d{6,20})", url)
    slug = m.group(1).replace("-", " ") if m else ""

    result: dict[str, Any] = {
        "success": False,
        "product_id": product_id,
        "slug": slug,
        "images": [],
        "title": "",
        "category": "",
        "price": "",
        "error": None,
    }

    try:
        status_code, html = _fetch_page(url, proxy=proxy, timeout=timeout)

        if status_code == 403:
            # Check if anti-bot blocked
            if "ozon-antibot" in str(html).lower() or "datadome" in str(html).lower():
                result["error"] = "Ozon 反爬拦截 (DataDome)。需要俄罗斯代理或更换 IP。"
            else:
                result["error"] = f"Ozon 返回 403 (已拦截)"
            return result

        if status_code != 200:
            result["error"] = f"HTTP {status_code}"
            return result

        # Extract data from multiple sources
        json_ld_data = _extract_from_json_ld(html)
        ssr_data = _extract_from_ssr_state(html)
        html_images = _extract_images_from_html(html)

        # Merge: JSON-LD is most reliable, fallback to SSR, then HTML patterns
        result["title"] = json_ld_data.get("title") or ssr_data.get("title") or ""
        result["category"] = json_ld_data.get("category", "")
        result["price"] = json_ld_data.get("price", "")
        result["currency"] = json_ld_data.get("currency", "RUB")

        # Images: prefer JSON-LD, then SSR
        images = json_ld_data.get("images", [])
        if not images:
            images = ssr_data.get("images", [])
        if not images:
            images = html_images
        result["images"] = images

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


def scrape_ozon_product_via_cdp(
    ozon_url: str,
    cdp_url: str = "http://127.0.0.1:9222",
    timeout: int = 30,
) -> dict[str, Any]:
    """Scrape Ozon product page via existing CDP Chrome (local testing).

    Requires a Chrome browser running with --remote-debugging-port=9222
    and already logged into Ozon.  Bypasses anti-bot via real browser session.

    Returns same dict as scrape_ozon_product().
    """
    from scripts.lib.config_store import _require_auth
    _require_auth()
    import requests as req_lib
    import websocket
    import time
    import json as _json

    product_id = _parse_product_id_from_url(ozon_url)
    if not product_id:
        return {"success": False, "error": f"无法解析 Ozon URL: {ozon_url}"}

    m = re.search(r"/product/(.+?)-(\d{6,20})", ozon_url)
    slug = m.group(1).replace("-", " ") if m else ""

    result: dict[str, Any] = {
        "success": False, "product_id": product_id, "slug": slug,
        "images": [], "title": "", "category": "", "price": "", "currency": "RUB",
        "description": "", "attributes": {}, "breadcrumbs": [], "hashtags": [],
        "sku": "",
        "description_category_id": "", "type_id": "",  # Ozon 类目 ID（从面包屑提取）
        "error": None,
    }

    def _cdp_eval(ws, expr: str, wait_ms: int = 0, await_promise: bool = False) -> str:
        """Send a Runtime.evaluate and return the result value."""
        import time as _t
        if wait_ms:
            _t.sleep(wait_ms / 1000.0)
        if not hasattr(_cdp_eval, '_counter'):
            _cdp_eval._counter = 0
        _cdp_eval._counter += 1
        cid = 10000 + _cdp_eval._counter
        params = {"expression": expr, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        ws.send(_json.dumps({
            "id": cid, "method": "Runtime.evaluate",
            "params": params,
        }))
        deadline = _t.time() + 15
        while _t.time() < deadline:
            try:
                ws.settimeout(3)
                msg = _json.loads(ws.recv())
                if msg.get("id") == cid:
                    return msg.get("result", {}).get("result", {}).get("value", "")
            except Exception:
                continue
        return ""

    try:
        # Check CDP availability
        version_resp = req_lib.get(f"{cdp_url}/json/version", timeout=5)
        if version_resp.status_code != 200:
            result["error"] = "CDP Chrome 未运行"
            return result

        # ✅ v0.10: 优先复用已有 ozon.ru tab（保留 cookie/session，避免 DataDome）
        tab = None
        tab_id = ""
        ws_url = ""
        tab_is_new = False
        try:
            tabs_resp = req_lib.get(f"{cdp_url}/json", timeout=5)
            if tabs_resp.status_code == 200:
                for t in tabs_resp.json():
                    if t.get("type") == "page" and "ozon.ru" in t.get("url", ""):
                        tab_id = t.get("id", "")
                        ws_url = t.get("webSocketDebuggerUrl", "")
                        if tab_id and ws_url:
                            tab = t
                            logger.info("复用已有 Ozon tab: %s", t.get("url", "")[:80])
                            break
        except Exception:
            pass

        if not tab:
            # 找不到已有 tab，创建新的
            blank_resp = req_lib.put(f"{cdp_url}/json/new?", timeout=10)
            if blank_resp.status_code != 200:
                result["error"] = "无法创建 CDP 标签页"
                return result
            tab = blank_resp.json()
            tab_id = tab.get('id', '')
            ws_url = tab.get("webSocketDebuggerUrl", "")
            tab_is_new = True

        if not ws_url:
            result["error"] = "无法获取 CDP WebSocket URL"
            return result

        # Connect and navigate
        ws = websocket.create_connection(ws_url, timeout=timeout)
        try:
            ws.send(_json.dumps({"id": 1, "method": "Page.enable", "params": {}}))
            ws.send(_json.dumps({
                "id": 2, "method": "Page.navigate",
                "params": {"url": ozon_url}
            }))

            # Drain navigation response and wait for page load (event-driven, 10s timeout)
            _load_deadline = time.time() + 10
            while time.time() < _load_deadline:
                try:
                    ws.settimeout(1)
                    _msg = _json.loads(ws.recv())
                    if _msg.get('method') in ('Page.loadEventFired', 'Page.frameStoppedLoading'):
                        break
                except Exception:
                    continue

            # Extract via JavaScript (most reliable)
            js_title = _cdp_eval(ws, "document.title") or ""
            js_jsonld = _cdp_eval(ws, """
                (function() {
                    var s = document.querySelector('script[type=\"application/ld+json\"]');
                    return s ? s.textContent : '';
                })()
            """) or ""

            js_jsonld = js_jsonld.strip()

            # Parse JSON-LD
            if js_jsonld:
                try:
                    ld_data = _json.loads(js_jsonld)
                    if ld_data.get("@type") == "Product":
                        result["title"] = ld_data.get("name", "")
                        result["price"] = str(ld_data.get("offers", {}).get("price", ""))
                        # Main image from JSON-LD
                        img = ld_data.get("image", "")
                        if isinstance(img, list):
                            result["images"] = img
                        elif img:
                            result["images"] = [img]
                except (_json.JSONDecodeError, TypeError):
                    pass

            # Fallback title from page title
            if not result["title"] and js_title:
                # Strip Ozon suffix: "купить на OZON по низкой цене (1234567890)"
                import re as _re
                result["title"] = _re.sub(
                    r"\s*купить\s+на\s+OZON.*$", "", js_title, flags=_re.IGNORECASE
                ).strip()

            # Get breadcrumb (category)
            js_breadcrumb = _cdp_eval(ws, """
                (function() {
                    var items = document.querySelectorAll('[data-widget=\"breadcrumb\"] a, nav[aria-label=\"breadcrumb\"] a, .breadcrumb a');
                    return Array.from(items).map(function(a) { return a.textContent.trim(); }).join(' > ');
                })()
            """) or ""
            if js_breadcrumb:
                result["category"] = js_breadcrumb

            # Extract product data from Ozon internal API (via CDP fetch)
            slug_for_api = slug.replace(" ", "-")
            api_url = f"/api/entrypoint-api.bx/page/json/v2?url=%2Fproduct%2F{slug_for_api}-{product_id}%2F"
            js_api = f'''
            (async () => {{
                try {{
                    const resp = await fetch("{api_url}");
                    const data = await resp.json();
                    const widgets = data.widgetStates || data.widgetState || {{}};
                    const out = {{chars: [], breadcrumbs: [], hashtags: []}};
                    for (const [k, v] of Object.entries(widgets)) {{
                        if (k.includes("webShortCharacteristics")) {{
                            try {{
                                const parsed = JSON.parse(v);
                                out.chars = (parsed.characteristics || []).map(c => ({{
                                    title: c.title?.textRs?.[0]?.content || "",
                                    value: c.values?.[0]?.text || ""
                                }}));
                            }} catch {{}}
                        }}
                        if (k.includes("breadCrumbs")) {{
                            try {{
                                const parsed = JSON.parse(v);
                                out.breadcrumbs = (parsed.breadcrumbs || []).map(b => ({{
                                    text: b.text || "",
                                    link: b.link || "",
                                    crumbType: b.crumbType || ""
                                }}));
                            }} catch {{}}
                        }}
                        if (k.includes("webHashtags")) {{
                            try {{
                                const parsed = JSON.parse(v);
                                const tags = parsed.hashtags || parsed.tags || [];
                                out.hashtags = tags.map(t => t.text || t.title || t).filter(Boolean);
                            }} catch {{
                                // 从DOM提取hashtags
                                try {{
                                    const el = document.querySelector("[data-widget=webHashtags]");
                                    if (el) {{
                                        const titles = el.querySelectorAll("[title]");
                                        out.hashtags = Array.from(titles).map(t => t.getAttribute("title")).filter(Boolean);
                                    }}
                                }} catch {{}}
                            }}
                        }}
                    }}
                    return JSON.stringify(out);
                }} catch(e) {{ return JSON.stringify({{error: e.message}}); }}
            }})()
            '''
            api_result = _cdp_eval(ws, js_api, await_promise=True)
            if api_result:
                try:
                    api_data = _json.loads(api_result)
                    # Attributes
                    if api_data.get("chars"):
                        attrs = {}
                        for c in api_data["chars"]:
                            if c.get("title") and c.get("value"):
                                attrs[c["title"]] = c["value"]
                        result["attributes"] = attrs
                    # Breadcrumbs with links (for category ID extraction)
                    if api_data.get("breadcrumbs"):
                        crumbs = []
                        for b in api_data["breadcrumbs"]:
                            link = b.get("link", "")
                            text = b.get("text", "")
                            crumb_type = b.get("crumbType", "")
                            # 从链接提取category ID: /category/xxx-14500/ → 14500
                            cat_id = ""
                            if link:
                                import re as _re
                                m = _re.search(r"-(\d+)/?$", link)
                                if m:
                                    cat_id = m.group(1)
                            crumbs.append({"text": text, "link": link, "category_id": cat_id, "crumbType": crumb_type})
                        result["breadcrumbs"] = crumbs
                        # ✅ v0.11: 使用 crumbType 区分真实类目 vs 品牌筛选页
                        # CRUMB_TYPE_FULL_LINK = 真实类目, 其他(品牌/搜索等) = 跳过
                        if crumbs:
                            result["category"] = " > ".join(c["text"] for c in crumbs if c["text"])
                            category_path = result["category"]

                            # 从后往前找第一个 crumbType = CRUMB_TYPE_FULL_LINK（非品牌）的面包屑
                            # 降级：如果没有 crumbType，用老方法（/category/ 出现次数=1）
                            valid = [
                                c for c in crumbs
                                if c.get("crumbType", "").startswith("CRUMB_TYPE_FULL")
                                or (not c.get("crumbType") and c.get("link", "").count("/category/") == 1)
                            ]
                            if valid:
                                best = valid[-1]  # 最具体的有效类目
                                result["description_category_id"] = best.get("category_id", "")  # 数字 ID
                                result["type_id"] = best.get("category_id", "")  # Worker 负责查真正 type_id
                                result["category_path"] = category_path  # 文本降级
                            else:
                                # 全是品牌页？保留文本路径降级
                                result["description_category_id"] = category_path
                                result["type_id"] = ""

                            # 语言检测：Cyrillic → RU，中文 → ZH_HANS
                            if any('\u4e00' <= c <= '\u9fff' for c in category_path):
                                result["breadcrumb_language"] = "ZH_HANS"
                            elif any('\u0400' <= c <= '\u04FF' for c in category_path):
                                result["breadcrumb_language"] = "RU"
                except (_json.JSONDecodeError, TypeError):
                    pass

            # Extract hashtags from DOM (not in API response)
            js_hashtags = _cdp_eval(ws, """
                (function() {
                    var el = document.querySelector('[data-widget="webHashtags"]');
                    if (!el) return '';
                    var tags = el.querySelectorAll('[title]');
                    return Array.from(tags).map(function(t) { return t.getAttribute('title'); }).filter(Boolean).join(',');
                })()
            """) or ""
            if js_hashtags:
                result["hashtags"] = [h.strip() for h in js_hashtags.split(",") if h.strip()]

            # Extract description from JSON-LD (real product description, not marketing)
            # JSON-LD description contains actual product specs (capacity, material, size, etc.)
            # Meta description is Ozon's generic marketing text - NOT useful
            js_desc = _cdp_eval(ws, """
                (function() {
                    var s = document.querySelector('script[type="application/ld+json"]');
                    if (s) {
                        try {
                            var d = JSON.parse(s.textContent);
                            return d.description || '';
                        } catch(e) {}
                    }
                    // Fallback: description widget
                    var el = document.querySelector('[data-widget="webDescription"]');
                    return el ? el.innerText.substring(0, 500) : '';
                })()
            """) or ""
            if js_desc and len(js_desc) > 20:
                result["description"] = js_desc[:500]

            # Augment images from HTML (more sizes/angles)
            # Get full HTML and extract image URLs
            html_val = _cdp_eval(ws, "document.documentElement.outerHTML") or ""
            if html_val:
                html_images = _extract_images_from_html(html_val)
                # Deduplicate and merge
                existing = set(result["images"])
                for img in html_images:
                    # Keep only unique image IDs (different photos, not same photo different sizes)
                    base = re.sub(r'/wc\d+/', '/', img)  # normalize: remove size prefix
                    if base not in existing:
                        existing.add(base)
                        result["images"].append(img)

            result["success"] = bool(result["title"] or result["images"])
        finally:
            ws.close()
            # ✅ v0.10: 只关闭新创建的 tab，保留复用的已有 tab
            if tab_id and tab_is_new:
                try:
                    req_lib.get(f"{cdp_url}/json/close/{tab_id}", timeout=3)
                except Exception:
                    pass

    except ImportError as e:
        result["error"] = f"缺少依赖: {e}. pip install websocket-client"
    except Exception as e:
        result["error"] = f"CDP 抓取异常: {e}"

    return result

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


def _pick_category_from_crumbs(crumbs: list[dict]) -> dict | None:
    """从面包屑列表里挑出真正的 Ozon 类目 crumb（v0.19.1）。

    规则：只认链接含 `/category/` 且能提取到数字 ID 的 crumb，取最具体（最后一个）。
    品牌页链接含 `/brand/`（crumbType 同为 CRUMB_TYPE_FULL_LINK，会误判——甩脂机
    取到品牌 Luxhommè 实锤），必须按链接形态过滤。
    无 `/category/` 链接时兼容旧逻辑（crumbType=CRUMB_TYPE_FULL_*）。
    """
    valid = [
        c for c in crumbs
        if "/category/" in str(c.get("link", "")) and c.get("category_id")
    ]
    if not valid:
        valid = [
            c for c in crumbs
            if str(c.get("crumbType", "")).startswith("CRUMB_TYPE_FULL")
            and c.get("category_id")
            and "/brand/" not in str(c.get("link", ""))  # 品牌页一律排除（v0.19.1）
        ]
    return valid[-1] if valid else None


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
    conn=None,
) -> dict[str, Any]:
    """Scrape Ozon product page via existing CDP Chrome (local testing).

    Requires a Chrome browser running with --remote-debugging-port=9222
    and already logged into Ozon.  Bypasses anti-bot via real browser session.

    ⚠️ v0.14 E4/E5: 用 cdp_client 封装替代手写 websocket/CDP；
    ``conn`` 可复用外部 CdpConnection（E5 follow_sell_cloud 共享连接）。

    Returns same dict as scrape_ozon_product().
    """
    from scripts.lib.config_store import _require_auth
    _require_auth()
    import requests as req_lib
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

    def _tab_eval(tab, expr: str, wait_ms: int = 0, await_promise: bool = False) -> str:
        """Runtime.evaluate via CdpTab（wait_ms 前置 sleep，await_promise 透传）"""
        import time as _t
        if wait_ms:
            _t.sleep(wait_ms / 1000.0)
        try:
            val = tab.evaluate(expr, await_promise=await_promise, timeout=15)
            return str(val) if val is not None else ""
        except Exception:
            return ""

    # ⚠️ v0.14 E4: 用 CdpConnection/CdpTab 统一封装
    # - 复用已有 ozon.ru tab（保留 cookie/session，避免 DataDome），find_tab 命中后 release 防止 conn.close() 误关
    # - 新建 tab 由本函数显式关闭
    from scripts.lib.cdp_client import CdpConnection
    own_conn = conn is None
    tab = None
    tab_is_new = False
    try:
        if own_conn:
            conn = CdpConnection(cdp_url)

        # Check CDP availability
        version_resp = req_lib.get(f"{cdp_url}/json/version", timeout=5)
        if version_resp.status_code != 200:
            result["error"] = "CDP Chrome 未运行"
            return result

        # ✅ v0.10: 优先复用已有 ozon.ru tab（保留 cookie/session，避免 DataDome）
        # ⚠️ v0.14 E4: find_tab 失败降级 new_tab（与旧逻辑一致，find 异常不阻断）
        try:
            tab = conn.find_tab("ozon.ru")
            if tab:
                logger.info("复用已有 Ozon tab")
                conn.release(tab)  # 用户已有 tab → 不随 conn.close() 被远程关闭
        except Exception:
            tab = None
        if tab is None:
            tab = conn.new_tab()
            tab_is_new = True

        # Navigate and wait for load (event-driven)
        tab.navigate(ozon_url, wait_until="load", timeout=timeout)

        # ✅ v0.19: 多段滚动触发懒加载（图片画廊/描述/评价区），尽可能获取更多信息
        for _scroll_pass in range(3):
            _tab_eval(tab, "window.scrollTo(0, document.body.scrollHeight);", wait_ms=600)
        _tab_eval(tab, "window.scrollTo(0, 0);", wait_ms=300)

        # Extract via JavaScript (most reliable)
        js_title = _tab_eval(tab, "document.title") or ""
        js_jsonld = _tab_eval(tab, """
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
        js_breadcrumb = _tab_eval(tab, """
            (function() {
                var items = document.querySelectorAll('[data-widget=\"breadcrumb\"] a, nav[aria-label=\"breadcrumb\"] a, .breadcrumb a');
                return Array.from(items).map(function(a) { return a.textContent.trim(); }).join(' > ');
            })()
        """) or ""
        if js_breadcrumb:
            result["category"] = js_breadcrumb

        # ✅ v0.19.1: 提取产品数据（entrypoint API via CDP fetch）
        # 1) 纯数字 ID 优先（插件实证稳定返回 breadCrumbs；slug 版偶发 widget 缺失）
        # 2) breadCrumbs 缺失自动回退 slug 版本
        # 3) 同时解析评分/评论/卖家/提问/跟卖（P1 信息补全）
        slug_for_api = slug.replace(" ", "-")
        api_url_num = f"/api/entrypoint-api.bx/page/json/v2?url=%2Fproduct%2F{product_id}%2F"
        api_url_slug = f"/api/entrypoint-api.bx/page/json/v2?url=%2Fproduct%2F{slug_for_api}-{product_id}%2F"
        js_api = f'''
        (async () => {{
            try {{
                const urls = ["{api_url_num}", "{api_url_slug}"];
                let data = null;
                for (const u of urls) {{
                    try {{
                        const resp = await fetch(u);
                        const d = await resp.json();
                        const ws = d.widgetStates || d.widgetState || {{}};
                        if (Object.keys(ws).some(k => k.includes("breadCrumbs"))) {{
                            data = d; break;
                        }}
                        data = data || d;
                    }} catch(e) {{}}
                }}
                if (!data) return JSON.stringify({{error: "entrypoint fetch failed"}});
                const widgets = data.widgetStates || data.widgetState || {{}};
                const out = {{chars: [], breadcrumbs: [], hashtags: [],
                             rating: "", reviewCount: 0, seller: "", questionCount: 0,
                             sellerCount: 0, minPrice: ""}};
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
                    // ✅ v0.19.1 P1: 评分/评论数
                    if (k.includes("webReviewProductScore")) {{
                        try {{
                            const parsed = JSON.parse(v);
                            out.rating = parsed.score || parsed.totalScore || "";
                            out.reviewCount = parsed.reviewsCount || 0;
                        }} catch {{}}
                    }}
                    // ✅ v0.19.1 P1: 当前卖家（名称/评分）
                    if (k.includes("webCurrentSeller")) {{
                        try {{
                            const parsed = JSON.parse(v);
                            const s = parsed.seller || parsed;
                            out.seller = (s.title || s.name || s.brandName || "") + (s.score ? ("|" + s.score) : "");
                        }} catch {{}}
                    }}
                    // ✅ v0.19.1 P1: 提问数
                    if (k.includes("webQuestionCount")) {{
                        try {{
                            const parsed = JSON.parse(v);
                            out.questionCount = parsed.count || 0;
                        }} catch {{}}
                    }}
                    // ✅ v0.19.1 P1: 跟卖列表（卖家数/最低价）
                    if (k.includes("webSellerList")) {{
                        try {{
                            const parsed = JSON.parse(v);
                            const sellers = parsed.sellers || [];
                            out.sellerCount = sellers.length;
                            let min = 0;
                            for (const s of sellers) {{
                                const p = (s.price && s.price.cardPrice && s.price.cardPrice.price)
                                    || (s.price && s.price.price) || 0;
                                if (p && (!min || p < min)) min = p;
                            }}
                            out.minPrice = min || "";
                        }} catch {{}}
                    }}
                }}
                return JSON.stringify(out);
            }} catch(e) {{ return JSON.stringify({{error: e.message}}); }}
        }})()
        '''
        api_result = _tab_eval(tab, js_api, await_promise=True)
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
                # ✅ v0.19.1 P1: 评分/评论/卖家/提问/跟卖（可选字段，契约兼容）
                if api_data.get("rating"):
                    result["ozon_rating"] = api_data.get("rating")
                if api_data.get("reviewCount"):
                    result["ozon_reviews"] = api_data.get("reviewCount")
                if api_data.get("seller"):
                    result["ozon_seller"] = api_data.get("seller")
                if api_data.get("questionCount"):
                    result["ozon_questions"] = api_data.get("questionCount")
                if api_data.get("sellerCount"):
                    result["competitor_sellers"] = api_data.get("sellerCount")
                if api_data.get("minPrice"):
                    result["competitor_min_price"] = api_data.get("minPrice")
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

                        # ✅ v0.19.1: 只认 /category/ 链接的类目 crumb（品牌页 /brand/ 排除）
                        best = _pick_category_from_crumbs(crumbs)
                        if best:
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
        js_hashtags = _tab_eval(tab, """
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
        js_desc = _tab_eval(tab, """
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
        html_val = _tab_eval(tab, "document.documentElement.outerHTML") or ""
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

    except ImportError as e:
        result["error"] = f"缺少依赖: {e}. pip install websocket-client"
    except Exception as e:
        result["error"] = f"CDP 抓取异常: {e}"
    finally:
        # ⚠️ v0.14 E4: 封装统一收尾
        # - 新建 tab → 全关（WS + 远程 tab）
        # - 复用的用户已有 tab → 只关 WS（保留用户标签页）
        # - own_conn（本函数自建连接）→ conn.close() 收尾（tab 已 release，不会误关用户 tab）
        try:
            if tab and tab_is_new:
                tab.close(close_remote=True)
            elif tab:
                tab.close(close_remote=False)
        except Exception:
            pass
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    return result

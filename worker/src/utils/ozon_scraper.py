"""
Ozon Product Page Scraper — Worker-side (cloud deployment).

Requires: curl_cffi (Chrome TLS impersonation)
Optional: OZON_SCRAPER_PROXY env var (Russian residential proxy URL)

Scrapes public Ozon product pages for images, title, and category.
Deployed on Worker server — Skill users don't need proxies.
"""
from __future__ import annotations

import json
import logging
import os
import random as _random
import re
from typing import Any

logger = logging.getLogger(__name__)

OZON_PRODUCT_URL_RE = re.compile(
    r"ozon\.ru/product/(.+?)-(\d{6,20})", re.IGNORECASE
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _extract_from_json_ld(html: str) -> dict[str, Any]:
    result: dict[str, Any] = {"images": [], "title": "", "category": "", "price": ""}
    json_ld_matches = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    for ld_str in json_ld_matches:
        try:
            data = json.loads(ld_str)
            if not isinstance(data, dict):
                continue
            if data.get("@type") == "Product":
                result["title"] = data.get("name", "")
                images = data.get("image", [])
                if isinstance(images, str):
                    images = [images]
                result["images"] = [img for img in images if isinstance(img, str)]
                offers = data.get("offers", {})
                if isinstance(offers, dict):
                    result["price"] = str(offers.get("price", ""))
            elif data.get("@type") == "BreadcrumbList":
                items = data.get("itemListElement", [])
                cats = []
                for item in items:
                    if isinstance(item, dict) and "item" in item:
                        name = item["item"].get("name", "")
                        if name:
                            cats.append(name)
                result["category"] = " > ".join(cats)
        except (json.JSONDecodeError, TypeError):
            continue
    return result


def _extract_images_from_html(html: str) -> list[str]:
    patterns = [
        r'https://ir\.ozone\.ru/s3/[^\"\'\\s<>]+\.(?:jpg|jpeg|png|webp)',
        r'https://cdn\.ozon\.ru/[^\"\'\\s<>]+\.(?:jpg|jpeg|png|webp)',
    ]
    images: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for img in re.findall(pat, html, re.IGNORECASE):
            if img not in seen:
                seen.add(img)
                images.append(img)
    return images


def scrape_ozon_product(
    url: str,
    proxy: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Scrape an Ozon product page.

    Args:
        url: Ozon product page URL
        proxy: Optional proxy URL (e.g. "http://user:pass@proxy.ru:8080")
        timeout: Request timeout in seconds

    Returns:
        {success, product_id, slug, images[], title, category, price, error}
    """
    from curl_cffi import requests

    m = OZON_PRODUCT_URL_RE.search(url)
    if not m:
        return {"success": False, "error": f"无法解析 Ozon URL: {url}"}

    product_id = m.group(2)
    slug = m.group(1).replace("-", " ") if m.group(1) else ""

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

        resp = requests.get(url, **kwargs)

        if resp.status_code == 403:
            if "ozon-antibot" in resp.text.lower() or "datadome" in resp.text.lower():
                result["error"] = "Ozon 反爬拦截 (DataDome)。请配置 OZON_SCRAPER_PROXY 俄罗斯代理。"
            else:
                result["error"] = "Ozon 返回 403"
            return result

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        html = resp.text

        json_ld_data = _extract_from_json_ld(html)
        html_images = _extract_images_from_html(html)

        result["title"] = json_ld_data.get("title", "")
        result["category"] = json_ld_data.get("category", "")
        result["price"] = json_ld_data.get("price", "")
        result["images"] = json_ld_data.get("images", []) or html_images
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result

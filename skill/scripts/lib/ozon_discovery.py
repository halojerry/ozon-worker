"""Ozon product discovery -- find profitable products from China highlight page.

Flow:
1. CDP browse Ozon highlight page (中国商品 / tovary-iz-kitaya)
2. Scroll to load products
3. For each product:
   a. Fetch product info + competing sellers via widget API
   b. Image search on 1688 for matching source
   c. Calculate profit margin
4. Filter: profit > threshold, competing sellers < max
5. Cache results to JSON log
6. Return sorted by profit margin
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

    # Status: pending, matched, profitable, rejected, no_match, error
    status: str = "pending"
    error: str = ""


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
    commission_pct: float = DEFAULT_COMMISSION_PCT,
) -> list[ProductCandidate]:
    """Browse Ozon China highlight page and discover profitable products.

    Args:
        cdp_url: Chrome CDP debug URL (e.g. http://127.0.0.1:9222)
        max_products: Maximum products to analyze per run.
        fx_rate: RUB to CNY exchange rate.
        min_margin_pct: Minimum profit margin (%) to keep a product.
        max_competitors: Skip products with more competing sellers.
        logistics_cny: Estimated logistics cost in CNY per item.
        commission_pct: Ozon commission as a fraction (0.10 = 10%).

    Returns:
        List of profitable ProductCandidate objects, sorted by margin descending.
    """
    from scripts.lib.cdp_client import CdpConnection
    from scripts.lib.ozon_widget import (
        extract_product_id,
        fetch_competing_sellers,
        fetch_product_info,
    )

    candidates: list[ProductCandidate] = []

    with CdpConnection(cdp_url) as cdp:
        # 1. Open the China highlight page
        logger.info("Opening Ozon China highlight page: %s", CHINA_HIGHLIGHT_URL)
        tab = cdp.new_tab(CHINA_HIGHLIGHT_URL)
        time.sleep(5)  # initial page load

        # 2. Scroll and collect product URLs
        product_urls = _scroll_and_collect_urls(tab, max_products)
        logger.info("Collected %d product URLs from highlight page", len(product_urls))

        # 3. Analyze each product
        for i, url in enumerate(product_urls):
            pid = extract_product_id(url)
            if not pid:
                logger.debug("Skipping URL with no product ID: %s", url)
                continue

            candidate = ProductCandidate(
                ozon_product_id=pid,
                ozon_title="",
                ozon_price=0.0,
                ozon_images=[],
                ozon_url=url,
            )

            logger.info("[%d/%d] Analyzing product %s ...", i + 1, len(product_urls), pid)

            try:
                # Fetch product info via widget API
                info = fetch_product_info(cdp_url, pid)
                candidate.ozon_title = info.get("title", "")
                candidate.ozon_price = _parse_price(info.get("price", ""))
                candidate.ozon_images = info.get("images", [])

                if not candidate.ozon_title:
                    logger.debug("Product %s: no title, skipping", pid)
                    candidate.status = "error"
                    candidate.error = "no title returned"
                    candidates.append(candidate)
                    continue

                # Fetch competing sellers
                sellers = fetch_competing_sellers(cdp_url, pid)
                candidate.competing_sellers = sellers.get("count", 0)
                candidate.min_competing_price = sellers.get("min_price", 0)

                # Skip if too many competitors
                if candidate.competing_sellers > max_competitors:
                    logger.info("Product %s: too many competitors (%d > %d), skipping",
                                pid, candidate.competing_sellers, max_competitors)
                    candidate.status = "rejected"
                    candidate.error = f"too many competitors ({candidate.competing_sellers})"
                    candidates.append(candidate)
                    continue

                # Search 1688 for matching source
                match = _search_1688_source(
                    cdp_url, candidate.ozon_images, candidate.ozon_title
                )
                if match:
                    candidate.match_1688_url = match.get("url", "")
                    candidate.match_1688_title = match.get("title", "")
                    candidate.match_1688_price = float(match.get("price", 0))
                    candidate.match_1688_images = match.get("images", [])
                    candidate.status = "matched"

                    # Calculate profit
                    _calculate_profit(
                        candidate,
                        fx_rate=fx_rate,
                        logistics_cny=logistics_cny,
                        commission_pct=commission_pct,
                    )

                    if candidate.profit_margin >= min_margin_pct:
                        candidate.status = "profitable"
                        logger.info(
                            "Product %s: PROFITABLE (margin=%.1f%%, 1688=%.1f CNY, ozon=%.0f RUB)",
                            pid, candidate.profit_margin,
                            candidate.match_1688_price, candidate.ozon_price,
                        )
                    else:
                        candidate.status = "rejected"
                        logger.debug(
                            "Product %s: margin too low (%.1f%% < %.1f%%)",
                            pid, candidate.profit_margin, min_margin_pct,
                        )
                else:
                    candidate.status = "no_match"
                    logger.debug("Product %s: no 1688 match found", pid)

            except Exception as exc:
                candidate.status = "error"
                candidate.error = str(exc)
                logger.warning("Product %s analysis failed: %s", pid, exc)

            candidates.append(candidate)

            # Brief pause between products to avoid rate limiting
            time.sleep(1)

        # Close the highlight page tab
        try:
            tab.close()
        except Exception:
            pass

    # 4. Cache all results (including rejected/errors for audit)
    _save_discovery_log(candidates)

    # 5. Return only profitable ones, sorted by margin descending
    profitable = [c for c in candidates if c.status == "profitable"]
    profitable.sort(key=lambda c: c.profit_margin, reverse=True)
    logger.info(
        "Discovery complete: %d total, %d profitable",
        len(candidates), len(profitable),
    )
    return profitable


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scroll_and_collect_urls(tab: Any, max_products: int) -> list[str]:
    """Scroll the page and collect unique product URLs.

    Uses CDP evaluate to query the DOM for product links, then scrolls
    down to trigger lazy loading.  Repeats until we have enough URLs or
    hit the scroll limit.
    """
    urls: set[str] = set()
    max_scroll_iterations = 20

    for iteration in range(max_scroll_iterations):
        # Extract product links from the current DOM
        new_urls_raw = tab.evaluate(r'''(() => {
            return [...document.querySelectorAll('a[href*="/product/"]')]
                .map(a => a.href.split('?')[0])
                .filter(h => h.match(/-\d{5,}\/?$/) || h.match(/\/product\/\d{5,}\/?$/))
                .filter((v, i, a) => a.indexOf(v) === i);
        })()''')

        if isinstance(new_urls_raw, list):
            for u in new_urls_raw:
                if isinstance(u, str) and "/product/" in u:
                    urls.add(u.split("?")[0])  # strip query params

        if len(urls) >= max_products:
            break

        # Scroll to bottom to load more
        tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2.5)

        # Check if we've reached the end (no new content)
        height_after = tab.evaluate("document.body.scrollHeight")
        time.sleep(0.5)
        height_check = tab.evaluate("document.body.scrollHeight")
        if height_after == height_check and iteration > 2:
            logger.debug("Scroll height unchanged at iteration %d, stopping", iteration)
            break

    result = list(urls)[:max_products]
    return result


def _parse_price(price_str: str) -> float:
    """Parse price string like '327 \u20bd' or '1 299,99 \u20bd' to float.

    Handles Russian number formatting (space as thousands separator,
    comma as decimal separator).
    """
    if not price_str:
        return 0.0
    # Remove currency symbols and whitespace, normalize
    cleaned = re.sub(r"[^\d,.]", "", str(price_str))
    # Russian format: 1 299,99 -> 1299.99
    cleaned = cleaned.replace(",", ".")
    # Remove any remaining spaces (thousands separator)
    cleaned = cleaned.replace(" ", "")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


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
    commission_pct: float = DEFAULT_COMMISSION_PCT,
) -> None:
    """Calculate profit margin for a candidate.

    Updates candidate fields in-place:
      estimated_logistics_cny, estimated_commission,
      estimated_profit_cny, profit_margin
    """
    if not candidate.match_1688_price or not candidate.ozon_price:
        return

    cost_cny = candidate.match_1688_price
    revenue_cny = candidate.ozon_price * fx_rate

    if revenue_cny <= 0:
        return

    # Logistics estimate
    candidate.estimated_logistics_cny = logistics_cny

    # Commission
    candidate.estimated_commission = revenue_cny * commission_pct

    total_cost = cost_cny + candidate.estimated_logistics_cny + candidate.estimated_commission
    candidate.estimated_profit_cny = revenue_cny - total_cost
    candidate.profit_margin = (candidate.estimated_profit_cny / revenue_cny) * 100.0


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

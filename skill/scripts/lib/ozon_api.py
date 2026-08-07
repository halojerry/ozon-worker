"""Thin Ozon REST API client — inlined from pounding-ozon-cloud.

This module replaces the pounding-ozon-cloud dependency.  Only the functions
actually used by cloud_client.py are ported; the heavier cloud-only modules
(COS, Windmill, orchestration, attribute resolution, etc.) are not needed here.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.lib.logging_utils import AuditLogger

logger = logging.getLogger(__name__)

OZON_BASE_URL = "https://api-seller.ozon.ru"

# ---------------------------------------------------------------------------
# Custom exception (replaces pounding_ozon_cloud.domain.OzonApiError)
# ---------------------------------------------------------------------------


class OzonApiError(RuntimeError):
    """Raised when Ozon API calls fail."""


# ---------------------------------------------------------------------------
# Session pool — reduces TLS handshake overhead for repeated API calls
# ---------------------------------------------------------------------------

_ozon_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _ozon_session
    if _ozon_session is None:
        s = requests.Session()
        retries = Retry(total=2, backoff_factor=0.5, allowed_methods={"POST"})
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        s.mount("https://", adapter)
        _ozon_session = s
    return _ozon_session


def _ozon_headers(client_id: str, api_key: str) -> dict[str, str]:
    if not client_id or not api_key:
        raise OzonApiError("缺少 Ozon 凭证，无法访问 Ozon API")
    return {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }


def _post(
    client_id: str, api_key: str, path: str, body: dict[str, Any], timeout: int = 20
) -> dict[str, Any]:
    response = _get_session().post(
        f"{OZON_BASE_URL}{path}",
        headers=_ozon_headers(client_id, api_key),
        json=body,
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise OzonApiError(f"Ozon 返回非 JSON: {response.text[:500]}") from exc
    if response.status_code >= 400:
        raise OzonApiError(f"Ozon API 请求失败 ({response.status_code}): {payload}")
    return payload


# ---------------------------------------------------------------------------
# Private helpers (only used internally by the public functions below)
# ---------------------------------------------------------------------------


def _query_category_tree(
    client_id: str, api_key: str, language: str = "ZH_HANS"
) -> list[dict[str, Any]]:
    # ⚠️ v0.14 C1: 类目树 TTL 缓存（24h）— 旧代码每次搜索都重拉整棵 ~2-5s 的树
    # 复用 scripts.lib.cache 命名空间缓存，按 language 缓存（中/俄各一棵）
    try:
        from scripts.lib.cache import cache_get, cache_set
        cache_key = f"category_tree_{language}"
        cached = cache_get("category_tree", cache_key)
        if cached is not None:
            return cached
    except Exception:
        pass

    payload = _post(
        client_id, api_key, "/v1/description-category/tree", {"language": language}
    )
    tree = list(payload.get("result") or [])

    try:
        from scripts.lib.cache import cache_set
        cache_set("category_tree", f"category_tree_{language}", tree, ttl=86400)
    except Exception:
        pass
    return tree


def _get_import_info(
    client_id: str, api_key: str, task_id: str
) -> dict[str, Any]:
    return _post(
        client_id, api_key,
        "/v1/product/import/info",
        {"task_id": int(task_id)},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Public API — the 8 functions used by cloud_client.py
# ---------------------------------------------------------------------------


def _walk_single(
    nodes: list[dict[str, Any]],
    query_lower: str,
    results: list[dict[str, Any]],
    parent_desc_cat_id: int | None = None,
    parent_name: str = "",
) -> None:
    """Walk the category tree with a single-word query (used as tiebreaker fallback)."""
    for node in nodes:
        if node.get("disabled"):
            children = node.get("children") or []
            if children:
                _walk_single(children, query_lower, results,
                             node.get("description_category_id") or parent_desc_cat_id,
                             node.get("category_name", ""))
            continue

        desc_cat_id = node.get("description_category_id")
        type_id = node.get("type_id")
        category_name = node.get("category_name", "") or ""
        type_name = node.get("type_name", node.get("category_name", "")) or ""
        children = node.get("children") or []
        name_to_search = (type_name or category_name or "").lower()

        if query_lower in name_to_search:
            if name_to_search == query_lower:
                score = 0
            elif name_to_search.startswith(query_lower):
                score = 5
            else:
                score = 10

            if type_id:
                results.append({
                    "description_category_id": desc_cat_id or parent_desc_cat_id,
                    "type_id": type_id,
                    "category_name": parent_name or category_name,
                    "type_name": type_name or category_name,
                    "score": score,
                })
            elif children:
                for child in children:
                    c_tid = child.get("type_id")
                    if c_tid:
                        c_tname = child.get("type_name", child.get("category_name", "")) or ""
                        c_dc = child.get("description_category_id") or desc_cat_id
                        results.append({
                            "description_category_id": c_dc,
                            "type_id": c_tid,
                            "category_name": category_name or parent_name,
                            "type_name": c_tname or category_name,
                            "score": score + 1,
                        })

        if children:
            _walk_single(children, query_lower, results,
                         desc_cat_id or parent_desc_cat_id, category_name)


def search_categories(
    client_id: str,
    api_key: str,
    query: str,
    *,
    language: str = "RU",
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Search Ozon category tree by keyword and return matching leaf types.

    Fetches the full category tree, then searches for matching category/type
    names.  Returns a list of candidates, each with: description_category_id,
    type_id, category_name, type_name, score.
    """
    tree = _query_category_tree(client_id, api_key, language=language)
    query_lower = query.strip().lower()
    # Filter Russian stop words — they match too many unrelated categories
    RU_STOP = {'для', 'и', 'в', 'на', 'с', 'от', 'к', 'по', 'из', 'или', 'не', 'а', 'за', 'без', 'до', 'под', 'об', 'у', 'же', 'то', 'как', 'что', 'это', 'все', 'еще', 'бы', 'мы', 'вы', 'он', 'она', 'они', 'там', 'тут', 'где', 'его', 'ее', 'их', 'активного', 'активный', 'активная'}
    query_words = [w for w in query_lower.split() if w not in RU_STOP]
    if not query_words:
        query_words = query_lower.split()  # fallback if all words are stop words
    results: list[dict[str, Any]] = []

    def _walk(
        nodes: list[dict[str, Any]],
        parent_desc_cat_id: int | None,
        parent_name: str = "",
    ) -> None:
        for node in nodes:
            # Skip disabled nodes — Ozon won't allow product creation in these
            if node.get("disabled"):
                children = node.get("children") or []
                if children:
                    _walk(children, node.get("description_category_id") or parent_desc_cat_id, node.get("category_name", ""))
                continue

            desc_cat_id = node.get("description_category_id")
            type_id = node.get("type_id")
            category_name = node.get("category_name", "") or ""
            type_name = node.get("type_name", node.get("category_name", "")) or ""
            children = node.get("children") or []

            name_to_search = (type_name or category_name or "").lower()

            # Score: word matching
            matched_words = sum(1 for w in query_words if w in name_to_search)
            word_score = matched_words / max(len(query_words), 1)

            # Heavily weight whole-phrase match, then word fraction
            if query_lower in name_to_search:
                if name_to_search == query_lower:
                    score = 0  # Exact match — best
                elif name_to_search.startswith(query_lower):
                    score = 5  # Starts with
                else:
                    score = 10  # Contains substring
            elif word_score > 0:
                score = 100 - int(word_score * 90)  # 1/1=10, 1/2=55, 1/3=70
            else:
                score = 999

            if score < 999:
                if type_id:
                    results.append({
                        "description_category_id": desc_cat_id or parent_desc_cat_id,
                        "type_id": type_id,
                        "category_name": parent_name or category_name,
                        "type_name": type_name or category_name,
                        "score": score,
                    })
                elif children:
                    for child in children:
                        c_tid = child.get("type_id")
                        if c_tid:
                            c_tname = child.get("type_name", child.get("category_name", "")) or ""
                            # Use child's own description_category_id if available,
                            # otherwise fall back to parent's. Bug fix: tree API
                            # children often have dc=None but their type_ids require
                            # a different dc for /v3/product/import.
                            c_dc = child.get("description_category_id") or desc_cat_id
                            results.append({
                                "description_category_id": c_dc,
                                "type_id": c_tid,
                                "category_name": category_name or parent_name,
                                "type_name": c_tname or category_name,
                                "score": score + 1,
                            })

            if children:
                _walk(children, desc_cat_id or parent_desc_cat_id, category_name)

    _walk(tree, None)

    # ── Accessory/parts penalty ──
    # Categories like "Аксессуар для вентилятора" (fan accessory) are not
    # appropriate for a complete product (e.g. a desk fan).  Penalise them
    # so actual product categories ("Вентилятор") win ties.
    ACCESSORY_WORDS = {
        "аксессуар", "аксессуары", "запчасти", "запчасть",
        "комплектующие", "комплектующая", "расходные", "расходный",
    }
    for r in results:
        name_lower = (r.get("type_name", "") + " " + r.get("category_name", "")).lower()
        if any(w in name_lower for w in ACCESSORY_WORDS):
            r["score"] += 15  # push down relative to real product categories

    # ── Sort: score first, then prefer shorter type_name (more specific) ──
    results.sort(key=lambda r: (
        r["score"],
        len(r.get("type_name", "")),
        r.get("category_name", ""),
    ))

    # ── Single-word fallback on partial-match ties ──
    # When a multi-word query (e.g. "мини вентилятор") yields ALL top results
    # at the same partial-match score, the adjective didn't help.  Retry with
    # individual words.  Russian puts the noun last — try words in reverse
    # order (last = noun first).  Merge all single-word results, with exact
    # matches (score=0) ranked above the original partial-match results.
    if (
        len(query_words) > 1
        and len(results) >= 2
        and results[0]["score"] > 0
        and all(r["score"] == results[0]["score"] for r in results[:min(5, len(results))])
    ):
        merged: dict[tuple[int, int], dict[str, Any]] = {}
        # Keep original results as fallback (lower priority than exact hits)
        for r in results:
            key = (int(r["description_category_id"]), int(r["type_id"]))
            merged[key] = r

        # Try each word in reverse order (noun-last languages: RU, EN).
        # Collect results from ALL words — don't stop at the first hit.
        for word in reversed(query_words):
            if len(word) < 3:
                continue
            single_results: list[dict[str, Any]] = []
            _walk_single(tree, word, single_results)
            for r in single_results:
                name_lower = (r.get("type_name", "") + " " + r.get("category_name", "")).lower()
                if any(w in name_lower for w in ACCESSORY_WORDS):
                    r["score"] += 15
                key = (int(r["description_category_id"]), int(r["type_id"]))
                # Single-word exact or near-exact match always beats original partial match
                if key in merged and r["score"] < merged[key]["score"] or key not in merged:
                    merged[key] = r

        results = sorted(merged.values(), key=lambda r: (
            r["score"],
            len(r.get("type_name", "")),
            r.get("category_name", ""),
        ))

    return results[:max_results]


def validate_category(
    client_id: str,
    api_key: str,
    description_category_id: int,
    type_id: int,
    *,
    language: str = "ZH_HANS",
    task_id: str = "",
) -> bool:
    """Verify that a (description_category_id, type_id) pair is valid for import.

    Calls ``POST /v1/description-category/attribute`` — if Ozon returns
    a non-empty attribute list, the pair is valid for /v3/product/import.
    Returns False on any error (timeout, auth failure, empty result).

    Uses ``language=ZH_HANS`` by default — Ozon API supports Chinese natively,
    which makes LLM attribute mapping from 1688 Chinese attrs much more accurate.
    """
    try:
        resp = _post(
            client_id,
            api_key,
            "/v1/description-category/attribute",
            {
                "description_category_id": int(description_category_id),
                "type_id": int(type_id),
                "language": language,
            },
            timeout=15,
        )
        attrs = resp.get("result", []) if isinstance(resp, dict) else []
        valid = len(attrs) > 0
        if task_id:
            AuditLogger(task_id).info("ozon", "validate",
                f"dc={description_category_id} type={type_id} valid={valid} attrs={len(attrs)}")
        return valid
    except Exception:
        if task_id:
            AuditLogger(task_id).warn("ozon", "validate",
                f"dc={description_category_id} type={type_id} FAILED")
        return False


def search_categories_validated(
    client_id: str,
    api_key: str,
    query: str,
    *,
    language: str = "RU",
    max_results: int = 5,
    validate_count: int = 5,
    task_id: str = "",
) -> list[dict[str, Any]]:
    """Search Ozon categories with import-validation.

    Calls ``search_categories()`` to get tree API candidates, then
    validates the top candidates via ``validate_category()`` — only
    (dc, type) pairs that pass import validation are returned.

    Self-learning is handled by the pipeline (Category node reads/writes
    ``category_mapping_verified`` in Supabase). The client does NOT
    access Supabase directly — all DB access goes through webhooks.

    Returns validated candidates sorted by score (lower = better).
    Empty list if nothing passes validation.
    """
    if task_id:
        AuditLogger(task_id).info("ozon", "search", f'Category search: "{query}" lang={language}')
    candidates = search_categories(
        client_id, api_key, query,
        language=language,
        max_results=max(max_results * 2, validate_count * 2),
    )

    validated: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for c in candidates[:validate_count]:
        dc = int(c["description_category_id"])
        tid = int(c["type_id"])
        key = (dc, tid)
        if key in seen:
            continue
        seen.add(key)
        if validate_category(client_id, api_key, dc, tid, language=language, task_id=task_id):
            validated.append(c)
            if len(validated) >= max_results:
                break

    if task_id:
        AuditLogger(task_id).info("ozon", "search",
            f"Validated: {len(validated)}/{len(candidates[:validate_count])} candidates")
    return validated


def list_product_infos(
    client_id: str,
    api_key: str,
    *,
    product_ids: list[str] | None = None,
    offer_ids: list[str] | None = None,
    skus: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Look up Ozon product info by product_id, offer_id, or SKU.

    POST /v3/product/info/list
    """
    payload = _post(
        client_id,
        api_key,
        "/v3/product/info/list",
        {
            "product_id": [str(item) for item in (product_ids or [])],
            "offer_id": [str(item) for item in (offer_ids or [])],
            "sku": [str(item) for item in (skus or [])],
        },
        timeout=30,
    )
    return list(payload.get("items") or [])


def get_product_attributes_v4(
    client_id: str,
    api_key: str,
    *,
    product_ids: list[str] | None = None,
    offer_ids: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Get product attributes via /v4/product/info/attributes."""
    filter_payload: dict[str, Any] = {}
    if product_ids:
        filter_payload["product_id"] = [str(item) for item in product_ids]
    if offer_ids:
        filter_payload["offer_id"] = [str(item) for item in offer_ids]
    payload = _post(
        client_id,
        api_key,
        "/v4/product/info/attributes",
        {
            "filter": filter_payload,
            "limit": int(limit),
            "sort_dir": "ASC",
        },
        timeout=30,
    )
    return payload


def update_prices(
    client_id: str, api_key: str, prices: list[dict[str, Any]],
) -> dict[str, Any]:
    """Update product prices without re-submitting the entire product.

    POST /v1/product/import/prices — much faster than v3/product/import.
    Each price dict: {offer_id, price, currency_code, old_price?}
    Returns: {result: [{product_id, offer_id, updated, errors}]}
    """
    return _post(
        client_id, api_key, "/v1/product/import/prices",
        {"prices": prices}, timeout=30,
    )


def update_attributes(
    client_id: str, api_key: str, product_id: int,
    attributes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Update existing product attributes (cannot add new attrs).

    POST /v1/product/attributes/update — only updates attributes that
    already exist on the product. Returns "nothing to update" if the
    attribute doesn't exist or already has the same value.
    """
    return _post(
        client_id, api_key, "/v1/product/attributes/update",
        {"product_id": product_id, "attributes": attributes},
        timeout=30,
    )


def import_by_sku(
    client_id: str, api_key: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Copy a product from another seller by SKU (跟卖).

    POST /v1/product/import-by-sku
    items: [{"sku": 298789742, "name": "...", "offer_id": "...", ...}]

    Returns {"task_id": int, "unmatched_sku_list": [...]}
    """
    payload = _post(
        client_id, api_key,
        "/v1/product/import-by-sku",
        {"items": items},
        timeout=30,
    )
    return payload.get("result", payload)


def list_products(
    client_id: str,
    api_key: str,
    *,
    last_id: str = "",
    limit: int = 100,
    visibility: str = "ALL",
) -> dict[str, Any]:
    """List products in the store. POST /v3/product/list."""
    return _post(
        client_id,
        api_key,
        "/v3/product/list",
        {
            "filter": {"visibility": visibility},
            "last_id": last_id,
            "limit": int(limit),
        },
        timeout=30,
    )


def detect_contract_currency(
    client_id: str, api_key: str, limit: int = 3
) -> str | None:
    """Auto-detect the store's contract currency from existing products.

    POST /v5/product/info/prices — returns the first recognised currency code
    (RUB, CNY, USD, EUR) or None.
    """
    payload = _post(
        client_id,
        api_key,
        "/v5/product/info/prices",
        {
            "filter": {"visibility": "ALL"},
            "cursor": "",
            "limit": int(limit),
        },
        timeout=30,
    )
    items = payload.get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        direct = str(item.get("currency_code") or "").strip().upper()
        if direct in {"RUB", "CNY", "USD", "EUR"}:
            return direct
        price = item.get("price") or {}
        if isinstance(price, dict):
            nested = str(price.get("currency_code") or "").strip().upper()
            if nested in {"RUB", "CNY", "USD", "EUR"}:
                return nested
    return None


# ---------------------------------------------------------------------------
# Follow-sell (跟卖) helpers
# ---------------------------------------------------------------------------


def poll_import_task(
    client_id: str,
    api_key: str,
    task_id: str | int,
    *,
    max_wait_seconds: int = 300,
    poll_interval_seconds: int = 5,
) -> dict[str, Any]:
    """Poll /v1/product/import/info until the import task completes.

    Returns a dict with:
      - status: "completed" | "failed" | "timeout" | "copy_denied" | "already_imported"
      - product_id: str | None
      - offer_id: str | None
      - raw: the final API response
    """
    deadline = time.monotonic() + max_wait_seconds
    task_id_int = int(task_id)

    while time.monotonic() < deadline:
        try:
            info = _get_import_info(client_id, api_key, str(task_id_int))
        except Exception as exc:
            logger.warning("poll_import_task(%s): API error: %s", task_id, exc)
            time.sleep(poll_interval_seconds)
            continue

        result = info.get("result", info)
        items = result.get("items") or []
        first_item = items[0] if items else {}
        item_status = str(first_item.get("status") or "").lower()

        # Check for copy-related errors in items
        item_errors = first_item.get("errors") or []
        copy_denied_codes = (
            "foreign_seller_card_copy_denied",
            "copy_protection",
            "copy_denied",
            "copy_forbidden",
        )
        already_imported_codes = (
            "updating_with_seller_sku",
            "sku_already_exists",
            "duplicate_sku",
        )
        copy_errors = [
            e
            for e in item_errors
            if str(e.get("code", "")).lower() in copy_denied_codes
            or any(
                s in str(e.get("description", "")).lower()
                for s in ("невозможно скопировать", "copy", "копирова")
            )
        ]
        already_imported = any(
            str(e.get("code", "")).lower() in already_imported_codes
            for e in item_errors
        )

        if item_status in ("success", "completed", "done", "imported"):
            if copy_errors:
                error_descs = "; ".join(
                    str(
                        e.get("description")
                        or e.get("message")
                        or e.get("code", "")
                    )
                    for e in copy_errors
                )
                return {
                    "status": "copy_denied",
                    "product_id": str(first_item.get("product_id", "")),
                    "offer_id": str(first_item.get("offer_id", "")),
                    "error": error_descs,
                    "copy_errors": copy_errors,
                    "raw": result,
                }
            return {
                "status": "completed",
                "product_id": str(first_item.get("product_id", "")),
                "offer_id": str(first_item.get("offer_id", "")),
                "raw": result,
            }

        if item_status in ("failed", "error", "cancelled"):
            if already_imported:
                return {
                    "status": "already_imported",
                    "product_id": str(first_item.get("product_id", "")),
                    "offer_id": str(first_item.get("offer_id", "")),
                    "error": str(
                        first_item.get("errors", [{}])[0].get("description", "")
                        if first_item.get("errors")
                        else ""
                    ),
                    "raw": result,
                }
            if copy_errors:
                error_descs = "; ".join(
                    str(
                        e.get("description")
                        or e.get("message")
                        or e.get("code", "")
                    )
                    for e in copy_errors
                )
                return {
                    "status": "copy_denied",
                    "product_id": str(first_item.get("product_id", "")),
                    "offer_id": str(first_item.get("offer_id", "")),
                    "error": error_descs,
                    "copy_errors": copy_errors,
                    "raw": result,
                }
            error_msg = str(
                result.get("error")
                or result.get("message")
                or first_item.get("error")
                or ""
            )
            return {
                "status": "failed",
                "product_id": None,
                "offer_id": None,
                "error": error_msg,
                "raw": result,
            }

        time.sleep(poll_interval_seconds)

    return {
        "status": "timeout",
        "product_id": None,
        "offer_id": None,
        "error": f"Import task {task_id} did not complete within {max_wait_seconds}s",
        "raw": {},
    }


def update_existing_product(
    client_id: str,
    api_key: str,
    *,
    product_id: str,
    offer_id: str,
    name: str = "",
    images: list[str] | None = None,
    price: str = "",
    old_price: str = "",
    vat: str = "0.0",
    attributes: list[dict[str, Any]] | None = None,
    currency_code: str = "RUB",
    depth: int = 0,
    width: int = 0,
    height: int = 0,
    dimension_unit: str = "mm",
    weight: int = 0,
    weight_unit: str = "g",
) -> dict[str, Any]:
    """Update an existing Ozon product via /v3/product/import.

    Provide product_id to update instead of create.
    Only non-empty fields will be included in the update payload.
    """
    item: dict[str, Any] = {
        "product_id": int(product_id),
        "offer_id": str(offer_id),
    }

    if name:
        item["name"] = str(name)
    if images:
        item["images"] = [str(url) for url in images]
    if price:
        item["price"] = str(price)
        item["currency_code"] = str(currency_code)
    if old_price:
        item["old_price"] = str(old_price)
    if attributes:
        item["attributes"] = attributes
    if vat:
        item["vat"] = str(vat)

    if depth > 0:
        item["depth"] = depth
        item["dimension_unit"] = dimension_unit
    if width > 0:
        item["width"] = width
        if "dimension_unit" not in item:
            item["dimension_unit"] = dimension_unit
    if height > 0:
        item["height"] = height
        if "dimension_unit" not in item:
            item["dimension_unit"] = dimension_unit
    if weight > 0:
        item["weight"] = weight
        item["weight_unit"] = weight_unit

    # Auto-fetch category info from existing product if not in attributes
    if "description_category_id" not in item:
        try:
            infos = list_product_infos(
                client_id, api_key, product_ids=[str(product_id)]
            )
            if infos:
                existing = infos[0]
                cat_id = existing.get("description_category_id") or existing.get(
                    "category_id"
                )
                type_id = existing.get("type_id")
                if cat_id:
                    item["description_category_id"] = int(cat_id)
                if type_id:
                    item["type_id"] = int(type_id)
        except Exception as e:
            logger.debug('list_product_infos(%s) failed — proceeding without category info: %s',
                         product_id, e)

    try:
        result = _post(
            client_id, api_key, "/v3/product/import", {"items": [item]}, timeout=30
        )
        task_id = str((result.get("result") or {}).get("task_id", ""))
        return {
            "ok": True,
            "task_id": task_id,
            "product_id": product_id,
            "offer_id": offer_id,
        }
    except Exception as exc:
        logger.exception("update_existing_product(%s) failed", product_id)
        return {
            "ok": False,
            "error": str(exc),
            "product_id": product_id,
            "offer_id": offer_id,
        }

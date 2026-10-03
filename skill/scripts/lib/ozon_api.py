"""Thin Ozon REST API client — inlined from pounding-ozon-cloud.

This module replaces the pounding-ozon-cloud dependency.  Only the functions
actually used by cloud_client.py are ported; the heavier cloud-only modules
(COS, Windmill, orchestration, attribute resolution, etc.) are not needed here.
"""
from __future__ import annotations

import logging
import re
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
    # 中文无空格分词：整词包含匹配对「宠物饮水机」这类复合词必然 0 命中，
    # 增加 2-gram 子串匹配兜底（宠物饮水机 → 宠物/物饮/饮水/水机），无需 jieba 依赖。
    CJK_RE = re.compile(r"[\u4e00-\u9fff]")
    is_cjk = bool(CJK_RE.search(query_lower))
    cjk_grams = [query_lower[i : i + 2] for i in range(max(len(query_lower) - 1, 0))] if is_cjk else []
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
            elif cjk_grams:
                # 中文 2-gram 兜底：节点名命中任一 gram 即候选，按命中率排序
                matched_grams = sum(1 for g in cjk_grams if g in name_to_search)
                if matched_grams:
                    score = 100 - int(matched_grams / len(cjk_grams) * 80)  # 全命中=20
                else:
                    # ✅ F-B04（2026-09-09）：2-gram 零重叠时的**单字集合**兜底——
                    # 官方译名一字之差（保暖杯 vs 保温杯：bigram 保温/温杯 vs 保暖/暖杯
                    # 全不同）导致 graph 猜对 dc=17027928 却漏召/被闸错杀。单字覆盖
                    # ≥2/3 记为弱候选（分数劣于 gram 命中，精确匹配始终优先）。
                    query_chars = {c for c in query_lower if "\u4e00" <= c <= "\u9fff"}
                    name_chars = {c for c in name_to_search if "\u4e00" <= c <= "\u9fff"}
                    if query_chars and name_chars:
                        char_cov = len(query_chars & name_chars) / len(query_chars)
                        if char_cov >= 0.66:
                            score = 60 + int((1 - char_cov) * 30)  # 2/3≈69, 全命中=60
                        else:
                            score = 999
                    else:
                        score = 999
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





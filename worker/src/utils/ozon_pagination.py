"""Ozon cursor-paginated endpoint walker — merges all pages into a flat list.

Styles: "last" (last_id/has_next), "offset" (offset/total),
"cursor" (cursor/next_cursor), "auto" (detect from first response).
"""
from __future__ import annotations
import logging
from typing import Any, Callable, Optional
from utils.ozon_client import ozon_post as _default_post_fn

logger = logging.getLogger(__name__)
_LIST_KEYS = ("items", "postings", "products")


def _extract_items(parsed: dict[str, Any]) -> list[dict] | None:
    """Return the first non-empty list from the response, or None."""
    for key in _LIST_KEYS:
        items = parsed.get(key)
        if isinstance(items, list) and items:
            return items
    return None


def _detect_style(parsed: dict[str, Any]) -> str:
    if "has_next" in parsed or "last_id" in parsed:
        return "last"
    if "next_cursor" in parsed or "cursor" in parsed:
        return "cursor"
    if "total" in parsed:
        return "offset"
    return "none"


def paginate(
    client_id: str,
    api_key: str,
    endpoint: str,
    body: dict[str, Any],
    *,
    cursor_style: str = "auto",
    max_pages: int = 100,
    post_fn: Any = None,
    on_page: Optional[Callable[[list], None]] = None,
    **ozon_kwargs: Any,
) -> list[dict]:
    """Walk all pages of a cursor-paginated Ozon endpoint and merge items.

    Args:
        post_fn: Injectable callable for ozon_post (default: import from utils.ozon_client).
            Pass the service's own imported reference so mocks at the service namespace intercept.
        on_page: ✅ B6（2026-10 收敛批②）逐页回调——每页 items 合入 all_items 后
            以本页 items 调用一次（None 缺省，既有调用方 shelf/order 零变化）。
            用途：大结果集逐页 upsert 落库（如 store_sync._sync_products），
            避免全量攒内存 + 中途失败保留已落页。回调抛异常即中止遍历并向上抛。
    """
    if post_fn is None:
        post_fn = _default_post_fn
    all_items: list[dict] = []
    current_body = dict(body)
    detected_style: str | None = None
    page = 0
    while page < max_pages:
        page += 1
        resp = post_fn(client_id, api_key, endpoint, current_body, **ozon_kwargs)
        parsed = resp.get("result") or resp

        items = _extract_items(parsed)
        if items:
            all_items.extend(items)
            if on_page is not None:
                on_page(items)

        # ✅ B6: 空页即止——offset/total 语义下空页+total 偏大继续翻会以相同
        # offset 空转到 max_pages（旧手写循环「if not items: break」语义，
        # 迁移进通用 walker；shelf/order 既有行为只会更早停止，输出不变）。
        if not items:
            break

        if detected_style is None:
            if cursor_style == "auto":
                detected_style = _detect_style(parsed)
            else:
                detected_style = cursor_style
        has_more = False
        if detected_style == "last":
            has_more = parsed.get("has_next", False)
            if has_more and parsed.get("last_id") is not None:
                current_body = dict(body)
                current_body["last_id"] = parsed["last_id"]

        elif detected_style == "offset":
            total = parsed.get("total", 0)
            if len(all_items) < total:
                has_more = True
                current_body = dict(body)
                current_body["offset"] = len(all_items)

        elif detected_style == "cursor":
            next_cursor = parsed.get("next_cursor") or parsed.get("cursor")
            if next_cursor and next_cursor != current_body.get("cursor"):
                has_more = True
                current_body = dict(body)
                current_body["cursor"] = next_cursor

        if not has_more:
            break

    if page >= max_pages:
        logger.warning(
            "paginate hit max_pages=%d for %s (collected %d items)",
            max_pages, endpoint, len(all_items),
        )

    return all_items

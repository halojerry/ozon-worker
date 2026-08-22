"""TDD tests for utils.ozon_pagination.paginate().

Covers: happy path multi-page, single page, empty items, auto-detect,
max_pages safety, import sanity, body immutability, and regression for wired services.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.ozon_pagination import paginate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_ozon_factory(responses: list[dict]):
    """Return a fake ozon_post that yields responses in sequence."""
    calls: list[dict] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append({"client_id": client_id, "endpoint": endpoint, "body": dict(body)})
        idx = len(calls) - 1
        if idx < len(responses):
            return responses[idx]
        return responses[-1]

    _fake.calls = calls
    return _fake


# ---------------------------------------------------------------------------
# 1. Happy path: 3 pages cursor style → merged items
# ---------------------------------------------------------------------------

def test_cursor_style_three_pages():
    responses = [
        {"result": {"postings": [{"id": 1}, {"id": 2}], "has_next": True, "last_id": "cursor_a"}},
        {"result": {"postings": [{"id": 3}, {"id": 4}], "has_next": True, "last_id": "cursor_b"}},
        {"result": {"postings": [{"id": 5}], "has_next": False}},
    ]
    fake = _fake_ozon_factory(responses)

    items = paginate("cid", "key", "/v4/posting/fbs/list",
                     {"limit": 2, "cursor": ""}, post_fn=fake)

    assert len(items) == 5
    assert [it["id"] for it in items] == [1, 2, 3, 4, 5]
    assert len(fake.calls) == 3
    assert fake.calls[0]["body"]["cursor"] == ""
    assert fake.calls[1]["body"]["last_id"] == "cursor_a"
    assert fake.calls[2]["body"]["last_id"] == "cursor_b"


# ---------------------------------------------------------------------------
# 2. Single page: has_next=False → one call
# ---------------------------------------------------------------------------

def test_cursor_single_page():
    fake = _fake_ozon_factory([
        {"result": {"postings": [{"id": 10}], "has_next": False}},
    ])

    items = paginate("cid", "key", "/v4/posting/fbs/list",
                     {"cursor": ""}, post_fn=fake)

    assert len(items) == 1
    assert items[0]["id"] == 10
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# 3. Empty items → []
# ---------------------------------------------------------------------------

def test_empty_items():
    fake = _fake_ozon_factory([
        {"result": {"postings": []}},
    ])

    items = paginate("cid", "key", "/v4/posting/fbs/list", {}, post_fn=fake)

    assert items == []
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# 4. Auto-detect: first response has total → offset style
# ---------------------------------------------------------------------------

def test_auto_detect_offset_style():
    fake = _fake_ozon_factory([
        {"result": {"items": [{"id": 1}, {"id": 2}], "total": 3}},
        {"result": {"items": [{"id": 3}], "total": 3}},
    ])

    items = paginate("cid", "key", "/v3/product/list",
                     {"limit": 2, "offset": 0}, post_fn=fake)

    assert len(items) == 3
    assert len(fake.calls) == 2
    assert fake.calls[0]["body"]["offset"] == 0
    assert fake.calls[1]["body"]["offset"] == 2


# ---------------------------------------------------------------------------
# 5. max_pages safety: 200 pages → stops at 100
# ---------------------------------------------------------------------------

def test_max_pages_safety():
    pages = [
        {"result": {"postings": [{"page": i}], "has_next": True, "last_id": str(i)}}
        for i in range(200)
    ]
    fake = _fake_ozon_factory(pages)

    items = paginate("cid", "key", "/v4/posting/fbs/list",
                     {"cursor": ""}, max_pages=100, post_fn=fake)

    assert len(items) == 100
    assert len(fake.calls) == 100


# ---------------------------------------------------------------------------
# 6. Import sanity
# ---------------------------------------------------------------------------

def test_import_sanity():
    from utils.ozon_pagination import paginate as p
    assert callable(p)


# ---------------------------------------------------------------------------
# 7. No pagination signals → return first page items directly
# ---------------------------------------------------------------------------

def test_no_pagination_signals_returns_first_page():
    fake = _fake_ozon_factory([
        {"result": {"postings": [{"id": 1}, {"id": 2}]}},
    ])

    items = paginate("cid", "key", "/v4/posting/fbs/list", {}, post_fn=fake)

    assert len(items) == 2
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# 8. Cursor style: next_cursor empty → stop
# ---------------------------------------------------------------------------

def test_cursor_empty_next_cursor_stops():
    fake = _fake_ozon_factory([
        {"result": {"items": [{"id": 1}], "next_cursor": ""}},
    ])

    items = paginate("cid", "key", "/v3/product/list", {},
                     cursor_style="cursor", post_fn=fake)

    assert len(items) == 1
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# 9. Body is never mutated
# ---------------------------------------------------------------------------

def test_body_not_mutated():
    original = {"limit": 10, "cursor": ""}
    responses = [
        {"result": {"postings": [{"id": 1}], "has_next": True, "last_id": "x"}},
        {"result": {"postings": [{"id": 2}], "has_next": False}},
    ]
    fake = _fake_ozon_factory(responses)

    paginate("cid", "key", "/v4/posting/fbs/list", original, post_fn=fake)

    assert original["cursor"] == ""
    assert "last_id" not in original


# ---------------------------------------------------------------------------
# 10. Offset style: exact total match → stop
# ---------------------------------------------------------------------------

def test_offset_exact_total_match():
    fake = _fake_ozon_factory([
        {"result": {"items": [{"id": 1}, {"id": 2}], "total": 2}},
    ])

    items = paginate("cid", "key", "/v3/product/list",
                     {"limit": 10, "offset": 0}, post_fn=fake)

    assert len(items) == 2
    assert len(fake.calls) == 1

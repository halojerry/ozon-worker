"""Typed exception hierarchy for Ozon API errors.

Ported from docs/refs/ozon-mcp to plain Exception subclasses — no Pydantic,
no external deps. Lets Ozon API callers always catch a typed error.
    OzonError(base) — kw-only status_code/operation_id/payload
    OzonValidationError(400) OzonAuthError(401) OzonForbiddenError(403)
    OzonNotFoundError(404) OzonConflictError(409) OzonRateLimitError(429)
    OzonServerError(5xx)
    _raise_for_status(resp, endpoint) — map HTTP status → exception; 2xx → None
    _parse_retry_after(value) — delta-seconds or HTTP-date → float|None
    _extract_error_message(parsed) — pull message from Ozon JSON body
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional


class OzonError(Exception):
    """Base for all Ozon API errors."""

    def __init__(self, message, *, status_code=None, operation_id=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.operation_id = operation_id
        self.payload = payload


class OzonValidationError(OzonError):
    """400 — request body failed Ozon validation."""


class OzonAuthError(OzonError):
    """401 — missing/invalid Client-Id or Api-Key."""


class OzonForbiddenError(OzonError):
    """403 — authenticated but not allowed to call this endpoint."""


class OzonNotFoundError(OzonError):
    """404 — resource does not exist."""


class OzonConflictError(OzonError):
    """409 — state conflict (duplicate offer_id, etc.)."""


class OzonRateLimitError(OzonError):
    """429 — rate limit exceeded. ``retry_after`` is seconds to wait."""

    def __init__(self, message, *, retry_after=None, status_code=None,
                 operation_id=None, payload=None):
        super().__init__(message, status_code=status_code,
                        operation_id=operation_id, payload=payload)
        self.retry_after = retry_after


class OzonServerError(OzonError):
    """5xx — Ozon server-side error, usually retryable."""


_STATUS_MAP = {400: OzonValidationError, 401: OzonAuthError, 403: OzonForbiddenError,
               404: OzonNotFoundError, 409: OzonConflictError}


def _extract_error_message(parsed: Any) -> Optional[str]:
    """Pull a human-readable message out of an Ozon error body, if possible.

    Handles {"message": "..."}, {"error": {"message": "..."}}, and
    {"details": [{"description": "..."}]}. Unparseable → None.
    """
    if not isinstance(parsed, dict):
        return None
    msg = parsed.get("message")
    if isinstance(msg, str) and msg:
        return msg
    error = parsed.get("error")
    if isinstance(error, dict):
        nested = error.get("message")
        if isinstance(nested, str) and nested:
            return nested
    if isinstance(error, str) and error:
        return error
    details = parsed.get("details")
    if isinstance(details, list) and details:
        first = details[0]
        if isinstance(first, dict):
            d_msg = first.get("description") or first.get("message")
            if isinstance(d_msg, str) and d_msg:
                return d_msg
    return None


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header (RFC 7231 §7.1.3). Accepts
    delta-seconds ("60") or HTTP-date. Returns seconds to wait (>=0), or
    None when missing/unparseable. Past HTTP-dates clamp to 0.
    """
    if not header_value:
        return None
    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (when - datetime.now(tz=timezone.utc)).total_seconds()
    return max(0.0, delta)


def _raise_for_status(resp: Any, endpoint: Optional[str]) -> None:
    """Map an HTTP response's status code to a typed OzonError; 2xx → None.
    ``resp`` duck-types httpx/requests Response (status_code/json/headers/text).
    """
    status = resp.status_code
    if status < 400:
        return None
    try:
        parsed: Any = resp.json()
    except (ValueError, TypeError):
        parsed = None
    message = _extract_error_message(parsed) or getattr(resp, "reason_phrase", None) or ""
    if isinstance(parsed, dict):
        payload: dict[str, Any] = parsed
    elif parsed is not None:
        payload = {"raw": parsed}
    else:
        payload = {"raw_text": (resp.text or "")[:500]}
    kwargs = {"status_code": status, "operation_id": endpoint, "payload": payload}
    if status == 429:
        retry_after = _parse_retry_after(
            resp.headers.get("Retry-After") if resp.headers else None
        )
        raise OzonRateLimitError(message, retry_after=retry_after, **kwargs)
    if status >= 500:
        raise OzonServerError(message, **kwargs)
    exc_cls = _STATUS_MAP.get(status)
    if exc_cls is not None:
        raise exc_cls(message, **kwargs)
    raise OzonError(message, **kwargs)

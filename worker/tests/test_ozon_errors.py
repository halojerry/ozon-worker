"""Tests for utils.ozon_errors — typed Ozon API exception hierarchy.

Pure Python, no Pydantic, no external deps. Uses a MockResp duck-type of an
httpx/requests Response.
"""
import pytest

from utils.ozon_errors import (
    OzonAuthError,
    OzonError,
    OzonRateLimitError,
    OzonServerError,
    _raise_for_status,
)


class MockResp:
    """Minimal Response duck-type for _raise_for_status."""

    def __init__(self, status_code, json_data, *, headers=None, text="", reason_phrase=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers if headers is not None else {}
        self.text = text
        self.reason_phrase = reason_phrase

    def json(self):
        return self._json


def test_rate_limit_error_carries_retry_after():
    e = OzonRateLimitError("x", status_code=429, retry_after=60)
    assert e.retry_after == 60
    assert e.status_code == 429


def test_raise_for_status_2xx_returns_none():
    assert _raise_for_status(MockResp(200, {}), None) is None


def test_raise_for_status_5xx_raises_base_ozon_error():
    # 599 is a 5xx; OzonServerError subclasses OzonError, so the base catches it.
    with pytest.raises(OzonError):
        _raise_for_status(MockResp(599, {}), None)


def test_raise_for_status_599_is_server_error_subclass():
    with pytest.raises(OzonServerError):
        _raise_for_status(MockResp(599, {}), None)


def test_raise_for_status_429_parses_retry_after_header():
    with pytest.raises(OzonRateLimitError) as exc_info:
        _raise_for_status(
            MockResp(429, {}, headers={"Retry-After": "30"}), None
        )
    assert exc_info.value.retry_after == 30
    assert exc_info.value.status_code == 429


def test_raise_for_status_401_raises_auth_error():
    with pytest.raises(OzonAuthError):
        _raise_for_status(MockResp(401, {}), None)


def test_raise_for_status_propagates_operation_id():
    with pytest.raises(OzonAuthError) as exc_info:
        _raise_for_status(MockResp(401, {"message": "bad key"}), "/v1/product/list")
    assert exc_info.value.operation_id == "/v1/product/list"
    assert exc_info.value.payload == {"message": "bad key"}


def test_import_does_not_raise():
    # Re-import to prove the module is importable from utils.* namespace.
    import importlib

    mod = importlib.import_module("utils.ozon_errors")
    assert hasattr(mod, "OzonError")

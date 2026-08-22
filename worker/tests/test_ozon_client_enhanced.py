"""Tests for utils.ozon_client — tenacity retry + typed errors + rate limiter.

Mocks the shared HTTP session and tenacity's sleep so tests are instant and
do no real I/O. Verifies the task contract:
  - happy 200 → returns JSON, no retry
  - 429→429→200 → retries 2×, returns JSON
  - 500×3 → raises OzonServerError after _MAX_RETRIES attempts
  - 401 → raises OzonAuthError immediately, NO retry (call count == 1)
  - signature unchanged (no max_retries param added)
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from utils import ozon_client
from utils.ozon_client import ozon_post
from utils.ozon_errors import OzonAuthError, OzonServerError


class MockResponse:
    """Minimal Response duck-type for ozon_client + _raise_for_status."""

    def __init__(self, status_code, json_data=None, *, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers if headers is not None else {}
        self.text = text or ""
        self.ok = status_code < 400
        self.reason = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} Error")
            err.response = self
            raise err


def _queue_session(queue):
    """Build a mock session whose .post returns responses from `queue` in order."""
    session = MagicMock()

    def _post(*args, **kwargs):
        idx = session.post.call_count - 1
        return queue[idx] if idx < len(queue) else queue[-1]

    session.post.side_effect = _post
    return session


@pytest.fixture(autouse=True)
def _no_real_io():
    # tenacity's default sleep (tenacity.nap.sleep) calls time.sleep; patch the
    # shared time module so retries are instant. Also neutralize the rate limiter
    # so no test blocks on the token bucket.
    with patch("time.sleep", return_value=None), \
         patch.object(ozon_client._rate_limiter, "acquire", return_value=True):
        yield


def test_happy_200_no_retry():
    session = _queue_session([MockResponse(200, {"result": "ok"})])
    with patch("utils.http_session.session", session):
        result = ozon_post("cid", "key", "/v3/product/list", {})

    assert result == {"result": "ok"}
    assert session.post.call_count == 1


def test_429_then_429_then_200_retries_and_returns():
    session = _queue_session([
        MockResponse(429, {"message": "slow down"}, headers={"Retry-After": "0"}),
        MockResponse(429, {"message": "slow down"}, headers={"Retry-After": "0"}),
        MockResponse(200, {"result": "ok"}),
    ])
    with patch("utils.http_session.session", session):
        result = ozon_post("cid", "key", "/v3/product/list", {})

    assert result == {"result": "ok"}
    # 2 retries + 1 success = 3 attempts
    assert session.post.call_count == 3


def test_500_three_times_raises_ozon_server_error_after_max_retries():
    session = _queue_session([
        MockResponse(500, {"message": "boom"}),
        MockResponse(500, {"message": "boom"}),
        MockResponse(500, {"message": "boom"}),
    ])
    with patch("utils.http_session.session", session), pytest.raises(OzonServerError):
        ozon_post("cid", "key", "/v3/product/list", {})

    # _MAX_RETRIES == 3 → exactly 3 attempts, then reraise
    assert session.post.call_count == ozon_client._MAX_RETRIES


def test_401_raises_auth_error_immediately_no_retry():
    session = _queue_session([MockResponse(401, {"message": "bad key"})])
    with patch("utils.http_session.session", session), pytest.raises(OzonAuthError):
        ozon_post("cid", "key", "/v3/product/list", {})

    # Auth errors are NOT retried — exactly 1 attempt
    assert session.post.call_count == 1


def test_signature_unchanged():
    expected = ("client_id", "api_key", "endpoint", "body", "timeout", "language")
    actual = ozon_post.__code__.co_varnames[: ozon_post.__code__.co_argcount]
    assert actual == expected

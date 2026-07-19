"""
Wrapped requests module with proxy and CA certificate support.

This module provides a pre-configured requests session that automatically
uses the configured proxy and CA certificate settings.
"""

import logging
import requests
import threading
from collections.abc import Mapping
from typing import Any, Dict, Optional, Union
from requests.adapters import HTTPAdapter

# from requests.packages.urllib3.util.retry import Retry

from ._debug import coze_debug_enabled, coze_debug_print
from .proxy import HttpsProxy, CaBundlePath
from .skill_env import ensure_skill_env_loaded

logger = logging.getLogger("AuthProxyRequests")

DEFAULT_TIMEOUT = (5, 300)  # (connect_timeout, read_timeout) in seconds

ensure_skill_env_loaded()

class ConfiguredSession(requests.Session):
    """A requests Session subclass with automatic proxy and CA configuration."""

    def __init__(self):
        super().__init__()
        self._configure_session()

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        headers = dict(kwargs.pop("headers", {}) or {})
        kwargs["headers"] = headers
        kwargs.pop("verify", None)
        kwargs.pop("cert", None)
        self.verify = self._coze_ca_bundle_path
        self.cert = None
        try:
            response = super().request(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            if coze_debug_enabled():
                _print_request_failure_debug(exc, method, url, kwargs)
            raise
        if coze_debug_enabled():
            _print_request_debug(response)
        return response

    def _configure_session(self):
        """Configure the session with proxy and CA settings.

        Raises:
            ValueError: If HttpsProxy or CA bundle configuration fails
        """
        # Configure proxy settings - HttpsProxy must be configured
        self.trust_env = False
        proxy_url = HttpsProxy()
        coze_debug_print(f"Configured proxy_url: {proxy_url}")
        self.proxies = {"https": proxy_url, "http": proxy_url}
        logger.info(f"Use HTTPS proxy: {proxy_url}")

        # Configure CA certificate file path
        # Note: CaBundlePath() may raise ValueError if path is set but file doesn't exist.
        ca_bundle_path = CaBundlePath()
        if not ca_bundle_path:
            raise ValueError(
                "COZE_OUTBOUND_AUTH_PROXY_CA_PATH environment variable is not configured. "
                "Please set this variable to configure the CA bundle file path."
            )
        self.verify = ca_bundle_path
        self._coze_ca_bundle_path = ca_bundle_path
        self.cert = None
        logger.info(f"Use CA bundle: {ca_bundle_path}")

        # self._configure_adapter()

    # def _configure_adapter(self):
    #     """Configure retry strategy and mount adapter."""
    #     retry_strategy = Retry(
    #         total=2,
    #         backoff_factor=1,
    #         status_forcelist=[429, 500, 502, 503, 504],
    #     )
    #     adapter = HTTPAdapter(max_retries=retry_strategy)
    #     self.mount("http://", adapter)
    #     self.mount("https://", adapter)


# Default session will be created on first use
_default_session = None
_session_lock = threading.Lock()


def _get_default_session() -> ConfiguredSession:
    """Get or create the default configured session."""
    global _default_session
    if _default_session is None:
        with _session_lock:
            if _default_session is None:
                _default_session = ConfiguredSession()
    return _default_session


def get(url: str, **kwargs: Any) -> requests.Response:
    """Send a GET request with configured proxy and CA settings."""
    return _get_default_session().get(url, **kwargs)


def post(
    url: str,
    data: Optional[Union[Dict[str, Any], str, bytes]] = None,
    json: Optional[Any] = None,
    **kwargs: Any,
) -> requests.Response:
    """Send a POST request with configured proxy and CA settings."""
    return _get_default_session().post(url, data=data, json=json, **kwargs)


def put(
    url: str, data: Optional[Union[Dict[str, Any], str, bytes]] = None, **kwargs: Any
) -> requests.Response:
    """Send a PUT request with configured proxy and CA settings."""
    return _get_default_session().put(url, data=data, **kwargs)


def delete(url: str, **kwargs: Any) -> requests.Response:
    """Send a DELETE request with configured proxy and CA settings."""
    return _get_default_session().delete(url, **kwargs)


def head(url: str, **kwargs: Any) -> requests.Response:
    """Send a HEAD request with configured proxy and CA settings."""
    return _get_default_session().head(url, **kwargs)


def options(url: str, **kwargs: Any) -> requests.Response:
    """Send an OPTIONS request with configured proxy and CA settings."""
    return _get_default_session().options(url, **kwargs)


def patch(
    url: str, data: Optional[Union[Dict[str, Any], str, bytes]] = None, **kwargs: Any
) -> requests.Response:
    """Send a PATCH request with configured proxy and CA settings."""
    return _get_default_session().patch(url, data=data, **kwargs)


def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Send a request with the specified method and configured settings."""
    return _get_default_session().request(method, url, **kwargs)


def session() -> ConfiguredSession:
    """Create a new configured session instance."""
    return ConfiguredSession()


def _print_request_debug(response: requests.Response):
    prepared_request = getattr(response, "request", None)
    if prepared_request is not None:
        _print_prepared_request(prepared_request)
    print(
        "Configured response: "
        f"status={getattr(response, 'status_code', None)}, "
        f"headers={_format_headers(getattr(response, 'headers', {}))}"
    )


def _print_request_failure_debug(exc: requests.exceptions.RequestException, method, url, kwargs):
    prepared_request = getattr(exc, "request", None)
    if prepared_request is not None:
        _print_prepared_request(prepared_request)
    else:
        print(
            "Configured request: "
            f"method={method}, "
            f"url={url}, "
            f"headers={_format_headers(kwargs.get('headers', {}))}, "
            f"body={_format_body(kwargs.get('data') or kwargs.get('json'))}"
        )
    print(f"Configured request failed: {exc}")


def _print_prepared_request(prepared_request):
    print(
        "Configured request: "
        f"method={getattr(prepared_request, 'method', None)}, "
        f"url={getattr(prepared_request, 'url', None)}, "
        f"headers={_format_headers(getattr(prepared_request, 'headers', {}))}, "
        f"body={_format_body(getattr(prepared_request, 'body', None))}"
    )


def _format_headers(headers: Any):
    if isinstance(headers, Mapping):
        return dict(headers)
    try:
        return dict(headers)
    except (TypeError, ValueError):
        return headers


def _format_body(body: Any):
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return repr(body)
    return body


# Expose common requests exceptions and constants
from requests import exceptions, codes, status_codes
from requests.exceptions import (
    RequestException,
    ConnectionError,
    HTTPError,
    URLRequired,
    TooManyRedirects,
    Timeout,
    JSONDecodeError,
)

__all__ = [
    # Main functions
    "get",
    "post",
    "put",
    "delete",
    "head",
    "options",
    "patch",
    "request",
    "session",
    "ConfiguredSession",
    # Exceptions
    "RequestException",
    "ConnectionError",
    "HTTPError",
    "URLRequired",
    "TooManyRedirects",
    "Timeout",
    "JSONDecodeError",
    # Constants
    "codes",
    "status_codes",
    "exceptions",
]

"""Shared CDP (Chrome DevTools Protocol) client library.

Replaces direct Playwright usage with raw CDP over WebSocket.
Provides CdpConnection (context manager) and CdpTab (single tab operations).

Usage::

    with CdpConnection("http://127.0.0.1:9222") as cdp:
        tab = cdp.new_tab("https://example.com")
        title = tab.evaluate("document.title")
        tab.close()
"""

from __future__ import annotations

import itertools
import json as _json
import logging
import time
from typing import Any

import requests
import websocket

logger = logging.getLogger(__name__)


class CdpTab:
    """Single browser tab CDP operations. Replaces Playwright Page."""

    _counter = itertools.count(10000)  # thread-safe counter for message IDs

    def __init__(self, cdp_url: str, tab_id: str, ws_url: str) -> None:
        self._cdp_url = cdp_url
        self._tab_id = tab_id
        self._ws_url = ws_url
        self._ws: websocket.WebSocket = websocket.create_connection(ws_url, timeout=10)
        self._closed = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tab_id(self) -> str:
        return self._tab_id

    @property
    def url(self) -> str:
        """Get current page URL via Runtime.evaluate(location.href)."""
        try:
            return self.evaluate("location.href") or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Core CDP helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        return next(CdpTab._counter)

    def _send(self, method: str, params: dict | None = None, msg_id: int | None = None) -> int:
        """Send a CDP command and return the message ID used."""
        if msg_id is None:
            msg_id = self._next_id()
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        self._ws.send(_json.dumps(payload))
        return msg_id

    def _recv_until_id(self, target_id: int, timeout: float = 15) -> dict | None:
        """Drain WebSocket until we get a response matching *target_id*.

        Events (messages without ``id``) are silently discarded.
        Returns the matched message dict or ``None`` on timeout.

        Raises ``ConnectionError`` immediately if the WebSocket is closed
        (instead of silently looping until the full timeout).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                remaining = deadline - time.time()
                self._ws.settimeout(min(3.0, max(0.1, remaining)))
                raw = self._ws.recv()
                msg = _json.loads(raw)
                if msg.get("id") == target_id:
                    return msg
                # Otherwise it is an event -- discard.
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                self._closed = True
                raise ConnectionError("CDP WebSocket closed (target may have been destroyed)")
            except websocket.WebSocketProtocolException:
                self._closed = True
                raise ConnectionError("CDP WebSocket protocol error")
            except OSError as e:
                self._closed = True
                raise ConnectionError(f"CDP socket error: {e}")
            except Exception:
                continue
        return None

    def _recv_until_event(self, event_names: tuple[str, ...], timeout: float = 15) -> dict | None:
        """Drain WebSocket until we receive one of the named events.

        Command responses (messages with ``id``) are silently discarded.
        Returns the matched event dict or ``None`` on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                remaining = deadline - time.time()
                self._ws.settimeout(min(1.0, max(0.1, remaining)))
                raw = self._ws.recv()
                msg = _json.loads(raw)
                if msg.get("method") in event_names:
                    return msg
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                self._closed = True
                raise ConnectionError("CDP WebSocket closed")
            except OSError as e:
                self._closed = True
                raise ConnectionError(f"CDP socket error: {e}")
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, js: str, await_promise: bool = False, timeout: int = 15) -> Any:
        """``Runtime.evaluate`` -- returns the Python value.

        Parameters
        ----------
        js:
            JavaScript expression to evaluate.
        await_promise:
            If ``True``, the CDP ``awaitPromise`` flag is set so the result
            of a JS ``Promise`` is awaited before returning.
        timeout:
            Maximum seconds to wait for the response.

        Raises
        ------
        ConnectionError
            If the WebSocket is closed (target destroyed, Chrome killed, etc.).
            Callers should NOT retry on this error — the tab is dead.
        """
        params: dict[str, Any] = {
            "expression": js,
            "returnByValue": True,
        }
        if await_promise:
            params["awaitPromise"] = True

        msg_id = self._send("Runtime.evaluate", params)
        resp = self._recv_until_id(msg_id, timeout=timeout)
        if resp is None:
            logger.warning("evaluate() timed out after %ss for: %s", timeout, js[:120])
            return ""
        # Check for CDP error responses
        if "error" in resp:
            err = resp["error"]
            code = err.get("code", 0)
            msg = err.get("message", "")
            if code == -32000 and "target" in msg.lower():
                self._closed = True
                raise ConnectionError(f"CDP target gone: {msg}")
            logger.warning("evaluate() CDP error: %s", msg)
            return ""
        return resp.get("result", {}).get("result", {}).get("value", "")

    def navigate(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30) -> None:
        """Navigate to *url* and wait for the page to finish loading.

        Parameters
        ----------
        url:
            Target URL.
        wait_until:
            ``'domcontentloaded'`` (default) waits for
            ``Page.domContentEventFired``.  ``'load'`` waits for
            ``Page.loadEventFired``.
        timeout:
            Maximum seconds to wait for the load event.
        """
        self._send("Page.enable", msg_id=0)
        self._send("Page.navigate", {"url": url}, msg_id=1)

        if wait_until == "load":
            target_events = ("Page.loadEventFired",)
        else:
            target_events = ("Page.domContentEventFired", "Page.loadEventFired")

        self._recv_until_event(target_events, timeout=timeout)

    def wait_for_load(self, timeout: int = 15) -> None:
        """Wait for ``Page.loadEventFired`` or ``Page.domContentEventFired``."""
        self._recv_until_event(
            ("Page.loadEventFired", "Page.domContentEventFired"),
            timeout=timeout,
        )

    def add_init_script(self, js: str) -> None:
        """Add a script that runs before every page load.

        Must be called **before** ``navigate()``.  Wraps
        ``Page.addScriptToEvaluateOnNewDocument``.
        """
        self._send("Page.addScriptToEvaluateOnNewDocument", {"source": js})

    def set_extra_headers(self, headers: dict[str, str]) -> None:
        """Set extra HTTP headers for all subsequent requests.

        Enables ``Network`` domain first if not already active.
        """
        self._send("Network.enable", msg_id=0)
        self._send("Network.setExtraHTTPHeaders", {"headers": headers})

    def close(self) -> None:
        """Close WebSocket and the remote tab."""
        if self._closed:
            return
        self._closed = True
        try:
            self._ws.close()
        except Exception:
            pass
        try:
            requests.get(
                f"{self._cdp_url}/json/close/{self._tab_id}",
                timeout=3,
            )
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"<CdpTab tab_id={self._tab_id!r}>"


class CdpConnection:
    """Manages connection to Chrome CDP.  Replaces ``sync_playwright()``.

    Usage::

        with CdpConnection() as cdp:
            tab = cdp.new_tab("https://example.com")
            ...
    """

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222") -> None:
        self._cdp_url = cdp_url.rstrip("/")
        self._tabs: list[CdpTab] = []

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> CdpConnection:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def cdp_url(self) -> str:
        return self._cdp_url

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def new_tab(self, url: str = "about:blank") -> CdpTab:
        """Create a new tab via ``PUT /json/new?`` and return a :class:`CdpTab`."""
        # 清理已关闭的 tab 引用
        self._tabs = [t for t in self._tabs if not t._closed]

        resp = requests.put(f"{self._cdp_url}/json/new?", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tab_id = data.get("id", "")
        ws_url = data.get("webSocketDebuggerUrl", "")
        if not ws_url:
            raise RuntimeError("CDP did not return a webSocketDebuggerUrl")

        tab = CdpTab(self._cdp_url, tab_id, ws_url)
        self._tabs.append(tab)

        if url and url != "about:blank":
            tab.navigate(url)

        return tab

    def find_tab(self, url_pattern: str) -> CdpTab | None:
        """Find an existing tab whose URL contains *url_pattern*.

        Queries ``GET /json``, filters for ``type == "page"``, and returns
        the first match as a :class:`CdpTab`, or ``None``.
        """
        resp = requests.get(f"{self._cdp_url}/json", timeout=5)
        resp.raise_for_status()
        for t in resp.json():
            if t.get("type") != "page":
                continue
            if url_pattern in t.get("url", ""):
                ws_url = t.get("webSocketDebuggerUrl", "")
                if not ws_url:
                    continue
                tab = CdpTab(self._cdp_url, t.get("id", ""), ws_url)
                self._tabs.append(tab)
                return tab
        return None

    def close(self) -> None:
        """Close all tabs opened through this connection."""
        for tab in self._tabs:
            try:
                tab.close()
            except Exception:
                pass
        self._tabs.clear()

    def __repr__(self) -> str:
        return f"<CdpConnection cdp_url={self._cdp_url!r} tabs={len(self._tabs)}>"

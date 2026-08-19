"""Electron 浏览器宿主操作客户端（shopbang 式：BrowserWindow + executeJavaScript）。

skill 需要浏览器时，优先走本地 Electron 宿主（pounding-harness/electron-browser）的
操作 API（9224/ops/*）：每个操作 = 独立 BrowserWindow（共享 persist 登录态），
在客户端可见、可接手。Electron 不可用/不支持时，调用方降级走 skill 自启 Chrome。

端点（Electron 宿主 main.js）：
  POST /ops/open  {url, visible?} → {winId, url}
  POST /ops/exec  {winId, js}     → {result}
  POST /ops/html  {winId}         → {html}
  POST /ops/close {winId}         → {ok}
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

_OPS_BASE = "http://127.0.0.1:9224"


def _call(method: str, path: str, body: dict | None = None, timeout: float = 40) -> dict:
    url = f"{_OPS_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        return {}


def is_available(timeout: float = 1.5) -> bool:
    """Electron 宿主操作服务是否可用。"""
    return bool(_call("GET", "/status", timeout=timeout))


class ElectronTab:
    """一个 ops 会话（对应 Electron 里的一个独立 BrowserWindow）。"""

    def __init__(self, win_id: str, url: str = "") -> None:
        self.win_id = win_id
        self.url = url

    def exec(self, js: str, timeout: float = 40) -> Any:
        d = _call("POST", "/ops/exec", {"winId": self.win_id, "js": js}, timeout=timeout)
        return d.get("result")

    def html(self, timeout: float = 40) -> str:
        d = _call("POST", "/ops/html", {"winId": self.win_id}, timeout=timeout)
        return d.get("html", "")

    def close(self) -> None:
        _call("POST", "/ops/close", {"winId": self.win_id}, timeout=5)


def open_tab(url: str, visible: bool = False, timeout: float = 40) -> ElectronTab | None:
    """打开一个独立 BrowserWindow（共享 persist 登录态）。失败返回 None。"""
    d = _call("POST", "/ops/open", {"url": url, "visible": visible}, timeout=timeout)
    wid = d.get("winId")
    if not wid:
        return None
    return ElectronTab(wid, d.get("url", url))


def probe_1688(detail_url: str, wait_s: float = 4.0) -> dict:
    """用 Electron 浏览器探测 1688 详情页（shopbang 式），提取核心字段。

    失败（Electron 不可用/页面异常）返回 {ok: False, degraded: True}，
    调用方应降级 skill Chrome / API。
    """
    if not is_available():
        return {"ok": False, "degraded": True, "degraded_reason": "Electron 宿主不可用"}
    tab = open_tab(detail_url)
    if tab is None:
        return {"ok": False, "degraded": True, "degraded_reason": "打开 Electron 窗口失败"}
    try:
        time.sleep(wait_s)
        js = """
(() => {
  const title = document.title || '';
  const images = [...document.querySelectorAll('.detail-gallery-img, .gallery-img img, .desc-gallery img, img[src*="alicdn"]')]
    .map(i => i.src || i.getAttribute('data-src') || '').filter(Boolean).slice(0, 5);
  const priceEl = document.querySelector('.price-text, .price, [class*="price"]');
  const price = priceEl ? priceEl.textContent.trim().slice(0, 30) : '';
  const body = document.body ? document.body.innerText.slice(0, 400) : '';
  return JSON.stringify({ title, images, price, body });
})()
"""
        result = tab.exec(js)
        data = {}
        try:
            data = json.loads(result) if isinstance(result, str) else {}
        except Exception:
            pass
        ok = bool(data.get("title"))
        return {
            "ok": ok,
            "degraded": not ok,
            "degraded_reason": "" if ok else "页面未加载到商品信息（可能风控/需登录）",
            "source": "electron-ops",
            "data": {"title": data.get("title", ""), "images": data.get("images", []),
                     "price": data.get("price", ""), "page_preview": (data.get("body") or "")[:200]},
        }
    finally:
        tab.close()

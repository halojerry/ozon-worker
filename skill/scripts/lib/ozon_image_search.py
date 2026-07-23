"""1688 以图搜款 — 通过 CDP 操作浏览器网页版，比 API 更准确。

流程：粘贴图片URL到1688搜索框 → 点击图搜按钮 → 提取结果
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# 1688 以图搜款页面
IMAGE_SEARCH_URL = "https://air.1688.com/kapp/1688-search/pc-image-search/"


def search_by_image_cdp(
    image_url: str,
    cdp_url: str = "http://127.0.0.1:9222",
    page_size: int = 5,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    """通过 CDP 操作1688以图搜款网页，返回匹配商品列表。

    Args:
        image_url: 图片 URL
        cdp_url: Chrome CDP 地址
        page_size: 返回数量
        timeout: 等待结果超时秒数

    Returns:
        [{"id": "offer_id", "title": "...", "price": ...}, ...]
    """
    import requests
    import websocket

    # 1. 打开新标签页
    try:
        resp = requests.put(f"{cdp_url}/json/new?", timeout=5)
        resp.raise_for_status()
        tab = resp.json()
        ws_url = tab.get("webSocketDebuggerUrl", "")
    except Exception as e:
        logger.error("Failed to open new tab: %s", e)
        return []

    if not ws_url:
        logger.error("No WebSocket URL returned")
        return []

    ws = None
    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        ws.send(json.dumps({"id": 1, "method": "Page.enable", "params": {}}))
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": IMAGE_SEARCH_URL}}))

        # 等待页面加载
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                ws.settimeout(1)
                m = json.loads(ws.recv())
                if m.get("method") == "Page.frameStoppedLoading":
                    time.sleep(1)
                    break
            except Exception:
                continue

        # 2. 聚焦搜索框
        _eval(ws, 10, 'document.querySelector("#alisearch-input").focus(); document.querySelector("#alisearch-input").select(); "ok"')

        # 3. 输入图片URL
        ws.send(json.dumps({"id": 11, "method": "Input.insertText", "params": {"text": image_url}}))
        ws.settimeout(5)
        for _ in range(10):
            try:
                m = json.loads(ws.recv())
                if m.get("id") == 11:
                    break
            except Exception:
                continue

        time.sleep(2)

        # 4. 获取图搜按钮坐标
        pos_str = _eval(ws, 12, '''
            const btn = document.querySelector(".input-button");
            if (btn) {
                const rect = btn.getBoundingClientRect();
                JSON.stringify({x: rect.x + rect.width/2, y: rect.y + rect.height/2});
            } else { "{}"; }
        ''')

        try:
            pos = json.loads(pos_str)
            x, y = pos.get("x", 0), pos.get("y", 0)
        except (json.JSONDecodeError, TypeError):
            logger.error("Failed to get button position")
            return []

        if x == 0 and y == 0:
            logger.error("Button position is (0,0)")
            return []

        # 5. CDP 鼠标点击
        ws.send(json.dumps({"id": 13, "method": "Input.dispatchMouseEvent", "params": {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1
        }}))
        time.sleep(0.05)
        ws.send(json.dumps({"id": 14, "method": "Input.dispatchMouseEvent", "params": {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1
        }}))

        # 6. 等待结果加载
        time.sleep(timeout)

        # 7. 提取结果
        result_str = _eval(ws, 20, '''
            const cards = document.querySelectorAll(".searchOfferWrapper, .cardui-normal");
            const results = [];
            for (let i = 0; i < Math.min(cards.length, ''' + str(page_size) + '''); i++) {
                const card = cards[i];
                const title = card.querySelector("[class*=title]")?.textContent?.trim().substring(0,80) || "";
                const priceEl = card.querySelector("[class*=price]");
                const priceText = priceEl?.textContent?.trim() || "";
                const priceMatch = priceText.match(/[\\d.]+/);
                const price = priceMatch ? parseFloat(priceMatch[0]) : 0;
                const link = card.querySelector("a")?.href || "";
                const offerMatch = link.match(/offer\\/(\\d+)/);
                const offerId = offerMatch ? offerMatch[1] : "";
                if (title) results.push({id: offerId, title, price});
            }
            JSON.stringify(results);
        ''')

        try:
            results = json.loads(result_str)
            logger.info("CDP image search found %d results", len(results))
            return results
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse search results")
            return []

    except Exception as e:
        logger.error("CDP image search failed: %s", e)
        return []
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


def _eval(ws, msg_id: int, expression: str) -> str:
    """发送 Runtime.evaluate 并返回结果值的字符串。"""
    ws.send(json.dumps({
        "id": msg_id,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True}
    }))
    ws.settimeout(8)
    for _ in range(15):
        try:
            m = json.loads(ws.recv())
            if m.get("id") == msg_id:
                return m.get("result", {}).get("result", {}).get("value", "")
        except Exception:
            continue
    return ""

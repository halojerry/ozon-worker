"""1688 以图搜款 — 通过 CDP 操作浏览器网页版。

流程：粘贴图片URL到搜索框 → 等预览加载 → 点击图搜 → 从新标签页提取结果
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
import websocket

logger = logging.getLogger(__name__)

IMAGE_SEARCH_URL = "https://air.1688.com/kapp/1688-search/pc-image-search/"


def _eval(ws, msg_id: int, expression: str) -> str:
    """Runtime.evaluate 并返回结果值。"""
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


def _wait_page_load(ws, timeout: int = 10) -> bool:
    """等待 Page.frameStoppedLoading。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ws.settimeout(1)
            m = json.loads(ws.recv())
            if m.get("method") == "Page.frameStoppedLoading":
                time.sleep(1)
                return True
        except Exception:
            continue
    return False


def search_by_image_cdp(
    image_url: str,
    cdp_url: str = "http://127.0.0.1:9222",
    page_size: int = 5,
    wait_seconds: int = 12,
) -> list[dict[str, Any]]:
    """通过 CDP 操作1688以图搜款网页，返回匹配商品列表。

    Args:
        image_url: 图片 URL
        cdp_url: Chrome CDP 地址
        page_size: 返回数量
        wait_seconds: 等待搜索结果秒数

    Returns:
        [{"id": "offer_id", "title": "...", "price": float, "badge": "..."}, ...]
    """
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
        return []

    ws = None
    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        ws.send(json.dumps({"id": 1, "method": "Page.enable", "params": {}}))

        # 2. 导航到图搜页面
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": IMAGE_SEARCH_URL}}))
        if not _wait_page_load(ws):
            logger.warning("Page load timeout")
            return []

        # 3. 聚焦搜索框 + 清空
        _eval(ws, 10, 'document.querySelector("#alisearch-input").focus(); document.querySelector("#alisearch-input").select(); document.querySelector("#alisearch-input").value=""')

        # 4. 输入图片URL（用execCommand触发正确的事件）
        _eval(ws, 11, f'document.execCommand("insertText", false, "{image_url}")')

        # 5. 等待预览加载
        time.sleep(3)

        # 6. 点击图搜按钮
        _eval(ws, 12, 'document.querySelector(".input-button").click()')

        # 7. 等待点击生效，再关闭WebSocket
        time.sleep(2)
        ws.close()
        ws = None

        # 8. 等待新标签页出现（带imageId）
        result_ws_url = None
        for _ in range(15):
            time.sleep(1)
            tabs_resp = requests.get(f"{cdp_url}/json", timeout=5)
            tabs = tabs_resp.json()
            # 找最后一个imageId标签页（最新的搜索结果）
            for t in reversed(tabs):
                url = t.get("url", "")
                if "imageId" in url and "1688.com" in url:
                    result_ws_url = t.get("webSocketDebuggerUrl", "")
                    break
            if result_ws_url:
                break

        if not result_ws_url:
            logger.warning("No result tab found with imageId")
            return []

        # 9. 等待结果加载（新标签页需要时间渲染）
        time.sleep(wait_seconds)

        # 10. 从结果标签页提取数据
        ws = websocket.create_connection(result_ws_url, timeout=10)

        # 滚动页面触发懒加载
        _eval(ws, 15, 'window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(2)
        _eval(ws, 16, 'window.scrollTo(0, 0)')
        time.sleep(1)

        result_str = _eval(ws, 20, f'''
            const cards = document.querySelectorAll(".cardui-normal");
            const results = [];
            for (let i = 0; i < Math.min(cards.length, {page_size}); i++) {{
                const card = cards[i];
                const text = card.innerText || "";
                const lines = text.split("\\n").map(l => l.trim()).filter(l => l.length > 0);
                // 跳过徽章行（符合X个条件），找产品标题（中文/俄文开头的行）
                let title = "";
                let badge = "";
                for (const line of lines) {{
                    if (line.match(/符合[\\d\\/]+个条件/)) {{
                        badge = line;
                    }} else if (line.length > 5 && !line.startsWith("¥") && !line.match(/^[\\d.]+$/) && !line.includes("运费") && !line.includes("件") && !line.includes("起批") && !line.includes("揽收")) {{
                        title = line.substring(0, 80);
                        break;
                    }}
                }}
                const priceMatch = text.match(/¥\\s*([\\d.]+)/);
                const price = priceMatch ? parseFloat(priceMatch[1]) : 0;
                const link = card.querySelector("a")?.href || "";
                const offerMatch = link.match(/offer\\/(\\d+)/);
                const offerId = offerMatch ? offerMatch[1] : "";
                if (title) results.push({{id: offerId, title, price, badge}});
            }}
            JSON.stringify(results);
        ''')

        try:
            results = json.loads(result_str)
            logger.info("CDP image search: %d results", len(results))
            return results
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse CDP search results")
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

"""1688 以图搜款 — 通过 CDP 操作浏览器网页版。

流程：粘贴图片URL到搜索框 → 等预览加载 → 点击图搜 → 从新标签页提取结果
支持 YOLO crop region 自动选择（框选主体），提升多主体图片的匹配准确率。
"""
from __future__ import annotations

import json
import logging
import re
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


def _extract_results(ws, page_size: int = 5) -> list[dict[str, Any]]:
    """从结果标签页提取商品列表。"""
    result_str = _eval(ws, 20, f'''
        const cards = document.querySelectorAll(".cardui-normal");
        const results = [];
        for (let i = 0; i < Math.min(cards.length, {page_size}); i++) {{
            const card = cards[i];
            const text = card.innerText || "";
            const lines = text.split("\\n").map(l => l.trim()).filter(l => l.length > 0);
            let title = "";
            let badge = "";
            let supplier = "";
            let sold = "";
            // 非产品关键词
            const skipWords = ["运费","件","起批","揽收","代发","晚揽","必赔","铺货","月代",
                "分销商","评分","回购","店铺","卖家","客服","发货","退货","包邮",
                "H揽","K揽","内天月","¥","价格","库存","现货","秒杀","优惠",
                "有限公司","公司","工厂","厂家","集团","营业","执照","注册",
                "电器厂","制造厂","加工厂","生产厂","配件厂","用品厂","工具厂","日化厂",
                "面单支持","入驻"];
            const companyRe = /[市县].*[厂公司有限]/;
            for (const line of lines) {{
                if (line.match(/符合[\\d\\/]+个条件/)) {{
                    badge = line;
                }} else if (line.match(/[\\d.]+万?\\+件/)) {{
                    sold = line;
                }} else if (companyRe.test(line) || line.match(/[\\u4e00-\\u9fff].*有限|[\\u4e00-\\u9fff].*公司$/)) {{
                    supplier = line;
                }} else if (line.length >= 8) {{
                    const isSkip = skipWords.some(w => line.includes(w)) || line.startsWith("¥") || line.match(/^[\\d.]+$/) || line.match(/^[\\d]+[内天月HK]/) || line.match(/^[\\d]+k?[+]/i);
                    if (!isSkip) {{
                        const cnCount = (line.match(/[\\u4e00-\\u9fff]/g) || []).length;
                        if (cnCount >= 3 && line.length > (title || "").length) {{
                            title = line.substring(0, 80);
                        }}
                    }}
                }}
            }}
            const priceMatch = text.match(/¥\\s*([\\d.]+)/);
            const price = priceMatch ? parseFloat(priceMatch[1]) : 0;
            const links = Array.from(card.querySelectorAll("a") || []);
            let offerId = "";
            let detailUrl = "";
            for (const a of links) {{
                const href = a.href || "";
                // 格式1: /offer/123456.html
                const m1 = href.match(/offer\\/(\\d+)/);
                if (m1) {{ offerId = m1[1]; detailUrl = href; break; }}
                // 格式2: offerId=123456 (查询参数)
                const m2 = href.match(/offerId=(\\d+)/);
                if (m2) {{ offerId = m2[1]; break; }}
            }}
            const img = card.querySelector("img")?.src || "";
            if (title) results.push({{id: offerId, title, price, badge, sold, supplier, image: img, detail_url: detailUrl}});
        }}
        JSON.stringify(results);
    ''')
    try:
        return json.loads(result_str) if result_str else []
    except (json.JSONDecodeError, TypeError):
        return []


def _get_badge_score(badge: str) -> int:
    """从 '符合2/3个条件' 提取分子作为分数。"""
    m = re.search(r"符合(\d+)/(\d+)个条件", badge)
    if m:
        return int(m.group(1))
    return 0


def _click_crop_regions(ws, wait_seconds: int = 8) -> list[dict[str, Any]]:
    """点击所有 YOLO crop regions，返回每个区域的最佳结果。

    1688 用 YOLO 检测图片中的多个主体，用户可以框选主体来精确搜索。
    CDP 鼠标点击可以触发重新搜索（虽然 UI 状态不会更新）。
    """
    # 读取 crop regions
    regions_str = _eval(ws, 30, '''
        const regions = document.querySelectorAll("[data-tracker=yoloCrop]");
        const info = [];
        regions.forEach((r, i) => {
            const rect = r.getBoundingClientRect();
            info.push({
                i: i,
                x: rect.x + rect.width/2,
                y: rect.y + rect.height/2,
                w: rect.width,
                h: rect.height,
                selected: r.className.includes("selectRegion")
            });
        });
        JSON.stringify(info);
    ''')

    try:
        regions = json.loads(regions_str) if regions_str else []
    except (json.JSONDecodeError, TypeError):
        return []

    if len(regions) <= 1:
        return []

    all_results = []

    for region in regions:
        idx = region["i"]
        x, y = region["x"], region["y"]

        if x == 0 and y == 0:
            continue

        # CDP 鼠标点击 crop region
        ws.send(json.dumps({"id": 40 + idx, "method": "Input.dispatchMouseEvent", "params": {
            "type": "mouseMoved", "x": x, "y": y
        }}))
        time.sleep(0.1)
        ws.send(json.dumps({"id": 41 + idx, "method": "Input.dispatchMouseEvent", "params": {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1
        }}))
        time.sleep(0.05)
        ws.send(json.dumps({"id": 42 + idx, "method": "Input.dispatchMouseEvent", "params": {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1
        }}))

        # 等待结果更新
        time.sleep(wait_seconds)

        # 提取结果
        results = _extract_results(ws, page_size=3)
        if results:
            best = results[0]
            best["region_index"] = idx
            all_results.append(best)
            logger.debug("Region %d: %s (score=%d)", idx, best.get("title", "")[:30],
                         _get_badge_score(best.get("badge", "")))

    return all_results


def search_by_image_cdp(
    image_url: str,
    cdp_url: str = "http://127.0.0.1:9222",
    page_size: int = 5,
    wait_seconds: int = 10,
    try_crop_regions: bool = True,
) -> list[dict[str, Any]]:
    """通过 CDP 操作1688以图搜款网页，返回匹配商品列表。

    流程：
    1. 打开图搜页面，输入图片URL
    2. 点击图搜按钮，等待结果
    3. 如果有多个 YOLO crop regions，逐个点击并选择最佳结果
    4. 返回匹配商品列表

    Args:
        image_url: 图片 URL
        cdp_url: Chrome CDP 地址
        page_size: 返回数量
        wait_seconds: 等待搜索结果秒数
        try_crop_regions: 是否尝试 crop region 选择

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

        # 9. 等待结果加载
        time.sleep(wait_seconds)

        # 10. 从结果标签页提取数据
        ws = websocket.create_connection(result_ws_url, timeout=10)

        # 滚动页面触发懒加载
        _eval(ws, 15, 'window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(2)
        _eval(ws, 16, 'window.scrollTo(0, 0)')
        time.sleep(1)

        # 提取默认结果
        results = _extract_results(ws, page_size)

        # 11. 如果有多个 crop regions，尝试点击每个区域获取更精准的结果
        if try_crop_regions and results:
            default_badge = _get_badge_score(results[0].get("badge", ""))
            logger.info("Default result: %s (badge score=%d)", results[0].get("title", "")[:30], default_badge)

            region_results = _click_crop_regions(ws, wait_seconds=max(5, wait_seconds - 3))

            if region_results:
                # 选择 badge 分数最高的结果
                best_region = max(region_results, key=lambda r: _get_badge_score(r.get("badge", "")))
                region_badge = _get_badge_score(best_region.get("badge", ""))

                if region_badge > default_badge:
                    logger.info("Crop region %d better: %s (badge=%d vs %d)",
                                best_region.get("region_index", -1),
                                best_region.get("title", "")[:30],
                                region_badge, default_badge)
                    # 用区域结果替换默认结果的第一个
                    results[0] = best_region

        logger.info("CDP image search: %d results, best: %s", len(results),
                     results[0].get("title", "")[:30] if results else "none")
        return results

    except Exception as e:
        logger.error("CDP image search failed: %s", e)
        return []
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass

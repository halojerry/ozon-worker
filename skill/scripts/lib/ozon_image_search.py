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

from scripts.lib.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

IMAGE_SEARCH_URL = "https://air.1688.com/kapp/1688-search/pc-image-search/"


def _get_badge_score(badge: str) -> int:
    """从 badge 文本提取匹配分数（越高越好）。
    支持格式: "符合2/3个条件" → 2, "匹配度85%" → 85, "相似度: 高" → 50
    """
    if not badge:
        return 0
    # 格式1: "符合X/Y个条件"
    m = re.search(r"符合(\d+)/(\d+)个条件", badge)
    if m:
        return int(m.group(1))
    # 格式2: 百分比 "匹配度85%" / "相似度 92%" 
    m = re.search(r"(匹配度|相似度|similarity)\s*[:：]?\s*(\d+)", badge, re.IGNORECASE)
    if m:
        score = int(m.group(2))
        return min(score, 100)  # cap at 100
    # 格式3: 文本等级 "匹配度较高" / "精准匹配"
    level_map = {"精准": 90, "较高": 70, "高": 60, "一般": 30, "低": 10}
    for k, v in level_map.items():
        if k in badge:
            return v
    return 0


def _extract_results_from_tab(tab, page_size: int = 5) -> list[dict[str, Any]]:
    """从结果标签页提取商品列表（使用 CdpTab.evaluate）。

    ⚠️ 必须包 async IIFE + await_promise=True：脚本内用了顶层 await，
    裸顶层 await 在 awaitPromise=False 时要么 SyntaxError 要么返回无 value
    的 Promise，导致结果恒为空（审计 P0-1）。
    """
    result_str = tab.evaluate(f'''(async () => {{
        // 先滚动触发懒加载
        window.scrollTo(0, document.body.scrollHeight / 2);
        await new Promise(r => setTimeout(r, 500));
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(r => setTimeout(r, 500));

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
            const skipWords = ["运费","件","起批","揽收","代发","晚揽","必赔","铺货","月代",
                "分销商","评分","回购","店铺","卖家","客服","发货","退货","包邮",
                "H揽","K揽","内天月","¥","价格","库存","现货","秒杀","优惠",
                "有限公司","公司","工厂","厂家","集团","营业","执照","注册",
                "电器厂","制造厂","加工厂","生产厂","配件厂","用品厂","工具厂","日化厂",
                "面单支持","入驻"];
            const companyRe = /[市县].*[厂公司有限]/;

            // ✅ 优先用 querySelector 找 badge（类名带随机后缀: badge--XXXXXXXX）
            const badgeEl = card.querySelector('[class*="badge--"]');
            if (badgeEl) {{
                badge = badgeEl.textContent.trim();
            }}

            for (const line of lines) {{
                // badge 已从 DOM 提取，fallback 文本扫描
                if (!badge && line.match(/符合[\\d\\/]+个条件/)) {{
                    badge = line;
                }}
                if (!badge && line.match(/(匹配度|相似度)[\\s:]*[\\d.]+/)) {{
                    badge = line;
                }}
                if (line.match(/[\\d.]+万?\\+件/)) {{
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
            // 价格解析：兼容 半角¥/全角￥/无货币前缀（"4.5元"/"¥4.5起"）
            // 部分卡片价格区懒加载或格式特殊，无匹配时尝试兜底正则
            let priceMatch = text.match(/[¥￥]\\s*([\\d.]+)/);
            if (!priceMatch) {{
                priceMatch = text.match(/([\\d.]+)\\s*元/);
            }}
            const price = priceMatch ? parseFloat(priceMatch[1]) : 0;
            const links = Array.from(card.querySelectorAll("a") || []);
            let offerId = "";
            let detailUrl = "";
            for (const a of links) {{
                const href = a.href || "";
                const m1 = href.match(/offer\\/(\\d+)/);
                if (m1) {{ offerId = m1[1]; detailUrl = href; break; }}
                const m2 = href.match(/offerId=(\\d+)/);
                if (m2) {{ offerId = m2[1]; break; }}
            }}
            const img = card.querySelector("img")?.src || "";
            if (title) results.push({{id: offerId, title, price, badge, sold, supplier, image: img, detail_url: detailUrl}});
        }}
        return JSON.stringify(results);
    }})()''', await_promise=True, timeout=15)
    try:
        return json.loads(result_str) if result_str else []
    except (json.JSONDecodeError, TypeError):
        return []


def _click_crop_regions_on_tab(tab, wait_seconds: int = 8) -> list[dict[str, Any]]:
    """点击所有 YOLO crop regions，返回每个区域的最佳结果。"""
    regions_str = tab.evaluate('''
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
    ''', timeout=10)

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
        try:
            tab._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            time.sleep(0.1)
            tab._send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
            time.sleep(0.05)
            tab._send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        except ConnectionError:
            logger.warning("CDP died clicking crop region %d", idx)
            break

        time.sleep(wait_seconds)

        results = _extract_results_from_tab(tab, page_size=3)
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

    使用 CdpTab 类（而非原始 WebSocket）确保 CDP 通信稳定性。

    Args:
        image_url: 图片 URL
        cdp_url: Chrome CDP 地址
        page_size: 返回数量
        wait_seconds: 等待搜索结果秒数
        try_crop_regions: 是否尝试 crop region 选择

    Returns:
        [{"id": "offer_id", "title": "...", "price": float, "badge": "..."}, ...]
    """
    from scripts.lib.config_store import _require_auth
    from scripts.lib.cdp_client import CdpConnection

    _require_auth()

    cached = cache_get("search", image_url)
    if cached is not None:
        return cached

    conn = None
    search_tab = None
    result_tab = None
    try:
        conn = CdpConnection(cdp_url)

        # 1. 打开图搜页面
        search_tab = conn.new_tab()
        search_tab.navigate(IMAGE_SEARCH_URL, timeout=20)
        time.sleep(2)

        # 2. 输入图片 URL
        safe_url = json.dumps(image_url)
        search_tab.evaluate(
            'document.querySelector("#alisearch-input").focus();'
            'document.querySelector("#alisearch-input").select();'
            'document.querySelector("#alisearch-input").value=""',
            timeout=10,
        )
        time.sleep(0.3)
        search_tab.evaluate(
            f'document.execCommand("insertText", false, {safe_url})',
            timeout=10,
        )
        time.sleep(3)  # 等预览加载

        # 记录搜索前已有的标签页 ID（避免匹配旧的结果页）
        existing_tab_ids = set()
        try:
            for t in requests.get(f"{cdp_url}/json", timeout=5).json():
                existing_tab_ids.add(t.get("id", ""))
        except Exception:
            pass

        # 3. 点击图搜按钮
        search_tab.evaluate(
            'document.querySelector(".input-button").click()',
            timeout=10,
        )
        time.sleep(2)

        # 4. 关闭搜索标签页，等结果标签页出现（只匹配新标签页）
        search_tab.close()
        search_tab = None

        result_ws_url = None
        result_tab_id = None
        for _ in range(20):
            time.sleep(1)
            try:
                tabs_resp = requests.get(f"{cdp_url}/json", timeout=5)
                for t in reversed(tabs_resp.json()):
                    url = t.get("url", "")
                    tid = t.get("id", "")
                    if "imageId" in url and "1688.com" in url and tid not in existing_tab_ids:
                        result_ws_url = t.get("webSocketDebuggerUrl", "")
                        result_tab_id = tid
                        break
            except Exception:
                continue
            if result_ws_url:
                break

        if not result_ws_url:
            logger.warning("No result tab found with imageId")
            return []

        # 5. 连接结果标签页
        from scripts.lib.cdp_client import CdpTab
        result_tab = CdpTab(cdp_url, result_tab_id, result_ws_url)
        time.sleep(wait_seconds)

        # 6. 滚动触发懒加载
        result_tab.evaluate('window.scrollTo(0, document.body.scrollHeight)', timeout=10)
        time.sleep(2)
        result_tab.evaluate('window.scrollTo(0, 0)', timeout=10)
        time.sleep(1)

        # 7. 提取默认结果
        results = _extract_results_from_tab(result_tab, page_size)

        # 8. YOLO crop regions
        if try_crop_regions and results:
            default_badge = _get_badge_score(results[0].get("badge", ""))
            logger.info("Default result: %s (badge score=%d)", results[0].get("title", "")[:30], default_badge)

            region_results = _click_crop_regions_on_tab(result_tab, wait_seconds=max(5, wait_seconds - 3))
            if region_results:
                best_region = max(region_results, key=lambda r: _get_badge_score(r.get("badge", "")))
                region_badge = _get_badge_score(best_region.get("badge", ""))
                if region_badge > default_badge:
                    logger.info("Crop region %d better: %s (badge=%d vs %d)",
                                best_region.get("region_index", -1),
                                best_region.get("title", "")[:30],
                                region_badge, default_badge)
                    results[0] = best_region

        logger.info("CDP image search: %d results, best: %s", len(results),
                     results[0].get("title", "")[:30] if results else "none")
        try:
            from scripts.lib.logging_utils import AuditLogger
            AuditLogger().log("cdp", "image_search", "info", "Image search completed", {
                "result_count": len(results),
                "best_title": results[0].get("title", "")[:50] if results else "",
                "image_url": image_url[:80],
            })
        except Exception:
            pass
        # 只缓存有效结果，避免空结果污染缓存（降级数据不缓存）
        if results:
            cache_set("search", image_url, results, ttl=21600)
        return results

    except ConnectionError as e:
        logger.error("CDP connection died during image search: %s", e)
        return []
    except Exception as e:
        logger.error("CDP image search failed: %s", e)
        return []
    finally:
        if search_tab:
            try:
                search_tab.close()
            except Exception:
                pass
        if result_tab:
            try:
                result_tab.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass

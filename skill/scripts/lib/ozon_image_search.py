"""1688 以图搜款 — 双通道：CDP 网页版 + aibuy mtop API 直调。

流程：
- CDP 通道（search_by_image_cdp）：粘贴图片URL到搜索框 → 点图搜 → 从结果页提取
- aibuy API 通道（search_by_image_aibuy）：Chrome 会话 cookie → mtop 签名直调
  `mtop.com.alibaba.cbu.crossBorder.lp.imageSearch`（免浏览器，秒级返回结构化结果，
  含 offerId/标题/价格/月销/回头率/类目/供应商/normalizationScore）

v0.39: aibuy API 通道（Step 0 实测打通——offerId 直给、guest 视图排序=精准图搜排序、
customerId 用通用 cbu、同 token 连续调用稳定）。fail-fast 纪律：无 token/失败快速返回
[] 由调用方降级，不阻塞 CDP/AK 路径。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

import requests

from scripts.lib.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

IMAGE_SEARCH_URL = "https://air.1688.com/kapp/1688-search/pc-image-search/"

# ═══════════════════════════════════════════════════════════════════════════
# aibuy mtop API 直调（v0.39，Step 0 实测打通）
# ═══════════════════════════════════════════════════════════════════════════
AIBUY_IMAGE_UPLOAD_API = "mtop.com.alibaba.global.select.aibuy.image.upload"
AIBUY_IMAGE_SEARCH_API = "mtop.com.alibaba.cbu.crossBorder.lp.imageSearch"
MTOP_BASE_URL = "https://h5api.m.1688.com/h5/{api}/1.0/"
MTOP_APP_KEY = "12574478"
# token 缓存 key（存 settings.json；含时间戳用于过期判断）
AIBUY_TOKEN_KEY = "aibuy_mtop_token"
AIBUY_TOKEN_TTL_SECONDS = 6 * 3600  # 6h 后需重新从 Chrome 会话刷新
_AIBUY_COOKIE_KEYS = ("_m_h5_tk", "_m_h5_tk_enc", "tfstk", "isg")

# 缓动滚动（3000ms ease-in-out + rAF，每屏 80% 视口——上品帮 scrollPage 反爬节奏）
_EASE_SCROLL_JS = r'''(() => {
    const duration = 3000;
    const startY = window.scrollY;
    const scrollAmount = Math.floor(window.innerHeight * 0.8);
    const targetY = Math.min(startY + scrollAmount, document.documentElement.scrollHeight - window.innerHeight);
    const startTime = performance.now();
    function step(now) {
        const progress = Math.min((now - startTime) / duration, 1);
        const ease = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
        window.scrollTo(0, startY + (targetY - startY) * ease);
        if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
})()'''

# 缓动滚动回顶部（同缓动公式，targetY=0）
_EASE_SCROLL_TOP_JS = r'''(() => {
    const duration = 3000;
    const startY = window.scrollY;
    const targetY = 0;
    const startTime = performance.now();
    function step(now) {
        const progress = Math.min((now - startTime) / duration, 1);
        const ease = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
        window.scrollTo(0, startY + (targetY - startY) * ease);
        if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
})()'''


def _get_badge_score(badge: str) -> int:
    """从 badge 文本提取匹配分数（越高越好）。
    支持格式: "符合2/3个条件" → 2, "匹配度85%" → 85, "相似度: 高" → 50
    v0.19: "全部符合"（matchBadgeFull，1688 官方"满足所有条件"）→ 100
    """
    if not badge:
        return 0
    # 格式0: 全匹配徽标（静态文本为空，由 class 识别后置为"全部符合"）
    if "全部符合" in badge or "满足所有" in badge or "符合全部" in badge:
        return 100
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
        // v0.19: 多段滚动触发懒加载，尽可能加载更多卡片（1688 结果页可达 60+ 张）
        for (let pass = 0; pass < 3; pass++) {{
            window.scrollTo(0, document.body.scrollHeight);
            await new Promise(r => setTimeout(r, 600));
        }}
        window.scrollTo(0, 0);

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
                "面单支持","入驻",
                "商行","贸易","商行","经营部","个体户","专营店","旗舰店"];
            const companyRe = /[市县].*[厂公司有限]/;

            // ✅ v0.19: 全匹配徽标（matchBadgeFull）优先按 class 识别——
            // 1688 官方"满足所有条件"，静态 textContent 为空（hover 才显示属性级原因），
            // 不能依赖文本解析；部分匹配徽标（badge--XXXXXXXX）才读文本
            const fullEl = card.querySelector('[class*="matchBadgeFull"]');
            if (fullEl) {{
                badge = "全部符合";
            }} else {{
                const badgeEl = card.querySelector('[class*="badge--"]');
                if (badgeEl) {{
                    badge = badgeEl.textContent.trim();
                }}
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
    page_size: int = 20,
    wait_seconds: int = 10,
    try_crop_regions: bool = True,
    conn=None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """通过 CDP 操作1688以图搜款网页，返回匹配商品列表。

    使用 CdpTab 类（而非原始 WebSocket）确保 CDP 通信稳定性。

    Args:
        image_url: 图片 URL
        cdp_url: Chrome CDP 地址
        page_size: 返回数量
        wait_seconds: 等待搜索结果秒数
        try_crop_regions: 是否尝试 crop region 选择
        conn: ⚠️ v0.14 E6: 可复用的 CdpConnection（批量场景传同一连接，避免每产品新建）
        force_refresh: ⚠️ v0.14 E5: True 时绕过缓存强制重新图搜（匹配质量低重试用）

    Returns:
        [{"id": "offer_id", "title": "...", "price": float, "badge": "..."}, ...]
    """
    from scripts.lib.cdp_client import CdpConnection
    from scripts.lib.config_store import _require_auth

    _require_auth()

    if not force_refresh:
        cached = cache_get("search", image_url)
        if cached is not None:
            return cached

    # ⚠️ v0.14 E6: 复用外部传入的连接（不新建/不关闭），未传才新建并自持
    own_conn = conn is None
    search_tab = None
    result_tab = None
    try:
        if own_conn:
            conn = CdpConnection(cdp_url)

        # 1. 打开图搜页面
        search_tab = conn.new_tab()
        search_tab.navigate(IMAGE_SEARCH_URL, timeout=20)
        time.sleep(2)

        # 2. 输入图片 URL
        safe_url = json.dumps(image_url)
        # ⚠️ v0.22: 新版图搜页输入框是 .ali-search-input（无 #alisearch-input）。
        # 旧选择器找不到 → 输入失败 → 点搜索时空输入框会误触上传图片按钮。
        # 多选择器兼容 + 输入后校验 value，未填入则重试等待页面 JS 就绪。
        _INPUT_SEL = '#alisearch-input, .ali-search-input, input[class*="search-input"]'
        _input_ok = False
        for _in_attempt in range(3):
            _in_state = search_tab.evaluate(
                f'(() => {{ const el = document.querySelector({json.dumps(_INPUT_SEL)});'
                f' if (!el) return "NO_INPUT";'
                f' el.focus(); el.select(); el.value="";'
                f' document.execCommand("insertText", false, {safe_url});'
                f' return (el.value || "").includes("http") ? "OK" : "EMPTY"; }})()',
                timeout=10,
            )
            if _in_state == "OK":
                _input_ok = True
                break
            logger.warning("图搜输入框状态 %s，等待重试 %d/3", _in_state, _in_attempt + 1)
            time.sleep(2)
        if not _input_ok:
            logger.warning("图搜输入框无法填入 URL（页面结构可能变化），降级 API 图搜")
            return []
        time.sleep(3)  # 等预览加载

        # ⚠️ v0.14 E5 修复: 1688 图搜点按钮后 window.open 弹新窗口，被 Chrome 弹窗拦截
        # → 注入覆盖：window.open 改为当前 tab 延迟导航（结果页就在本 tab，无需新窗口）
        # 返回 mock 窗口对象防止 1688 脚本因 open 返回值异常中断
        _POPUP_BYPASS_JS = (
            "window.open = function(url) {"
            "  setTimeout(function(){ window.location.href = url; }, 200);"
            "  return {closed: false, focus: function(){}, blur: function(){}, postMessage: function(){}};"
            "}; true"
        )
        search_tab.evaluate(_POPUP_BYPASS_JS, timeout=10)

        # 3. 点击图搜按钮
        # ⚠️ v0.22: 点击前再次确认输入框有 URL，避免空输入时误触上传图片按钮
        _click_state = search_tab.evaluate(
            f'(() => {{ const el = document.querySelector({json.dumps(_INPUT_SEL)});'
            f' if (!el || !(el.value || "").includes("http")) return "NO_URL";'
            f' const btn = document.querySelector(".input-button");'
            f' if (!btn) return "NO_BTN";'
            f' btn.click(); return "CLICKED"; }})()',
            timeout=10,
        )
        if _click_state != "CLICKED":
            logger.warning("图搜点击状态 %s（不点上传按钮），降级 API 图搜", _click_state)
            return []

        # 4. 轮询本 tab URL 是否导航到结果页（URL 含 imageId）——不再依赖弹窗新窗口
        # 若 200ms 延迟导航未生效（页面未跳转）→ 整体重试一次（重新打开图搜页再搜）
        result_tab = None
        for _attempt in range(2):
            for _ in range(20):
                time.sleep(1)
                try:
                    cur_url = search_tab.url  # evaluate location.href
                except Exception:
                    cur_url = ""
                if "imageId" in cur_url and "1688.com" in cur_url:
                    result_tab = search_tab
                    break
            if result_tab:
                break
            # 重试：重新导航图搜页 + 重新注入 + 重新输入 + 重新点击
            logger.warning(f"图搜结果页未打开（第{_attempt+1}次），重新打开图搜页重试...")
            try:
                search_tab.navigate(IMAGE_SEARCH_URL, timeout=20)
                time.sleep(2)
                search_tab.evaluate(_POPUP_BYPASS_JS, timeout=10)
                for _in_attempt in range(3):
                    _in_state = search_tab.evaluate(
                        f'(() => {{ const el = document.querySelector({json.dumps(_INPUT_SEL)});'
                        f' if (!el) return "NO_INPUT";'
                        f' el.focus(); el.select(); el.value="";'
                        f' document.execCommand("insertText", false, {safe_url});'
                        f' return (el.value || "").includes("http") ? "OK" : "EMPTY"; }})()',
                        timeout=10,
                    )
                    if _in_state == "OK":
                        break
                    time.sleep(2)
                time.sleep(3)
                search_tab.evaluate(
                    f'(() => {{ const el = document.querySelector({json.dumps(_INPUT_SEL)});'
                    f' if (!el || !(el.value || "").includes("http")) return "NO_URL";'
                    f' const btn = document.querySelector(".input-button");'
                    f' if (btn) btn.click(); return true; }})()',
                    timeout=10,
                )
            except Exception as _re_e:
                logger.debug(f"图搜重试异常: {_re_e}")

        if result_tab is None:
            logger.warning("图搜结果页未打开（弹窗拦截或页面未导航），降级 API 图搜")
            return []

        time.sleep(wait_seconds)

        # 6. 滚动触发懒加载（v0.22: 多次分段滚动合并候选，无徽标环境靠更多样本提匹配率）
        all_results: list[dict[str, Any]] = []
        _seen_ids: set[str] = set()
        for _scroll in range(3):
            result_tab.evaluate(_EASE_SCROLL_JS, timeout=10)
            time.sleep(2)
            _batch = _extract_results_from_tab(result_tab, page_size)
            for _r in _batch:
                _rid = str(_r.get("id", ""))
                if _rid and _rid not in _seen_ids:
                    _seen_ids.add(_rid)
                    all_results.append(_r)
            logger.info("图搜滚动 %d/3: 累计 %d 个候选", _scroll + 1, len(all_results))
        result_tab.evaluate(_EASE_SCROLL_TOP_JS, timeout=10)
        time.sleep(1)

        # 7. 取合并结果（至少 page_size 条）
        results = all_results[: max(page_size, 20)]

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
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# aibuy mtop API 直调（v0.39，免浏览器图搜）
# ═══════════════════════════════════════════════════════════════════════════

def _mtop_sign(token: str, t: str, data: str, app_key: str = MTOP_APP_KEY) -> str:
    """mtop 签名：md5(token & t & appKey & data)。实测成功路径（Step 0）。"""
    return hashlib.md5(f"{token}&{t}&{app_key}&{data}".encode()).hexdigest()


def _parse_mtop_jsonp(text: str) -> dict[str, Any]:
    """解析 JSONP 响应 `callback({...})`，失败返回 {}（fail-fast，不 raise）。"""
    start = text.find("({")
    end = text.rfind("})")
    if start < 0 or end < 0:
        return {}
    try:
        return json.loads(text[start + 1:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}


def _read_aibuy_token() -> dict[str, Any] | None:
    """读取缓存的 mtop token（settings.json）。过期返回 None 触发刷新。"""
    from scripts.lib.config_store import get_setting

    cached = get_setting(AIBUY_TOKEN_KEY) or {}
    if not isinstance(cached, dict):
        return None
    saved_at = float(cached.get("saved_at") or 0)
    if time.time() - saved_at > AIBUY_TOKEN_TTL_SECONDS:
        logger.info("aibuy mtop token 过期（%ds），需从 Chrome 会话刷新", int(time.time() - saved_at))
        return None
    return cached


def _save_aibuy_token(cookies: dict[str, str]) -> None:
    """缓存 mtop token 到 settings.json（含时间戳）。⚠️ 不打印明文（日志脱敏）。"""
    from scripts.lib.config_store import set_setting

    set_setting(AIBUY_TOKEN_KEY, {**cookies, "saved_at": time.time()})


def _fetch_aibuy_cookies_from_chrome(cdp_url: str = "http://127.0.0.1:9222") -> dict[str, str]:
    """从 Chrome 会话读取 1688 cookie（复用常驻 Chrome，无需启动新浏览器）。

    返回 {cookie名: 值}，缺任何关键 cookie 返回 {}（fail-fast）。
    """
    from scripts.lib.cdp_client import CdpConnection

    tab = None
    conn = None
    try:
        conn = CdpConnection(cdp_url)
        tab = conn.new_tab()
        # 打开 1688 首页触发 cookie 就绪（会话已有则直接读）
        tab.navigate("https://www.1688.com/", timeout=20)
        time.sleep(2)
        raw = tab.evaluate("document.cookie", timeout=10) or ""
        cookies: dict[str, str] = {}
        for part in str(raw).split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if k in _AIBUY_COOKIE_KEYS:
                    cookies[k] = v
        return cookies if all(k in cookies for k in _AIBUY_COOKIE_KEYS) else {}
    except Exception as e:
        logger.warning("读取 Chrome cookie 失败（%s），aibuy API 通道不可用", e)
        return {}
    finally:
        if tab:
            try:
                tab.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _mtop_request(
    api: str,
    data: dict[str, Any],
    token_cookies: dict[str, str],
    timeout: int = 15,
) -> dict[str, Any]:
    """mtop 签名请求（GET + JSONP）。失败返回 {}（fail-fast，不 raise）。"""
    mh5tk = token_cookies.get("_m_h5_tk", "")
    token = mh5tk.split("_")[0] if "_" in mh5tk else mh5tk
    if not token:
        logger.warning("aibuy mtop token 为空，通道不可用")
        return {}
    data_str = json.dumps(data, ensure_ascii=False)
    t = str(int(time.time() * 1000))
    sign = _mtop_sign(token, t, data_str)
    params = {
        "jsv": "2.7.5", "appKey": MTOP_APP_KEY, "t": t, "sign": sign,
        "api": api, "v": "1.0", "H5Request": "true",
        "type": "jsonp", "dataType": "jsonp", "callback": "mtopjsonp_aibuy",
        "data": data_str,
    }
    try:
        resp = requests.get(
            MTOP_BASE_URL.format(api=api),
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://aibuy.1688.com/",
                "Accept": "*/*",
            },
            cookies=token_cookies,
        )
        if resp.status_code != 200:
            logger.warning("aibuy mtop %s HTTP %d", api, resp.status_code)
            return {}
        parsed = _parse_mtop_jsonp(resp.text)
        ret = parsed.get("ret") or []
        if ret and "SUCCESS" not in str(ret[0]):
            logger.debug("aibuy mtop %s ret: %s", api, ret[0])
            return {}
        return parsed.get("data") or {}
    except Exception as e:
        logger.warning("aibuy mtop %s 请求异常: %s", api, e)
        return {}


def _aibuy_image_upload(image_url: str, token_cookies: dict[str, str]) -> str:
    """image.upload 拿 yoloCropRegion（主体裁剪区域）。失败返回空串。"""
    data = {"imageUrl": image_url}
    result = _mtop_request(AIBUY_IMAGE_UPLOAD_API, data, token_cookies)
    inner = result.get("result") or {}
    return str(inner.get("yoloCropRegion") or "")


def _aibuy_image_search(
    image_url: str,
    token_cookies: dict[str, str],
    region: str = "",
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """imagesearch 直调，返回归一化候选列表（含 offerId）。失败返回 []。"""
    search_param: dict[str, Any] = {
        "imageAddress": image_url,
        "beginPage": 1,
        "pageSize": page_size,
    }
    if region:
        search_param["imageRegion"] = region
    data = {
        "bizType": "ERP",
        "customerId": "cbu",
        "language": "zh",
        "currency": "CNY",
        "searchParam": json.dumps(search_param, ensure_ascii=False),
    }
    result = _mtop_request(AIBUY_IMAGE_SEARCH_API, data, token_cookies)
    items = ((result.get("result") or {}).get("data")) or []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        offer_id = str(item.get("offerId") or "")
        if not offer_id:
            continue
        price = item.get("price") or "0"
        try:
            price_f = float(str(price).replace(",", ""))
        except (TypeError, ValueError):
            price_f = 0.0
        normalized.append({
            "id": offer_id,
            "title": str(item.get("title") or item.get("translateTitle") or ""),
            "price": price_f,
            "image": str(item.get("imageUrl") or ""),
            "badge": "",  # aibuy 无徽章，靠官方排序 + normalizationScore
            "badge_score": 0,
            "normalization_score": float(item.get("normalizationScore") or 0),
            "month_sold": str(item.get("monthSold") or ""),
            "repurchase_rate": str(item.get("repurchaseRate") or ""),
            "supplier": str(item.get("companyName") or ""),
            "offer_publish_time": str(item.get("offerPublishTime") or ""),
            # v0.39 Issue3 协同: 1688 类目 ID（Issue 3 类目匹配增强——即使 AK 详情
            # 失败也能拿到类目线索，供 source_category_path 推导）
            "cate_level1_id": str(item.get("cateLevel1Id") or ""),
            "cate_level2_id": str(item.get("cateLevel2Id") or ""),
            "category_name": str(item.get("categoryName") or ""),
        })
    return normalized


def search_by_image_aibuy(
    image_url: str,
    cdp_url: str = "http://127.0.0.1:9222",
    page_size: int = 20,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """aibuy mtop API 直调图搜（免浏览器，v0.39）。

    主路径：Chrome 会话 cookie → image.upload 拿 yoloCropRegion → imagesearch 签名直调。
    fail-fast 纪律（Momus 评审）：无 token / 请求失败 → 快速返回 []，由调用方降级
    到 CDP/AK——不 raise、不重试、不慢等。

    Returns:
        [{"id": offerId, "title", "price", "image", "badge": "", "normalization_score", ...}, ...]
    """
    # ⚠️ 不调 _require_auth：aibuy 走 Chrome cookie 通道（无需 MXOU_TOKEN），
    # 且 fail-fast 纪律要求无 token 快速返回 [] 由调用方降级——auth guard 会
    # 在无 MXOU_TOKEN 环境抛 AuthError 破坏契约（CI 无 token 时 3 测试炸）。

    if not force_refresh:
        cached = cache_get("aibuy_search", image_url)
        if cached is not None:
            return cached

    token_cookies = _read_aibuy_token()
    if token_cookies is None:
        # 刷新：从 Chrome 会话读 cookie
        token_cookies = _fetch_aibuy_cookies_from_chrome(cdp_url)
        if not token_cookies:
            logger.warning("aibuy token 刷新失败（Chrome 无 1688 会话），降级 CDP/AK 图搜")
            return []
        _save_aibuy_token(token_cookies)

    # 先上传拿主体区域（提升多主体图匹配率），失败不阻塞搜索
    region = _aibuy_image_upload(image_url, token_cookies)
    results = _aibuy_image_search(image_url, token_cookies, region=region, page_size=page_size)
    if not results:
        logger.warning("aibuy image search 返回空，降级 CDP/AK 图搜")
        return []

    logger.info("aibuy image search: %d results, best: %s",
                len(results), results[0].get("title", "")[:30])
    if results:
        cache_set("aibuy_search", image_url, results, ttl=21600)
    return results

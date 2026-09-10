"""跨平台关键词搜索通道 — discover 跨平台静默货源匹配 v1 批1。

一次 ``search_taobao`` / ``search_pdd``：后台共享 tab 开搜索页（淘宝
``s.taobao.com/search?q=`` / 拼多多 ``mobile.yangkeduo.com/search_result.html
?search_key=``）→ 等渲染 → 解析商品卡 → ``list[SourceOffer]``。给批3 的
discover 接线用：1688 图搜定款照旧，本模块只做**关键词副通道比价**。

实机先例（2026-09-10，feat/cross-platform-sourcing gate，改解析前必读）：
- 淘宝搜索页登录态下 ``[...document.querySelectorAll('a[href*="item.htm"]')]``
  可挖商品卡，卡片带价格/销量文案；登录探测 ``_m_h5_tk`` cookie 缺失（同
  taobao_client 页内 fetch 流程的判定）。
- 拼多多搜索页 ``[data-goods-id]`` 属性**缺席**（客户端渲染），但 innerHTML
  正则 ``goods[_-]?id["'=\\s:]{1,4}(\\d{8,20})`` 能挖到真实 goods_id；渲染后
  body ~344KB；登录态过期重定向 ``login.html``。

纪律（红线）：
- **调用方（批3）决定静默**——本模块所有失败抛类型化异常、绝不返回编造数据：
  未登录 → ``NotLoggedIn``（写模块级负缓存，批3 按 run 跳过该平台）；超时 →
  ``SearchTimeoutError``；其他 → ``CrossSourceSearchError``。解析不出零条 →
  诚实返回 ``[]``（「没找到」不是编造）。
- **字段缺=None 绝不 0**——平台页面不暴露的字段（拼多多卡片销量、正则兜底
  路径的标题/价格/图）一律 ``None``；批2 比价按 freight_unknown 处理。
- **登录态用用户自己 Chrome 会话（CDP）**，本模块不搬 cookie、不落任何凭证；
  登录探测在**页内** JS 完成（``document.cookie``），回传 Python 的只有
  status 布尔语义，cookie 明文绝不离开页面、绝不落日志。
- **tab 生命周期对齐 ``ozon_widget._tab_for_variant_truth``**：find_tab 命中
  （用户/此前搜索遗留的**同源搜索页** tab——搜索页导航到新关键词是该页自身
  语义，安全）→ ``release()`` 只读复用，绝不随连接 close 远程关；未命中 →
  后台新建（``Target.createTarget(background=True)``, 不抢前台），用后关闭。
- 超时预算：``timeout``（默认 12s）覆盖导航+两阶段解析；页内渲染等待预算按
  剩余时间折算（≈60%，上限 8s）注入 JS。建 tab 的浏览器级握手（≤10s）不在
  Python 预算内（基础设施开销）；``timeout < 1s`` 直接按超时拒绝。
- 兜底语义：DOM/属性扫描阶段超时 → 抛 ``SearchTimeoutError``（页面不可用）；
  **兜底正则阶段**失败/超时 → 诚实 ``[]`` + debug 日志（主阶段已真实完成，
  零命中是合法观测）。

价格/运费文案解析复用 ``taobao_client.parse_freight_cny`` 唯一实现（pdd_client
先例，不内联复制）。worker 零改动；批2 ``source_matcher`` 消费本模块产出。

测试：``tests/test_cross_source_search.py``（全 mock CDP evaluate，DOM/
innerHTML 夹具按实机形态手工脱敏重建，零真实 cookie/凭证/Chrome）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from scripts.lib.cdp_client import CdpConnection
from scripts.lib.pdd_client import PDD_LOGIN_URL_MARKERS  # 登录标记唯一实现
from scripts.lib.taobao_client import parse_freight_cny   # 运费文案唯一实现

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 12.0
MAX_OFFERS = 20            # 比价快照不需要整页——够批2 确认同款即可
_RENDER_WAIT_CAP_MS = 8000  # 页内渲染等待上限（占剩余预算 ≈60%，下限 1.5s）

PLATFORM_LABELS = {"taobao": "淘宝", "pdd": "拼多多"}

# 淘宝搜索页登录/验证重定向标记（payload url 兜底判定；页内 JS 同款正则）
_TAOBAO_LOGIN_URL_MARKERS = ("login.taobao.com", "login.tmall.com")


class NotLoggedIn(Exception):
    """平台未登录/会话过期（写模块级负缓存，批3 按 run 静默跳过该平台）。

    有意**不**继承 CrossSourceSearchError：批3 需要先 except NotLoggedIn
    做缓存/跳过，再兜其他错误。
    """

    def __init__(self, platform: str, reason: str = ""):
        label = PLATFORM_LABELS.get(platform, platform)
        super().__init__(
            f"{label}未登录或会话过期（{reason or '原因未知'}）——"
            f"跨源比价静默跳过该平台")
        self.platform = platform
        self.reason = reason


class CrossSourceSearchError(RuntimeError):
    """跨平台关键词搜索失败（人话消息，携带页内原文——失败出声）。"""


class SearchTimeoutError(CrossSourceSearchError):
    """搜索预算耗尽（导航/解析超时）——批3 按静默跳过处理。"""


@dataclass(frozen=True)
class SourceOffer:
    """跨平台货源搜索结果条目（批2 ``source_matcher`` 消费）。

    字段缺失一律 ``None``（页面不暴露 ≠ 0）——绝不编造；``url`` 恒有值
    （无 id 无链接的垃圾条目在归一层丢弃）。
    """

    platform: str  # "taobao" | "pdd"
    title: str | None = None
    price: float | None = None
    freight: float | None = None
    sold: int | None = None
    url: str = ""
    image: str | None = None


# 模块级登录负缓存（platform → False=已确认未登录）。只存失败——成功与否
# 由每次搜索的页内探测自然覆盖，不需要正缓存。批3 每 run 开始 reset。
_LOGIN_STATE: dict[str, bool] = {}


def reset_login_cache(platform: str | None = None) -> None:
    """清登录负缓存（批3 每 run 开始调用；测试隔离用）。"""
    if platform is None:
        _LOGIN_STATE.clear()
    else:
        _LOGIN_STATE.pop(platform, None)


# ────────────────────────── 纯函数（可独立测试）──────────────────────────


def parse_price_text(text: Any) -> float | None:
    """价格文案 → float（区间取低档——比价保守口径）；解析不出 → None。"""
    nums = re.findall(r"\d+(?:\.\d+)?", str(text or ""))
    if not nums:
        return None
    try:
        return min(float(n) for n in nums)
    except ValueError:
        return None


def parse_sold_text(text: Any) -> int | None:
    """销量文案 → int：``1.5万人收货``→15000 / ``月销1万+``→10000 /
    ``2,300人付款``→2300 / ``200+人收货``→200；解析不出 → None。"""
    t = str(text or "").strip()
    if not t:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", t)
    if m:
        try:
            return int(float(m.group(1)) * 10000)
        except ValueError:
            return None
    m = re.search(r"(\d[\d,]*)", t)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


# 正则挖 id（实机先例单一常量）：Python 侧 dig_*（测试/文档）与注入 JS 共用
# 同一 pattern（经 json.dumps 注入 new RegExp），两处绝不漂移。
_TAOBAO_ID_PATTERN = r"""[?&]id=(\d{10,15})"""   # 淘宝 item id 10-15 位
_PDD_ID_PATTERN = r"""goods[_-]?id["'=\s:]{1,4}(\d{8,20})"""  # 实机验证原文

_TAOBAO_ID_RE = re.compile(_TAOBAO_ID_PATTERN)
_PDD_ID_RE = re.compile(_PDD_ID_PATTERN)


def dig_taobao_ids(html: str) -> list[str]:
    """innerHTML 正则挖淘宝 item id（去重保序——JS 兜底阶段的 Python 镜像）。"""
    return _dig_ids(_TAOBAO_ID_RE, html)


def dig_pdd_ids(html: str) -> list[str]:
    """innerHTML 正则挖拼多多 goods_id（去重保序，实机验证 pattern）。"""
    return _dig_ids(_PDD_ID_RE, html)


def _dig_ids(compiled: re.Pattern, html: str) -> list[str]:
    out: list[str] = []
    for m in compiled.finditer(str(html or "")):
        value = m.group(1)
        if value not in out:
            out.append(value)
        if len(out) >= MAX_OFFERS:
            break
    return out


def _https_url(value: Any) -> str:
    """协议相对 URL（//img.pddpic.com/...）→ https 绝对化。"""
    u = str(value or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    return u


def canonical_url(platform: str, item_id: str) -> str:
    """搜索 id → 商品详情 canonical URL（口径对齐 source_platforms）。"""
    if platform == "pdd":
        return f"https://mobile.yangkeduo.com/goods.html?goods_id={item_id}"
    return f"https://item.taobao.com/item.htm?id={item_id}"


def build_offers(platform: str, raw_offers: Any) -> list[SourceOffer]:
    """页内解析载荷 offers → ``list[SourceOffer]``（去重保序，cap MAX_OFFERS）。

    归一口径：url 缺失用 id 拼 canonical；title/price/sold/image 解析不出
    → ``None``（绝不 0/空串编造）；运费走 ``parse_freight_cny``（包邮→0.0
    是**页面文案真实声明**，不算编造）；无 id 无 url 的条目丢弃。
    """
    out: list[SourceOffer] = []
    seen_urls: set[str] = set()
    for raw in (raw_offers or []):
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        url = _https_url(raw.get("url")) or (canonical_url(platform, item_id)
                                             if item_id else "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        freight_text = str(raw.get("freight") or "").strip()
        out.append(SourceOffer(
            platform=platform,
            title=str(raw.get("title") or "").strip() or None,
            price=parse_price_text(raw.get("price")),
            freight=parse_freight_cny(freight_text) if freight_text else None,
            sold=parse_sold_text(raw.get("sold")),
            url=url,
            image=_https_url(raw.get("image")) or None,
        ))
        if len(out) >= MAX_OFFERS:
            break
    return out


def ids_to_offers(platform: str, ids: Any) -> list[SourceOffer]:
    """正则兜底阶段挖到的 id → 仅带 url 的 SourceOffer（其余字段 None）。"""
    out: list[SourceOffer] = []
    seen: set[str] = set()
    for raw in (ids or []):
        item_id = str(raw or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        out.append(SourceOffer(platform=platform,
                               url=canonical_url(platform, item_id)))
        if len(out) >= MAX_OFFERS:
            break
    return out


def _taobao_search_url(keyword: str) -> str:
    return "https://s.taobao.com/search?q=" + quote_plus(str(keyword))


def _pdd_search_url(keyword: str) -> str:
    return ("https://mobile.yangkeduo.com/search_result.html?search_key="
            + quote_plus(str(keyword)))


# ────────────────────────── 页内 JS（单次 evaluate，await_promise）──────────
# 渲染等待 + DOM/卡片解析全部在页内完成（body 不回传 Python——拼多多 ~344KB）。
# 载荷契约（JSON 字符串）：
#   {status: 'ok'|'empty'|'login'|'error', message, url, offers: [
#     {id, title, price, sold, url, image}]}   # 文本原样回传，数值解析在 Python


_TAOBAO_SEARCH_JS = r"""(async () => {
    const BUDGET_MS = __BUDGET_MS__;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    function classifyLogin() {
        const href = String(location.href || '');
        if (/login\.taobao\.com|login\.tmall\.com/i.test(href)) return '搜索页被重定向到登录页';
        const m = document.cookie.match(/(?:^|;\s*)_m_h5_tk=([^;]+)/);
        const raw = m ? decodeURIComponent(m[1]) : '';
        const token = raw.indexOf('_') >= 0 ? raw.split('_')[0] : raw;
        if (!token) return '页内无 _m_h5_tk cookie（未登录或 cookie 未就绪）';
        return '';
    }
    function abs(u) {
        u = String(u || '');
        if (!u) return '';
        if (u.indexOf('//') === 0) return 'https:' + u;
        return u;
    }
    function offersFromDom() {
        const out = [];
        const seen = {};
        document.querySelectorAll('a[href*="item.htm"]').forEach((a) => {
            const href = a.getAttribute('href') || '';
            const m = href.match(/[?&]id=(\d+)/);
            if (!m) return;
            const id = m[1];
            if (seen[id]) return;
            seen[id] = true;
            let card = a;
            for (let i = 0; i < 4; i += 1) {
                if (!card.parentElement) break;
                card = card.parentElement;
                const txt = String(card.innerText || card.textContent || '');
                if (/[¥￥]\s*\d/.test(txt) || /\d+\.\d{2}/.test(txt)) break;
            }
            const text = String(card.innerText || card.textContent || '');
            const pm = text.match(/[¥￥]\s*(\d+(?:\.\d+)?)/);
            const sm = text.match(/(\d+(?:\.\d+)?\s*万|\d[\d,]{0,9})\s*(?:人收货|人付款|月销|收货)/);
            const img = card.querySelector('img');
            const title = (a.getAttribute('title') || a.textContent || '').trim();
            out.push({
                id: id,
                title: title ? title.slice(0, 200) : null,
                price: pm ? pm[1] : null,
                sold: sm ? sm[1].replace(/\s+/g, '') : null,
                url: 'https://item.taobao.com/item.htm?id=' + id,
                image: img ? abs(img.getAttribute('data-src') || img.getAttribute('src') || img.src || '') : '',
            });
        });
        return out.slice(0, 40);
    }
    try {
        const loginReason = classifyLogin();
        if (loginReason) {
            return JSON.stringify({status: 'login', message: loginReason, url: String(location.href || ''), offers: []});
        }
        const start = Date.now();
        while (document.querySelectorAll('a[href*="item.htm"]').length === 0 && Date.now() - start < BUDGET_MS) {
            await sleep(250);
            if (/login\.taobao\.com|login\.tmall\.com/i.test(String(location.href || ''))) {
                return JSON.stringify({status: 'login', message: '渲染等待中被重定向到登录页', url: String(location.href || ''), offers: []});
            }
        }
        const offers = offersFromDom();
        return JSON.stringify({status: offers.length ? 'ok' : 'empty', message: null, url: String(location.href || ''), offers: offers});
    } catch (e) {
        return JSON.stringify({status: 'error', message: String((e && e.message) || e), url: String(location.href || ''), offers: []});
    }
})()"""

_TAOBAO_ID_DIG_JS = r"""(() => {
    try {
        const html = String((document.body && document.body.innerHTML) || '');
        const re = new RegExp(__ID_RE__, 'g');
        const ids = [];
        let m;
        while ((m = re.exec(html)) !== null) {
            if (ids.indexOf(m[1]) < 0) ids.push(m[1]);
            if (ids.length >= 40) break;
        }
        return JSON.stringify({status: ids.length ? 'ok' : 'empty', url: String(location.href || ''), ids: ids});
    } catch (e) {
        return JSON.stringify({status: 'error', message: String((e && e.message) || e), url: String(location.href || ''), ids: []});
    }
})()"""

_PDD_SEARCH_JS = r"""(async () => {
    const BUDGET_MS = __BUDGET_MS__;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    function classifyLogin() {
        const href = String(location.href || '');
        if (/passport\.pinduoduo\.com|renzheng\.pinduoduo\.com/i.test(href)) return '登录/验证页重定向';
        if (/\/login/i.test(String(location.pathname || ''))) return '登录页重定向（login.html）';
        return '';
    }
    function abs(u) {
        u = String(u || '');
        if (!u) return '';
        if (u.indexOf('//') === 0) return 'https:' + u;
        return u;
    }
    function offersFromDom() {
        const out = [];
        const seen = {};
        document.querySelectorAll('[data-goods-id], a[href*="goods"]').forEach((node) => {
            let id = String((node.getAttribute && node.getAttribute('data-goods-id')) || '');
            if (!id) {
                const href = node.getAttribute('href') || '';
                const m = href.match(/[?&]goods_id=(\d+)/);
                if (m) id = m[1];
            }
            if (!id || seen[id]) return;
            seen[id] = true;
            let card = node;
            for (let i = 0; i < 4; i += 1) {
                if (!card.parentElement) break;
                card = card.parentElement;
                const txt = String(card.innerText || card.textContent || '');
                if (/¥\s*\d/.test(txt) || /\d+\.\d{2}/.test(txt)) break;
            }
            const text = String(card.innerText || card.textContent || '');
            const pm = text.match(/¥\s*(\d+(?:\.\d+)?)/);
            const img = card.querySelector('img');
            const title = (node.getAttribute('title') || node.textContent || '').trim();
            out.push({
                id: id,
                title: title ? title.slice(0, 200) : null,
                price: pm ? pm[1] : null,
                sold: null,
                url: 'https://mobile.yangkeduo.com/goods.html?goods_id=' + id,
                image: img ? abs(img.getAttribute('data-src') || img.getAttribute('src') || img.src || '') : '',
            });
        });
        return out.slice(0, 40);
    }
    try {
        const loginReason = classifyLogin();
        if (loginReason) {
            return JSON.stringify({status: 'login', message: loginReason, url: String(location.href || ''), offers: []});
        }
        const start = Date.now();
        while (document.querySelectorAll('[data-goods-id], a[href*="goods"]').length === 0 && Date.now() - start < BUDGET_MS) {
            await sleep(250);
            if (/\/login/i.test(String(location.pathname || ''))) {
                return JSON.stringify({status: 'login', message: '渲染等待中被重定向到登录页', url: String(location.href || ''), offers: []});
            }
        }
        const offers = offersFromDom();
        return JSON.stringify({status: offers.length ? 'ok' : 'empty', message: null, url: String(location.href || ''), offers: offers});
    } catch (e) {
        return JSON.stringify({status: 'error', message: String((e && e.message) || e), url: String(location.href || ''), offers: []});
    }
})()"""

_PDD_ID_DIG_JS = _TAOBAO_ID_DIG_JS  # 同构：只差 __ID_RE__ 注入的 pattern


# ────────────────────────── CDP 编排 ──────────────────────────


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw


def _render_budget_ms(deadline: float) -> int:
    """页内渲染等待预算 ≈ 剩余时间的 60%（上限 8s，下限 1.5s）。"""
    remaining = max(0.0, deadline - time.monotonic())
    return int(min(_RENDER_WAIT_CAP_MS, max(1500, remaining * 1000 * 0.6)))


def _tab_for_search(cdp: CdpConnection, find_pattern: str, url: str,
                    deadline: float) -> tuple[Any, bool]:
    """搜索 tab 获取（对齐 ``ozon_widget._tab_for_variant_truth`` 纪律）。

    - find_tab 命中（用户/此前搜索遗留的**同源搜索页** tab）→ ``release()``
      只读复用（绝不随连接 close 远程关用户页），并导航到本次关键词——搜索页
      导航到新关键词是该页自身语义，非破坏性（计划拍板口径）。
    - 未命中 → 后台新建（``Target.createTarget(background=True)``，不抢前台
      ——静默纪律），再按剩余预算导航。
    返回 (tab, created)；created=True 由调用方 finally 关闭。
    """
    tab = cdp.find_tab(find_pattern)
    if tab is not None:
        cdp.release(tab)
        _navigate_within_budget(tab, url, deadline)
        return tab, False
    tab = cdp.new_tab("about:blank", background=True)
    _navigate_within_budget(tab, url, deadline)
    return tab, True


def _navigate_within_budget(tab: Any, url: str, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining < 1.0:
        return  # 预算耗尽——后续 _evaluate_phase 按 strict 抛超时
    try:
        tab.navigate(url, wait_until="domcontentloaded",
                     timeout=max(1, int(min(remaining, 15.0))))
    except Exception:
        pass  # 导航事件没等到也继续——页内渲染等待/evaluate 超时兜底


def _evaluate_phase(tab: Any, js: str, deadline: float, label: str,
                    *, strict: bool) -> dict | None:
    """单次 evaluate（预算内）；无响应/非 JSON → strict 抛超时，否则 None。"""
    remaining = deadline - time.monotonic()
    if remaining < 1.0:
        if strict:
            raise SearchTimeoutError(
                f"{label} 预算耗尽（timeout 已到）——跨源搜索按超时处理")
        return None
    raw = tab.evaluate(js, await_promise=True,
                       timeout=max(1, int(min(remaining, 30.0))))
    payload = _safe_json(raw)
    if not isinstance(payload, dict):
        if strict:
            raise SearchTimeoutError(
                f"{label} 无响应（CDP evaluate 超时或页面 JS 异常）——"
                "跨源搜索按超时处理")
        return None
    return payload


def _kind_of(payload: dict, platform: str) -> tuple[str, str]:
    """evaluate 载荷 → (kind, detail)。kind ∈ ok/empty/login/error。"""
    status = str(payload.get("status") or "").strip()
    url = str(payload.get("url") or "")
    markers = (_TAOBAO_LOGIN_URL_MARKERS if platform == "taobao"
               else PDD_LOGIN_URL_MARKERS)
    if status == "login" or any(m in url for m in markers):
        return "login", str(payload.get("message") or f"搜索页被重定向到登录页（{url or '未知 URL'}）")
    if status == "ok":
        return "ok", ""
    if status == "empty":
        return "empty", ""
    return "error", str(payload.get("message") or status or "未知载荷状态")


def _ensure_keyword(keyword: str) -> None:
    if not str(keyword or "").strip():
        raise CrossSourceSearchError("搜索关键词为空——跨源比价拒绝空关键词")


def _run_keyword_search(platform, cdp_url, keyword, timeout, cdp, *,
                        find_pattern, search_url, phase1_js, phase2_js,
                        phase2_re, fallback_label) -> list[SourceOffer]:
    """两阶段搜索主体（淘宝/拼多多共享骨架；平台差异全在参数里）。

    阶段1 DOM/属性扫描（strict：无响应抛超时）；零命中且预算允许 → 阶段2
    innerHTML 正则兜底（非 strict：失败降级为诚实空列表）。
    """
    _ensure_keyword(keyword)
    if _LOGIN_STATE.get(platform) is False:
        raise NotLoggedIn(platform, "本 run 此前探测已确认未登录（负缓存命中）")
    if timeout is None or float(timeout) <= 0:
        raise CrossSourceSearchError("timeout 必须为正数")
    if float(timeout) < 1.0:
        # 预算不足以覆盖一次 evaluate（≥1s 下限）——不触达 CDP 直接按超时拒绝
        raise SearchTimeoutError(
            f"timeout={timeout}s 预算过小（<1s）——跨源搜索按超时拒绝")
    label = PLATFORM_LABELS.get(platform, platform)

    own_connection = cdp is None
    if own_connection:
        try:
            cdp = CdpConnection(cdp_url)
        except Exception as exc:
            raise CrossSourceSearchError(
                f"无法连接工具 Chrome CDP（{cdp_url}）：{exc}——请先运行 "
                f"`python scripts/cli.py check` 确认 Chrome 已启动") from exc

    tab = None
    created = False
    try:
        deadline = time.monotonic() + float(timeout)
        tab, created = _tab_for_search(cdp, find_pattern, search_url, deadline)

        payload = _evaluate_phase(
            tab, phase1_js.replace("__BUDGET_MS__", str(_render_budget_ms(deadline))),
            deadline, f"{label}搜索页解析", strict=True)
        kind, detail = _kind_of(payload, platform)
        if kind == "login":
            _LOGIN_STATE[platform] = False
            raise NotLoggedIn(platform, detail)
        if kind == "error":
            raise CrossSourceSearchError(
                f"{label}搜索页解析失败（{detail}）——拒绝编造数据")

        offers = build_offers(platform, payload.get("offers"))
        if not offers and phase2_js is not None:
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                logger.debug("跨源搜索 %s：预算耗尽，跳过 %s 兜底",
                             platform, fallback_label)
                return []
            payload2 = _evaluate_phase(
                tab,
                phase2_js.replace("__ID_RE__", json.dumps(phase2_re.pattern)),
                deadline, f"{label} {fallback_label}兜底", strict=False)
            if payload2 is None:
                # 兜底失败 → 主阶段已真实完成的诚实空（纪律见模块 docstring）
                logger.debug("跨源搜索 %s：%s兜底无响应，按零命中处理",
                             platform, fallback_label)
                return []
            offers = ids_to_offers(platform, payload2.get("ids"))
        return offers[:MAX_OFFERS]
    except (NotLoggedIn, CrossSourceSearchError):
        raise
    except Exception as exc:
        raise CrossSourceSearchError(f"{label}关键词搜索失败: {exc}") from exc
    finally:
        if created and tab is not None:
            try:
                tab.close()
            except Exception:
                pass
        if own_connection:
            try:
                cdp.close()  # type: ignore[union-attr]
            except Exception:
                pass


def search_taobao(cdp_url: str, keyword: str, *, timeout: float = DEFAULT_TIMEOUT,
                  cdp: CdpConnection | None = None) -> list[SourceOffer]:
    """淘宝关键词搜索 → ``list[SourceOffer]``（后台共享 tab，见模块 docstring）。

    Raises:
        NotLoggedIn: 未登录（页内 ``_m_h5_tk`` 缺失/登录页重定向；写负缓存）。
        SearchTimeoutError: 预算耗尽。
        CrossSourceSearchError: 连接/解析失败（绝不返回编造数据）。
    """
    return _run_keyword_search(
        "taobao", cdp_url, keyword, timeout, cdp,
        find_pattern="s.taobao.com/search",
        search_url=_taobao_search_url(keyword),
        phase1_js=_TAOBAO_SEARCH_JS,
        phase2_js=_TAOBAO_ID_DIG_JS,
        phase2_re=_TAOBAO_ID_RE,
        fallback_label="innerHTML 正则挖 item id")


def search_pdd(cdp_url: str, keyword: str, *, timeout: float = DEFAULT_TIMEOUT,
               cdp: CdpConnection | None = None) -> list[SourceOffer]:
    """拼多多关键词搜索 → ``list[SourceOffer]``（search_taobao 同契约）。

    登录探测：``login.html`` 重定向（PDD_LOGIN_URL_MARKERS）→ NotLoggedIn。
    """
    return _run_keyword_search(
        "pdd", cdp_url, keyword, timeout, cdp,
        find_pattern="yangkeduo.com/search_result",
        search_url=_pdd_search_url(keyword),
        phase1_js=_PDD_SEARCH_JS,
        phase2_js=_PDD_ID_DIG_JS,
        phase2_re=_PDD_ID_RE,
        fallback_label="innerHTML 正则挖 goods_id")

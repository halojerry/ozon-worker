"""ozon_fission — discover v3 裂变选品引擎（BFS 卖家扩散）。

以 Ozon 竞品卖家为产品发现引擎：种子商品 → 竞品卖家（fetch_competing_sellers
top 20）→ 卖家店铺产品（fetch_seller_products）→ 再发现 → 迭代。

图结构: 二分图 G=(P∪S, E)，P=商品集，S=卖家集，环路必然存在（卖家互相跟卖）。
终止性: 双 visited 集合（product_id / normalized seller_id）截断环路 +
        三重预算（max_depth / max_total_products / time_budget）任一触顶即停。

依赖方向: ozon_fission → ozon_discovery → ozon_widget/ozon_seller_analytics（无环）。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

from scripts.lib import ozon_discovery
from scripts.lib.ozon_discovery import ProductCandidate

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "discovery",
)


def normalize_seller_id(raw: Any) -> Optional[str]:
    """从 seller_url / seller_id 提取规范化卖家 ID。

    启发式校验：纯数字且长度 ≥5 才有效；slug（如 abc-123）原样返回。
    无效（None/空/太短）→ None（调用方跳过该卖家，防 visited 去重被击穿）。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"/seller/([^/?#]+)", s)
    if m:
        s = m.group(1)
    elif s.startswith("/seller/"):
        s = s[len("/seller/"):]
    elif s.startswith("seller/"):
        s = s[len("seller/"):]
    s = s.rstrip("/").split("?")[0].split("#")[0]
    if not s:
        return None
    if re.fullmatch(r"\d+", s) and len(s) < 5:
        return None
    return s


@dataclass
class FissionBudget:
    max_depth: int = 2
    max_total_products: int = 300
    time_budget: float = 600.0
    max_sellers_per_product: int = 20
    max_products_per_seller: int = 15

    @property
    def budget_exceeded(self) -> bool:
        return False


@dataclass
class FissionState:
    """裂变 BFS 运行时状态（可 JSON 序列化做断点续跑）。"""

    session_id: str
    max_depth: int = 2
    max_total_products: int = 300
    time_budget: float = 600.0
    visited_products: set = field(default_factory=set)
    visited_sellers: set = field(default_factory=set)
    frontier: list = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def should_visit_product(self, pid: str) -> bool:
        return pid not in self.visited_products

    def should_visit_seller(self, sid: str) -> bool:
        return sid not in self.visited_sellers

    def mark_product_seen(self, pid: str) -> None:
        self.visited_products.add(pid)

    def mark_seller_seen(self, sid: str) -> None:
        self.visited_sellers.add(sid)

    def can_add_product(self) -> bool:
        return len(self.visited_products) < self.max_total_products

    def add_product(self, pid: str) -> None:
        self.visited_products.add(pid)

    def depth_allowed(self, depth: int) -> bool:
        return depth <= self.max_depth

    def time_left(self) -> float:
        return self.time_budget - (time.time() - self.started_at)

    @staticmethod
    def extend_chain(chain: list, node_type: str, node_id: str,
                     name: str, depth: int) -> list:
        return chain + [{"type": node_type, "id": node_id, "name": name, "depth": depth}]

    def save(self, path: str) -> None:
        payload = {
            "session_id": self.session_id,
            "max_depth": self.max_depth,
            "max_total_products": self.max_total_products,
            "time_budget": self.time_budget,
            "visited_products": sorted(self.visited_products),
            "visited_sellers": sorted(self.visited_sellers),
            "frontier": self.frontier,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "FissionState":
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return cls(
            session_id=payload["session_id"],
            max_depth=payload.get("max_depth", 2),
            max_total_products=payload.get("max_total_products", 300),
            time_budget=payload.get("time_budget", 600.0),
            visited_products=set(payload.get("visited_products", [])),
            visited_sellers=set(payload.get("visited_sellers", [])),
            frontier=payload.get("frontier", []),
        )


def rank_by_consensus(candidates: list[dict], top_k: int = 10) -> list[dict]:
    """共识排名：按被多少个不同一跳卖家售卖降序，取 top-K（非硬阈值 ≥2）。"""
    ranked = sorted(candidates, key=lambda c: len(c.get("sellers", set())), reverse=True)
    return ranked[:top_k]


def _parallel_workers() -> int:
    """并行 worker 数：min(max(2, os.cpu_count() or 4), 4)。

    纯 stdlib（无 psutil），上限 4 —— CDP 单浏览器多 tab 并发收益有限且 Chrome
    负载随 tab 数上升，4 为实测安全值（worker 端 ThreadPoolExecutor 先例同为 4）。
    """
    return min(max(2, os.cpu_count() or 4), 4)


# ---------------------------------------------------------------------------
# Stage-1 浅抓（D2）：widget API 单请求拉卖家在售商品 → 便宜评分 → 决定深抓
# ---------------------------------------------------------------------------

# 卖家店铺产品页 widget API（in-tab fetch 模式，同 ozon_widget._FETCH_PRODUCT_JS）。
# ⚠️ composer-api.bx 为卖家页研究确认端点；entrypoint-api.bx（仓库内 ozon_widget
# 已实证的端点）为等价兜底，两者都返回 {widgetStates, nextPage}。plain HTTP 会
# 307 __rr 循环，登录态 tab 内 fetch 绕过（local 先例 ozon_widget.py:166）。
_FETCH_SELLER_PRODUCTS_JS = r'''(() => {
    return new Promise(async (resolve) => {
        try {
            const origin = window.location.origin;
            const url = origin + '/api/composer-api.bx/page/json/v2?url='
                + encodeURIComponent('__PAGE_URL__');
            const resp = await fetch(url, {
                method: 'get',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await resp.json();
            resolve(JSON.stringify({
                widgetStates: data.widgetStates || {},
                nextPage: data.nextPage || ''
            }));
        } catch(e) {
            resolve(JSON.stringify({widgetStates: {}, nextPage: '', error: e.message}));
        }
    });
})()'''

# 浅抓翻页上限（顶层 nextPage 跟随的有界页数）
_SHALLOW_MAX_PAGES = 3

# 便宜评分阈值：默认 0.0（全放行，不改变现有裂变行为）；调高即启用「只深抓
# 高热度商品」——被过滤商品不消费预算、不进 visited_products。
_CHEAP_MIN_SCORE = 0.0


def _extract_num(text: Any, default: float = 0.0) -> float:
    """从价格文本提取数字（'1 299 ₽'/'599,50 ₽' → float），失败返回 default。"""
    if text is None:
        return default
    try:
        s = str(text).replace(",", ".").replace(" ", "")
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else default
    except (TypeError, ValueError):
        return default


def _cheap_score(info: dict) -> float:
    """0-1 流行度代理分（stage-1 浅抓评分，决定是否深抓）。

    仅用浅抓可得的最便宜热度信号：rating∈[0,5]→/5；review_count 对数压缩
    （log10(n+1)/4，100 评 ≈0.5）。price<=0 或字段缺失 → 0.0（fail-safe，
    不误放行无价格商品）。评分权重高于评论数（0.7/0.3）。
    """
    try:
        price = float(info.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return 0.0
    try:
        rating = float(info.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    try:
        reviews = int(info.get("review_count") or 0)
    except (TypeError, ValueError):
        reviews = 0
    rating_n = max(0.0, min(rating, 5.0)) / 5.0
    review_n = min(math.log10(reviews + 1) / 4.0, 1.0) if reviews > 0 else 0.0
    return round(0.7 * rating_n + 0.3 * review_n, 4)


def _parse_tile_grid_item(tile: dict) -> dict:
    """tileGridDesktop-* 单 tile：mainState 原子 → sku/title/price/rating/评论数。"""
    d: dict[str, Any] = {
        "sku": str(tile.get("sku") or tile.get("id") or ""),
        "title": "", "price": 0.0, "rating": 0.0,
        "review_count": 0, "image": "", "url": "",
    }
    action = tile.get("action") or {}
    link = action.get("link") or ""
    if link:
        d["url"] = link if str(link).startswith("http") else "https://www.ozon.ru" + link
    for atom in tile.get("mainState") or []:
        if not isinstance(atom, dict):
            continue
        atype = atom.get("type", "")
        if atype == "textDS":
            state = atom.get("textDS") or {}
            if state.get("id") == "name":
                d["title"] = state.get("text", "") or d["title"]
        elif atype == "priceV2":
            prices = (atom.get("priceV2") or {}).get("price") or []
            if prices:
                first = prices[0]
                d["price"] = _extract_num(
                    first.get("text") if isinstance(first, dict) else first)
        elif atype == "labelListV2":
            label = atom.get("labelListV2") or {}
            if (label.get("testInfo") or {}).get("automatizationId") != "tile-list-rating":
                continue
            for l in label.get("labels") or []:
                text = str(l.get("text", "")) if isinstance(l, dict) else ""
                if not text:
                    continue
                m = re.search(r"\d[.,]\d", text)
                if m and not d["rating"]:
                    d["rating"] = float(m.group(0).replace(",", "."))
                if "отзыв" in text.lower():
                    digits = re.sub(r"\D", "", text)
                    if digits:
                        d["review_count"] = int(digits)
    for img_item in (tile.get("tileImage") or {}).get("items") or []:
        if not isinstance(img_item, dict):
            continue
        img = img_item.get("image") or {}
        if img.get("link"):
            d["image"] = img["link"]
            break
    return d


def _parse_search_result_item(item: dict) -> dict:
    """searchResultsV2-*（旧版）: cellTrackingInfo → 同构浅抓 dict。"""
    d: dict[str, Any] = {
        "sku": "", "title": "", "price": 0.0, "rating": 0.0,
        "review_count": 0, "image": "", "url": "",
    }
    cti = item.get("cellTrackingInfo") or {}
    d["sku"] = str(cti.get("id") or "")
    d["title"] = cti.get("title") or ""
    d["price"] = _extract_num(cti.get("finalPrice"))
    link = item.get("link") or ""
    if link:
        d["url"] = link if str(link).startswith("http") else "https://www.ozon.ru" + link
    imgs = item.get("images") or []
    if imgs:
        d["image"] = imgs[0]
    return d


def _parse_seller_tile_widgets(ws: dict) -> list[dict]:
    """卖家店铺 widgetStates → 浅抓 dict 列表（按 sku 去重）。

    tileGridDesktop-*（新版 tile 网格）：mainState 原子解析
    （textDS name / priceV2 / labelListV2 tile-list-rating）；
    searchResultsV2-*（旧版）: cellTrackingInfo 兜底。
    返回 [{sku,title,price,rating,review_count,image,url}]。
    """
    products: list[dict] = []
    seen: set[str] = set()
    for key, raw in (ws or {}).items():
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            if key.startswith("tileGridDesktop"):
                d = _parse_tile_grid_item(item)
            elif key.startswith("searchResultsV2"):
                d = _parse_search_result_item(item)
            else:
                continue
            if not d["sku"] or d["sku"] in seen:
                continue
            seen.add(d["sku"])
            products.append(d)
    return products


def fetch_seller_products_shallow(
    cdp_url: str = "http://127.0.0.1:9222",
    seller_id: str = "",
    max_products: int = 60,
    cdp: Any = None,
) -> list[dict]:
    """Stage-1 浅抓：widget API 单请求拉卖家在售商品（无逐商品导航）。

    复用 ``_ensure_ozon_tab``（ozon_widget）导航到 /seller/{sid}/products/ 后
    in-tab fetch composer-api.bx（登录态 tab 内请求绕过 __rr 307 循环），沿顶层
    ``nextPage`` 翻页（有界 _SHALLOW_MAX_PAGES 页）直到 max_products。任何异常
    → []（fail-safe，调用方回退深抓）。``cdp`` 提供时复用调用方连接，绝不新建。
    返回 [{sku,title,price,rating,review_count,image,url}]。
    """
    if not seller_id:
        return []
    from scripts.lib.ozon_widget import _ensure_ozon_tab

    own_conn = None
    try:
        if cdp is None:
            from scripts.lib.cdp_client import CdpConnection
            own_conn = CdpConnection(cdp_url)
            cdp = own_conn
        tab = _ensure_ozon_tab(cdp, f"https://www.ozon.ru/seller/{seller_id}/products/")
        out: list[dict] = []
        seen: set[str] = set()
        page_url = f"/seller/{seller_id}/products/"
        for _page in range(_SHALLOW_MAX_PAGES):
            js = _FETCH_SELLER_PRODUCTS_JS.replace("__PAGE_URL__", page_url)
            raw = tab.evaluate(js, await_promise=True, timeout=20)
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
            for d in _parse_seller_tile_widgets(parsed.get("widgetStates") or {}):
                if d["sku"] not in seen:
                    seen.add(d["sku"])
                    out.append(d)
            if len(out) >= max_products:
                break
            next_page = parsed.get("nextPage") or ""
            if not next_page or next_page == page_url:
                break
            page_url = next_page
        return out[:max_products]
    except Exception as exc:
        logger.warning("fetch_seller_products_shallow(%s) failed: %s", seller_id, exc)
        return []
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:
                pass


def run_fission(
    seed_products: list[ProductCandidate],
    cdp_url: str = "http://127.0.0.1:9222",
    max_depth: int = 2,
    max_total_products: int = 300,
    time_budget: float = 600.0,
    max_sellers_per_product: int = 20,
    max_products_per_seller: int = 15,
    session_id: str = "fission",
    checkpoint_dir: Optional[str] = None,
    stage_callback=None,
) -> list[ProductCandidate]:
    """BFS 裂变主循环。

    输入种子（深度 0 候选，含 competing_seller_list）→ 逐层展开：
    - 商品 → 其竞品卖家（top N）→ 卖家店铺产品 → 再发现竞品卖家 → 下一层
    - 双 visited 截断环路；三重预算（max_depth/max_total_products/time_budget）终止
    - 每出队一个元素 checkpoint（原子写），断点续跑
    返回合并后的全部候选（种子 + 裂变发现）。
    """
    from scripts.lib.cdp_client import CdpConnection

    state = FissionState(
        session_id=session_id,
        max_depth=max_depth,
        max_total_products=max_total_products,
        time_budget=time_budget,
    )
    out: list[ProductCandidate] = list(seed_products)
    seed_category = next((str(c.category) for c in seed_products if c.category), "")
    for c in seed_products:
        state.mark_product_seen(c.ozon_product_id)
        if seed_category:
            c._seed_category_id = seed_category
    frontier = [("product", c.ozon_product_id, 0, []) for c in seed_products]

    with CdpConnection(cdp_url) as cdp:
        while frontier:
            node_type, node_id, depth, chain = frontier.pop(0)
            if state.time_left() <= 0:
                break
            if node_type == "product":
                _expand_product(state, cdp, cdp_url, node_id, depth, chain,
                                frontier, out, max_sellers_per_product, seed_category)
            else:
                _expand_seller(state, cdp, cdp_url, node_id, depth, chain,
                               frontier, out, max_products_per_seller, seed_category)
            if checkpoint_dir:
                path = os.path.join(checkpoint_dir, f"fission_state_{session_id}.json")
                try:
                    os.makedirs(checkpoint_dir, exist_ok=True)
                    state.save(path)
                except Exception:
                    pass
            if stage_callback:
                stage_callback(len(out), len(state.visited_sellers), depth)

    return out


def _expand_product(state: FissionState, cdp: Any, cdp_url: str, pid: str,
                    depth: int, chain: list, frontier: list, out: list,
                    max_sellers: int, seed_category: str = "") -> None:
    if not state.depth_allowed(depth + 1):
        return
    # 种子节点（depth=0）直接用候选已保留的 competing_seller_list（P3 零成本）；
    # 裂变商品节点（depth>0）才重新调 widget API。
    sellers = []
    if depth == 0:
        for c in out:
            if c.ozon_product_id == pid:
                sellers = c.competing_seller_list
                break
    else:
        from scripts.lib.ozon_widget import fetch_competing_sellers
        sellers = fetch_competing_sellers(cdp_url, pid, cdp=cdp).get("sellers", []) or []
    for s in sellers[:max_sellers]:
        sid = normalize_seller_id(s.get("seller_id") or s.get("seller_url"))
        if not sid or not state.should_visit_seller(sid):
            continue
        state.mark_seller_seen(sid)
        name = s.get("seller_name", "") or sid
        new_chain = state.extend_chain(chain, "seller", sid, name, depth)
        frontier.append(("seller", sid, depth + 1, new_chain))


def _analyze_product_parallel(cdp_url: str, pid: str) -> ProductCandidate:
    """线程 worker：独立 CdpConnection + 独立 tab 分析单产品。

    ⚠️ 并行安全（v0.31.1 教训）：``CdpConnection._tabs`` 非线程安全，绝不跨线程共享
    连接；``find_tab`` 是**浏览器级**查询（返回「第一个 ozon.ru tab」——可能是用户 tab
    或首个 worker tab），并行 worker 若走默认 find_tab 会抢同一 tab 导航 → 实例级覆盖
    ``find_tab`` 恒返回本线程自己 new_tab 创建的 tab。本线程 tab 非用户 tab，关闭走
    release-before-close 契约：``_ensure_ozon_tab`` 复用后 ``release`` 移出连接管理，
    finally 中显式 ``tab.close(close_remote=True)`` + ``conn.close`` 兜底（连接内所有
    tab 均为本线程创建，可安全远程关闭，不误伤用户标签页）。
    """
    from scripts.lib.cdp_client import CdpConnection

    conn = CdpConnection(cdp_url)
    tab = None
    try:
        tab = conn.new_tab()  # about:blank 零导航；_ensure_ozon_tab 之后再导航到产品页
        conn.find_tab = lambda _pattern: tab  # 实例级覆盖：绝不命中用户首个 ozon.ru tab
        return ozon_discovery._analyze_product(cdp_url, conn, pid)
    finally:
        if tab is not None:
            try:
                tab.close(close_remote=True)
            except Exception:
                pass
        try:
            conn.close(close_remote=True)
        except Exception:
            pass


def _finalize_product_candidate(state: FissionState, candidate: ProductCandidate,
                                pid: str, depth: int, chain: list, frontier: list,
                                out: list, seed_category: str = "") -> None:
    """主线程收尾：写 FissionState 相关字段 + 追加 out/frontier（全部主线程执行）。"""
    candidate.chain_depth = depth
    candidate.source_chain = chain
    candidate._seed_category_id = seed_category
    out.append(candidate)
    new_chain = state.extend_chain(chain, "product", pid,
                                   candidate.ozon_title[:30] or pid, depth)
    frontier.append(("product", pid, depth, new_chain))


def _expand_seller(state: FissionState, cdp: Any, cdp_url: str, sid: str,
                   depth: int, chain: list, frontier: list, out: list,
                   max_products: int, seed_category: str = "") -> None:
    # Stage-1 浅抓（widget 单请求含评分/评论数）→ 便宜评分过滤后仅深抓高分商品；
    # 浅抓失败/为空 → 回退深抓（DOM 滚动采集，v0.29 原逻辑）
    shallow = fetch_seller_products_shallow(
        cdp_url=cdp_url, seller_id=sid, max_products=max_products, cdp=cdp)
    score_map: dict[str, float] = {}
    if shallow:
        pids = [str(d["sku"]) for d in shallow[:max_products] if d.get("sku")]
        score_map = {str(d["sku"]): _cheap_score(d) for d in shallow[:max_products]
                     if d.get("sku")}
    else:
        pids = ozon_discovery.fetch_seller_products(
            cdp_url=cdp_url, seller_id=sid, max_products=max_products, cdp=cdp)
    # 主线程预过滤：预算 + visited + 便宜评分守卫后再派发（预算精确记账，绝不跨线程动 FissionState）
    work: list[str] = []
    for pid in pids:
        if not state.can_add_product():
            break
        if not state.should_visit_product(pid):
            continue
        # ⚠️ D2 预算记账：便宜过滤必须先于 mark_product_seen——被分数过滤的商品
        # 不消费 max_total_products 预算、不进 visited_products（原代码先 mark 后过滤）
        if score_map and score_map.get(pid, 0) < _CHEAP_MIN_SCORE:
            continue
        state.mark_product_seen(pid)
        work.append(pid)
    if not work:
        return
    workers = _parallel_workers()
    if workers <= 1:
        # 单 worker：沿用共享 cdp 串行路径（零回归，不新建连接）
        for pid in work:
            candidate = ozon_discovery._analyze_product(cdp_url, cdp, pid)
            _finalize_product_candidate(state, candidate, pid, depth, chain,
                                        frontier, out, seed_category)
        return
    # 并行：只把每 pid 的 _analyze_product I/O 丢给线程池；状态收集回主线程
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_pid = {
            pool.submit(_analyze_product_parallel, cdp_url, pid): pid
            for pid in work
        }
        for fut in as_completed(future_to_pid):
            pid = future_to_pid[fut]
            candidate = fut.result()
            _finalize_product_candidate(state, candidate, pid, depth, chain,
                                        frontier, out, seed_category)

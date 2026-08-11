"""ozon_fission stage-1 浅抓测试（D2）— widget 单请求浅抓 + 便宜评分过滤 + 预算记账回归。

核心验证点：
1. tileGridDesktop-* widgetStates → 浅抓 dict（sku/title/price/url/rating/review_count）。
2. _cheap_score：更高评分/评论数 → 更高分；price<=0 或字段缺失 → 0.0。
3. _expand_seller 浅抓命中 → 只有过分数阈值的 pid 才进深抓（_analyze_product）；
   被拒 pid 不消耗预算、不进 visited_products（预算记账 gotcha 回归）。
4. 浅抓为空 → 回退深抓（ozon_discovery.fetch_seller_products）→ 全部处理。
5. fetch_seller_products_shallow：复用 _ensure_ozon_tab、沿 nextPage 翻页、
   cdp= 提供时绝不新建连接。
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as ozon_discovery
from scripts.lib import ozon_fission as ozon_fission
from scripts.lib.ozon_discovery import ProductCandidate


def _mk_seed(pid: str, sellers: list) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"种子{pid}", ozon_price=1500)
    c.competing_sellers = len(sellers)
    c.competing_seller_list = sellers
    return c


def _tile(sku: str, title: str, price_text: str, rating_text: str,
          review_text: str) -> dict:
    """按真实 tileGridDesktop mainState 原子结构构造单 tile。"""
    return {
        "sku": sku,
        "action": {"link": f"/product/slug-{sku}/"},
        "tileImage": {"items": [{"image": {"link": f"https://ir.ozone.ru/img/{sku}.jpg"}}]},
        "mainState": [
            {"type": "textDS", "textDS": {"id": "name", "text": title}},
            {"type": "priceV2", "priceV2": {"price": [{"text": price_text}, {"text": ""}]}},
            {"type": "labelListV2", "labelListV2": {
                "testInfo": {"automatizationId": "tile-list-rating"},
                "labels": [{"text": rating_text}, {"text": review_text}],
            }},
        ],
    }


def _tile_grid_widget_states() -> dict[str, str]:
    """tileGridDesktop-* 的 widgetStates 样本（值为 JSON 字符串，同真实响应）。"""
    return {"tileGridDesktop-1": json.dumps({"items": [
        _tile("1001", "商品A", "1 299 ₽", "4.8", "123 отзыва"),
        _tile("1002", "商品B", "599 ₽", "3.5", "4 отзыва"),
    ]})}


def test_parse_tile_grid_widgets():
    """tileGridDesktop-* → 浅抓 dict 正确（sku/title/price/url/rating/评论数）。"""
    parsed = ozon_fission._parse_seller_tile_widgets(_tile_grid_widget_states())
    assert len(parsed) == 2
    first = parsed[0]
    assert first["sku"] == "1001"
    assert first["title"] == "商品A"
    assert first["price"] == 1299.0
    assert first["url"] == "https://www.ozon.ru/product/slug-1001/"
    assert first["image"] == "https://ir.ozone.ru/img/1001.jpg"
    assert first["rating"] == 4.8
    assert first["review_count"] == 123
    second = parsed[1]
    assert second["price"] == 599.0
    assert second["rating"] == 3.5
    assert second["review_count"] == 4


def test_parse_seller_tile_widgets_legacy_search_results():
    """searchResultsV2-*（旧版 cellTrackingInfo）→ 同构浅抓 dict。"""
    ws = {"searchResultsV2-1": json.dumps({"items": [{
        "cellTrackingInfo": {"id": "7001", "title": "旧版商品",
                             "finalPrice": "999 ₽"},
        "link": "/product/slug-7001/",
        "images": ["https://ir.ozone.ru/img/7001.jpg"],
    }]})}
    parsed = ozon_fission._parse_seller_tile_widgets(ws)
    assert len(parsed) == 1
    assert parsed[0]["sku"] == "7001"
    assert parsed[0]["title"] == "旧版商品"
    assert parsed[0]["price"] == 999.0
    assert parsed[0]["url"] == "https://www.ozon.ru/product/slug-7001/"


def test_parse_seller_tile_widgets_dedups_and_skips_invalid():
    """同 sku 跨 tile 去重；缺 sku 的 tile 跳过。"""
    ws = {"tileGridDesktop-1": json.dumps({"items": [
        _tile("1001", "A", "100 ₽", "4.0", "1 отзыв"),
        _tile("1001", "A dup", "100 ₽", "4.0", "1 отзыв"),
        {"action": {"link": "/product/x-99999/"}},  # 无 sku
    ]})}
    parsed = ozon_fission._parse_seller_tile_widgets(ws)
    assert [d["sku"] for d in parsed] == ["1001"]


def test_cheap_score_ordering_and_gates():
    """评分/评论数更高 → 分更高；price<=0 或字段缺失 → 0.0。"""
    low = ozon_fission._cheap_score({"price": 100, "rating": 3.0, "review_count": 5})
    high = ozon_fission._cheap_score({"price": 100, "rating": 5.0, "review_count": 5000})
    assert high > low, f"{high} 应 > {low}"
    more = ozon_fission._cheap_score({"price": 100, "rating": 4.0, "review_count": 5000})
    fewer = ozon_fission._cheap_score({"price": 100, "rating": 4.0, "review_count": 10})
    assert more > fewer, f"评论数更多应分更高: {more} > {fewer}"
    assert ozon_fission._cheap_score({"price": 0, "rating": 5.0, "review_count": 500}) == 0.0
    assert ozon_fission._cheap_score({}) == 0.0
    assert ozon_fission._cheap_score({"price": -5, "rating": 5.0, "review_count": 500}) == 0.0


def test_expand_seller_shallow_filters_by_score():
    """浅抓命中 → 只有过分数阈值 pid 进深抓；被拒 pid 不进 visited_products（预算 gotcha）。"""
    shallow = [
        {"sku": "1001", "title": "热门", "price": 100, "rating": 5.0,
         "review_count": 5000, "image": "", "url": ""},
        {"sku": "1002", "title": "冷门", "price": 0, "rating": 0.0,
         "review_count": 0, "image": "", "url": ""},
    ]
    state = ozon_fission.FissionState(session_id="t", max_total_products=100)
    analyzed: list[str] = []

    def fake_analyze(cdp_url, cdp, pid):
        analyzed.append(pid)
        return _mk_seed(pid, [])

    with mock.patch.object(ozon_fission, "fetch_seller_products_shallow",
                           return_value=shallow) as ms, \
         mock.patch.object(ozon_fission, "_parallel_workers", return_value=1), \
         mock.patch.object(ozon_discovery, "fetch_seller_products") as deep, \
         mock.patch.object(ozon_discovery, "_analyze_product",
                           side_effect=fake_analyze) as ap, \
         mock.patch.object(ozon_fission, "_CHEAP_MIN_SCORE", 0.5):
        ozon_fission._expand_seller(
            state, cdp=None, cdp_url="http://127.0.0.1:9222", sid="10001",
            depth=1, chain=[], frontier=[], out=[], max_products=10, seed_category="")
    ms.assert_called_once()
    deep.assert_not_called()
    assert analyzed == ["1001"], f"只有高分商品应深抓, got {analyzed}"
    assert ap.call_count == 1
    assert "1001" in state.visited_products
    assert "1002" not in state.visited_products, \
        "被分数过滤的商品不应消耗预算/进 visited_products"


def test_expand_seller_shallow_empty_falls_back_deep():
    """浅抓为空 → 回退深抓（ozon_discovery.fetch_seller_products）→ 全部处理。"""
    state = ozon_fission.FissionState(session_id="t", max_total_products=100)
    analyzed: list[str] = []

    def fake_analyze(cdp_url, cdp, pid):
        analyzed.append(pid)
        return _mk_seed(pid, [])

    with mock.patch.object(ozon_fission, "fetch_seller_products_shallow",
                           return_value=[]) as ms, \
         mock.patch.object(ozon_fission, "_parallel_workers", return_value=1), \
         mock.patch.object(ozon_discovery, "fetch_seller_products",
                           return_value=["D1", "D2"]) as deep, \
         mock.patch.object(ozon_discovery, "_analyze_product",
                           side_effect=fake_analyze):
        ozon_fission._expand_seller(
            state, cdp=None, cdp_url="http://127.0.0.1:9222", sid="10001",
            depth=1, chain=[], frontier=[], out=[], max_products=10, seed_category="")
    ms.assert_called_once()
    deep.assert_called_once()
    assert analyzed == ["D1", "D2"], f"深抓回退应处理全部 pid, got {analyzed}"
    assert "D1" in state.visited_products and "D2" in state.visited_products


def test_fetch_seller_products_shallow_parses_and_follows_next_page():
    """单 tab in-tab evaluate，沿顶层 nextPage 翻页，跨页去重。"""
    tab = mock.MagicMock()
    page1 = json.dumps({
        "widgetStates": _tile_grid_widget_states(),
        "nextPage": "/seller/10001/products/?page=2",
    })
    page2 = json.dumps({
        "widgetStates": {"tileGridDesktop-2": json.dumps({"items": [
            _tile("2001", "商品C", "399 ₽", "4.2", "50 отзывов"),
        ]})},
        "nextPage": "",
    })
    tab.evaluate.side_effect = [page1, page2]
    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch("scripts.lib.ozon_widget._ensure_ozon_tab",
                    return_value=tab) as ensure:
        out = ozon_fission.fetch_seller_products_shallow(
            cdp_url="http://127.0.0.1:9222", seller_id="10001", max_products=10)
    ensure.assert_called_once()
    assert tab.evaluate.call_count == 2, "应翻页一次（page1 + page2）"
    assert {d["sku"] for d in out} == {"1001", "1002", "2001"}
    assert len(out) == 3


def test_fetch_seller_products_shallow_reuses_cdp_no_new_connection():
    """cdp= 提供 → 复用调用方连接，绝不新建 CdpConnection。"""
    tab = mock.MagicMock()
    tab.evaluate.return_value = json.dumps({"widgetStates": {}, "nextPage": ""})
    cdp = mock.MagicMock()
    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch("scripts.lib.ozon_widget._ensure_ozon_tab", return_value=tab) as ensure:
        out = ozon_fission.fetch_seller_products_shallow(
            cdp_url="http://127.0.0.1:9222", seller_id="10001", cdp=cdp)
    conn_cls.assert_not_called()
    ensure.assert_called_once_with(cdp, "https://www.ozon.ru/seller/10001/products/")
    assert out == []


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

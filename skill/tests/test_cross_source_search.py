#!/usr/bin/env python3
"""discover 跨平台静默货源匹配 v1 批1 — 关键词搜索通道（cross_source_search）TDD。

全部 mock CDP evaluate，绝不真启 Chrome、零真实 cookie/凭证；DOM/innerHTML
夹具按 2026-09-10 实机探测形态手工脱敏重建（淘宝：a[href*="item.htm"] 卡片带
价/销量文案；拼多多：[data-goods-id] 属性缺席、innerHTML 正则挖 goods_id、
body ~344KB、登录态重定向 login.html）。

覆盖:
- SourceOffer：冻结 + 缺字段 None 纪律（绝不编造 0）
- 淘宝 DOM 解析路径 / innerHTML 正则兜底路径 / 空结果 / 未登录（_m_h5_tk 缺失
  → NotLoggedIn + 负缓存）/ 超时（evaluate 无响应 → SearchTimeoutError）
- 拼多多同矩阵（属性扫描 + 正则兜底 + login.html 重定向）
- tab 生命周期：自建 tab 用后关闭；用户/同源 tab 只读复用绝不 close（release）
- 纯函数：价/销量文案解析、正则挖 id（344KB 形态夹具）、offers 归一

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_cross_source_search.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import cross_source_search as css  # noqa: E402

# ── 手工夹具（2026-09-10 实机 DOM 形态脱敏重建）──

_TB_OFFER_A = {
    "id": "679836775118",
    "title": "创意简约陶瓷马克杯办公水杯定制",
    "price": "12.90",
    "sold": "1.5万人收货",
    "url": "https://item.taobao.com/item.htm?id=679836775118",
    "image": "//img.alicdn.com/imgextra/tb-main.jpg",
}
_TB_OFFER_B = {
    "id": "654654136372",
    "title": "保温杯女316不锈钢大容量",
    "price": "¥25.80",
    "sold": "2,300人付款",
    "url": "https://item.taobao.com/item.htm?id=654654136372",
    "image": "https://img.alicdn.com/imgextra/tb-second.jpg",
}
_PDD_OFFER_A = {
    "id": "538120412345",
    "title": "保温杯女316不锈钢便携小巧",
    "price": "9.90",
    "sold": None,
    "url": "https://mobile.yangkeduo.com/goods.html?goods_id=538120412345",
    "image": "//img.pddpic.com/mian.jpg",
}


def _tb_dom_payload(offers, *, status=None, url="https://s.taobao.com/search?q=cup"):
    return json.dumps({
        "status": status or ("ok" if offers else "empty"),
        "message": None,
        "url": url,
        "offers": offers,
    })


def _ids_payload(ids, *, url="https://s.taobao.com/search?q=cup"):
    return json.dumps({
        "status": "ok" if ids else "empty",
        "url": url,
        "ids": ids,
    })


# ── mock CDP（绝不启 Chrome）──


class _FakeTab:
    def __init__(self, url: str, evaluate_results: list | None = None):
        self.url = url
        self.evaluate_calls: list[tuple[str, int]] = []
        self._results = list(evaluate_results or [])
        self.closed = False
        self.nav_calls: list[tuple[str, int]] = []

    def evaluate(self, js, await_promise=False, timeout=15):
        self.evaluate_calls.append((js, timeout))
        if self._results:
            item = self._results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return ""

    def navigate(self, url, wait_until="domcontentloaded", timeout=30):
        self.nav_calls.append((url, timeout))
        self.url = url

    def wait_for_load(self, timeout=15):
        return None

    def close(self, close_remote: bool = True):
        self.closed = True


class _FakeConnection:
    def __init__(self, tab: _FakeTab | None = None, cdp_url="http://127.0.0.1:9222",
                 new_tab_results: list | None = None):
        self.cdp_url = cdp_url
        self._tab = tab
        self._new_tab_results = list(new_tab_results or [])
        self.created_tabs: list[_FakeTab] = []
        self.released: list = []
        self.closed = False

    def find_tab(self, pattern):
        if self._tab is not None and pattern in self._tab.url:
            return self._tab
        return None

    def new_tab(self, url="about:blank", background=False):
        tab = _FakeTab(url, self._new_tab_results)
        tab.created_background = background
        self.created_tabs.append(tab)
        return tab

    def release(self, tab):
        self.released.append(tab)

    def close(self, close_remote=True):
        self.closed = True


class _BoomConnection:
    """登录负缓存命中后任何 CDP 触达都算 bug。"""

    def find_tab(self, pattern):
        raise AssertionError("登录缓存命中后不得触达 CDP")

    def new_tab(self, *a, **k):
        raise AssertionError("登录缓存命中后不得触达 CDP")


@pytest.fixture(autouse=True)
def _fresh_login_cache():
    css.reset_login_cache()
    yield
    css.reset_login_cache()


# ── SourceOffer：冻结 + None 纪律 ──


class TestSourceOffer:
    def test_frozen(self):
        offer = css.SourceOffer(platform="taobao", url="https://x")
        with pytest.raises(Exception):
            offer.price = 1.0

    def test_missing_fields_default_none_not_zero(self):
        offer = css.SourceOffer(platform="pdd", url="https://x")
        assert offer.title is None
        assert offer.price is None, "缺价必须是 None，绝不编造 0"
        assert offer.freight is None
        assert offer.sold is None
        assert offer.image is None

    def test_login_error_is_plain_exception_not_search_error(self):
        assert issubclass(css.NotLoggedIn, Exception)
        assert not issubclass(css.NotLoggedIn, css.CrossSourceSearchError)
        exc = css.NotLoggedIn("taobao", "页内无 _m_h5_tk cookie")
        assert exc.platform == "taobao"


# ── 纯函数：文案解析 ──


class TestParseTexts:
    def test_parse_price(self):
        assert css.parse_price_text("¥12.90") == 12.9
        assert css.parse_price_text("12.90-25.00") == 12.9, "价格区间取低档（保守）"
        assert css.parse_price_text("￥9.9") == 9.9
        assert css.parse_price_text("25") == 25.0
        assert css.parse_price_text("暂无") is None
        assert css.parse_price_text("") is None
        assert css.parse_price_text(None) is None

    def test_parse_sold(self):
        assert css.parse_sold_text("1.5万人收货") == 15000
        assert css.parse_sold_text("月销1万+") == 10000
        assert css.parse_sold_text("2,300人付款") == 2300
        assert css.parse_sold_text("200+人收货") == 200
        assert css.parse_sold_text("月销 236") == 236
        assert css.parse_sold_text("包邮") is None
        assert css.parse_sold_text("") is None
        assert css.parse_sold_text(None) is None


class TestBuildOffers:
    def test_full_mapping_and_abs_image(self):
        offers = css.build_offers("taobao", [_TB_OFFER_A])
        assert len(offers) == 1
        o = offers[0]
        assert o.platform == "taobao"
        assert o.title == "创意简约陶瓷马克杯办公水杯定制"
        assert o.price == 12.9
        assert o.sold == 15000
        assert o.image == "https://img.alicdn.com/imgextra/tb-main.jpg"
        assert o.freight is None, "搜索卡片无运费文案 → None（绝不 0）"

    def test_garbage_price_stays_none(self):
        raw = dict(_TB_OFFER_A, price="暂无报价")
        (o,) = css.build_offers("taobao", [raw])
        assert o.price is None, "解析不出的价格必须是 None，绝不编造 0"

    def test_freight_text_parsed_via_shared_resolver(self):
        raw = dict(_TB_OFFER_A, freight="包邮")
        assert css.build_offers("taobao", [raw])[0].freight == 0.0
        raw2 = dict(_TB_OFFER_A, freight="运费：¥8.00")
        assert css.build_offers("taobao", [raw2])[0].freight == 8.0

    def test_id_without_url_builds_canonical(self):
        raw = {"id": "679836775118", "title": "马克杯"}
        (o,) = css.build_offers("taobao", [raw])
        assert o.url == "https://item.taobao.com/item.htm?id=679836775118"
        raw2 = {"id": "538120412345"}
        (o2,) = css.build_offers("pdd", [raw2])
        assert o2.url == "https://mobile.yangkeduo.com/goods.html?goods_id=538120412345"

    def test_no_url_no_id_dropped(self):
        assert css.build_offers("taobao", [{"title": "无 id 无链接"}]) == []

    def test_dedup_by_url_and_cap(self):
        raws = [dict(_TB_OFFER_A)] * 3 + [
            dict(_TB_OFFER_A, id=f"6798367751{i:02d}",
                 url=f"https://item.taobao.com/item.htm?id=6798367751{i:02d}")
            for i in range(30)
        ]
        offers = css.build_offers("taobao", raws)
        urls = [o.url for o in offers]
        assert len(urls) == len(set(urls)), "必须按 url 去重"
        assert len(offers) <= css.MAX_OFFERS


class TestDigIds:
    def test_taobao_ids(self):
        html = ('<a href="//item.taobao.com/item.htm?id=679836775118">a</a>'
                '<a href="//item.taobao.com/item.htm?id=679836775118">dup</a>'
                '<a href="//item.taobao.com/item.htm?id=654654136372">b</a>'
                '<a href="//item.taobao.com/item.htm?id=12345">短 id 不算</a>')
        assert css.dig_taobao_ids(html) == ["679836775118", "654654136372"]

    def test_pdd_ids_on_real_page_shape(self):
        """实机形态：body ~344KB、客户端渲染、goods_id 只在 innerHTML 里。"""
        noise = '<div class="x"><span>loading</span></div>' * 14760  # ~344KB
        html = ('<!doctype html><html><body><script>window.rawData={"stores":1};'
                '</script>' + noise +
                '<script>var a={goods_id:538120412345};var b={"goods_id":"777777777777"};'
                'var c="goods-id=888888888888";</script></body></html>')
        assert css.dig_pdd_ids(html) == ["538120412345", "777777777777", "888888888888"]
        assert len(html) > 300_000


# ── search_taobao：mock CDP ──


class TestSearchTaobao:
    def test_success_dom_path_creates_and_closes_own_tab(self):
        conn = _FakeConnection(new_tab_results=[
            _tb_dom_payload([_TB_OFFER_A, _TB_OFFER_B]),
        ])
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert [o.url for o in offers] == [
            "https://item.taobao.com/item.htm?id=679836775118",
            "https://item.taobao.com/item.htm?id=654654136372",
        ]
        assert offers[0].title == "创意简约陶瓷马克杯办公水杯定制"
        assert offers[0].price == 12.9
        assert offers[0].sold == 15000
        assert offers[1].price == 25.8
        assert offers[1].sold == 2300
        assert len(conn.created_tabs) == 1
        tab = conn.created_tabs[0]
        assert tab.closed is True, "自建 tab 用后必须关闭"
        assert tab.created_background is True, "静默纪律：后台 tab，不抢前台"
        assert "s.taobao.com/search?q=" in tab.nav_calls[0][0], "必须导航到搜索 URL"
        assert conn.closed is False, "外部连接归调用方所有，不得关闭"

    def test_search_url_keyword_urlencoded(self):
        conn = _FakeConnection(new_tab_results=[_tb_dom_payload([_TB_OFFER_A])])
        css.search_taobao("http://127.0.0.1:9222", "保温杯 大容量",
                          timeout=12.0, cdp=conn)
        url = conn.created_tabs[0].nav_calls[0][0]
        assert url.startswith("https://s.taobao.com/search?q=")
        assert " " not in url, "关键词必须 url 编码"

    def test_js_targets_dom_then_login_probe(self):
        conn = _FakeConnection(new_tab_results=[_tb_dom_payload([_TB_OFFER_A])])
        css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[0][0]
        assert "_m_h5_tk" in js, "登录探测必须在页内查 _m_h5_tk（实机先例）"
        assert 'a[href*="item.htm"]' in js, "DOM 路径必须扫 item.htm 卡片（实机先例）"

    def test_evaluate_timeout_bounded_by_budget(self):
        conn = _FakeConnection(new_tab_results=[_tb_dom_payload([_TB_OFFER_A])])
        css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        _, ev_timeout = conn.created_tabs[0].evaluate_calls[0]
        assert 1 <= ev_timeout <= 12

    def test_fallback_regex_path_after_empty_dom(self):
        conn = _FakeConnection(new_tab_results=[
            _tb_dom_payload([]),                     # DOM 空
            _ids_payload(["679836775118", "654654136372"]),  # 正则兜底
        ])
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert len(offers) == 2
        assert offers[0].url == "https://item.taobao.com/item.htm?id=679836775118"
        assert offers[0].title is None, "正则兜底只有 id——缺字段 None，不编造"
        assert offers[0].price is None
        assert offers[0].sold is None
        assert offers[0].image is None
        created = conn.created_tabs[0]
        assert len(created.evaluate_calls) == 2, "DOM 空后必须走 innerHTML 正则兜底"

    def test_empty_results_returns_empty_list(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            _tb_dom_payload([]), _ids_payload([]),
        ])  # type: ignore[method-assign]
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert offers == []

    def test_not_logged_in_raises_and_caches(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            _tb_dom_payload([], status="login", url="https://login.taobao.com/"),
        ])  # type: ignore[method-assign]
        with pytest.raises(css.NotLoggedIn) as ei:
            css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        assert ei.value.platform == "taobao"
        assert css._LOGIN_STATE.get("taobao") is False, "未登录必须写负缓存"

    def test_login_cache_skips_cdp_entirely(self):
        css._LOGIN_STATE["taobao"] = False
        with pytest.raises(css.NotLoggedIn):
            css.search_taobao("http://127.0.0.1:9222", "马克杯",
                              timeout=12.0, cdp=_BoomConnection())

    def test_reset_login_cache_clears(self):
        css._LOGIN_STATE["taobao"] = False
        css.reset_login_cache()
        assert css._LOGIN_STATE == {}

    def test_timeout_no_response_raises(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            "",  # evaluate 超时 → cdp_client 返回空串
        ])  # type: ignore[method-assign]
        with pytest.raises(css.SearchTimeoutError):
            css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)

    def test_tiny_budget_raises_without_cdp_touch(self):
        conn = _BoomConnection()
        with pytest.raises(css.SearchTimeoutError):
            css.search_taobao("http://127.0.0.1:9222", "马克杯",
                              timeout=0.5, cdp=conn)

    def test_error_status_raises_search_error(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            json.dumps({"status": "error", "message": "页面被风控拦截",
                        "url": "https://s.taobao.com/search?q=cup", "offers": []}),
        ])  # type: ignore[method-assign]
        with pytest.raises(css.CrossSourceSearchError) as ei:
            css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        assert "风控" in str(ei.value), "错误消息必须携带页内原文（失败出声）"

    def test_user_tab_released_and_never_closed(self):
        user_tab = _FakeTab("https://s.taobao.com/search?q=旧关键词", [
            _tb_dom_payload([_TB_OFFER_A]),
        ])
        conn = _FakeConnection(user_tab)
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert len(offers) == 1
        assert user_tab.closed is False, "复用的用户/同源 tab 绝不能被 close"
        assert conn.released == [user_tab], "find_tab 命中必须 release 取消跟踪"
        assert "s.taobao.com/search?q=" in user_tab.nav_calls[0][0], (
            "复用 tab 必须导航到本次关键词")
        assert len(user_tab.evaluate_calls) == 1

    def test_own_connection_closed_when_no_external_cdp(self):
        fake_conn = _FakeConnection()
        fake_conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            _tb_dom_payload([_TB_OFFER_A]),
        ])  # type: ignore[method-assign]
        with mock.patch.object(css, "CdpConnection", return_value=fake_conn):
            css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0)
        assert fake_conn.closed is True, "自建连接用后必须关闭"

    def test_empty_keyword_rejected(self):
        with pytest.raises(css.CrossSourceSearchError):
            css.search_taobao("http://127.0.0.1:9222", "  ", timeout=12.0,
                              cdp=_BoomConnection())

    def test_connection_failure_raises_search_error(self):
        class _DeadConn:
            def __init__(self, *a, **k):
                raise OSError("connect refused")

        with mock.patch.object(css, "CdpConnection", _DeadConn):
            with pytest.raises(css.CrossSourceSearchError) as ei:
                css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0)
        assert "CDP" in str(ei.value)


# ── search_pdd：mock CDP ──


class TestSearchPdd:
    def test_success_dom_path(self):
        conn = _FakeConnection(new_tab_results=[
            json.dumps({"status": "ok", "message": None,
                        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=cup",
                        "offers": [_PDD_OFFER_A]}),
        ])
        offers = css.search_pdd("http://127.0.0.1:9222", "保温杯",
                                timeout=12.0, cdp=conn)
        assert len(offers) == 1
        o = offers[0]
        assert o.platform == "pdd"
        assert o.title == "保温杯女316不锈钢便携小巧"
        assert o.price == 9.9
        assert o.sold is None, "pdd 卡片挖不到销量 → None（绝不 0）"
        assert o.image == "https://img.pddpic.com/mian.jpg"
        tab = conn.created_tabs[0]
        assert tab.closed is True
        assert tab.created_background is True
        assert "search_result.html?search_key=" in tab.nav_calls[0][0]

    def test_fallback_regex_path(self):
        conn = _FakeConnection(new_tab_results=[
            json.dumps({"status": "empty", "url":
                        "https://mobile.yangkeduo.com/search_result.html", "offers": []}),
            _ids_payload(["538120412345", "777777777777"]),
        ])
        offers = css.search_pdd("http://127.0.0.1:9222", "保温杯",
                                timeout=12.0, cdp=conn)
        assert len(conn.created_tabs[0].evaluate_calls) == 2, (
            "属性扫描空后必须走 innerHTML 正则兜底")
        assert offers[0].url == "https://mobile.yangkeduo.com/goods.html?goods_id=538120412345"
        assert offers[0].title is None and offers[0].price is None

    def test_pdd_dig_regex_embedded_in_phase2_js(self):
        """兜底 JS 必须内嵌实机验证过的 goods_id 正则（单一常量，勿两处漂移）。"""
        conn = _FakeConnection(new_tab_results=[
            json.dumps({"status": "empty", "url": "https://mobile.yangkeduo.com/x",
                        "offers": []}),
            _ids_payload([]),
        ])
        css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[1][0]
        assert json.dumps(css._PDD_ID_RE.pattern) in js, (
            "注入正则必须与 Python 侧 dig_pdd_ids 同一常量")

    def test_empty_results(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            json.dumps({"status": "empty", "url": "https://mobile.yangkeduo.com/x",
                        "offers": []}),
            _ids_payload([]),
        ])  # type: ignore[method-assign]
        assert css.search_pdd("http://127.0.0.1:9222", "保温杯",
                              timeout=12.0, cdp=conn) == []

    def test_login_html_redirect_raises_not_logged_in(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            json.dumps({"status": "login",
                        "message": "登录页重定向（login.html）",
                        "url": "https://mobile.yangkeduo.com/login.html?redirect=...",
                        "offers": []}),
        ])  # type: ignore[method-assign]
        with pytest.raises(css.NotLoggedIn) as ei:
            css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)
        assert ei.value.platform == "pdd"
        assert css._LOGIN_STATE.get("pdd") is False

    def test_login_url_marker_even_with_ok_status(self):
        """status 误报 ok 但 location 已在登录页 → 按 URL 标记判登录。"""
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [
            json.dumps({"status": "empty",
                        "url": "https://mobile.yangkeduo.com/login.html",
                        "offers": []}),
        ])  # type: ignore[method-assign]
        with pytest.raises(css.NotLoggedIn):
            css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)

    def test_login_cache_skips_cdp(self):
        css._LOGIN_STATE["pdd"] = False
        with pytest.raises(css.NotLoggedIn):
            css.search_pdd("http://127.0.0.1:9222", "保温杯",
                           timeout=12.0, cdp=_BoomConnection())

    def test_timeout_raises(self):
        conn = _FakeConnection()
        conn.new_tab = lambda url="about:blank", background=False: _FakeTab(url, [""
        ])  # type: ignore[method-assign]
        with pytest.raises(css.SearchTimeoutError):
            css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)

    def test_user_tab_released_never_closed(self):
        user_tab = _FakeTab("https://mobile.yangkeduo.com/search_result.html?search_key=旧", [
            json.dumps({"status": "ok", "url": "", "offers": [_PDD_OFFER_A]}),
        ])
        conn = _FakeConnection(user_tab)
        offers = css.search_pdd("http://127.0.0.1:9222", "保温杯",
                                timeout=12.0, cdp=conn)
        assert len(offers) == 1
        assert user_tab.closed is False
        assert conn.released == [user_tab]

    def test_empty_keyword_rejected(self):
        with pytest.raises(css.CrossSourceSearchError):
            css.search_pdd("http://127.0.0.1:9222", "", timeout=12.0,
                           cdp=_BoomConnection())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

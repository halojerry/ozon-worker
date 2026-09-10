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
from urllib.parse import quote_plus

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
# 批4 gate 校准 修2：pdd 搜索列表真实形态脱敏重建——数据源是页内 script JSON
# 的 "list":[{goodsID, goodsName, price(分·券前), priceInfo(元展示串·券后),
# salesTip, imgUrl}]；渲染卡片无锚点/无 data-* 属性（客户端渲染 div，
# 2026-09-10 实机取证），混淆 hash class 随构建漂移、有意不采用。
_PDD_OFFER_A = {
    "id": "538120412345",
    "title": "星空迷你保温杯男女学生真空304不锈钢便携水杯",
    "price": "27.5",        # priceInfo 展示串（券后到手价，元）
    "price_fen": "2950",    # price 原始字段（分，券前）——priceInfo 缺席时兜底
    "sold": "本店已拼41件",
    "url": "https://mobile.yangkeduo.com/goods.html?goods_id=538120412345",
    "image": "https://img.pddpic.com/2026-09-01/main.jpeg",
}


def _tb_dom_payload(offers, *, status=None, url=None, keyword="马克杯"):
    """DOM 载荷夹具——url 默认对到该关键词的完整搜索 URL（Python 侧上下文守卫
    按 payload.url.startswith(search_url) 判 context，夹具必须如实携带）。"""
    return json.dumps({
        "status": status or ("ok" if offers else "empty"),
        "message": None,
        "url": url or css._taobao_search_url(keyword),
        "offers": offers,
    })


def _pdd_dom_payload(offers, *, status=None, message=None, url=None,
                     keyword="保温杯"):
    return json.dumps({
        "status": status or ("ok" if offers else "empty"),
        "message": message,
        "url": url or css._pdd_search_url(keyword),
        "offers": offers,
    })


def _ids_payload(ids, *, url=None, keyword="马克杯"):
    return json.dumps({
        "status": "ok" if ids else "empty",
        "url": url or css._taobao_search_url(keyword),
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
        assert css.parse_sold_text("本店已拼41件") == 41, "pdd salesTip 形态（gate 校准）"
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
        conn = _FakeConnection(new_tab_results=[
            _tb_dom_payload([_TB_OFFER_A], keyword="保温杯 大容量")])
        css.search_taobao("http://127.0.0.1:9222", "保温杯 大容量",
                          timeout=12.0, cdp=conn)
        url = conn.created_tabs[0].nav_calls[0][0]
        assert url.startswith("https://s.taobao.com/search?q=")
        assert " " not in url, "关键词必须 url 编码"

    def test_js_targets_dom_then_login_probe(self):
        """注入 JS：DOM 路径扫 item.htm 卡片（实机先例）；登录判据只认登录页
        重定向——_m_h5_tk cookie 门已废（批4 gate 实机取证：登录态完好的搜索页
        也不带该 token，cookie 门会把每次 run 毒化成 NotLoggedIn）。"""
        conn = _FakeConnection(new_tab_results=[_tb_dom_payload([_TB_OFFER_A])])
        css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[0][0]
        assert "document.cookie" not in js, "cookie 登录门必须移除（实机反证：登录态完好的搜索页无 _m_h5_tk）"
        assert "login\\.taobao\\.com" in js, "登录判据 = 登录页重定向"
        assert 'a[href*="item.htm"]' in js, "DOM 路径必须扫 item.htm 卡片（实机先例）"

    def test_js_sold_regex_tolerates_plus_separator(self):
        """实机形态「7万+人付款」「200+人付款」——数字与关键词间的 + 必须吃掉。"""
        conn = _FakeConnection(new_tab_results=[_tb_dom_payload([_TB_OFFER_A])])
        css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[0][0]
        assert "[+\\s]*" in js, "销量正则必须容忍 + 分隔（gate 实机 sold 恒 null 根因）"

    def test_parse_sold_wan_without_unit_word(self):
        """JS 捕获组只含「7万」（+ 已被吃掉）→ Python 侧换算 70000。"""
        assert css.parse_sold_text("7万") == 70000
        assert css.parse_sold_text("1.5万") == 15000

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
                        "url": css._taobao_search_url("马克杯"), "offers": []}),
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
    def test_success_json_path_full_fields(self):
        """批4 gate 校准 修2：script-JSON 挖掘路径——id+标题+价+销量+图全拿到。"""
        conn = _FakeConnection(new_tab_results=[_pdd_dom_payload([_PDD_OFFER_A])])
        offers = css.search_pdd("http://127.0.0.1:9222", "保温杯",
                                timeout=12.0, cdp=conn)
        assert len(offers) == 1
        o = offers[0]
        assert o.platform == "pdd"
        assert o.title == "星空迷你保温杯男女学生真空304不锈钢便携水杯"
        assert o.price == 27.5, "priceInfo（券后展示价）为比对价权威"
        assert o.sold == 41, "salesTip 本店已拼41件 → 41（gate 前恒 None）"
        assert o.image == "https://img.pddpic.com/2026-09-01/main.jpeg"
        tab = conn.created_tabs[0]
        assert tab.closed is True
        assert tab.created_background is True
        assert "search_result.html?search_key=" in tab.nav_calls[0][0]

    def test_price_fen_fallback_when_price_info_missing(self):
        """priceInfo 缺席 → price 字段（分）换元；垃圾值 → None 绝不 0。"""
        raw = dict(_PDD_OFFER_A, price=None, price_fen="2950")
        assert css.build_offers("pdd", [raw])[0].price == 29.5
        raw2 = dict(_PDD_OFFER_A, price=None, price_fen="abc")
        assert css.build_offers("pdd", [raw2])[0].price is None
        raw3 = dict(_PDD_OFFER_A, price=None, price_fen=None)
        assert css.build_offers("pdd", [raw3])[0].price is None

    def test_pdd_js_mines_script_json_not_dom_classes(self):
        """注入 JS 必须挖页内 script JSON（goodsID/goodsName/priceInfo），
        死代码 DOM 扫描（data-goods-id/锚点——实机 0 命中）必须移除。"""
        conn = _FakeConnection(new_tab_results=[_pdd_dom_payload([_PDD_OFFER_A])])
        css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[0][0]
        assert '"goodsID"' in js and '"goodsName"' in js and '"priceInfo"' in js
        assert '"salesTip"' in js and '"imgUrl"' in js
        assert "data-goods-id" not in js and 'a[href*="goods"]' not in js, (
            "实机 0 命中的死扫描必须移除")
        assert json.dumps("search_key") in js, "pdd 上下文比对键必须是 search_key"
        assert "__CTX_URL__" not in js and "__CTX_QKEY__" not in js

    def test_fallback_regex_path(self):
        conn = _FakeConnection(new_tab_results=[
            _pdd_dom_payload([], status="empty"),
            _ids_payload(["538120412345", "777777777777"],
                         keyword="保温杯"),
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
            _pdd_dom_payload([], status="empty"),
            _ids_payload([], keyword="保温杯"),
        ])
        css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[1][0]
        assert json.dumps(css._PDD_ID_RE.pattern) in js, (
            "注入正则必须与 Python 侧 dig_pdd_ids 同一常量")

    def test_empty_results(self):
        conn = _FakeConnection(new_tab_results=[
            _pdd_dom_payload([], status="empty"),
            _ids_payload([], keyword="保温杯"),
        ])
        assert css.search_pdd("http://127.0.0.1:9222", "保温杯",
                              timeout=12.0, cdp=conn) == []

    def test_login_html_redirect_raises_not_logged_in(self):
        conn = _FakeConnection(new_tab_results=[
            _pdd_dom_payload([], status="login",
                             message="登录页重定向（login.html）",
                             url="https://mobile.yangkeduo.com/login.html?redirect=..."),
        ])
        with pytest.raises(css.NotLoggedIn) as ei:
            css.search_pdd("http://127.0.0.1:9222", "保温杯", timeout=12.0, cdp=conn)
        assert ei.value.platform == "pdd"
        assert css._LOGIN_STATE.get("pdd") is False

    def test_login_url_marker_even_with_ok_status(self):
        """status 误报 ok 但 location 已在登录页 → 按 URL 标记判登录
        （登录标记判序最先，先于上下文校验——真登录重定向必须写负缓存）。"""
        conn = _FakeConnection(new_tab_results=[
            _pdd_dom_payload([], status="empty",
                             url="https://mobile.yangkeduo.com/login.html"),
        ])
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
            _pdd_dom_payload([_PDD_OFFER_A]),
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


# ── 批1 fix round 1：上下文守卫（Important #1）+ 图片 data: URI 过滤（Minor #5）──

_CTX_PAYLOAD = json.dumps({"status": "context", "url": "about:blank",
                           "message": "页面不在目标搜索域", "offers": []})


class TestContextGuard:
    def test_taobao_about_blank_race_retried_then_success(self):
        """about:blank 导航竞态（JS 报 context）→ 重试一次 → 成功；
        竞态上下文错误绝不写登录负缓存（毒化防线）。"""
        conn = _FakeConnection(new_tab_results=[
            _CTX_PAYLOAD, _tb_dom_payload([_TB_OFFER_A]),
        ])
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert len(offers) == 1
        assert len(conn.created_tabs[0].evaluate_calls) == 2, "context 必须重试一次"
        assert css._LOGIN_STATE == {}, "竞态上下文错误绝不毒化登录负缓存"

    def test_taobao_context_error_persists_raises_loud_no_cache(self):
        conn = _FakeConnection(new_tab_results=[_CTX_PAYLOAD, _CTX_PAYLOAD])
        with pytest.raises(css.CrossSourceSearchError) as ei:
            css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        assert "上下文不符" in str(ei.value) or "搜索源" in str(ei.value)
        assert css._LOGIN_STATE == {}, "上下文错误绝不写 NotLoggedIn 缓存"

    def test_python_url_guard_rejects_stale_ok_payload(self):
        """JS 误报 ok 但 payload.url 还在 about:blank → Python 侧按 url 判 context
        → 拒绝 + 重试（不信任 JS 状态位，原始地址是权威）。"""
        conn = _FakeConnection(new_tab_results=[
            json.dumps({"status": "ok", "url": "about:blank",
                        "offers": [_TB_OFFER_A]}),
            _tb_dom_payload([_TB_OFFER_A]),
        ])
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert len(offers) == 1
        assert css._LOGIN_STATE == {}

    def test_stale_previous_keyword_page_rejected_and_retried(self):
        """复用 tab 停在上一关键词搜索页（同域、marker 挡不住）→ payload.url 与
        本次 search_url 前缀不符 → 丢弃过期结果 + 重试新页。"""
        conn = _FakeConnection(new_tab_results=[
            _tb_dom_payload([_TB_OFFER_A], keyword="旧关键词"),
            _tb_dom_payload([_TB_OFFER_A], keyword="马克杯"),
        ])
        offers = css.search_taobao("http://127.0.0.1:9222", "马克杯",
                                   timeout=12.0, cdp=conn)
        assert len(conn.created_tabs[0].evaluate_calls) == 2
        assert len(offers) == 1, "上一关键词的过期商品绝不吐出"
        assert css._LOGIN_STATE == {}

    def test_pdd_context_race_retried_then_success(self):
        conn = _FakeConnection(new_tab_results=[
            _CTX_PAYLOAD, _pdd_dom_payload([_PDD_OFFER_A]),
        ])
        offers = css.search_pdd("http://127.0.0.1:9222", "保温杯",
                                timeout=12.0, cdp=conn)
        assert len(offers) == 1
        assert css._LOGIN_STATE == {}

    def test_js_context_marker_injected_and_image_filter(self):
        """注入 JS 必须带搜索域标记（占位符消失）；图片经 absImg 只收 http(s)。"""
        conn = _FakeConnection(new_tab_results=[_tb_dom_payload([_TB_OFFER_A])])
        css.search_taobao("http://127.0.0.1:9222", "马克杯", timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[0][0]
        assert "__CTX_MARKER__" not in js, "上下文标记占位符必须注入"
        assert "s.taobao.com/search" in js
        assert "indexOf('http://')" in js and "indexOf('https://')" in js, (
            "absImg 必须按 http(s) 前缀过滤懒加载占位图（Minor #5）")

    def test_image_data_uri_filtered_in_normalize(self):
        raw = dict(_TB_OFFER_A, image="data:image/gif;base64,R0lGODlhAQAB")
        assert css.build_offers("taobao", [raw])[0].image is None, (
            "data: URI 占位图 → None（绝不冒充主图）")


# ── 批4 gate 校准 修1：上下文解析后比对（origin+path+q，忽略附加参数）──


class TestContextMatching:
    _KW = "不锈钢保温杯"

    def test_normalized_param_order_and_extra_params_pass(self):
        """实机 gate 误杀回归：淘宝把 URL 规范化成 ?page=1&q=...&tab=all
        （参数乱序 + 附加参数），整串前缀比对恒 False——解析后比对必须通过。"""
        expected = css._taobao_search_url(self._KW)
        actual = ("https://s.taobao.com/search?page=1&q="
                  + quote_plus(self._KW) + "&tab=all")
        assert css._context_matches(actual, expected, "q") is True

    def test_same_url_and_appended_params_pass(self):
        expected = css._taobao_search_url("马克杯")
        assert css._context_matches(expected, expected, "q") is True
        assert css._context_matches(expected + "&spm=xyz", expected, "q") is True

    def test_blank_foreign_diff_q_diff_path_rejected(self):
        expected = css._taobao_search_url("马克杯")
        assert css._context_matches("about:blank", expected, "q") is False
        assert css._context_matches("https://evil.com/search?q=%E9%A9%AC%E5%85%8B%E6%9D%AF",
                                    expected, "q") is False, "异域必须拒"
        assert css._context_matches(css._taobao_search_url("保温杯"),
                                    expected, "q") is False, "异 q 必须拒（防旧关键词残留）"
        assert css._context_matches("https://s.taobao.com/item.htm?q=%E9%A9%AC%E5%85%8B%E6%9D%AF",
                                    expected, "q") is False, "异 path 必须拒"
        assert css._context_matches("", expected, "q") is False

    def test_kind_of_accepts_normalized_url_payload(self):
        """_kind_of 级回归：规范化 URL 的 ok 载荷不再误判 context。"""
        payload = json.dumps({
            "status": "ok",
            "url": ("https://s.taobao.com/search?page=1&q="
                    + quote_plus(self._KW) + "&tab=all"),
            "offers": [_TB_OFFER_A],
        })
        kind, _ = css._kind_of(json.loads(payload), "taobao",
                               css._taobao_search_url(self._KW), "q")
        assert kind == "ok"

    def test_kind_of_rejects_diff_q_as_context(self):
        payload = json.dumps({"status": "ok",
                              "url": css._taobao_search_url("旧关键词"),
                              "offers": [_TB_OFFER_A]})
        kind, detail = css._kind_of(payload and json.loads(payload), "taobao",
                                    css._taobao_search_url("马克杯"), "q")
        assert kind == "context"

    def test_taobao_search_accepts_normalized_url_no_retry(self):
        """端到端：规范化 URL（page 在前 + tab=all）载荷直接放行，不触发重试。"""
        normalized = ("https://s.taobao.com/search?page=1&q="
                      + quote_plus(self._KW) + "&tab=all")
        conn = _FakeConnection(new_tab_results=[
            json.dumps({"status": "ok", "url": normalized,
                        "offers": [_TB_OFFER_A]}),
        ])
        offers = css.search_taobao("http://127.0.0.1:9222", self._KW,
                                   timeout=12.0, cdp=conn)
        assert len(offers) == 1
        assert len(conn.created_tabs[0].evaluate_calls) == 1, (
            "规范化 URL 必须一次放行（gate 误杀回归：此前恒重试后 error）")
        assert css._LOGIN_STATE == {}

    def test_js_context_uses_parsed_url_comparison(self):
        """注入 JS 必须用 URL 解析比对（new URL + searchParams）而非整串前缀。"""
        conn = _FakeConnection(new_tab_results=[
            _tb_dom_payload([_TB_OFFER_A], keyword=self._KW)])
        css.search_taobao("http://127.0.0.1:9222", self._KW, timeout=12.0, cdp=conn)
        js = conn.created_tabs[0].evaluate_calls[0][0]
        assert "new URL" in js and "searchParams" in js
        assert json.dumps("q") in js, "taobao 上下文比对键必须是 q"
        assert "__CTX_URL__" not in js and "__CTX_QKEY__" not in js


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

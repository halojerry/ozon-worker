#!/usr/bin/env python3
"""跨平台货源 v1 批3 — 拼多多适配器（pdd_client）TDD。

全部 mock CDP evaluate，绝不真启 Chrome；rawData / render/sku 夹具按参考实现
的字段名手工构造（goldminer v2.43 background.js ``buildPddGoodsSnapshot`` /
``fetchPddGoodsFallback`` / ``readPddGoodsRuntimeInfo`` :13591/:13740/:13723 +
ozonAI V2.3.3 ``pdd-main.js``：goodsName/topGallery/detailGallery/goodsProperty/
skus[].specs[{spec_key,spec_value}]/groupPrice/thumbUrl/limitQuantity/
minGroupPrice/maxGroupPrice），不含任何真实 cookie/凭证/截图
（pdd_user_id 夹具一律合成值如 ``123456``）。

覆盖:
- rawData 双形态归一（store.initDataObj.goods / goods）→ 统一 ProductInfo
- 兜底 render/sku 归一（groupPrice 分 → 元，price 单位换算在 Python 侧）
- freight：包邮 → 0.0；未知 → 缺席（绝不编造）；weight 缺失 → None（不是 0）
- 大声失败分支：双形态全缺（报错点名两形态）/ 标题缺 / 图缺 / 价格缺
- 登录页 / pdd_user_id 缺失 / 验证码 / 强制 App 引导 → 人话错误
- pdduid 只在页内使用（cookie 不出页，payload 只带 pdduid_present 布尔）
- tab 复用纪律：find_tab 命中绝不 close；自建 tab 用后关闭

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_pdd_client_normalize.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import pdd_client  # noqa: E402
from scripts.lib.source_platforms import parse_platform_url  # noqa: E402

_PDD_URL = "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"

# ── 形态一：rawData.store.initDataObj.goods（完整夹具，字段名按参考实现）──

_PDD_GOODS_STORE = {
    "goodsID": "734654654654",
    "goodsName": "家用简约陶瓷马克杯带勺办公室水杯",
    "topGallery": [
        {"url": "//img.pddpic.com/mms-material-main1.jpeg"},
        {"url": "//img.pddpic.com/mms-material-main2.jpeg"},
    ],
    "detailGallery": [
        {"url": "//img.pddpic.com/mms-material-main1.jpeg"},  # 与主图重复→去重
        {"url": "//img.pddpic.com/mms-detail1.jpeg"},
    ],
    "goodsProperty": [
        {"key": "品牌", "values": ["某杂货"]},
        {"key": "材质", "values": ["陶瓷"]},
        {"key": "净重", "values": ["300g"]},
    ],
    "skus": [
        {"skuId": "1001", "specs": [{"spec_key": "颜色", "spec_value": "白色"}],
         "groupPrice": 12.9, "thumbUrl": "//img.pddpic.com/white.jpg"},
        {"skuId": "1002", "specs": [{"spec_key": "颜色", "spec_value": "黑色"}],
         "groupPrice": 15.8, "thumbUrl": "//img.pddpic.com/black.jpg"},
    ],
    "minGroupPrice": 12.9,
    "maxGroupPrice": 15.8,
    "mall": {"mall_name": "某某百货官方旗舰店"},
}

# ── 形态二：rawData.goods（最小合法夹具）──

_PDD_GOODS_DIRECT = {
    "goodsID": "734654654654",
    "goodsName": "夏季冰丝凉感短袖T恤男女宽松上衣",
    "topGallery": [{"hdThumbUrl": "//img.pddpic.com/tshirt-main.jpg"}],
    "skus": [
        {"sku_id": "2001", "spec": [{"spec_key": "尺码", "spec_value": "M"}],
         "group_price": 19.9, "thumb_url": "//img.pddpic.com/m.jpg"},
    ],
}

# ── 兜底：render/sku 映射产物（JS 只做键名归一，groupPrice 保持分，Python 换算）──

_PDD_GOODS_FALLBACK = {
    "goodsID": "734654654654",
    "goodsName": "北欧简约收纳盒塑料桌面零食盒",
    "goodsProperty": [
        {"key": "面料", "values": ["塑料"]},
        {"key": "图案", "values": ["纯色"]},
    ],
    "topGallery": [{"url": "https://img.pddpic.com/box-main.jpg"}],
    "detailGallery": [],
    "skus": [
        {"skuId": "3001", "specs": [{"spec_key": "规格", "spec_value": "大号"}],
         "groupPrice": 1990, "thumbUrl": "//img.pddpic.com/big.jpg", "weight": 180},
        {"skuId": "3002", "specs": [{"spec_key": "规格", "spec_value": "小号"}],
         "groupPrice": 1390, "thumbUrl": "//img.pddpic.com/small.jpg"},
    ],
}

def _goods(source: str, goods) -> dict:
    """构造页内 JS evaluate 返回载荷（与 _PDD_EXTRACT_JS 的返回键一致）。"""
    return {
        "status": "ok",
        "error": None,
        "message": None,
        "url": _PDD_URL,
        "captcha": False,
        "app_banner": False,
        "source": source,
        "goods": goods,
        "freightText": "包邮",
        "pdduid_present": source == pdd_client.FALLBACK_SOURCE,
    }


# ── 纯函数：规格/属性/价格 归一 ──


class TestSkuSpecNormalization:
    def test_spec_key_value_objects(self):
        specs = [{"spec_key": "颜色", "spec_value": "白色"}]
        assert pdd_client._normalize_specs(specs) == [("颜色", "白色")]

    def test_string_colon_form(self):
        assert pdd_client._normalize_specs(["颜色:白色"]) == [("颜色", "白色")]

    def test_object_map_form(self):
        assert pdd_client._normalize_specs({"颜色": "白色"}) == [("颜色", "白色")]

    def test_empty_specs(self):
        assert pdd_client._normalize_specs(None) == []
        assert pdd_client._normalize_specs([{"spec_key": "颜色"}]) == []


class TestPriceParsing:
    def test_yuan_display_value(self):
        assert pdd_client._price_number({"groupPrice": 12.9}) == 12.9
        assert pdd_client._price_number({"group_price": "19.90"}) == 19.9

    def test_fen_conversion(self):
        """兜底 render/sku 价格是分（goldminer fromFen 先例）→ 元。"""
        assert pdd_client._price_number({"groupPrice": 1990}, from_fen=True) == 19.9
        assert pdd_client._price_number({"groupPrice": 1390}, from_fen=True) == 13.9

    def test_missing_price_none(self):
        assert pdd_client._price_number({}) is None
        assert pdd_client._price_number({"groupPrice": 0}) is None


# ── 归一：双形态 + 兜底 ──


class TestNormalizeStoreShape:
    def test_full_mapping(self):
        target = parse_platform_url(_PDD_URL)
        product = pdd_client._normalize_product(
            _PDD_GOODS_STORE, "rawData.store.initDataObj.goods", target, "包邮")
        assert product["platform"] == "pdd"
        assert product["item_id"] == "734654654654"
        assert product["canonical_url"] == \
            "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"
        assert product["title"] == "家用简约陶瓷马克杯带勺办公室水杯"
        # 主图 + 详情图合并去重 + 协议相对 → https
        assert product["images"] == [
            "https://img.pddpic.com/mms-material-main1.jpeg",
            "https://img.pddpic.com/mms-material-main2.jpeg",
            "https://img.pddpic.com/mms-detail1.jpeg",
        ]
        assert product["price"] == "12.90"
        assert product["price_ranges"] == [12.9, 15.8]
        skus = product["sku_details"]
        assert [s["sku_id"] for s in skus] == ["1001", "1002"]
        assert skus[0]["name"] == "白色"
        assert skus[0]["price"] == 12.9
        assert skus[0]["image"] == "https://img.pddpic.com/white.jpg"
        og = product["option_groups"]
        assert og[0]["name"] == "颜色"
        assert {"name": "白色", "image": "https://img.pddpic.com/white.jpg"} \
            in og[0]["values"]
        assert {"name": "黑色"} in og[0]["values"] or \
            {"name": "黑色", "image": "https://img.pddpic.com/black.jpg"} \
            in og[0]["values"]
        assert product["attributes"] == [
            {"name": "品牌", "value": "某杂货"},
            {"name": "材质", "value": "陶瓷"},
            {"name": "净重", "value": "300g"},
        ]
        assert product["brand"] == "某杂货"
        assert product["seller"] == "某某百货官方旗舰店"
        assert product["shipping"] == {
            "freightCny": 0.0, "freeShipping": True, "displayText": "包邮"}
        # 重量来自 goodsProperty 净重（带单位）——绝不 0
        assert product["weight_grams"] == 300

    def test_freight_unknown_absent_not_zero(self):
        target = parse_platform_url(_PDD_URL)
        product = pdd_client._normalize_product(
            _PDD_GOODS_STORE, "rawData.goods", target, "月销2万+")
        assert product["shipping"] == {}


class TestNormalizeDirectShape:
    def test_minimal_goods(self):
        target = parse_platform_url(_PDD_URL)
        product = pdd_client._normalize_product(
            _PDD_GOODS_DIRECT, "rawData.goods", target, "")
        assert product["title"] == "夏季冰丝凉感短袖T恤男女宽松上衣"
        assert product["images"] == ["https://img.pddpic.com/tshirt-main.jpg"]
        assert product["price"] == "19.90"
        assert product["price_ranges"] == [19.9, 19.9]
        assert product["sku_details"][0]["sku_id"] == "2001"
        # 无属性 → 无 brand；无 mall → seller 空串（不是编造）
        assert product["attributes"] == []
        assert product["brand"] == ""
        assert product["seller"] == ""
        # 无重量信息 → None（绝不 0）
        assert product["weight_grams"] is None


class TestNormalizeFallbackShape:
    def test_fen_prices_converted_to_yuan(self):
        target = parse_platform_url(_PDD_URL)
        product = pdd_client._normalize_product(
            _PDD_GOODS_FALLBACK, pdd_client.FALLBACK_SOURCE, target, "包邮")
        assert product["title"] == "北欧简约收纳盒塑料桌面零食盒"
        assert product["price"] == "13.90"
        assert product["price_ranges"] == [13.9, 19.9]
        assert [s["price"] for s in product["sku_details"]] == [19.9, 13.9]
        # 重量兜底：skus[].weight（ozonAI pieceWeight 先例，克）best-effort
        assert product["weight_grams"] == 180
        assert product["shipping"]["freightCny"] == 0.0


class TestLoudFailures:
    """失败出声：缺标题/图/价格任一 → 点名已尝试形态的人话错误，绝不半猜。"""

    def test_goods_none_raises_naming_shapes(self):
        target = parse_platform_url(_PDD_URL)
        with pytest.raises(pdd_client.PddFetchError) as ei:
            pdd_client._normalize_product(None, "", target, "包邮")
        msg = str(ei.value)
        assert "rawData.store.initDataObj.goods" in msg
        assert "rawData.goods" in msg
        assert "render/sku" in msg

    def test_missing_title_raises_loud(self):
        goods = json.loads(json.dumps(_PDD_GOODS_STORE))
        goods["goodsName"] = ""
        target = parse_platform_url(_PDD_URL)
        with pytest.raises(pdd_client.PddFetchError) as ei:
            pdd_client._normalize_product(
                goods, "rawData.goods", target, "包邮")
        assert "标题" in str(ei.value)
        assert "rawData.goods" in str(ei.value)

    def test_missing_images_raises_loud(self):
        goods = json.loads(json.dumps(_PDD_GOODS_STORE))
        goods["topGallery"] = []
        goods["detailGallery"] = []
        target = parse_platform_url(_PDD_URL)
        with pytest.raises(pdd_client.PddFetchError):
            pdd_client._normalize_product(goods, "rawData.goods", target, "包邮")

    def test_missing_price_raises_loud(self):
        goods = json.loads(json.dumps(_PDD_GOODS_STORE))
        goods["skus"] = []
        for key in ("minGroupPrice", "min_group_price", "groupPrice",
                    "group_price", "maxGroupPrice"):
            goods.pop(key, None)
        target = parse_platform_url(_PDD_URL)
        with pytest.raises(pdd_client.PddFetchError) as ei:
            pdd_client._normalize_product(goods, "rawData.goods", target, "包邮")
        assert "价格" in str(ei.value)


# ── 页内载荷分类 ──


class TestClassifyPayload:
    def test_ok(self):
        kind, _ = pdd_client._classify_payload(_goods("rawData.goods", {}))
        assert kind == "ok"

    def test_login_url(self):
        payload = _goods("", None)
        payload["status"] = "login"
        payload["url"] = "https://passport.pinduoduo.com/?redirect=xxx"
        kind, detail = pdd_client._classify_payload(payload)
        assert kind == "login" and "passport.pinduoduo.com" in detail

    def test_pdduid_missing_is_login(self):
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "pdd_user_id_missing"
        kind, _ = pdd_client._classify_payload(payload)
        assert kind == "login"

    def test_captcha_is_risk(self):
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "fallback_http_403"
        payload["captcha"] = True
        kind, _ = pdd_client._classify_payload(payload)
        assert kind == "risk"

    def test_app_banner_without_data_is_risk(self):
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "fallback_empty"
        payload["app_banner"] = True
        kind, detail = pdd_client._classify_payload(payload)
        assert kind == "risk"
        assert "App" in detail or "app" in detail

    def test_fallback_http_error_is_error(self):
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "fallback_http_403"
        kind, detail = pdd_client._classify_payload(payload)
        assert kind == "error" and "403" in detail

    def test_login_message_mentions_login_hint(self):
        """人话错误必须包含「请在工具 Chrome 登录拼多多后重试」语义。"""
        assert "登录拼多多" in pdd_client.LOGIN_HINT


# ── fetch_product：mock CDP（绝不启 Chrome）──


class _FakeTab:
    def __init__(self, url: str, evaluate_results: list | None = None):
        self.url = url
        self.evaluate_calls: list[str] = []
        self.navigations: list[str] = []
        self._results = list(evaluate_results or [])
        self.closed = False
        self.close_remote_values: list[bool] = []

    def evaluate(self, js, await_promise=False, timeout=15):
        self.evaluate_calls.append(js)
        if self._results:
            item = self._results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return json.dumps({"status": "error", "error": "no_more_mock_results",
                           "message": None, "url": self.url, "captcha": False,
                           "app_banner": False, "source": "", "goods": None,
                           "freightText": None, "pdduid_present": None})

    def wait_for_load(self, timeout=15):
        return None

    def navigate(self, url, wait_until="domcontentloaded", timeout=30):
        self.navigations.append(url)
        self.url = url

    def close(self, close_remote: bool = True):
        self.closed = True
        self.close_remote_values.append(close_remote)


class _FakeConnection:
    def __init__(self, tab: _FakeTab | None, cdp_url="http://127.0.0.1:9222"):
        self.cdp_url = cdp_url
        self._tab = tab
        self.created_tabs: list[_FakeTab] = []
        self.released: list = []
        self.closed = False

    def find_tab(self, pattern):
        if self._tab is not None and pattern in self._tab.url:
            return self._tab
        return None

    def new_tab(self, url, background=False):
        tab = _FakeTab(url)
        self.created_tabs.append(tab)
        return tab

    def release(self, tab):
        self.released.append(tab)

    def close(self, close_remote=True):
        self.closed = True


class TestFetchProductMockedCDP:
    def test_success_raw_data_creates_and_closes_own_tab(self):
        target = parse_platform_url(_PDD_URL)
        created = _FakeTab(_PDD_URL, [
            _goods("rawData.store.initDataObj.goods", _PDD_GOODS_STORE)])
        fake_conn = _FakeConnection(None)
        fake_conn.new_tab = lambda url, background=False: created  # type: ignore[method-assign]
        with mock.patch.object(pdd_client, "CdpConnection",
                               return_value=fake_conn):
            product = pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target, login_wait=False)
        assert product["title"] == "家用简约陶瓷马克杯带勺办公室水杯"
        assert created.closed is True and fake_conn.closed is True

    def test_reused_user_tab_never_closed(self):
        target = parse_platform_url(_PDD_URL)
        user_tab = _FakeTab(_PDD_URL, [
            _goods("rawData.goods", _PDD_GOODS_DIRECT)])
        conn = _FakeConnection(user_tab)
        product = pdd_client.fetch_product(
            "http://127.0.0.1:9222", _PDD_URL, target, cdp=conn, login_wait=False)
        assert product["item_id"] == "734654654654"
        assert user_tab.closed is False, "复用的用户 tab 绝不能被 close"
        assert conn.released == [user_tab], "find_tab 命中必须 release 取消跟踪"
        assert len(user_tab.evaluate_calls) == 1
        assert conn.closed is False, "外部连接归调用方所有，不得关闭"

    def test_fallback_render_sku_success(self):
        target = parse_platform_url(_PDD_URL)
        user_tab = _FakeTab(_PDD_URL, [
            _goods(pdd_client.FALLBACK_SOURCE, _PDD_GOODS_FALLBACK)])
        conn = _FakeConnection(user_tab)
        product = pdd_client.fetch_product(
            "http://127.0.0.1:9222", _PDD_URL, target, cdp=conn, login_wait=False)
        assert product["price"] == "13.90"

    def test_login_payload_raises_with_login_hint(self):
        target = parse_platform_url(_PDD_URL)
        payload = _goods("", None)
        payload["status"] = "login"
        payload["url"] = "https://mobile.yangkeduo.com/login.html?redirect=xx"
        user_tab = _FakeTab(_PDD_URL, [payload])
        conn = _FakeConnection(user_tab)
        with pytest.raises(pdd_client.PddLoginRequired) as ei:
            pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target,
                cdp=conn, login_wait=False)
        assert "登录拼多多" in str(ei.value)

    def test_pdduid_missing_raises_login(self):
        target = parse_platform_url(_PDD_URL)
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "pdd_user_id_missing"
        payload["pdduid_present"] = False
        user_tab = _FakeTab(_PDD_URL, [payload])
        conn = _FakeConnection(user_tab)
        with pytest.raises(pdd_client.PddLoginRequired):
            pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target,
                cdp=conn, login_wait=False)

    def test_captcha_raises_risk_loud(self):
        target = parse_platform_url(_PDD_URL)
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "fallback_http_403"
        payload["captcha"] = True
        user_tab = _FakeTab(_PDD_URL, [payload])
        conn = _FakeConnection(user_tab)
        with pytest.raises(pdd_client.PddRiskControlError) as ei:
            pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target,
                cdp=conn, login_wait=False)
        assert "验证" in str(ei.value) or "风控" in str(ei.value)

    def test_all_shapes_missing_error_names_both_and_fallback(self):
        """双形态全缺 + 兜底失败 → 错误点名两形态 + 兜底错误原文（失败出声）。"""
        target = parse_platform_url(_PDD_URL)
        payload = _goods("", None)
        payload["status"] = "no_data"
        payload["error"] = "fallback_http_403"
        user_tab = _FakeTab(_PDD_URL, [payload])
        conn = _FakeConnection(user_tab)
        with pytest.raises(pdd_client.PddFetchError) as ei:
            pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target,
                cdp=conn, login_wait=False)
        msg = str(ei.value)
        assert "rawData.store.initDataObj.goods" in msg
        assert "rawData.goods" in msg
        assert "fallback_http_403" in msg

    def test_login_wait_retries_once_after_login(self):
        target = parse_platform_url(_PDD_URL)
        payload_login = _goods("", None)
        payload_login["status"] = "login"
        payload_login["url"] = "https://passport.pinduoduo.com/web?xx"
        ok = _goods("rawData.goods", _PDD_GOODS_DIRECT)
        user_tab = _FakeTab(_PDD_URL, [payload_login, ok])
        conn = _FakeConnection(user_tab)
        with mock.patch.object(pdd_client, "wait_for_login", return_value=True) as w:
            product = pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target,
                cdp=conn, login_wait=True)
        w.assert_called_once()
        assert product["item_id"] == "734654654654"
        assert user_tab.navigations, "登录后应回商品页重取"

    def test_login_wait_exhausted_raises(self):
        target = parse_platform_url(_PDD_URL)
        payload_login = _goods("", None)
        payload_login["status"] = "login"
        user_tab = _FakeTab(_PDD_URL, [payload_login, payload_login])
        conn = _FakeConnection(user_tab)
        with mock.patch.object(pdd_client, "wait_for_login", return_value=False):
            with pytest.raises(pdd_client.PddLoginRequired):
                pdd_client.fetch_product(
                    "http://127.0.0.1:9222", _PDD_URL, target,
                    cdp=conn, login_wait=True)

    def test_evaluate_exception_wrapped_loud(self):
        target = parse_platform_url(_PDD_URL)
        user_tab = _FakeTab(_PDD_URL, [ConnectionError("CDP WebSocket closed")])
        conn = _FakeConnection(user_tab)
        with pytest.raises(pdd_client.PddFetchError):
            pdd_client.fetch_product(
                "http://127.0.0.1:9222", _PDD_URL, target,
                cdp=conn, login_wait=False)

    def test_non_pdd_target_rejected(self):
        with pytest.raises(pdd_client.PddFetchError):
            pdd_client.fetch_product(
                "http://127.0.0.1:9222",
                "https://item.taobao.com/item.htm?id=679836775118",
                parse_platform_url("https://item.taobao.com/item.htm?id=679836775118"))

    def test_injected_js_targets_rawdata_and_fallback_in_page(self):
        """注入 JS 必须读三形态 rawData + 页内 fetch render/sku（pdduid 不出页）。"""
        target = parse_platform_url(_PDD_URL)
        user_tab = _FakeTab(_PDD_URL, [
            _goods("rawData.store.initDataObj.goods", _PDD_GOODS_STORE)])
        conn = _FakeConnection(user_tab)
        pdd_client.fetch_product(
            "http://127.0.0.1:9222", _PDD_URL, target, cdp=conn, login_wait=False)
        js = user_tab.evaluate_calls[0]
        # 双形态（+goldminer 的 store.goods 中间形态）都要读
        assert "rawData" in js and "initDataObj" in js
        assert js.count("window.rawData") >= 2, "至少读 store.initDataObj.goods 与 goods 两形态"
        # 兜底：页内 fetch + cookie 页内读（红线：cookie 明文不出页）
        assert "proxy/api/api/oak/integration/render/sku" in js
        assert "pdd_user_id" in js
        assert "encodeURIComponent(pdduid)" in js
        assert "credentials" in js and "include" in js
        assert "734654654654" in js, "goods_id 必须注入兜底请求体"
        # 回传 payload 只允许 pdduid_present 布尔——cookie 值绝不进 JSON
        assert "pdduid_present" in js
        assert "'pdduid':" not in js and '"pdduid":' not in js
        # 运费文案页内提取（包邮语义）
        assert "包邮" in js
        # 强制 App 引导标记（ozonAI pdd-go-to-app-pc 先例）
        assert "pdd-go-to-app-pc" in js

    def test_pdd_login_url_markers(self):
        """登录/验证页标记：passport / renzheng / login 路径（参考 goldminer 正则）。"""
        js_markers = pdd_client.PDD_LOGIN_URL_MARKERS
        assert any("passport.pinduoduo.com" in m for m in js_markers)
        assert any("renzheng.pinduoduo.com" in m for m in js_markers)

    def test_wait_for_login_tab_patterns_are_pdd_bound(self):
        """wait_for_login 的 tab 匹配必须 pdd 域限定——裸 "/login" 会误配其他
        平台登录 tab（https://login.taobao.com 含 "/login"，跨平台串台）。"""
        assert pdd_client.PDD_LOGIN_TAB_PATTERNS
        for pattern in pdd_client.PDD_LOGIN_TAB_PATTERNS:
            assert "pinduoduo.com" in pattern or "yangkeduo.com" in pattern, pattern


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

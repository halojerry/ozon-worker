#!/usr/bin/env python3
"""跨平台货源 v1 批2 — 淘宝/天猫适配器（taobao_client）TDD。

全部 mock CDP evaluate，绝不真启 Chrome；mtop 响应夹具按 goldminer 参考实现
的字段名手工构造（pcdetail.data.get：ret/item/skuCore/skuBase/plusViewVO/
componentsVO），不含任何真实 cookie/凭证/截图。

覆盖:
- mtop 归一 → 统一 ProductInfo（title/price/price_ranges/images/sku_details/
  option_groups/attributes/shipping/weight）
- freight：包邮 → 0.0；未知 → 缺席（绝不编造）
- weight 缺失 → None（不是 0）
- 风控/未登录/mtop FAIL 分支 → 大声抛人话错误（失败出声，绝不半猜）
- tab 复用纪律：find_tab 命中绝不 close；自建 tab 用后关闭

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_taobao_client_normalize.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import taobao_client  # noqa: E402
from scripts.lib.source_platforms import parse_platform_url  # noqa: E402

# ── 手工构造的 mtop pcdetail 响应夹具（字段名取自 goldminer 参考实现）──

_TAOBAO_MTOP = {
    "ret": ["SUCCESS::调用成功"],
    "data": {
        "item": {
            "title": "创意简约陶瓷马克杯办公水杯定制",
            "images": [
                "//img.alicdn.com/imgextra/i4/O1CN01main/!!6000000000000-0-tp.jpg",
                "//img.alicdn.com/imgextra/i2/O1CN01second/!!6000000000001-0-tp.jpg",
            ],
        },
        "seller": {"shopName": "某某家居专营店"},
        "skuCore": {
            "sku2info": {
                "0": {"price": {"priceText": "12.90"}},
                "594367812345": {"price": {"priceText": "12.90"}},
                "594367812346": {"price": {"priceText": "15.80"}},
                "594367812347": {"price": {"priceText": "25.00"}},
            },
        },
        "skuBase": {
            "props": [
                {"pid": "1627207", "name": "颜色分类", "values": [
                    {"vid": "3232478", "name": "白色",
                     "image": "//img.alicdn.com/imgextra/white.jpg"},
                    {"vid": "3232479", "name": "黑色",
                     "image": "//img.alicdn.com/imgextra/black.jpg"},
                ]},
                {"pid": "20509", "name": "容量", "values": [
                    {"vid": "115322", "name": "300ml"},
                    {"vid": "115323", "name": "500ml"},
                ]},
            ],
            "skus": [
                {"skuId": "594367812345",
                 "propPath": "1627207:3232478;20509:115322"},
                {"skuId": "594367812346",
                 "propPath": "1627207:3232479;20509:115322"},
                {"skuId": "594367812347",
                 "propPath": "1627207:3232479;20509:115323"},
            ],
        },
        "price": {"price": {"priceText": "12.90-25.00"}},
        "plusViewVO": {"industryParamVO": {"basicParamList": [
            {"propertyName": "材质", "valueName": "陶瓷"},
            {"propertyName": "适用人群", "valueName": "通用"},
        ]}},
    },
}

_TMALL_MTOP = {
    "ret": ["SUCCESS::调用成功"],
    "data": {
        "item": {
            "title": "旗舰店正品无线蓝牙耳机降噪",
            "images": ["//img.alicdn.com/imgextra/tm-main.jpg"],
        },
        "skuCore": {"sku2info": {"0": {"price": {"priceText": "199.00"}}}},
        "skuBase": {"props": [], "skus": []},
        "price": {"price": {"priceText": "199.00"}},
        "componentsVO": {"extensionInfoVO": {"infos": [
            {"type": "BASE_PROPS", "items": [
                {"title": "品牌", "text": "某某旗舰"},
                {"title": "产地", "text": ["中国大陆"]},
            ]},
        ]}},
    },
}

_TB_URL = "https://item.taobao.com/item.htm?id=679836775118"
_TM_URL = "https://detail.tmall.com/item.htm?id=654654136372"


# ── 纯函数：运费文本解析 ──


class TestFreightParsing:
    def test_free_shipping_variants(self):
        assert taobao_client.parse_freight_cny("包邮") == 0.0
        assert taobao_client.parse_freight_cny("免运费") == 0.0
        assert taobao_client.parse_freight_cny("满88元免邮") == 0.0

    def test_amount_forms(self):
        assert taobao_client.parse_freight_cny("运费：¥8.00") == 8.0
        assert taobao_client.parse_freight_cny("快递费 10元") == 10.0
        assert taobao_client.parse_freight_cny("运费￥6") == 6.0

    def test_unknown_none(self):
        assert taobao_client.parse_freight_cny("") is None
        assert taobao_client.parse_freight_cny(None) is None
        assert taobao_client.parse_freight_cny("月销1万+") is None
        assert taobao_client.parse_freight_cny("5分钱福利购") is None


class TestMtopFailureClassification:
    def test_success_none(self):
        assert taobao_client.classify_mtop_failure(["SUCCESS::调用成功"]) is None

    def test_token_expired_is_login(self):
        assert taobao_client.classify_mtop_failure(
            ["FAIL_SYS_TOKEN_EXPIRED::session过期"]) == "login"
        assert taobao_client.classify_mtop_failure(
            ["FAIL_SYS_SESSION_EXPIRED::需要登录"]) == "login"

    def test_user_validate_is_risk(self):
        assert taobao_client.classify_mtop_failure(
            ["FAIL_SYS_USER_VALIDATE::验证码"]) == "risk"

    def test_other_fail_is_error(self):
        assert taobao_client.classify_mtop_failure(
            ["FAIL_BIZ_ITEM_NOT_EXIST::商品不存在"]) == "error"


class TestNormalizeProduct:
    def test_taobao_full_mapping(self):
        target = parse_platform_url(_TB_URL)
        product = taobao_client._normalize_product(_TAOBAO_MTOP, target, "包邮")
        assert product["platform"] == "taobao"
        assert product["item_id"] == "679836775118"
        assert product["title"] == "创意简约陶瓷马克杯办公水杯定制"
        # 主图协议相对 URL → https 绝对化
        assert product["images"] == [
            "https://img.alicdn.com/imgextra/i4/O1CN01main/!!6000000000000-0-tp.jpg",
            "https://img.alicdn.com/imgextra/i2/O1CN01second/!!6000000000001-0-tp.jpg",
        ]
        # 价格：min/max（字符串 price 取 min —— parse_price 语义一致）
        assert product["price"] == "12.90"
        assert product["price_ranges"] == [12.9, 25.0]
        # SKU 归一：propPath → 名称/价格/SKU 图
        skus = product["sku_details"]
        assert [s["sku_id"] for s in skus] == [
            "594367812345", "594367812346", "594367812347"]
        assert skus[0]["name"] == "白色 300ml"
        assert skus[0]["price"] == 12.9
        assert skus[0]["image"] == "https://img.alicdn.com/imgextra/white.jpg"
        assert skus[2]["name"] == "黑色 500ml"
        assert skus[2]["price"] == 25.0
        # option_groups：skuBase.props → name+values（含图）
        og = product["option_groups"]
        assert og[0]["name"] == "颜色分类"
        assert og[0]["values"][0] == {
            "name": "白色", "image": "https://img.alicdn.com/imgextra/white.jpg"}
        assert og[1]["name"] == "容量"
        assert og[1]["values"] == [{"name": "300ml"}, {"name": "500ml"}]
        # 属性（taobao 走 plusViewVO basicParamList）
        assert product["attributes"] == [
            {"name": "材质", "value": "陶瓷"},
            {"name": "适用人群", "value": "通用"},
        ]
        # 包邮 → freightCny=0.0
        assert product["shipping"] == {
            "freightCny": 0.0, "freeShipping": True, "displayText": "包邮"}
        # 卖家 best-effort
        assert product["seller"] == "某某家居专营店"
        # 淘宝详情无重量字段 → None（绝不编造成 0）
        assert product["weight_grams"] is None
        assert product["canonical_url"] == "https://item.taobao.com/item.htm?id=679836775118"

    def test_tmall_attributes_via_base_props(self):
        target = parse_platform_url(_TM_URL)
        product = taobao_client._normalize_product(_TMALL_MTOP, target, "")
        assert product["platform"] == "tmall"
        assert product["title"] == "旗舰店正品无线蓝牙耳机降噪"
        assert product["attributes"] == [
            {"name": "品牌", "value": "某某旗舰"},
            {"name": "产地", "value": "中国大陆"},
        ]
        assert product["sku_details"] == []
        assert product["option_groups"] == []

    def test_freight_unknown_absent_not_zero(self):
        """运费未知 → shipping 无 freightCny 键（绝不编造成 0）。"""
        target = parse_platform_url(_TB_URL)
        product = taobao_client._normalize_product(_TAOBAO_MTOP, target, "月销1万+")
        assert product["shipping"] == {}

    def test_freight_from_amount_text(self):
        target = parse_platform_url(_TB_URL)
        product = taobao_client._normalize_product(_TAOBAO_MTOP, target, "运费：¥8.00")
        assert product["shipping"] == {"freightCny": 8.0, "displayText": "运费：¥8.00"}

    def test_weight_from_attributes(self):
        mtop = json.loads(json.dumps(_TAOBAO_MTOP))
        mtop["data"]["plusViewVO"]["industryParamVO"]["basicParamList"].append(
            {"propertyName": "重量", "valueName": "250g"})
        target = parse_platform_url(_TB_URL)
        product = taobao_client._normalize_product(mtop, target, "包邮")
        assert product["weight_grams"] == 250

    def test_weight_kg_form(self):
        mtop = json.loads(json.dumps(_TAOBAO_MTOP))
        mtop["data"]["plusViewVO"]["industryParamVO"]["basicParamList"] = [
            {"propertyName": "净重", "valueName": "0.5kg"}]
        target = parse_platform_url(_TB_URL)
        product = taobao_client._normalize_product(mtop, target, "包邮")
        assert product["weight_grams"] == 500

    def test_missing_title_raises_loud(self):
        mtop = json.loads(json.dumps(_TAOBAO_MTOP))
        mtop["data"]["item"]["title"] = ""
        target = parse_platform_url(_TB_URL)
        with pytest.raises(taobao_client.TaobaoFetchError) as ei:
            taobao_client._normalize_product(mtop, target, "包邮")
        assert "标题" in str(ei.value) or "title" in str(ei.value).lower()

    def test_missing_images_raises_loud(self):
        mtop = json.loads(json.dumps(_TAOBAO_MTOP))
        mtop["data"]["item"]["images"] = []
        target = parse_platform_url(_TB_URL)
        with pytest.raises(taobao_client.TaobaoFetchError):
            taobao_client._normalize_product(mtop, target, "包邮")

    def test_no_sku_prices_falls_back_to_price_block(self):
        """sku2info 空 → price.priceText 区间块兜底（绝不静默 0 价）。"""
        mtop = json.loads(json.dumps(_TAOBAO_MTOP))
        mtop["data"]["skuCore"] = {"sku2info": {}}
        target = parse_platform_url(_TB_URL)
        product = taobao_client._normalize_product(mtop, target, "包邮")
        assert product["price"] == "12.90"
        assert product["price_ranges"] == [12.9, 25.0]


# ── fetch_product：mock CDP（绝不启 Chrome）──


class _FakeTab:
    def __init__(self, url: str, evaluate_results: list | None = None):
        self.url = url
        self.evaluate_calls: list[str] = []
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
        return json.dumps({"error": "no_more_mock_results"})

    def wait_for_load(self, timeout=15):
        return None

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


def _evaluate_payload(mtop, *, url=_TB_URL, captcha=False, freight="包邮",
                      ret=None, error=None):
    return json.dumps({
        "status": 200,
        "error": error,
        "message": None,
        "captcha": captcha,
        "url": url,
        "ret": ret if ret is not None else mtop.get("ret", []),
        "body": mtop,
        "freightText": freight,
    })


class TestFetchProductMockedCDP:
    def test_success_creates_and_closes_own_tab(self):
        target = parse_platform_url(_TB_URL)
        created = _FakeTab(_TB_URL, [_evaluate_payload(_TAOBAO_MTOP)])
        fake_conn = _FakeConnection(None)
        fake_conn.new_tab = lambda url, background=False: created  # type: ignore[method-assign]
        with mock.patch.object(taobao_client, "CdpConnection",
                               return_value=fake_conn):
            product = taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target, login_wait=False)
        assert product["title"] == "创意简约陶瓷马克杯办公水杯定制"
        assert created.closed is True and fake_conn.closed is True

    def test_reused_user_tab_never_closed(self):
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab(_TB_URL, [_evaluate_payload(_TAOBAO_MTOP)])
        conn = _FakeConnection(user_tab)
        product = taobao_client.fetch_product(
            "http://127.0.0.1:9222", _TB_URL, target, cdp=conn, login_wait=False)
        assert product["item_id"] == "679836775118"
        assert user_tab.closed is False, "复用的用户 tab 绝不能被 close"
        assert conn.released == [user_tab], "find_tab 命中必须 release 取消跟踪"
        assert len(user_tab.evaluate_calls) == 1, "应恰好一次 mtop 页内 fetch evaluate"
        assert conn.closed is False, "外部连接归调用方所有，不得关闭"

    def test_own_connection_tab_closed_and_connection_closed(self):
        target = parse_platform_url(_TB_URL)
        fake_conn = _FakeConnection(None)
        created = _FakeTab(_TB_URL, [_evaluate_payload(_TAOBAO_MTOP)])
        fake_conn.new_tab = lambda url, background=False: created  # type: ignore[method-assign]
        with mock.patch.object(taobao_client, "CdpConnection",
                               return_value=fake_conn):
            taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target, login_wait=False)
        assert created.closed is True, "自建 tab 用后必须关闭"
        assert fake_conn.closed is True, "自建连接用后必须关闭"

    def test_no_mtop_token_raises_login_loud(self):
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab(_TB_URL, [json.dumps({
            "status": 200, "error": "NO_MTOP_TOKEN",
            "message": "无法获取 _m_h5_tk", "captcha": False,
            "url": _TB_URL, "ret": [], "body": None, "freightText": None,
        })])
        conn = _FakeConnection(user_tab)
        with pytest.raises(taobao_client.TaobaoLoginRequired) as ei:
            taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target,
                cdp=conn, login_wait=False)
        assert "登录" in str(ei.value)

    def test_login_page_url_raises_login(self):
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab("https://login.taobao.com/member/login.jhtml?redirect=" + _TB_URL,
                            [_evaluate_payload(_TAOBAO_MTOP, url="https://login.taobao.com/member/login.jhtml")])
        conn = _FakeConnection(user_tab)
        with pytest.raises(taobao_client.TaobaoLoginRequired):
            taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target,
                cdp=conn, login_wait=False)

    def test_captcha_raises_risk_control_loud(self):
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab(_TB_URL, [_evaluate_payload(
            _TAOBAO_MTOP, captcha=True,
            ret=["FAIL_SYS_USER_VALIDATE::亲，访问受限了"])]
        )
        conn = _FakeConnection(user_tab)
        with pytest.raises(taobao_client.TaobaoRiskControlError) as ei:
            taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target,
                cdp=conn, login_wait=False)
        assert "验证" in str(ei.value) or "滑块" in str(ei.value) or "风控" in str(ei.value)

    def test_mtop_fail_ret_raises_loud(self):
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab(_TB_URL, [_evaluate_payload(
            _TAOBAO_MTOP, ret=["FAIL_BIZ_ITEM_NOT_EXIST::商品不存在"])])
        conn = _FakeConnection(user_tab)
        with pytest.raises(taobao_client.TaobaoFetchError) as ei:
            taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target,
                cdp=conn, login_wait=False)
        assert "FAIL_BIZ_ITEM_NOT_EXIST" in str(ei.value), (
            "错误消息必须携带 mtop ret 原文（失败出声）")

    def test_evaluate_exception_wrapped_loud(self):
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab(_TB_URL, [ConnectionError("CDP WebSocket closed")])
        conn = _FakeConnection(user_tab)
        with pytest.raises(taobao_client.TaobaoFetchError):
            taobao_client.fetch_product(
                "http://127.0.0.1:9222", _TB_URL, target,
                cdp=conn, login_wait=False)

    def test_non_taobao_target_rejected(self):
        with pytest.raises(taobao_client.TaobaoFetchError):
            taobao_client.fetch_product(
                "http://127.0.0.1:9222",
                "https://mobile.yangkeduo.com/goods.html?goods_id=1",
                parse_platform_url("https://mobile.yangkeduo.com/goods.html?goods_id=1"))

    def test_js_targets_pcdetail_mtop_api(self):
        """注入 JS 必须打 pcdetail.data.get 且 appKey 与 goldminer 一致。"""
        target = parse_platform_url(_TB_URL)
        user_tab = _FakeTab(_TB_URL, [_evaluate_payload(_TAOBAO_MTOP)])
        conn = _FakeConnection(user_tab)
        taobao_client.fetch_product(
            "http://127.0.0.1:9222", _TB_URL, target, cdp=conn, login_wait=False)
        js = user_tab.evaluate_calls[0]
        assert "mtop.taobao.pcdetail.data.get" in js
        assert "12574478" in js
        assert "credentials" in js and "include" in js
        assert "679836775118" in js, "item id 必须注入 fetch payload"
        assert "h5api.m.taobao.com" in js
        assert "_m_h5_tk" in js, "签名 token 必须来自页内 cookie"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

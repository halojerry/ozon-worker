#!/usr/bin/env python3
"""v0.39 aibuy mtop API 图搜通道单测：签名算法、JSONP 解析、请求构造、结果归一化、
token 缓存过期、fail-fast 降级、结果缓存。

运行：
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_aibuy_search.py -v
    cd skill && PYTHONPATH=. python3 tests/test_aibuy_search.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import ozon_image_search as ois  # noqa: E402

MOCK_COOKIES = {
    "_m_h5_tk": "6499814d73071a8266d07f43c4b4b5d8_1786528017499",
    "_m_h5_tk_enc": "7704ca511fa0591dea466c5ae7d4250d",
    "tfstk": "gAOImrvbD3dwTYuK2kuZ1ilQWQCS0Vl4yz",
    "isg": "BKys-7El6gMgJv4_rpqToqBTfYzeZVAPkoirMwbtuNf6",
}

MOCK_ITEM = {
    "offerId": "707351271432",
    "title": "跨境苹果airtag猫咪宠物项圈围脖 PU定位防丟狗追踪项圈 宠物用品",
    "price": "10.90",
    "imageUrl": "https://cbu01.alicdn.com/O1CN01ePQoHd1wGkbJYS84Y_!!2215585976281-0-cib.jpg",
    "monthSold": "800+",
    "repurchaseRate": "39%",
    "companyName": "深圳市一宠科技有限公司",
    "offerPublishTime": "2023/03/10",
    "normalizationScore": "0.046148162335157394",
}

MOCK_SEARCH_RESP = (
    'mtopjsonp_aibuy({"api":"mtop.com.alibaba.cbu.crossBorder.lp.imageSearch",'
    '"v":"1.0","data":{"result":{"currentPage":"1","data":['
    + '{"offerId":"707351271432","title":"T1","price":"10.90",'
    '"imageUrl":"https://img.jpg","monthSold":"800+","repurchaseRate":"39%",'
    '"companyName":"S1","offerPublishTime":"2023/03/10","normalizationScore":"0.0461"},'
    '{"offerId":"707351271433","title":"T2","price":"5.5",'
    '"imageUrl":"https://img2.jpg","normalizationScore":"0.03"}]}}})'
)


# ── 签名算法 ──────────────────────────────────────────────────────────────

def test_mtop_sign_matches_known_value():
    """mtop 签名 = md5(token & t & appKey & data)，与实测成功请求一致。"""
    token = "6499814d73071a8266d07f43c4b4b5d8"
    t = "1786520287285"
    data = "{}"
    sign = ois._mtop_sign(token, t, data)
    assert isinstance(sign, str) and len(sign) == 32
    assert sign == hashlib_md5(f"{token}&{t}&12574478&{{}}").hexdigest()


def hashlib_md5(s: str) -> str:
    import hashlib
    return hashlib.md5(s.encode())


# ── JSONP 解析 ────────────────────────────────────────────────────────────

def test_parse_mtop_jsonp_success():
    parsed = ois._parse_mtop_jsonp(MOCK_SEARCH_RESP)
    assert parsed["api"] == "mtop.com.alibaba.cbu.crossBorder.lp.imageSearch"
    assert parsed["data"]["result"]["data"][0]["offerId"] == "707351271432"


def test_parse_mtop_jsonp_malformed_returns_empty():
    assert ois._parse_mtop_jsonp("not json") == {}
    assert ois._parse_mtop_jsonp("callback(abc)") == {}


# ── 请求构造 ──────────────────────────────────────────────────────────────

@mock.patch("scripts.lib.ozon_image_search.requests.get")
def test_mtop_request_builds_correct_signature(mock_get):
    """签名用 token 前缀（_ 前部分）+ 当前 t + appKey + data。"""
    mock_get.return_value = mock.Mock(
        status_code=200,
        text='cb({"ret":["SUCCESS::调用成功"],"data":{}})',  # noqa: F541
    )
    with mock.patch.object(ois, "time") as mock_time:
        mock_time.time.return_value = 1786520287.285
        result = ois._mtop_request(
            "mtop.test.api", {"a": 1}, MOCK_COOKIES, timeout=10
        )
    assert result == {}
    args, kwargs = mock_get.call_args
    params = kwargs["params"]
    assert params["appKey"] == "12574478"
    assert params["api"] == "mtop.test.api"
    assert params["t"] == "1786520287285"
    # 签名 = md5(token&t&appKey&data)
    token = "6499814d73071a8266d07f43c4b4b5d8"
    expect_sign = hashlib_md5(f"{token}&1786520287285&12574478&{{\"a\": 1}}").hexdigest()
    assert params["sign"] == expect_sign


@mock.patch("scripts.lib.ozon_image_search.requests.get")
def test_mtop_request_failure_returns_empty(mock_get):
    mock_get.side_effect = Exception("conn refused")
    assert ois._mtop_request("mtop.test", {}, MOCK_COOKIES) == {}
    mock_get.return_value = mock.Mock(status_code=500, text="err")
    assert ois._mtop_request("mtop.test", {}, MOCK_COOKIES) == {}


# ── 结果归一化 ────────────────────────────────────────────────────────────

@mock.patch("scripts.lib.ozon_image_search._mtop_request")
def test_aibuy_image_search_normalizes(mock_mtop):
    mock_mtop.return_value = {
        "result": {"data": [MOCK_ITEM, {
            "offerId": "707351271433", "title": "T2", "price": "5.5",
            "imageUrl": "https://img2.jpg", "normalizationScore": "0.03",
        }]}
    }
    results = ois._aibuy_image_search("https://img.jpg", MOCK_COOKIES, region="1,2,3,4")
    assert len(results) == 2
    r0 = results[0]
    assert r0["id"] == "707351271432"
    assert r0["price"] == 10.9
    assert r0["normalization_score"] == 0.046148162335157394
    assert r0["badge"] == ""  # aibuy 无徽章
    assert r0["supplier"] == "深圳市一宠科技有限公司"
    # data 是第 2 个位置参数（api, data, cookies）；searchParam 含 imageRegion
    _api, call_data, _cookies = mock_mtop.call_args.args
    search_param = __import__("json").loads(call_data["searchParam"])
    assert search_param["imageRegion"] == "1,2,3,4"
    assert search_param["pageSize"] == 20


def test_aibuy_image_search_skips_missing_offerid():
    results = ois._aibuy_image_search("img", MOCK_COOKIES, region="",
                                      page_size=5)
    # 无 mock 时 _mtop_request 真实调用会失败返回 {} → 结果 []
    assert results == []


# ── token 缓存 ────────────────────────────────────────────────────────────

@mock.patch("scripts.lib.config_store.get_setting")
def test_read_aibuy_token_fresh(mock_get):
    mock_get.return_value = {**MOCK_COOKIES, "saved_at": time.time()}
    token = ois._read_aibuy_token()
    assert token is not None
    assert token["_m_h5_tk"] == MOCK_COOKIES["_m_h5_tk"]


@mock.patch("scripts.lib.config_store.get_setting")
def test_read_aibuy_token_expired(mock_get):
    mock_get.return_value = {**MOCK_COOKIES, "saved_at": time.time() - 7 * 3600}
    assert ois._read_aibuy_token() is None


# ── 主入口 fail-fast + 缓存 ───────────────────────────────────────────────

@mock.patch("scripts.lib.ozon_image_search.cache_get")
@mock.patch("scripts.lib.ozon_image_search._read_aibuy_token")
@mock.patch("scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome")
def test_search_by_image_aibuy_failfast_no_token(mock_chrome, mock_token, mock_cache):
    """token 刷新失败 → 快速返回 [] 不 raise（降级调用方处理）。"""
    mock_cache.return_value = None
    mock_token.return_value = None
    mock_chrome.return_value = {}
    result = ois.search_by_image_aibuy("https://img.jpg")
    assert result == []
    mock_chrome.assert_called_once()


@mock.patch("scripts.lib.ozon_image_search.cache_set")
@mock.patch("scripts.lib.ozon_image_search.cache_get")
@mock.patch("scripts.lib.ozon_image_search._aibuy_image_search")
@mock.patch("scripts.lib.ozon_image_search._aibuy_image_upload")
@mock.patch("scripts.lib.ozon_image_search._read_aibuy_token")
def test_search_by_image_aibuy_success(mock_token, mock_upload, mock_search,
                                       mock_cache, mock_cache_set):
    """token 有效 → upload + search → 归一化结果 + 缓存。"""
    mock_cache.return_value = None
    mock_token.return_value = {**MOCK_COOKIES, "saved_at": time.time()}
    mock_upload.return_value = "50,312,60,172"
    mock_search.return_value = [{
        "id": "707351271432", "title": "T1", "price": 10.9,
        "image": "https://img.jpg", "badge": "", "normalization_score": 0.046,
    }]
    results = ois.search_by_image_aibuy("https://img.jpg")
    assert len(results) == 1
    assert results[0]["id"] == "707351271432"
    mock_upload.assert_called_once_with("https://img.jpg", mock_token.return_value)
    mock_cache_set.assert_called_once()
    assert mock_cache_set.call_args.args[0] == "aibuy_search"
    assert mock_cache_set.call_args.kwargs.get("ttl") == 21600


@mock.patch("scripts.lib.ozon_image_search.cache_get")
def test_search_by_image_aibuy_cache_hit(mock_cache):
    """缓存命中直接返回，不触发网络。"""
    mock_cache.return_value = [{"id": "1"}]
    with mock.patch("scripts.lib.ozon_image_search._read_aibuy_token") as mock_tok:
        results = ois.search_by_image_aibuy("https://img.jpg")
        mock_tok.assert_not_called()
    assert results == [{"id": "1"}]


# ── 主入口（直接运行）────────────────────────────────────────────────────

def test_image_upload_failure_does_not_block_search():
    """upload 失败（region 空）不阻塞 search（带空 region 继续）。"""
    with mock.patch("scripts.lib.ozon_image_search._mtop_request") as mock_mtop:
        mock_mtop.return_value = {}
        region = ois._aibuy_image_upload("img", MOCK_COOKIES)
        assert region == ""


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

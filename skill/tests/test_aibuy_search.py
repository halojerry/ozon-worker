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
    "cateLevel1Id": "122916001",
    "cateLevel2Id": "201305503",
    "categoryName": "宠物及园艺",
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
    # v0.39 Issue3: cateLevel 类目字段透传（类目匹配增强）
    assert r0["cate_level1_id"] == "122916001"
    assert r0["cate_level2_id"] == "201305503"
    assert r0["category_name"] == "宠物及园艺"
    # 缺 cateLevel 字段的候选不报错（容错）
    assert results[1]["cate_level1_id"] == ""
    assert results[1]["cate_level2_id"] == ""
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


@mock.patch("scripts.lib.config_store.get_setting")
def test_read_aibuy_token_poisoned_value_invalidates(mock_get):
    """已落盘的毒 token（_m_h5_tk 空值、未过期）→ 视作无效，触发刷新（W5.2）。"""
    mock_get.return_value = {**MOCK_COOKIES, "_m_h5_tk": "", "saved_at": time.time()}
    assert ois._read_aibuy_token() is None


# ── W5.1/W5.2: 毒 token value 校验（v0.57）──────────────────────────────

def test_aibuy_token_valid_rejects_empty_value():
    """_m_h5_tk value 空 → 无效（毒 token 不落盘，I-8 根因修复）。"""
    assert ois._aibuy_token_valid(dict(MOCK_COOKIES, _m_h5_tk="")) is False
    assert ois._aibuy_token_valid(dict(MOCK_COOKIES, _m_h5_tk="   ")) is False
    assert ois._aibuy_token_valid({}) is False
    assert ois._aibuy_token_valid(None) is False
    assert ois._aibuy_token_valid({"a": 1}) is False
    # 缺 4 key 中的任意一个 → 无效
    assert ois._aibuy_token_valid({k: v for k, v in MOCK_COOKIES.items() if k != "isg"}) is False


def test_aibuy_token_valid_accepts_real():
    assert ois._aibuy_token_valid(MOCK_COOKIES) is True
    assert ois._aibuy_token_valid({**MOCK_COOKIES, "saved_at": 123}) is True


@mock.patch("scripts.lib.ozon_image_search.cache_get")
@mock.patch("scripts.lib.ozon_image_search._read_aibuy_token")
@mock.patch("scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome")
@mock.patch("scripts.lib.ozon_image_search._save_aibuy_token")
def test_search_by_image_aibuy_does_not_save_poison_token(mock_save, mock_chrome,
                                                          mock_token, mock_cache):
    """Chrome 返回空 value 的 cookie dict → 不落盘、快速返回 []（W5.2）。"""
    mock_cache.return_value = None
    mock_token.return_value = None
    mock_chrome.return_value = {"_m_h5_tk": "", "_m_h5_tk_enc": "x", "tfstk": "y", "isg": "z"}
    result = ois.search_by_image_aibuy("https://img.jpg")
    assert result == []
    mock_save.assert_not_called()


@mock.patch("scripts.lib.ozon_image_search.cache_set")
@mock.patch("scripts.lib.ozon_image_search._aibuy_image_search")
@mock.patch("scripts.lib.ozon_image_search._aibuy_image_upload")
@mock.patch("scripts.lib.ozon_image_search._save_aibuy_token")
@mock.patch("scripts.lib.ozon_image_search._fetch_aibuy_cookies_from_chrome")
@mock.patch("scripts.lib.ozon_image_search._read_aibuy_token")
@mock.patch("scripts.lib.ozon_image_search.cache_get")
def test_search_by_image_aibuy_saves_valid_fetched_token(mock_cache, mock_token, mock_chrome,
                                                         mock_save, mock_upload, mock_search,
                                                         mock_cache_set):
    """Chrome 返回有效 cookie → 落盘 token 并继续搜索。"""
    mock_cache.return_value = None
    mock_token.return_value = None
    mock_chrome.return_value = dict(MOCK_COOKIES)
    mock_upload.return_value = ""
    mock_search.return_value = [{"id": "1", "title": "T", "price": 1.0}]
    results = ois.search_by_image_aibuy("https://img.jpg")
    assert results
    mock_save.assert_called_once_with(mock_chrome.return_value)


# ── W5.3: mtop token 舞步等待（v0.57）────────────────────────────────────

@mock.patch("scripts.lib.ozon_image_search.time.sleep")
@mock.patch("scripts.lib.cdp_client.CdpConnection")
def test_fetch_aibuy_cookies_polls_until_token_ready(mock_conn_cls, mock_sleep):
    """导航后轮询 document.cookie：token 异步就绪时提前退出（非固定 2s）。"""
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    mock_conn_cls.return_value = conn
    conn.new_tab.return_value = tab
    tab.evaluate.side_effect = [
        "tfstk=abc; isg=def; _m_h5_tk_enc=xyz",                       # 第 1 次：缺 _m_h5_tk
        "tfstk=abc; isg=def; _m_h5_tk_enc=xyz; _m_h5_tk=" + MOCK_COOKIES["_m_h5_tk"],  # 第 2 次：就绪
    ]
    cookies = ois._fetch_aibuy_cookies_from_chrome()
    assert cookies["_m_h5_tk"] == MOCK_COOKIES["_m_h5_tk"]
    assert tab.evaluate.call_count == 2, "token 就绪后应立即退出轮询"
    mock_sleep.assert_called_once()


@mock.patch("scripts.lib.ozon_image_search.time.sleep")
@mock.patch("scripts.lib.cdp_client.CdpConnection")
def test_fetch_aibuy_cookies_poll_timeout_returns_empty(mock_conn_cls, mock_sleep):
    """轮询到上限仍无有效 token → 返回 {}（毒 cookie 不过关）。"""
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    mock_conn_cls.return_value = conn
    conn.new_tab.return_value = tab
    # 每次 evaluate 返回缺 _m_h5_tk 或空 value 的 cookie 串
    tab.evaluate.return_value = "tfstk=abc; isg=def; _m_h5_tk_enc=xyz; _m_h5_tk="
    _clock = [0.0]

    def _fake_time():
        _clock[0] += 1.0  # 每次 +1s，快速越过 8s 上限
        return _clock[0]

    with mock.patch("scripts.lib.ozon_image_search.time.time", side_effect=_fake_time):
        cookies = ois._fetch_aibuy_cookies_from_chrome()
    assert cookies == {}
    mock_sleep.assert_called()  # 确实走了轮询路径


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


# ── Step 2: _pick_best_match trusted_source 分通道护栏 ──────────────────

def test_pick_best_match_trusted_rank1_passes_without_badge():
    """aibuy trusted_source: 无徽章 + 官方排序前 2 位 → 直接放行（不依赖词对词典）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "跨境苹果airtag猫咪宠物项圈围脖", "price": 10.9,
         "badge": "", "normalization_score": 0.03},
        {"id": "2", "title": "宠物梳子套装", "price": 5.5,
         "badge": "", "normalization_score": 0.05},
    ]
    best = od._pick_best_match(results, "Ошейник для кошки", token="t", trusted_source=True)
    assert best is not None
    assert best["id"] == "1"
    assert best["normalization_score"] == 0.03  # 元数据透传


def test_pick_best_match_trusted_rank3_still_guardrailed():
    """aibuy trusted_source 但 best 排第 3 位之后（idx_rank<0.33）→ 仍走 conf 护栏。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "0", "title": "符合0条件", "price": 10.0, "badge": "符合 0/3 个条件",
         "normalization_score": 0.9},
        {"id": "1", "title": "无价格", "price": 0.0, "badge": "",
         "normalization_score": 0.9},
        {"id": "3", "title": "完全无关C", "price": 10.0, "badge": "",
         "normalization_score": 0.9},
    ]
    # 前 2 位被过滤（0/N 徽章 + 无价格）→ best 是第 3 位，idx_rank=0.25 < 0.33
    # 词对 conf 为 0 + LLM 判 False → 应拒绝（排名靠后不因 trusted 放行）
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, "Ошейник для кошки", token="t", trusted_source=True)
    assert best is None, "第 3 位之后即使 normalizationScore 高也不应放行"


def test_pick_best_match_trusted_false_preserves_old_guardrail():
    """trusted_source=False（AK/CDP 默认）：无徽章 + 弱标题 → 仍拒绝（护栏不放松）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "花开富贵香开花香檀香供佛香", "price": 8.0, "badge": ""},
    ]
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, "Палочки от комаров", token="t")
    assert best is None, "非 trusted 无徽章弱标题应维持拒绝"


def test_pick_best_match_normalization_score_bonus_does_not_override_rank():
    """norm_bonus 上限 5 分，不压倒 idx_rank 主信号（rank1 无 norm 仍胜 rank2 高 norm）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "跨境苹果airtag猫咪宠物项圈围脖", "price": 10.9,
         "badge": "", "normalization_score": 0.0},
        {"id": "2", "title": "宠物梳子套装", "price": 5.5,
         "badge": "", "normalization_score": 1.0},
    ]
    best = od._pick_best_match(results, "Ошейник для кошки", token="t", trusted_source=True)
    assert best is not None
    assert best["id"] == "1", "rank1 应胜出，norm_bonus 不应压倒 idx_rank"


# ── Step 3: AK similarity_score 上膛（v0.39）──────────────────────────────

def test_pick_best_match_ak_high_score_passes_no_badge():
    """AK 候选官方相似度高（≥0.8）+ 排名前 2 → no-badge 分支放行（此前只看 conf/LLM）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "完全无关标题词", "price": 8.0, "badge": "",
         "similarity_score": 92.0},  # AK find_product score 0-100
    ]
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, "Ошейник для кошки", token="t")
    assert best is not None, "AK 高分相似度应放行"
    assert best["id"] == "1"


def test_pick_best_match_ak_low_score_still_rejected():
    """AK 相似度低（<0.8）+ conf 弱 + LLM False → 仍拒绝（高分放行有下限）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "花开富贵香开花香檀香供佛香", "price": 8.0, "badge": "",
         "similarity_score": 30.0},
    ]
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, "Палочки от комаров", token="t")
    assert best is None, "AK 低分相似度应维持拒绝"


def test_pick_best_match_ak_score_bonus_ranks_high():
    """AK score 加分使高相似度候选排名提升——rank1 被过滤（无价）时高相似度 rank2 胜出。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "完全不相关A", "price": 0.0, "badge": "",  # 无价 → 被过滤
         "similarity_score": 0.0},
        {"id": "2", "title": "完全不相关B", "price": 10.0, "badge": "",
         "similarity_score": 95.0},  # 唯一候选，相似度极高
    ]
    # trusted_source=False（AK 路径），conf 弱 → 高 AK 相似度（95→19分加分）应放行
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, "Ошейник для кошки", token="t")
    assert best is not None
    assert best["id"] == "2", "高相似度 AK 候选应凭高分放行"


def test_pick_best_match_ak_score_ignored_for_aibuy():
    """trusted_source=True（aibuy）时 similarity_score 不参与加分（aibuy 无此信号）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    results = [
        {"id": "1", "title": "跨境苹果airtag猫咪宠物项圈围脖", "price": 10.9,
         "badge": "", "normalization_score": 0.0, "similarity_score": 99.0},
        {"id": "2", "title": "宠物梳子套装", "price": 5.5,
         "badge": "", "normalization_score": 0.0, "similarity_score": 0.0},
    ]
    best = od._pick_best_match(results, "Ошейник для кошки", token="t", trusted_source=True)
    assert best is not None
    assert best["id"] == "1", "aibuy 通道靠 idx_rank 放行（前 2 位），similarity_score 不干扰"


# ── Issue3: 类目末级词净化（v0.39）───────────────────────────────────────

def test_category_search_variants_split_compound():
    """复合末级词（顿号）分拆：'化妆刷、刷包' → ['化妆刷', '刷包']（Ozon 树按单段匹配）。"""
    from scripts.cloud_probe import _category_search_variants
    v = _category_search_variants("美容护肤/彩妆 > 美妆工具 > 化妆刷、刷包")
    assert v == ["化妆刷", "刷包"]


def test_category_search_variants_single():
    """无顿号末级词返回单段。"""
    from scripts.cloud_probe import _category_search_variants
    assert _category_search_variants("玩具 > 运动、休闲、传统玩具 > 陀螺") == ["陀螺"]


def test_category_search_variants_empty_path():
    """空面包屑返回空列表（无候选词）。"""
    from scripts.cloud_probe import _category_search_variants
    assert _category_search_variants("") == []
    assert _category_search_variants("  ") == []


# ── 需求3: 类目歧义 LLM 消歧（v0.39）─────────────────────────────────────

def test_llm_disambiguate_category_selects_correct():
    """护手霜案例：LLM 选中护肤霜（Крем для ухода）而非首位私密霜。"""
    import scripts.lib.ozon_discovery as od
    cands = [
        {"category_name": "Интимная", "type_name": "Крем интимный"},
        {"category_name": "Уход", "type_name": "Крем для ухода за кожей"},
    ]
    with mock.patch("requests.post", return_value=mock.Mock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "1"}}]},
        )):
        idx = od._llm_disambiguate_category("护手霜", cands, token="t")
    assert idx == 1


def test_llm_disambiguate_category_fallback_first():
    """LLM 失败/无 token → 返回 0（维持首位，宁缺毋滥不放大错误）。"""
    import scripts.lib.ozon_discovery as od
    cands = [{"type_name": "A"}, {"type_name": "B"}]
    # 无 token → 0
    assert od._llm_disambiguate_category("护手霜", cands, token="") == 0
    # 单候选 → 0（无需消歧）
    assert od._llm_disambiguate_category("护手霜", [cands[0]], token="t") == 0
    # HTTP 失败 → 0
    with mock.patch("requests.post", return_value=mock.Mock(status_code=500, json=lambda: {})):
        assert od._llm_disambiguate_category("护手霜", cands, token="t") == 0
    # 异常 → 0
    with mock.patch("requests.post", side_effect=Exception("conn refused")):
        assert od._llm_disambiguate_category("护手霜", cands, token="t") == 0


def test_llm_disambiguate_category_bounds():
    """LLM 返回越界索引 → 0（防候选越界）。"""
    import scripts.lib.ozon_discovery as od
    cands = [{"type_name": "A"}, {"type_name": "B"}]
    with mock.patch("requests.post", return_value=mock.Mock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "99"}}]},
        )):
        assert od._llm_disambiguate_category("护手霜", cands, token="t") == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

"""PRD M5b: skill 货源匹配上报 source_candidates 单测。

覆盖:
1. report_source_candidates 单次 POST /api/v1/source-candidates,payload 含
   token/client_id/candidates,offer_id 从 URL 缺失时自动补 detail_url。
2. 无 token / 无 product_id → 跳过不 POST(fail-open)。
3. 价格解析(¥12.34 / "12.34-15.00" / 纯数字 → 取区间下限)。
4. match_score:badge_score > confidence > 排名归一化。
5. spawn_source_report 非阻塞(fail-open)。
纯 mock requests/config_store,不依赖真实网络。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import source_candidates as sc


def _matches():
    return [
        {"id": "100001", "title": "商品A", "price": "¥12.34",
         "badge_score": 0.9},
        {"id": "100002", "title": "商品B", "price": "8.50-10.00",
         "confidence": 0.75, "detail_url": "https://detail.1688.com/offer/100002.html"},
        {"id": "100003", "title": "商品C", "price": "5"},
    ]


def test_report_posts_correct_payload():
    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        sc.report_source_candidates("ozon-1", _matches(), "aibuy",
                                    client_id="store-1", token="sk-test")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://worker.mxou.cn/api/v1/source-candidates"
        payload = kwargs["json"]
        assert payload["token"] == "sk-test"
        assert payload["client_id"] == "store-1"
        rows = payload["candidates"]
        assert len(rows) == 3
        assert rows[0]["source_offer_id"] == "100001"
        assert rows[0]["source_url"] == "https://detail.1688.com/offer/100001.html"
        assert rows[0]["price_cny"] == 12.34
        assert rows[0]["match_score"] == 0.9
        assert rows[0]["match_method"] == "aibuy"
        # 无 badge → confidence;无 detail_url → 用 id 补 detail_url
        assert rows[1]["match_score"] == 0.75
        assert rows[1]["source_url"] == "https://detail.1688.com/offer/100002.html"
        # 无评分 → 排名归一化递减
        assert rows[2]["match_score"] < rows[0]["match_score"]


def test_report_skips_without_token_or_product():
    with mock.patch("requests.post") as mock_post, \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""):
        mock_post.return_value.status_code = 200
        sc.report_source_candidates("ozon-1", _matches(), "aibuy", token="")
        sc.report_source_candidates("ozon-1", _matches(), "aibuy")
        sc.report_source_candidates("", _matches(), "aibuy", token="sk-test")
        sc.report_source_candidates("ozon-1", [], "aibuy", token="sk-test")
        mock_post.assert_not_called()


def test_parse_price_cny():
    assert sc._parse_price_cny("¥12.34") == 12.34
    assert sc._parse_price_cny("8.50-10.00") == 8.5
    assert sc._parse_price_cny("5") == 5.0
    assert sc._parse_price_cny("") is None
    assert sc._parse_price_cny(None) is None


def test_spawn_report_fail_open():
    """spawn 线程缺 token → 内部直接返回,不抛异常。"""
    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""):
        sc.spawn_source_report("ozon-1", _matches(), "ak")

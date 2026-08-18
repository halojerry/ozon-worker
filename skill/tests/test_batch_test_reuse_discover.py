#!/usr/bin/env python3
"""v0.58: batch_test 复用 discover 已匹配货源 + 默认重量一致性回归。

问题背景：
- batch_test 走 Ozon 跟卖曾每次重跑 CDP 图搜，不复用 discover 已匹配货源；
  且图搜质量差时护栏拦截多个（既慢又容易漏）。
- discover 选品分析无重量时落 DEFAULT_LOGISTICS_CNY=15，而上架管线默认
  500g → ¥6，差 ¥9/单 → 轻小件被误判「利润不足」。

覆盖：
- process_ozon_url 命中 discover 缓存 → 复用直上（不调 follow_sell_cloud）
- process_ozon_url 未命中 → 走 follow 图搜链路
- discover 缓存无 match_1688_url / 非 profitable → 降级 follow
- estimate_shipping_cny 与 cloud_probe price_estimate 分段一致（防漂移）

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_batch_test_reuse_discover.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import batch_test  # noqa: E402
from scripts.lib.ozon_discovery import estimate_shipping_cny  # noqa: E402


def _discover_entry(pid="4767514314", url="https://detail.1688.com/offer/1001.html",
                    status="profitable") -> dict:
    return {
        "ozon_product_id": pid,
        "ozon_title": "Автопоилка для кошек",
        "ozon_price": 1500.0,
        "ozon_url": f"https://www.ozon.ru/product/{pid}",
        "match_1688_url": url,
        "match_1688_title": "宠物自动饮水器",
        "match_1688_price": 25.0,
        "match_1688_images": [],
        "weight_g": 0,
        "dimensions_mm": {},
        "competing_sellers": 7,
        "status": status,
    }


def _run_process(product_id="4767514314", dry_run=False):
    return batch_test.process_ozon_url(
        f"https://www.ozon.ru/product/slug-{product_id}/",
        product_id,
        "cid",
        "akey",
        "http://localhost:8080",
        dry_run=dry_run,
        store_id="",
    )


def test_reuse_hit_uses_build_envelope_not_follow():
    """命中 discover 缓存 → 复用直上（不调 follow_sell_cloud）。"""
    with mock.patch("batch_test._find_discover_source",
                    return_value=_discover_entry()), \
         mock.patch("batch_test._product_candidate_from_dict",
                    return_value=object()), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    return_value={"draft": {}, "source": {}, "extensions": {}}), \
         mock.patch("scripts.cloud_probe.submit_envelope",
                    return_value={"ok": True, "task_id": "T-reuse"}) as m_submit, \
         mock.patch("scripts.cloud_probe.follow_sell_cloud") as m_follow:
        r = _run_process()
    assert r.get("success") is True
    assert r.get("task_id") == "T-reuse"
    assert r.get("reused_discover_source") is True
    m_submit.assert_called_once()
    m_follow.assert_not_called()


def test_reuse_hit_dry_run_no_submit():
    """命中缓存 + dry-run → 只组装不提交，标记 success。"""
    with mock.patch("batch_test._find_discover_source",
                    return_value=_discover_entry()), \
         mock.patch("batch_test._product_candidate_from_dict",
                    return_value=object()), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    return_value={"draft": {}, "source": {}, "extensions": {}}), \
         mock.patch("scripts.cloud_probe.submit_envelope") as m_submit, \
         mock.patch("scripts.cloud_probe.follow_sell_cloud") as m_follow:
        r = _run_process(dry_run=True)
    assert r.get("success") is True
    assert r.get("dry_run") is True
    assert r.get("reused_discover_source") is True
    m_submit.assert_not_called()
    m_follow.assert_not_called()


def test_reuse_miss_falls_back_to_follow():
    """未命中缓存 → 走 follow 图搜链路（原行为）。"""
    with mock.patch("batch_test._find_discover_source",
                    return_value=None), \
         mock.patch("scripts.cloud_probe.follow_sell_cloud",
                    return_value={"success": True, "task_id": "T-follow",
                                  "1688_matches": [{"id": "1", "badge_score": 3}]}) as m_follow:
        r = _run_process()
    assert r.get("success") is True
    assert r.get("task_id") == "T-follow"
    m_follow.assert_called_once()


def test_reuse_cached_without_match_url_falls_back():
    """缓存存在但无 match_1688_url → _find_discover_source 过滤掉 → 降级 follow。"""
    entry = _discover_entry()
    entry["match_1688_url"] = ""
    with mock.patch("scripts.lib.ozon_discovery.load_latest_discovery",
                    return_value=[entry]), \
         mock.patch("scripts.cloud_probe.follow_sell_cloud",
                    return_value={"success": True, "task_id": "T-follow",
                                  "1688_matches": [{"id": "1", "badge_score": 3}]}) as m_follow:
        r = _run_process()
    assert r.get("success") is True
    m_follow.assert_called_once()


def test_reuse_cached_non_profitable_falls_back():
    """缓存 status 非 profitable/matched → _find_discover_source 过滤掉 → 降级 follow。"""
    entry = _discover_entry(status="rejected")
    with mock.patch("scripts.lib.ozon_discovery.load_latest_discovery",
                    return_value=[entry]), \
         mock.patch("scripts.cloud_probe.follow_sell_cloud",
                    return_value={"success": True, "task_id": "T-follow",
                                  "1688_matches": [{"id": "1", "badge_score": 3}]}) as m_follow:
        r = _run_process()
    assert r.get("success") is True
    m_follow.assert_called_once()


def test_find_discover_source_filters_by_product_id():
    """_find_discover_source 按 ozon_product_id 匹配 + 只收 profitable/matched。"""
    entries = [
        _discover_entry(pid="111", url="https://detail.1688.com/offer/1.html"),
        _discover_entry(pid="222", url="https://detail.1688.com/offer/2.html", status="matched"),
        _discover_entry(pid="333", url="", status="profitable"),  # 无货源
        _discover_entry(pid="444", url="https://detail.1688.com/offer/4.html", status="rejected"),
    ]
    with mock.patch("scripts.lib.ozon_discovery.load_latest_discovery",
                    return_value=entries) as m_load:
        hit = batch_test._find_discover_source("222")
    assert hit is not None
    assert hit["ozon_product_id"] == "222"
    m_load.assert_called_once()
    with mock.patch("scripts.lib.ozon_discovery.load_latest_discovery",
                    return_value=entries):
        assert batch_test._find_discover_source("444") is None  # rejected 不采信
        assert batch_test._find_discover_source("333") is None  # 无货源不采信
        assert batch_test._find_discover_source("999") is None  # 不存在


def test_find_discover_source_empty_cache():
    """无缓存 → None（不阻断，降级 follow）。"""
    with mock.patch("scripts.lib.ozon_discovery.load_latest_discovery",
                    return_value=[]):
        assert batch_test._find_discover_source("111") is None


def test_estimate_shipping_cny_consistency():
    """默认重量/运费与 cloud_probe price_estimate 分段一致（防漂移）。

    无重量 → 500g → ¥6；≤500g → ¥6；≤1000g → ¥8；>1000g → ¥15。
    """
    from scripts.lib.ozon_discovery import DEFAULT_WEIGHT_G
    assert DEFAULT_WEIGHT_G == 500
    assert estimate_shipping_cny(None) == 6.0
    assert estimate_shipping_cny(0) == 6.0
    assert estimate_shipping_cny(300) == 6.0
    assert estimate_shipping_cny(500) == 6.0
    assert estimate_shipping_cny(501) == 8.0
    assert estimate_shipping_cny(1000) == 8.0
    assert estimate_shipping_cny(1001) == 15.0
    assert estimate_shipping_cny(5000) == 15.0


def test_calculate_profit_missing_weight_uses_default_500g():
    """_calculate_profit 无重量 → 按默认 500g 查费率表（不再跳过费率表落本地 ¥15）。

    回归：此前 _query_logistics_from_worker 对 weight≤0 直接 return None →
    discover 落 DEFAULT_LOGISTICS_CNY=15，而上架管线默认 500g 走费率表，
    两条路径不一致，轻小件被误判利润不足。修复后无重量也按 500g 查表。
    """
    from scripts.lib.ozon_discovery import (
        ProductCandidate,
        _calculate_profit,
    )

    c = ProductCandidate(ozon_product_id="1", ozon_title="t", ozon_price=1000.0)
    c.match_1688_price = 20.0
    c.weight_g = 0
    # 费率表命中：Worker 返回真实费率（假设 500g → ¥8.5）
    with mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                    return_value=mock.Mock(cost=8.5, estimated=False, fallback_chain="q1_hit")) as m_q:
        _calculate_profit(c)
    # 必须调用费率表查询（传候选原始 weight_g=0，转换在 _query_logistics_from_worker 内部）
    m_q.assert_called_once()
    assert m_q.call_args[0][0] == 0, f"应传候选原始 weight_g, got {m_q.call_args[0][0]}"
    assert c.estimated_logistics_cny == 8.5, "应使用费率表真实费率"
    assert c.logistics_fallback_chain == "q1_hit"

    # 费率表+last-good 均不可达（worker 离线）→ 本地兜底与上架管线同源（默认 500g → ¥6）
    c2 = ProductCandidate(ozon_product_id="2", ozon_title="t", ozon_price=1000.0)
    c2.match_1688_price = 20.0
    c2.weight_g = 0
    with mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                    return_value=None):
        _calculate_profit(c2)
    assert c2.estimated_logistics_cny == 6.0, f"兜底应按默认 500g 估 ¥6, got {c2.estimated_logistics_cny}"
    assert c2.logistics_fallback_chain == "default_500g"


def test_query_logistics_from_worker_missing_weight_queries_default():
    """_query_logistics_from_worker 无重量 → 按 DEFAULT_WEIGHT_G 查费率表（不再 return None）。"""
    from scripts.lib.ozon_discovery import DEFAULT_WEIGHT_G, _query_logistics_from_worker

    with mock.patch("scripts.lib.config_store.get_mxou_token",
                    return_value="tok"), \
         mock.patch("requests.post") as m_post, \
         mock.patch.dict("scripts.lib.ozon_discovery._LOGISTICS_QUOTE_CACHE",
                         {}, clear=True):
        m_post.return_value.status_code = 200
        m_post.return_value.json.return_value = {"logistics_cost_cny": 8.5}
        q = _query_logistics_from_worker(0)
    assert q is not None, "无重量也应按默认重量查费率表"
    assert q.cost == 8.5
    assert m_post.call_args[1]["json"]["weight_g"] == DEFAULT_WEIGHT_G, \
        f"payload 应按默认 {DEFAULT_WEIGHT_G}g, got {m_post.call_args[1]['json']['weight_g']}"

    with mock.patch("scripts.lib.config_store.get_mxou_token",
                    return_value="tok"), \
         mock.patch("requests.post") as m_post, \
         mock.patch.dict("scripts.lib.ozon_discovery._LOGISTICS_QUOTE_CACHE",
                         {}, clear=True):
        m_post.return_value.status_code = 200
        m_post.return_value.json.return_value = {"logistics_cost_cny": 7.0}
        q = _query_logistics_from_worker(None)
    assert q is not None
    assert q.cost == 7.0


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
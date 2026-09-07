#!/usr/bin/env python3
"""seller 富化 cookie 直调优先 + CDP 兜底回归（discover 漏斗 v2 · Task 6）。

背景：discover ②b 富化此前必经 seller.ozon.ru 页面导航（_tab_for_seller +
check_seller_login/wait_for_seller_login 登录窗口）——seller 未登录时阻塞等输入。
queries 命令已有 cookie 直调先例（_fetch_seller_session_cookies 只读不导航 +
_seller_direct_post），本批把畅销榜 map 富化接上同一通道：直调命中 → 完全跳过
seller 页导航；直调失败/未登录 → 出声降级原 CDP 路径（可用性不回退）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_seller_direct_enrichment.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


def _cand(pid: str, status: str = "ok") -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title="t", ozon_price=1000.0)
    c.status = status
    return c


def _patch_osa(**overrides):
    """patch 富化依赖的 ozon_seller_analytics 符号（od 内延迟 import，须 patch 源模块）。"""
    from scripts.lib import ozon_seller_analytics as osa
    spec = {
        "apply_analytics_to_candidate": mock.DEFAULT,
        "check_seller_login": mock.DEFAULT,
        "wait_for_seller_login": mock.DEFAULT,
        "fetch_bestseller_metrics_map": mock.DEFAULT,
        "fetch_sales_analytics": mock.DEFAULT,
    }
    spec.update(overrides)
    return mock.patch.multiple(osa, **spec)


def test_direct_hit_skips_seller_navigation():
    """cookie 直调命中 → 不触 check_seller_login/wait_for_seller_login（免导航免登录等待）。"""
    calls = {"login_checked": 0}

    def _no_login(cdp):
        calls["login_checked"] += 1
        return True

    cands = [_cand("111"), _cand("222", "uncertain")]
    direct_map = {"111": {"sku": "111", "sold_count": 99}}
    with _patch_osa(
        _fetch_seller_session_cookies=lambda url: {"sc_company_id": "c1"},
        fetch_bestseller_metrics_map_direct=lambda cookies: direct_map,
        check_seller_login=_no_login,
        wait_for_seller_login=lambda cdp, **kw: True,
        apply_analytics_to_candidate=lambda c, m: bool(m),
        fetch_bestseller_metrics_map=lambda cdp, **kw: (_ for _ in ()).throw(
            AssertionError("CDP map 不应被调用")),
        fetch_sales_analytics=lambda cdp, skus, **kw: {},
    ):
        enriched = od._enrich_with_seller_metrics(cands, cdp=None,
                                                  cdp_url="http://127.0.0.1:9222")
    assert enriched == {"111": direct_map["111"]}
    assert calls["login_checked"] == 0   # 直调命中，登录检查整段跳过


def test_direct_miss_falls_back_to_cdp_with_login_window():
    """直调未登录（无 cookie）→ 回落 CDP 路径（login 检查 + CDP map）。"""
    cands = [_cand("333")]
    cdp_map = {"333": {"sku": "333", "sold_count": 7}}
    with _patch_osa(
        _fetch_seller_session_cookies=lambda url: {},          # 未登录无 cookie
        fetch_bestseller_metrics_map_direct=lambda cookies: {},
        check_seller_login=lambda cdp: True,
        wait_for_seller_login=lambda cdp, **kw: True,
        apply_analytics_to_candidate=lambda c, m: bool(m),
        fetch_bestseller_metrics_map=lambda cdp, **kw: cdp_map,
        fetch_sales_analytics=lambda cdp, skus, **kw: {},
    ):
        enriched = od._enrich_with_seller_metrics(cands, cdp=object(),
                                                  cdp_url="http://127.0.0.1:9222")
    assert enriched == {"333": cdp_map["333"]}


def test_direct_exception_sounds_and_falls_back():
    """直调抛异常 → warning 出声（W5 纪律）且 CDP 兜底照常。"""
    cands = [_cand("444")]
    cdp_map = {"444": {"sku": "444", "sold_count": 3}}

    def _boom(url):
        raise RuntimeError("cdp gone")

    with _patch_osa(
        _fetch_seller_session_cookies=_boom,
        fetch_bestseller_metrics_map_direct=lambda cookies: (_ for _ in ()).throw(
            AssertionError("cookie 为空不应进直调")),
        check_seller_login=lambda cdp: True,
        wait_for_seller_login=lambda cdp, **kw: True,
        apply_analytics_to_candidate=lambda c, m: bool(m),
        fetch_bestseller_metrics_map=lambda cdp, **kw: cdp_map,
        fetch_sales_analytics=lambda cdp, skus, **kw: {},
    ):
        enriched = od._enrich_with_seller_metrics(cands, cdp=object(),
                                                  cdp_url="http://127.0.0.1:9222")
    assert enriched == {"444": cdp_map["444"]}


def test_remaining_pids_go_per_sku():
    """畅销榜 map 未命中的候选 → 降级逐 SKU fetch_sales_analytics（原可用性保持）。"""
    cands = [_cand("555"), _cand("666")]
    per_sku = {"666": {"sku": "666", "sold_count": 5}}
    with _patch_osa(
        _fetch_seller_session_cookies=lambda url: {},
        fetch_bestseller_metrics_map_direct=lambda cookies: {},
        check_seller_login=lambda cdp: True,
        wait_for_seller_login=lambda cdp, **kw: True,
        apply_analytics_to_candidate=lambda c, m: bool(m),
        fetch_bestseller_metrics_map=lambda cdp, **kw: {"555": {"sku": "555", "sold_count": 1}},
        fetch_sales_analytics=lambda cdp, skus, **kw: per_sku,
    ):
        enriched = od._enrich_with_seller_metrics(cands, cdp=object(),
                                                  cdp_url="http://127.0.0.1:9222")
    assert set(enriched) == {"555", "666"}


def test_empty_enrichment_warns():
    """全部缺失 → 空 enriched（调用方打缺失警告的行为不变，此处返回空 dict）。"""
    cands = [_cand("777")]
    with _patch_osa(
        _fetch_seller_session_cookies=lambda url: {},
        fetch_bestseller_metrics_map_direct=lambda cookies: {},
        check_seller_login=lambda cdp: True,
        wait_for_seller_login=lambda cdp, **kw: True,
        apply_analytics_to_candidate=lambda c, m: bool(m),
        fetch_bestseller_metrics_map=lambda cdp, **kw: {},
        fetch_sales_analytics=lambda cdp, skus, **kw: {},
    ):
        assert od._enrich_with_seller_metrics(cands, cdp=object(),
                                              cdp_url="http://127.0.0.1:9222") == {}


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

#!/usr/bin/env python3
"""match_selected 目标驱动达标即停回归（v0.70 discover-task 语义翻转）。

--target-count 现在是「达标数」（profitable 出口数）：
① 匹配阶段 profitable 数触顶即停止发起新图搜（护 aibuy 配额，串行逐个判/
   并行分块间判）；
② 匹配池按达标可能性降序（rank_match_pool：月销高→跟卖少）；
③ target_profitable=0（缺省）保持旧行为全量匹配。
④ 配套：cli.cmd_discover_task 的 match-limit 缺省 = 目标×3（argparse 默认
   None → cli 内解析），此处锁 lib 侧行为。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_target_driven_match_v070.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import (  # noqa: E402
    ProductCandidate,
    match_selected,
    rank_match_pool,
)

_GOOD_MATCH = {"url": "https://detail.1688.com/offer/1.html",
               "title": "货源", "price": "5.0", "images": [],
               "confidence": 0.9, "badge_eff": 0.9}


def _cands(n: int) -> list[ProductCandidate]:
    out = []
    for i in range(n):
        c = ProductCandidate(ozon_product_id=str(100 + i), ozon_title="t",
                             ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
        c.status = "ok"
        out.append(c)
    return out


def _profitable(candidate, **_):
    """利润计算替身：一律 50% margin（> min_margin 15 → profitable）。"""
    candidate.profit_margin = 50.0


def _run(cands, *, workers=1, match=None, profitable=True, **kw):
    """patch 外部依赖跑 match_selected；match(i) 决定第 i 个候选的匹配结果；
    profitable=False 时利润替身改判 0%（→ rejected）。"""
    calls = {"n": 0}

    def _fake_search(cdp_url, images, title, conn=None, mxou_token="", **_):
        idx = calls["n"]
        calls["n"] += 1
        return match(idx) if match else dict(_GOOD_MATCH)

    def _fake_profit(candidate, **_):
        candidate.profit_margin = 50.0 if profitable else 0.0

    with mock.patch.object(od, "_search_1688_source", _fake_search), \
         mock.patch.object(od, "_discover_workers", return_value=workers), \
         mock.patch.object(od, "_calculate_profit", _fake_profit), \
         mock.patch.object(od, "_log_review_record"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch.object(od.time, "sleep", lambda s: None), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        result = match_selected(cands, "http://127.0.0.1:9222", **kw)
    return result, calls


def test_target_reached_stops_serial():
    """串行：全部必中且 target=3 → 恰好 3 次图搜即停，其余保持 ok。"""
    cands = _cands(8)
    result, calls = _run(cands, workers=1, target_profitable=3)
    assert calls["n"] == 3
    assert sum(1 for c in result if c.status == "profitable") == 3
    assert sum(1 for c in result if c.status == "ok") == 5


def test_target_not_reached_matches_all():
    """目标大于池子 → 全池匹配不早停（缺口由调用方如实报告）。"""
    cands = _cands(3)
    result, calls = _run(cands, workers=1, target_profitable=10)
    assert calls["n"] == 3
    assert all(c.status == "profitable" for c in result)


def test_target_zero_keeps_old_behavior():
    """target_profitable=0（缺省）→ 无达标即停，行为与 v0.69 逐字一致。"""
    cands = _cands(5)
    result, calls = _run(cands, workers=1)
    assert calls["n"] == 5
    assert all(c.status == "profitable" for c in result)


def test_parallel_target_stop_chunk_granularity():
    """并行（workers=2）：块内全部达标 → 块间停，不再加码后续块。"""
    cands = _cands(8)
    result, calls = _run(cands, workers=2, target_profitable=2)
    assert calls["n"] == 2                       # 块1 即达标
    assert sum(1 for c in result if c.status == "profitable") == 2
    assert sum(1 for c in result if c.status == "ok") == 6


def test_target_stop_precedes_streak():
    """达标即停优先：idx0 一达标立即停（streak 还没机会累积，后续不烧图搜）。"""
    cands = _cands(6)

    def match(i):
        return None if i == 1 else dict(_GOOD_MATCH)

    result, calls = _run(cands, workers=1, match=match,
                         stop_on_no_match_streak=2, target_profitable=1)
    assert calls["n"] == 1                       # idx0 profitable 达标 → 立即停
    assert sum(1 for c in result if c.status == "profitable") == 1
    assert sum(1 for c in result if c.status == "ok") == 5


def test_no_profitable_target_never_reached():
    """全 rejected（margin 不足）→ target 永不满足，按 max_matches 跑满。"""
    cands = _cands(4)
    result, calls = _run(cands, workers=1, profitable=False,
                         target_profitable=2, max_matches=4)
    assert calls["n"] == 4
    assert all(c.status == "rejected" for c in result)


def test_rank_match_pool_order():
    """排序：月销降序为主、跟卖升序为辅（预匹配字段，蓝海分匹配后才有）。"""

    def _c(pid, sales, sellers):
        c = ProductCandidate(ozon_product_id=pid, ozon_title="t",
                             ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
        c.monthly_sales = sales
        c.competing_sellers = sellers
        return c

    pool = [_c("low", 10, 1), _c("hi_many", 500, 20),
            _c("hi_few", 500, 3), _c("mid", 100, 5)]
    ranked = rank_match_pool(pool)
    assert [c.ozon_product_id for c in ranked] == ["hi_few", "hi_many", "mid", "low"]
    # 缺失字段（None/0）不炸、排最后
    sparse = ProductCandidate(ozon_product_id="bare", ozon_title="t",
                              ozon_price=1.0, ozon_images=["http://img/x.jpg"])
    assert rank_match_pool([sparse, _c("a", 5, 1)])[0].ozon_product_id == "a"


def test_match_limit_still_caps_with_target():
    """max_matches 与 target 双闸并存：先到者停。"""
    cands = _cands(6)
    result, calls = _run(cands, workers=1, target_profitable=10, max_matches=2)
    assert calls["n"] == 2                       # max_matches 先触顶
    assert sum(1 for c in result if c.status == "ok") == 4


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

#!/usr/bin/env python3
"""match_selected 任务式限额回归（discover 漏斗 v2 · Task 8a）。

discover-task 无人值守需要：①max_matches 匹配数上限（护 aibuy 配额）；
②连续 no_match 早停（货源池耗尽信号，不再空烧）；③并行模式分块提交
（块间检查早停，已提交 futures 不可撤但不再加码）；④节奏间隔可调
（aibuy 请求间 ≥2s 抖动，串行模式生效）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_match_selected_limits.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate, match_selected  # noqa: E402


def _cands(n: int) -> list[ProductCandidate]:
    out = []
    for i in range(n):
        c = ProductCandidate(ozon_product_id=str(100 + i), ozon_title="t",
                             ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
        c.status = "ok"
        out.append(c)
    return out


def _run(cands, *, workers=1, match=None, **kw):
    """patch 外部依赖跑 match_selected；match(i) 决定第 i 个候选的匹配结果。"""
    calls = {"n": 0}

    def _fake_search(cdp_url, images, title, conn=None, mxou_token="", **_):
        idx = calls["n"]
        calls["n"] += 1
        return match(idx) if match else {"url": "https://detail.1688.com/offer/1.html",
                                         "title": "货源", "price": "5.0", "images": [],
                                         "confidence": 0.9, "badge_eff": 0.9}

    sleeps: list[float] = []
    with mock.patch.object(od, "_search_1688_source", _fake_search), \
         mock.patch.object(od, "_discover_workers", return_value=workers), \
         mock.patch.object(od.CdpConnection if hasattr(od, "CdpConnection") else od,
                           "__init__", mock.DEFAULT), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch.object(od.time, "sleep", lambda s: sleeps.append(s)), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        result = match_selected(cands, "http://127.0.0.1:9222", **kw)
    return result, calls, sleeps


def test_max_matches_caps_matched_count():
    """max_matches=2 → 只有 2 个候选进匹配，其余保持 ok（未匹配语义）。"""
    cands = _cands(5)
    result, calls, _ = _run(cands, max_matches=2)
    assert calls["n"] == 2                      # 只发起 2 次图搜
    done = [c for c in result if c.status in ("matched", "profitable", "rejected")]
    untouched = [c for c in result if c.status == "ok"]
    assert len(done) == 2 and len(untouched) == 3


def test_serial_streak_stop():
    """串行：连续 2 次 no_match → 早停，后续候选不再烧图搜。"""
    cands = _cands(6)

    def match(i):
        return None if i >= 2 else {"url": "https://detail.1688.com/offer/1.html",
                                    "title": "货源", "price": "5.0", "images": [],
                                    "confidence": 0.9, "badge_eff": 0.9}

    result, calls, _ = _run(cands, workers=1, match=match, stop_on_no_match_streak=2)
    # 前 2 个 matched，第 3/4 个 no_match 触发早停 → 图搜共 4 次（不是 6）
    assert calls["n"] == 4
    assert sum(1 for c in result if c.status in ("matched", "profitable")) == 2
    assert sum(1 for c in result if c.status == "no_match") == 2
    assert sum(1 for c in result if c.status == "ok") == 2  # 早停未匹配


def test_parallel_chunked_streak_stop():
    """并行（workers=2）分块提交：块间检查早停，不再加码后续块。"""
    cands = _cands(8)

    def match(i):
        # idx0/1 matched；idx2 起（第二块起）全部 no_match → 第二块后早停
        return None if i >= 2 else {"url": "https://detail.1688.com/offer/1.html",
                                    "title": "货源", "price": "5.0", "images": [],
                                    "confidence": 0.9, "badge_eff": 0.9}

    result, calls, _ = _run(cands, workers=2, match=match, stop_on_no_match_streak=2)
    # 块1(idx0,1) matched；块2(idx2,3) no_match×2 → 触发早停；idx4+ 不再提交
    assert calls["n"] == 4
    assert sum(1 for c in result if c.status in ("matched", "profitable")) == 2
    assert sum(1 for c in result if c.status == "ok") == 4


def test_parallel_mid_chunk_sentinel():
    """并行：阈值落在 chunk 中间（j=0 触顶）→ 哨兵不被同块后续候选洗掉（评审 B）。"""
    cands = _cands(6)

    def match(i):
        return None if i == 0 else {"url": "https://detail.1688.com/offer/1.html",
                                    "title": "货源", "price": "5.0", "images": [],
                                    "confidence": 0.9, "badge_eff": 0.9}

    result, calls, _ = _run(cands, workers=2, match=match, stop_on_no_match_streak=1)
    # 块1(idx0,1)：idx0 no_match 触顶(-1)，idx1 matched 不得洗掉哨兵 → 早停
    assert calls["n"] == 2
    assert sum(1 for c in result if c.status in ("matched", "profitable")) == 1
    assert sum(1 for c in result if c.status == "ok") == 4


def test_streak_resets_on_match():
    """间隔 no_match 不触发早停（matched 重置计数）。"""
    cands = _cands(6)

    def match(i):
        return None if i % 2 else {"url": "https://detail.1688.com/offer/1.html",
                                   "title": "货源", "price": "5.0", "images": [],
                                   "confidence": 0.9, "badge_eff": 0.9}

    result, calls, _ = _run(cands, workers=1, match=match, stop_on_no_match_streak=2)
    assert calls["n"] == 6                      # 无连续 2 次 no_match → 全跑
    assert sum(1 for c in result if c.status in ("matched", "profitable")) == 3


def test_pace_seconds_used_in_serial():
    """串行模式节奏间隔透传（task 模式传 2.0 → _finalize sleep 2.0）。"""
    cands = _cands(2)
    _, _, sleeps = _run(cands, workers=1, pace_seconds=2.0)
    assert sleeps and all(abs(s - 2.0) < 0.5 for s in sleeps)  # 含抖动


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

#!/usr/bin/env python3
"""discover 货源低置信拒绝门槛（P-E，TDD）。

实锤背景（Wave D 联调）：aibuy 图搜错配（暖风机→黑丝袜）match_confidence=0.0
仍被当有效货源走完利润/信封全链。本文件锁定：

1. 阈值常量 _MIN_SOURCE_CONFIDENCE == 0.3 模块级存在
2. match_confidence < 阈值（0.0）→ 候选进无货源流（status=no_match），
   match_reject_reason='low_confidence_source'；匹配证据（url/title/价格）
   保留供 review 流查看，绝不进自动提交（auto-submit 只取 profitable）
3. conf >= 阈值（0.3 边界含）→ 既有行为逐字保持（aibuy 正常置信零变化）
4. match=None（真无货源）→ 既有 no_match 语义不变

全部 mock 外部依赖，禁止真实网络/浏览器。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_source_confidence_guard.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402

HEATER_RU = "Тепловая завеса воздушная 2000Вт"
STOCKING_CN = "黑丝袜女薄款防勾丝"


def _mk(pid: str) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0)
    c.status = "ok"
    c.ozon_images = [f"https://img.ozone.ru/{pid}.jpg"]
    return c


def _match(confidence: float) -> dict:
    return {
        "url": "https://detail.1688.com/offer/1001.html",
        "title": STOCKING_CN, "price": 5.0, "images": ["https://i/1.jpg"],
        "confidence": confidence, "badge_eff": 0.0, "score": 50.0,
        "reject_reason": "",
    }


def _run(cands: list[ProductCandidate], match) -> list[ProductCandidate]:
    """统一 mock 入口跑 match_selected（无外部依赖）。"""
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_search_1688_source", return_value=match), \
         mock.patch.object(od, "_query_logistics_from_worker", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record"), \
         mock.patch("time.sleep"):
        return od.match_selected(cands, "http://127.0.0.1:9222", min_margin_pct=1)


# ── ① 阈值常量 ────────────────────────────────────────────────────────────

def test_min_source_confidence_constant_exists():
    """模块级阈值常量存在且为 0.3（低置信货源拒绝门槛）。"""
    assert hasattr(od, "_MIN_SOURCE_CONFIDENCE")
    assert float(od._MIN_SOURCE_CONFIDENCE) == 0.3


# ── ② 低置信拒绝 ──────────────────────────────────────────────────────────

def test_zero_confidence_match_rejected_to_no_match():
    """conf=0.0（暖风机→黑丝袜型错配）→ no_match + low_confidence_source，
    匹配证据保留（review 流可见），绝不成为可提交货源。"""
    c = _mk("p1")
    result = _run([c], _match(0.0))
    c = result[0]
    assert c.status == "no_match"
    assert c.match_reject_reason == "low_confidence_source"
    # 证据保留：review 流弱匹配名单按 match_1688_url 过滤，清空即失明
    assert c.match_1688_url == "https://detail.1688.com/offer/1001.html"
    assert c.match_confidence == 0.0
    # 不进自动提交：auto-submit 只取 status==profitable
    assert c.status != "profitable"


def test_low_confidence_writes_review_log():
    """低置信拒绝写 review_log（decision=no_match, reason=low_confidence_source）。"""
    c = _mk("p1")
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_search_1688_source", return_value=_match(0.1)), \
         mock.patch.object(od, "_query_logistics_from_worker", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log, \
         mock.patch("time.sleep"):
        od.match_selected([c], "http://127.0.0.1:9222", min_margin_pct=1)
    assert m_log.called
    rec = m_log.call_args[0][0]
    assert rec["decision"] == "no_match"
    assert rec["reject_reason"] == "low_confidence_source"


# ── ③ 阈值上通行（零行为变化红线）─────────────────────────────────────────

def test_confidence_at_threshold_passes():
    """conf == 0.3（边界）→ 既有正常流（matched→profitable），不拒绝。"""
    c = _mk("p1")
    result = _run([c], _match(0.3))
    c = result[0]
    assert c.status == "profitable"
    assert c.match_reject_reason == ""


def test_confidence_above_threshold_passes():
    """conf=0.35（aibuy 正常置信）→ 既有正常流逐字保持。"""
    c = _mk("p1")
    result = _run([c], _match(0.35))
    c = result[0]
    assert c.status == "profitable"
    assert c.match_reject_reason == ""
    assert c.match_1688_title == STOCKING_CN


# ── ④ 真无货源语义不变 ────────────────────────────────────────────────────

def test_no_match_none_semantics_unchanged():
    """match=None（图搜全灭）→ 既有 no_match，reject_reason 保持空。"""
    c = _mk("p1")
    result = _run([c], None)
    c = result[0]
    assert c.status == "no_match"
    assert c.match_reject_reason == ""


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

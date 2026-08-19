#!/usr/bin/env python3
"""discover 发货模式子串过滤单测（任务 4.1）— sales_schema × sales_mode。

竞品（毛子/上品帮）用 .includes() 子串匹配：FBS 过滤隐式覆盖 RFBS/REAL_FBS。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_sales_schema_filter.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import (
    ProductCandidate,
    _apply_sales_mode_filter,
    _match_sales_schema,
)


def test_fbs_substring_matches_rfbs():
    """FBS 子串匹配 RFBS（跨境直发隐式覆盖）。"""
    assert _match_sales_schema("RFBS", "FBS") is True


def test_fbs_matches_real_fbs():
    """FBS 子串匹配 REAL_FBS。"""
    assert _match_sales_schema("REAL_FBS", "FBS") is True


def test_fbo_only_does_not_match_fbs():
    """FBO 不含 FBS 子串 → 不匹配。"""
    assert _match_sales_schema("FBO", "FBS") is False


def test_empty_mode_no_filter():
    """mode 为空 → 不过滤（恒 True）。"""
    assert _match_sales_schema("FBO", "") is True


def test_exact_fbo_match():
    """FBO 精确匹配 FBO。"""
    assert _match_sales_schema("FBO", "FBO") is True


def test_comma_joined_schema_matches():
    """逗号拼接多模式（FBO,FBS）含 FBS → 匹配。"""
    assert _match_sales_schema("FBO,FBS", "FBS") is True


def test_empty_schema_with_mode():
    """sales_schema 为空但 mode 非空 → 不匹配（无数据不误放行）。"""
    assert _match_sales_schema("", "FBS") is False


# ── 任务 4.1b：主流程后置过滤（_apply_sales_mode_filter 纯函数）──


def _mk(pid: str, schema: str = "", status: str = "ok") -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"title-{pid}",
                         ozon_price=0.0)
    c.sales_schema = schema
    c.status = status
    return c


def test_apply_sales_mode_filter_marks_non_matching_filtered():
    """mode="FBS"：sales_schema="FBO" 的 ok/uncertain 候选 → filtered；
    "RFBS"（FBS 子串命中）保留；error 候选不动。"""
    cands = [
        _mk("fbo-ok", "FBO"),        # → filtered
        _mk("rfbs-ok", "RFBS"),      # 保留（FBS in RFBS）
        _mk("fbo-unc", "FBO", "uncertain"),  # → filtered
        _mk("err", "FBO", "error"),  # 非 ok/uncertain 不动
    ]
    _apply_sales_mode_filter(cands, "FBS")
    assert cands[0].status == "filtered"
    assert cands[0].error == "发货模式不含 FBS"
    assert cands[1].status == "ok"
    assert cands[2].status == "filtered"
    assert cands[3].status == "error"


def test_apply_sales_mode_filter_noop_when_mode_empty():
    """sales_mode 为空 → 所有候选 status 不变（默认不过滤，只标注）。"""
    cands = [_mk("fbo", "FBO"), _mk("empty-schema", "")]
    _apply_sales_mode_filter(cands, "")
    assert [c.status for c in cands] == ["ok", "ok"]
    assert all(c.error == "" for c in cands)


def test_apply_sales_mode_filter_noop_when_mode_empty_schema():
    """mode="FBS" 但候选 sales_schema 为空（无运营数据）→ 过滤（不误放行）。"""
    cands = [_mk("no-schema", "")]
    _apply_sales_mode_filter(cands, "FBS")
    assert cands[0].status == "filtered"
    assert cands[0].error == "发货模式不含 FBS"


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
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
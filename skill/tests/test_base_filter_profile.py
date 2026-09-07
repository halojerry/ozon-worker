#!/usr/bin/env python3
"""BASE 粗筛实装 + filter-profile 档位回归（discover 漏斗 v2 · Task 7）。

背景：`_BASE_FILTER_RULES` 18 项全部 (None,None) 空架（骨架仿上品帮但从未
通电）。本批：①ai 档复用 AI_PRESET（上品帮 aiFilterData 同款 4 硬规则+销量
阶梯），②`--base-filter` 自定义区间注入，③语义约定——
- profile off（缺省）+ 无自定义区间 → 行为与现状逐字一致（交互流程零变化）；
- ai 档 has_analytics 才跑完整预设；无 analytics 只判 seller_count，其余
  字段 0/未知不编造语义（月销阶梯降级放行）；
- --auto-submit 未显式指定档位时默认 ai（保护 aibuy 配额），显式指定优先。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_base_filter_profile.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import (  # noqa: E402
    ProductCandidate,
    _apply_profile_filter,
    _parse_filter_expr,
    _passes_base_filter,
    resolve_filter_profile,
)


def _cand(**kw) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id="1", ozon_title="t", ozon_price=kw.pop("price", 1000.0))
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ── off 档：现状逐字一致 ──

def test_profile_off_passes_everything():
    """off 档（缺省）无自定义区间 → 任何候选都过（18 项全 None 不限）。"""
    assert _passes_base_filter(_cand(monthly_sales=0, drr=999.0))
    assert _passes_base_filter(_cand(), profile="off")


def test_old_call_signature_still_works():
    """旧调用 _passes_base_filter(c) 单参形态保持可用（向后兼容）。"""
    assert _passes_base_filter(_cand(seller_count=999))


# ── ai 档 ──

def test_ai_full_preset_ladder_violation():
    """有 analytics：价格 300₽ 需月销 >500，月销 100 → 梯阶不过。"""
    c = _cand(price=300.0, monthly_sales=100, create_days=100,
              sales_growth=5.0, drr=5.0, seller_count=3, has_analytics=True)
    assert not _passes_base_filter(c, profile="ai")


def test_ai_full_preset_ladder_pass():
    """有 analytics：价格 3000₽ 需月销 >30，月销 100 → 过。"""
    c = _cand(price=3000.0, monthly_sales=100, create_days=100,
              sales_growth=5.0, drr=5.0, seller_count=3, has_analytics=True)
    assert _passes_base_filter(c, profile="ai")


def test_ai_no_analytics_degrades_to_seller_count_only():
    """无 analytics：只判 seller_count；sales_growth=0（未知）不得当真实值过滤。"""
    c = _cand(price=300.0, monthly_sales=0, sales_growth=0.0, seller_count=3,
              has_analytics=False)
    assert _passes_base_filter(c, profile="ai")  # 阶梯/动态降级放行


def test_ai_no_analytics_seller_count_cut():
    """无 analytics：跟卖 >30 → 行级安全子集仍砍（省 widget/匹配成本）。"""
    c = _cand(competing_sellers=45, has_analytics=False)
    assert not _passes_base_filter(c, profile="ai")


# ── 自定义区间 --base-filter ──

def test_parse_filter_expr():
    rules = _parse_filter_expr("monthly_sales>=50, drr<=15")
    assert ("monthly_sales", ">=", 50.0) in rules
    assert ("drr", "<=", 15.0) in rules


def test_parse_filter_expr_rejects_unknown_field():
    try:
        _parse_filter_expr("nonsense_field>=1")
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass


def test_parse_filter_expr_rejects_match_only_field():
    """margin 属匹配期字段（此前恒 0.0）→ --base-filter 显式拒绝（评审 E）。"""
    try:
        _parse_filter_expr("margin>=15")
        raise AssertionError("应抛 ValueError")
    except ValueError as exc:
        assert "--rules" in str(exc)


def test_extra_rules_interval_enforced():
    """自定义区间在所有档位生效：monthly_sales>=50 砍掉月销 10。"""
    c = _cand(monthly_sales=10, has_analytics=True)
    extra = _parse_filter_expr("monthly_sales>=50")
    assert not _passes_base_filter(c, extra_rules=extra)
    c2 = _cand(monthly_sales=80, has_analytics=True)
    assert _passes_base_filter(c2, extra_rules=extra)


# ── 后置批量判定 ──

def test_apply_profile_filter_noop_when_off():
    """off 且无自定义区间 → no-op（状态一个都不改）。"""
    cands = [_cand(status="ok", drr=999.0), _cand(status="uncertain")]
    assert _apply_profile_filter(cands, profile="off") == 0
    assert [c.status for c in cands] == ["ok", "uncertain"]


def test_apply_profile_filter_marks_filtered():
    """ai 档后置判定：不过 → filtered + reason；error 状态不动。"""
    bad = _cand(status="ok", price=300.0, monthly_sales=10, create_days=50,
                sales_growth=2.0, drr=3.0, seller_count=1, has_analytics=True)
    err = _cand(status="error", seller_count=999, has_analytics=False)
    n = _apply_profile_filter([bad, err], profile="ai")
    assert n == 1
    assert bad.status == "filtered" and "粗筛" in bad.error
    assert err.status == "error"


# ── CLI 档位解析 ──

def test_resolve_filter_profile():
    """显式指定优先；未指定 auto-submit→ai；交互→off。"""
    assert resolve_filter_profile("off", auto_submit=True) == "off"
    assert resolve_filter_profile("ai", auto_submit=False) == "ai"
    assert resolve_filter_profile(None, auto_submit=True) == "ai"
    assert resolve_filter_profile(None, auto_submit=False) == "off"


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

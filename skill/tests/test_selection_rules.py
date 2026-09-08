"""S5/B3: 列表内联解析 + 13 字段入粗筛 + 18 项 BASE 粗筛。

U1: _check_rule(None, ">=", 10) is True（None=不限，上品帮 checkRange 语义）；
    硬比较仍生效。
U2: _SELECTION_FIELDS 新增 13 字段被接受（apply_selection_rules 不抛）；
    非法字段仍抛 ValueError。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import (
    ProductCandidate,
    _SELECTION_FIELDS,
    _check_rule,
    apply_selection_rules,
)

NEW_FIELDS = [
    "sales_dynamics", "days_in_promo", "discount", "promo_revenue_share",
    "days_with_trafarets", "session_count", "conv_to_cart_pdp",
    "conv_to_cart_search", "nullable_redemption_rate", "weight_g",
    "dimensions", "return_cancel_rate", "review_count",
]

OLD_FIELDS = [
    "monthly_sales", "gmv", "drr", "seller_count", "margin",
    "price", "create_days", "sales_growth", "rating",
]


def _mk_candidate(pid: str = "1") -> ProductCandidate:
    c = ProductCandidate(
        ozon_product_id=pid, ozon_title="Test", ozon_price=100.0)
    c.status = "ok"
    return c


# ── U1: _check_rule None=不限 ──

def test_check_rule_none_means_unlimited():
    """None 值 → 该规则视为不限（上品帮 checkRange 0/None=不限）。"""
    assert _check_rule(None, ">=", 10) is True
    assert _check_rule(None, "<=", 10) is True
    assert _check_rule(None, ">", 0) is True
    assert _check_rule(None, "<", 0) is True


def test_check_rule_hard_compare_still_works():
    """硬比较仍生效：非 None 值与预期严格比较。"""
    assert _check_rule(15, ">=", 10) is True
    assert _check_rule(5, ">=", 10) is False
    assert _check_rule(5, "<=", 10) is True
    assert _check_rule(15, "<=", 10) is False
    assert _check_rule(5, ">", 5) is False
    assert _check_rule(5, "<", 5) is False
    assert _check_rule(5, "=", 5) is True
    assert _check_rule(0, ">=", 10) is False  # 0 是有效数值，仍硬比较


# ── U2: _SELECTION_FIELDS 13 新字段 ──

def test_selection_fields_accept_13_new_fields():
    """13 个新粗筛字段都在 _SELECTION_FIELDS 中（9+13=22）。"""
    for f in NEW_FIELDS:
        assert f in _SELECTION_FIELDS, f"{f} 应已接入 _SELECTION_FIELDS"
    assert len(_SELECTION_FIELDS) == 22, \
        f"应 22 字段, got {len(_SELECTION_FIELDS)}"


def test_selection_fields_keep_9_old_fields():
    """原 9 个字段名不变（向后兼容）。"""
    for f in OLD_FIELDS:
        assert f in _SELECTION_FIELDS


def test_apply_selection_rules_new_field_works():
    """新字段（缺省 None）规则应用不抛且放行（None=不限）。"""
    c = _mk_candidate()
    kept = apply_selection_rules([c], "days_in_promo>=10")
    assert len(kept) == 1  # days_in_promo 缺省 None → 不限 → 放行


def test_apply_selection_rules_unknown_field_raises():
    """非法字段仍抛 ValueError。"""
    c = _mk_candidate()
    try:
        apply_selection_rules([c], "bogus_field>=10")
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass


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


# ── v0.69: 两段式规则（匹配期字段 margin 挑选期恒 0.0 → 全灭，实证 0/30）──

def test_split_selection_rules_two_phase():
    from scripts.lib.ozon_discovery import split_selection_rules
    assert split_selection_rules("ai,margin>=20") == ("ai", "margin>=20")
    assert split_selection_rules("margin>=20") == ("", "margin>=20")
    assert split_selection_rules("ai,monthly_sales>=100") == ("ai,monthly_sales>=100", "")
    assert split_selection_rules("ai") == ("ai", "")
    assert split_selection_rules("") == ("", "")
    assert split_selection_rules("drr<=15, margin>=20, ai") == ("drr<=15,ai", "margin>=20")


def test_two_phase_flow_keeps_high_margin_after_match():
    """挑选期 ai 通过 → 匹配期 margin>=20 只留达标者（模拟 match 后 profit_margin 真值）。"""
    from scripts.lib.ozon_discovery import apply_selection_rules, split_selection_rules
    from tests.test_ai_preset import _mk
    cands = [_mk(pid="keep", price=1000.0, monthly_sales=500),
             _mk(pid="low", price=1000.0, monthly_sales=500)]
    pre, match = split_selection_rules("ai,margin>=20")
    selected = apply_selection_rules(cands, pre)
    assert {c.ozon_product_id for c in selected} == {"keep", "low"}, "挑选期 margin 不参与"
    # 模拟 match_selected 写入真值
    for c in selected:
        c.profit_margin = 30.0 if c.ozon_product_id == "keep" else 5.0
    final = apply_selection_rules(selected, match)
    assert {c.ozon_product_id for c in final} == {"keep"}

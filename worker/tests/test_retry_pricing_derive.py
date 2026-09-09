#!/usr/bin/env python3
"""F-F02 retry 手写定价公式回归唯一入口单测（P0 波修 3，2026-09-09 审计发现）。

审计发现（findings.md，高/资损类）：validation_retry_loop 修复路径手写
`int(suggested_price*1.2)`（int 截断，与 compute_price 的 ceil 漂移——99→118
vs 主链 119）与 `int(suggested_price*0.9)`（min_price 语义分叉——
update_min_price_floor 的 Ozon 底线是 50%（min_auto_price_too_small），
0.9 会把促销底线顶到逼近售价，修复改价可能触发拒单）。

修复契约（本文件锁定）：
  1. `pricing_estimate.derive_list_prices(price) -> (old_price, min_price)`：
     划线价与主链 compute_price 同款 ceil+≤25 加 5 规则；min_price 同款
     update_min_price_floor 的 50% 底线；
  2. validation_retry_loop 全文无 `int(suggested_price * 1.2)` / `* 0.9`
     手写残余（源码绊线）。

运行（纯 mock，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_retry_pricing_derive.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.pricing_estimate import compute_price, derive_list_prices


def test_derive_old_price_uses_ceil_not_truncate():
    """99×1.2=118.8：主链 ceil→119，旧 retry 手写 int→118（漂移实锤）。"""
    assert derive_list_prices(99)[0] == 119


def test_derive_old_price_low_price_rule():
    """price≤25：Ozon 要求折扣差额至少 5（max(price+5, ceil×1.2)）。"""
    assert derive_list_prices(20)[0] == 25   # 旧手写 int(24)=24 违反 ≥20% 差额规则
    assert derive_list_prices(1)[0] == 6


def test_derive_min_price_is_50pct_floor_not_90pct():
    """min_price 底线 = 50%（Ozon min_auto_price_too_small 线）：
    99 → 50（旧手写 int(89.1)=89 逼近售价，等于把促销底线焊死）。"""
    assert derive_list_prices(99)[1] == 50
    assert derive_list_prices(20)[1] == 10
    assert derive_list_prices(1)[1] == 1


def test_derive_consistent_with_compute_price_old_price():
    """与主链 compute_price 的 old_price 逐字一致（单档路径 ceil×1.2）。"""
    ref = compute_price(
        total_cost_cny=10.0, margin_rate=1.5, commission_rate=0.10,
        fx_buffer=0.0, currency_code="CNY",
    )
    assert derive_list_prices(ref["price"])[0] == ref["old_price"]


def test_retry_loop_no_inline_formula_remnant():
    """源码绊线：retry 修复路径必须走 derive_list_prices，手写公式清零。"""
    from graphs import validation_retry_loop as vrl

    src = inspect.getsource(vrl)
    assert "derive_list_prices" in src, "修复路径应调用共享派生入口"
    assert "int(suggested_price * 1.2)" not in src
    assert "int(suggested_price * 0.9)" not in src
    assert "int(float(price) * 1.2)" not in src
    assert "int(float(price) * 0.9)" not in src

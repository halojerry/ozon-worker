# -*- coding: utf-8 -*-
"""双价格体系（T2）：compute_price 三档定价 + 变动成本率 + 销售净利率口径测试。

用户拍板参数（2026-08-21）：
- margin_anchor(划线原价) = 2.0  / margin_rate(日常价) = 1.5 / margin_floor(促销底线) = 0.6
- variable_cost_rate(日常变动成本) = 0.155（推广6+退货5+提现1.5+汇损2+附加1）
- promo_variable_cost_rate(促销变动成本) = 0.245（推广12+退货8+提现1.5+汇损2+附加1）
- 利润口径 = 销售净利率（净利/售价），不再用成本利润率

演算基准（total_cost=100, commission=0.12）：
- daily_divisor = 1-0.12-0.155 = 0.725
- promo_divisor  = 1-0.12-0.245 = 0.635
- price  = ceil(100×2.5/0.725)   = 345   （日常价）
- old    = ceil(100×3.0/0.725)   = 414   （划线原价）
- promo  = ceil(100×1.6/0.635)   = 252   （促销底线）
- 打折空间 = 252/345 = 7.3 折 ✓
- 日常净利 = 345×0.725-100 = 150.125 → 销售净利率 0.4351
- 促销净利 = 252×0.635-100 = 60.02   → 销售净利率 0.2382

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_dual_margin.py -q
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.pricing_estimate import compute_price  # noqa: E402

COST = 100.0
COMM = 0.12
VCR = 0.155
PVCR = 0.245
FX = 0.05
RATE = 12.0


# ── 1. CNY 三档 + 销售净利率 ──
def test_cny_dual_margin_three_tiers():
    r = compute_price(
        COST, 1.5, COMM, FX, "CNY", None,
        margin_anchor=2.0, margin_floor=0.6,
        variable_cost_rate=VCR, promo_variable_cost_rate=PVCR,
    )
    assert r["price"] == 345, f"日常价应=345，实际 {r['price']}"
    assert r["old_price"] == 414, f"划线原价应=414，实际 {r['old_price']}"
    assert r["promo_price"] == 252, f"促销底线应=252，实际 {r['promo_price']}"
    # 打折空间（促销价/日常价 ≤ 0.8，真实 8 折内）
    assert r["promo_price"] / r["price"] <= 0.8, "促销价应 ≤ 日常价 8 折"
    # 销售净利率口径（净利/售价）
    assert abs(r["profit_cny"] - 150.125) < 0.01, f"净利应=150.125，实际 {r['profit_cny']}"
    assert abs(r["profit_rate"] - 0.4351) < 0.0005, f"销售净利率应=0.4351，实际 {r['profit_rate']}"
    # 划线价 ≥ 日常价×1.2（Ozon 折扣规则）
    assert r["old_price"] >= math.ceil(r["price"] * 1.2)


# ── 2. RUB 三档（fx + 汇率，净利换算回 CNY）──
def test_rub_dual_margin_three_tiers():
    r = compute_price(
        COST, 1.5, COMM, FX, "RUB", RATE,
        margin_anchor=2.0, margin_floor=0.6,
        variable_cost_rate=VCR, promo_variable_cost_rate=PVCR,
    )
    # price = ceil(100×2.5×1.05/0.725×12) = ceil(4344.83) = 4345
    assert r["price"] == 4345, f"RUB 日常价应=4345，实际 {r['price']}"
    assert r["old_price"] == 5214, f"RUB 划线应=5214，实际 {r['old_price']}"
    assert r["promo_price"] == 3175, f"RUB 促销应=3175，实际 {r['promo_price']}"
    # 净利（RUB 换算回 CNY，扣佣金+变动成本）：4345×0.725/12-100 = 162.51
    assert abs(r["profit_cny"] - 162.51) < 0.01, f"RUB 净利应=162.51，实际 {r['profit_cny']}"


# ── 3. 向后兼容：不传新参 → 旧行为不变（单档 margin + 无变动成本）──
def test_legacy_behavior_when_no_new_params():
    r_old = compute_price(17.5, 0.25, 0.10, 0.05, "RUB", None)
    r_new = compute_price(17.5, 0.25, 0.10, 0.05, "RUB", None,
                          margin_anchor=None, margin_floor=None,
                          variable_cost_rate=0.0, promo_variable_cost_rate=0.0)
    assert r_old == r_new, "缺省新参必须与旧行为完全一致"
    assert r_old["price"] == 25 and r_old["old_price"] == 30
    assert "promo_price" not in r_old, "旧路径不产生 promo_price"


# ── 4. 划线价 ≥ 日常价×1.2 强制（anchor 偏低时仍满足 Ozon 规则）──
def test_old_price_min_1_2x_enforced():
    r = compute_price(
        COST, 1.5, COMM, FX, "CNY", None,
        margin_anchor=1.3, margin_floor=0.6,
        variable_cost_rate=VCR, promo_variable_cost_rate=PVCR,
    )
    assert r["old_price"] >= math.ceil(r["price"] * 1.2), \
        f"anchor=1.3 时 old_price 仍须 ≥ price×1.2（实际 {r['old_price']} vs {r['price']}）"


# ── 5. 促销底线利润为正（margin_floor=0.6 → 销售净利率 ~23.8%）──
def test_promo_floor_still_profitable():
    r = compute_price(
        COST, 1.5, COMM, FX, "CNY", None,
        margin_anchor=2.0, margin_floor=0.6,
        variable_cost_rate=VCR, promo_variable_cost_rate=PVCR,
    )
    # 促销净利 = 252×0.635-100 = 60.02
    promo_profit = r["promo_price"] * (1 - COMM - PVCR) - COST
    assert promo_profit > 0, "促销底线必须仍有正利润"
    assert abs(promo_profit - 60.02) < 0.01


# ── 6. 除零守卫：commission+变动成本 ≥ 1 时兜底 ──
def test_divisor_floor_guard():
    r = compute_price(
        COST, 1.5, 0.9, FX, "CNY", None,
        margin_anchor=2.0, margin_floor=0.6,
        variable_cost_rate=0.2, promo_variable_cost_rate=0.2,
    )
    assert r["price"] > 0, "佣金+变动成本≥1 时须兜底除零（当前 price=0 会崩）"

"""S6/T9: discover --rules ai 销量阶梯门槛预设（上品帮 aiFilterData 同款）。

U3: apply_selection_rules(candidates, "ai") 走 AI_PRESET 四条硬淘汰
    （create_days<=365 / seller_count<=30 / sales_growth>0 / drr<=15）
    + AI_SALES_LADDER 销量阶梯（价≤500₽月销>500、≤1000₽>150、
    ≤5000₽>30、≤10000₽>15、其他>5），不抛异常。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import (
    AI_PRESET,
    AI_SALES_LADDER,
    ProductCandidate,
    apply_selection_rules,
)


def _mk(
    price: float = 1000.0,
    monthly_sales: int = 500,
    create_days: int = 100,
    seller_count: int = 10,
    sales_growth: float = 20.0,
    drr: float = 5.0,
    pid: str = "1",
) -> ProductCandidate:
    """基础合格候选；默认全部过 ai 预设门槛。"""
    c = ProductCandidate(
        ozon_product_id=pid, ozon_title="Test", ozon_price=price)
    c.status = "ok"
    c.monthly_sales = monthly_sales
    c.create_days = create_days
    c.competing_sellers = seller_count
    c.sales_growth = sales_growth
    c.drr = drr
    return c


def _kept(candidates, preset="ai"):
    return {c.ozon_product_id for c in apply_selection_rules(candidates, preset)}


# ── U3: 销量阶梯 ──

def test_price_le500_needs_monthly_sales_over_500():
    """价 ≤500₽：月销 ≤500 被砍、>500 保留。"""
    low = _mk(price=300, monthly_sales=500, pid="low500")
    high = _mk(price=300, monthly_sales=501, pid="low501")
    kept = _kept([low, high])
    assert "low500" not in kept
    assert "low501" in kept


def test_price_gt10000_needs_monthly_sales_over_5():
    """价 >10000₽：月销 >5 保留、≤5 被砍。"""
    ok = _mk(price=20000, monthly_sales=6, pid="hi6")
    cut = _mk(price=20000, monthly_sales=5, pid="hi5")
    kept = _kept([ok, cut])
    assert "hi6" in kept
    assert "hi5" not in kept


def test_ladder_middle_bands():
    """中间档：价≤1000₽>150、≤5000₽>30、≤10000₽>15。"""
    a = _mk(price=600, monthly_sales=150, pid="b150")   # ≤1000 档下限砍
    b = _mk(price=600, monthly_sales=151, pid="b151")   # 保留
    c = _mk(price=2000, monthly_sales=30, pid="c30")    # ≤5000 档下限砍
    d = _mk(price=2000, monthly_sales=31, pid="c31")    # 保留
    e = _mk(price=8000, monthly_sales=15, pid="d15")    # ≤10000 档下限砍
    f = _mk(price=8000, monthly_sales=16, pid="d16")    # 保留
    kept = _kept([a, b, c, d, e, f])
    assert kept == {"b151", "c31", "d16"}


# ── U3: 四条硬淘汰 ──

def test_hard_eliminations():
    """create_days>365 或 seller_count>30 或 sales_growth<=0 或 drr>15 被砍。"""
    bad_days = _mk(price=300, monthly_sales=1000, create_days=366, pid="days")
    bad_sellers = _mk(price=300, monthly_sales=1000, seller_count=31, pid="sellers")
    bad_growth = _mk(price=300, monthly_sales=1000, sales_growth=0, pid="growth")
    bad_drr = _mk(price=300, monthly_sales=1000, drr=16, pid="drr")
    ok = _mk(price=300, monthly_sales=1000, pid="ok")
    kept = _kept([bad_days, bad_sellers, bad_growth, bad_drr, ok])
    assert kept == {"ok"}


# ── U3: 边界不抛异常 ──

def test_ai_preset_does_not_raise_and_keeps_all_qualified():
    """合格候选整体保留，无异常。"""
    ok = _mk(price=400, monthly_sales=700, pid="all")
    kept = _kept([ok])
    assert kept == {"all"}


def test_ai_preset_constants_shape():
    """AI_PRESET 四条硬淘汰 + 阶梯末档 inf。"""
    assert set(AI_PRESET) == {"create_days", "seller_count", "sales_growth", "drr"}
    assert AI_PRESET["create_days"] == ("<=", 365)
    assert AI_PRESET["seller_count"] == ("<=", 30)
    assert AI_PRESET["sales_growth"] == (">", 0)
    assert AI_PRESET["drr"] == ("<=", 15)
    assert AI_SALES_LADDER[-1][0] == float("inf")


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

"""蓝海评分扩展单测（v0.31 P4）— chain_depth + 类目一致性因子，权重再平衡。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ozon_discovery import ProductCandidate, calculate_blue_ocean_score


def _mk(score=60, margin=35, sales=100, growth=20, drr=15, comm=8, price=1500,
        sellers=7, has_analytics=True, chain_depth=0, seed_cat="", category=""):
    c = ProductCandidate(
        ozon_product_id="p1", ozon_title="Test", ozon_price=price)
    c.competing_sellers = sellers
    c.profit_margin = margin
    c.monthly_sales = sales
    c.sales_growth = growth
    c.drr = drr
    c.commission_fbp = comm
    c.has_analytics = has_analytics
    c.chain_depth = chain_depth
    c._seed_category_id = seed_cat
    c.category = category
    return c


def test_chain_depth_factor_four_tiers():
    """chain_depth 四档: 0→+10, 1→+7, 2→+4, ≥3→+0。"""
    base = _mk()
    s0 = calculate_blue_ocean_score(base)
    base.chain_depth = 1
    s1 = calculate_blue_ocean_score(base)
    base.chain_depth = 2
    s2 = calculate_blue_ocean_score(base)
    base.chain_depth = 3
    s3 = calculate_blue_ocean_score(base)
    assert s0 > s1 > s2 > s3, f"深度越浅分越高: {s0} > {s1} > {s2} > {s3}"
    assert s0 - s1 == 3, f"0→1 差 3 分, got {s0 - s1}"
    assert s1 - s2 == 3, f"1→2 差 3 分, got {s1 - s2}"
    assert s2 - s3 == 4, f"2→3 差 4 分, got {s2 - s3}"


def test_category_consistency_three_states():
    """类目一致性三态: 同类目→+10, 跨类目→+3, 无数据→+0。"""
    c = _mk(seed_cat="宠物用品", category="宠物用品")
    same = calculate_blue_ocean_score(c)
    c.category = "家居用品"
    cross = calculate_blue_ocean_score(c)
    c._seed_category_id = 0
    c.category = ""
    nodata = calculate_blue_ocean_score(c)
    assert same > cross > nodata, f"同类目 > 跨类目 > 无数据: {same} > {cross} > {nodata}"
    assert same - cross == 7, f"同类目比跨类目高 7 分, got {same - cross}"
    assert cross - nodata == 3, f"跨类目比无数据高 3 分, got {cross - nodata}"


def test_score_total_ceiling_100_with_analytics():
    """有 analytics 时总分仍 ≤100（新增因子后不超上限）。"""
    best = _mk(margin=50, sales=30, growth=50, drr=5, comm=5, price=2500,
               sellers=3, chain_depth=0, seed_cat="x", category="x")
    assert calculate_blue_ocean_score(best) <= 100


def test_score_total_ceiling_100_no_analytics():
    """无 analytics 时总分仍 ≤100（降级模式 + 新因子）。"""
    best = _mk(margin=50, sales=30, growth=0, drr=0, comm=5, price=2500,
               sellers=3, has_analytics=False, chain_depth=0, seed_cat="x", category="x")
    assert calculate_blue_ocean_score(best) <= 100


def test_has_analytics_degrade_unchanged():
    """回归: 无 analytics 时增长/广告因子仍为 0（降级行为不变）。"""
    a = _mk(margin=35, sales=100, growth=50, drr=5, comm=10, price=1500,
            sellers=7, has_analytics=True, chain_depth=0)
    b = _mk(margin=35, sales=100, growth=50, drr=5, comm=10, price=1500,
            sellers=7, has_analytics=False, chain_depth=0)
    # 除 analytics 外相同 → 有 analytics 的分更高（多 growth+drr 因子）
    assert calculate_blue_ocean_score(a) > calculate_blue_ocean_score(b)


def test_no_fission_fields_unchanged():
    """回归: 无裂变字段（chain_depth=0/_seed_category_id=0）时，基础因子行为一致。"""
    c = _mk(chain_depth=0, seed_cat="", category="")
    s = calculate_blue_ocean_score(c)
    # 基础分(v3 权重): 17(7 sellers) + 17(35% margin) + 8(100 sales) + 4(growth 20)
    #   + 3(drr 15) + 10(1500 price) + 10(comm 8) = 69
    # chain_depth 0 种子 +10、category 无数据 +0 → 79
    assert s == 79, f"期望 79（v3 权重 + 种子加成）, got {s}"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

"""v0.81.1 old_price 双规则收敛回归（fix/import-exit-guards）。

背景（Fix 2）：两套冲突规则并存——content_enrich.clamp_old_price 用实机契约
「差价 <400 必须 ≥20」，pricing_estimate（compute_price/derive_list_prices）
用旧「price≤25 → +5 否则 ×1.2」——price∈(25,100) 时产出差价 5.2~19.8 违反
自家契约，低价卡被 Ozon 拒且 retry 用同一弱规则重 derive 自旋。

修复口径：唯一规则出口 utils/pricing_estimate.enforce_old_price_rule
（old = max(ceil(price×1.2), price+20 if price<400 else 0)，返回字符串），
compute_price / derive_list_prices / content_enrich.clamp_old_price 三处委托。

MCP 契约留证（ProductAPI_ImportProductsV3）：swagger 只说 «Если вы раньше
передавали old_price, то при обновлении price также обновите old_price»
（未量化差价）；「<400 → ≥20」量化规则来自实机拒单（content_enrich 2026-09-26
实机踩坑留证），与 swagger 无冲突（swagger 沉默），故维持实机口径。

运行（纯 mock，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_old_price_rule_v081.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.content_enrich import clamp_old_price
from utils.pricing_estimate import (
    LARGE_OLD_PRICE_GAP,
    MIN_OLD_PRICE_GAP,
    compute_price,
    derive_list_prices,
    enforce_old_price_rule,
)


# ── enforce_old_price_rule（唯一规则出口）─────────────────────


def test_enforce_price_90_old_rule_violation_fixed():
    """price=90：旧规则 ceil(108) 差价 18 违规 → 新规则抬到 110（差价 20）。"""
    assert enforce_old_price_rule(90) == "110"
    assert enforce_old_price_rule(90, 100) == "110"   # 候选差价 10 违规 → 抬
    assert enforce_old_price_rule(90, 108) == "110"   # 候选 = 旧规则产物 → 抬


def test_enforce_price_30_and_25_boundaries():
    """低价档差价下限 20 主导（20% 线 36/30 低于 price+20）。"""
    assert enforce_old_price_rule(30) == "50"
    assert enforce_old_price_rule(25) == "45"   # 旧规则 30（差 5）→ 45
    assert enforce_old_price_rule(1) == "21"
    assert enforce_old_price_rule(399) == "479"  # 399<400：max(ceil(478.8)=479, 419)


def test_enforce_price_above_400_only_20pct_floor():
    """price≥400：20% 线 ≥80 已满足差价 ≥20，无额外加成。"""
    assert enforce_old_price_rule(500) == "600"
    assert enforce_old_price_rule(500, 650) == "650"   # 合规候选原样保留
    assert enforce_old_price_rule(400) == "480"
    assert enforce_old_price_rule(740, 888) == "888"   # 现行三档产物不受影响


def test_enforce_candidate_respected_when_compliant():
    """候选划线价已合规 → 原样返回（不无关抬价）。"""
    assert enforce_old_price_rule(100, 350) == "350"   # 差 250 ∈ [20,400)
    assert enforce_old_price_rule(100, 600) == "600"   # 差 500 ≥400
    assert enforce_old_price_rule(100, 120) == "120"
    assert enforce_old_price_rule("254.0000", "305.0000") == "305"  # /v5 价格串


def test_enforce_invalid_price_returns_none():
    """price 畸形/≤0 → None（调用方决定省略或拒绝；绝不返回伪价格）。"""
    assert enforce_old_price_rule(0) is None
    assert enforce_old_price_rule(-5) is None
    assert enforce_old_price_rule(None) is None
    assert enforce_old_price_rule("abc") is None
    assert enforce_old_price_rule("") is None


def test_enforce_returns_string():
    """Ozon 价格是字符串（MCP 契约 price/old_price type=string）。"""
    out = enforce_old_price_rule(90)
    assert isinstance(out, str) and out.isdigit()


# ── 三消费方同源 ─────────────────────────────────────────────


def test_derive_list_prices_delegates_to_rule():
    """retry 链唯一派生入口走同一规则（99 → 119：99+20 与 ceil(118.8) 同值）。"""
    assert derive_list_prices(90) == (110, 45)
    assert derive_list_prices(99)[0] == 119
    assert derive_list_prices(20)[0] == 40
    assert derive_list_prices(1)[0] == 21
    # min_price 50% 底线不变（Ozon min_auto_price_too_small）
    assert derive_list_prices(99)[1] == 50
    assert derive_list_prices(1)[1] == 1


def test_compute_price_legacy_delegates_to_rule():
    """单档路径低价卡不再产出违规差价（25 → 45，旧 30 差 5 违规）。"""
    r = compute_price(17.5, 0.25, 0.10, 0.05, "CNY", None)
    assert r["price"] == 25 and r["old_price"] == 45


def test_compute_price_three_tier_low_anchor_bumped():
    """三档 anchor 偏低（old=71 < 59+20=79）→ 抬到合规线 79。"""
    r = compute_price(
        17.5, 1.5, 0.10, 0.0, "CNY", None,
        margin_anchor=2.0, margin_floor=0.6,
        variable_cost_rate=0.155, promo_variable_cost_rate=0.245,
    )
    assert r["price"] == 59 and r["old_price"] == 79 and r["promo_price"] == 43


def test_content_enrich_clamp_old_price_thin_forward():
    """clamp_old_price 薄转发唯一规则（保留名字兼容，消费方零改动）。"""
    assert clamp_old_price(90, 95) == 110        # 旧规则 95 差 5 违规 → 110
    assert clamp_old_price(100, 110) == 120
    assert clamp_old_price(100, 350) == 350      # 合规候选保留
    assert clamp_old_price(100, 600) == 600
    assert clamp_old_price(0, 0) == 20           # price 无效防御分支


def test_constants_single_source():
    """常量唯一事实源在 pricing_estimate，content_enrich 转发引用（不再各写一份）。"""
    import utils.content_enrich as ce

    assert ce.MIN_OLD_PRICE_GAP is MIN_OLD_PRICE_GAP == 20
    assert ce.LARGE_OLD_PRICE_GAP is LARGE_OLD_PRICE_GAP == 400


def test_all_derived_prices_satisfy_contract():
    """全价段扫查：任何产出的 (price, old_price) 都满足唯一规则（防回归）。"""
    for price in list(range(1, 120)) + [150, 199, 254, 305, 399, 400, 401, 500, 740, 1000, 4345]:
        minimum = max(
            -(-price * 6 // 5),  # ceil(price*1.2) 整数算法
            price + 20 if price < 400 else 0,
        )
        for out in (
            int(enforce_old_price_rule(price)),
            derive_list_prices(price)[0],
        ):
            assert out >= minimum, f"price={price}: old={out} < 下限 {minimum}"
            assert out > price

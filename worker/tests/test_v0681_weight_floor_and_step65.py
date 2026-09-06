"""v0.68.1 双修复回归：Ozon 重量硬下限 + Step 6.5 R2b 豁免。

实证（v0.68.0 回归）：
- A3 declined @INCORRECT_DIMENSION weight is out of range (min: 10, max: 5000)
  ——skill 信封 1g 原样上传；(0,10) 区间按 Ozon 契约必拒，应视同缺失走兜底。
- A4 被 Step 6.5 一致性重配从 R2b 确认的园艺地垫改配除草剂——R2b 确认
  （LLM + 非泛词源词 overlap）是比 RU 标题词面更强的信号，应豁免重配。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ═══ 重量硬下限（worker normalizer）═══

def test_weight_below_ozon_min_treated_as_missing():
    """1g（A3 实证）→ 视同缺失 → 默认 100g 兜底，带归因 marks。"""
    from utils.weight_dimension_normalizer import normalize_weight_dimensions
    weight_g, dims, marks = normalize_weight_dimensions(
        1, {"length": 360, "width": 250, "height": 100}, {})
    assert weight_g >= 10, "低于 Ozon 下限的重量必须被兜底替换"
    assert weight_g == 100
    assert any("weight_below_ozon_min" in r for r in marks["reasons"])
    assert marks["weight_source"] == "default"


def test_weight_below_ozon_min_uses_competitor():
    """1g + 竞品重量 450g → 用竞品值（非默认）。"""
    from utils.weight_dimension_normalizer import normalize_weight_dimensions
    weight_g, _, marks = normalize_weight_dimensions(
        1, {"length": 360, "width": 250, "height": 100},
        {"competitor_weight_g": 450})
    assert weight_g == 450
    assert marks["weight_source"] == "competitor"


def test_competitor_below_min_also_rejected():
    """1g + 竞品 5g（同样低于下限）→ 不能用 5g，落默认 100g。"""
    from utils.weight_dimension_normalizer import normalize_weight_dimensions
    weight_g, _, _ = normalize_weight_dimensions(
        1, {"length": 360, "width": 250, "height": 100},
        {"competitor_weight_g": 5})
    assert weight_g == 100


def test_normal_weights_untouched():
    """≥10g 的真实轻量值绝不改写（v0.37 保护不变）；0 仍走缺失兜底。"""
    from utils.weight_dimension_normalizer import normalize_weight_dimensions
    w1, _, m1 = normalize_weight_dimensions(12, {"length": 60, "width": 40, "height": 10}, {})
    assert w1 == 12 and m1["weight_source"] == "draft"
    w2, _, _ = normalize_weight_dimensions(0, {"length": 60, "width": 40, "height": 10}, {})
    assert w2 == 100
    w3, _, _ = normalize_weight_dimensions(80, {"length": 250, "width": 120, "height": 100}, {})
    assert w3 == 80


# ═══ Step 6.5 一致性重配豁免（R2b）═══

def test_step65_exempt_r2b_confirmed():
    """R2b 已确认（LLM+源词 overlap）→ 豁免重配（A4 除草剂改配实证）。"""
    from graphs.nodes.assemble_ozon_product_node import _step65_consistency_exempt
    assert _step65_consistency_exempt("L1", True, False) is True


def test_step65_exempt_skill_and_authoritative_l0():
    from graphs.nodes.assemble_ozon_product_node import _step65_consistency_exempt
    assert _step65_consistency_exempt("Skill", False, False) is True
    assert _step65_consistency_exempt("L1", False, True) is True


def test_step65_plain_l1_not_exempt():
    """普通 L1 不豁免——A2 型（三角头巾→遮阳帽）Step 6.5 救回语义保持。"""
    from graphs.nodes.assemble_ozon_product_node import _step65_consistency_exempt
    assert _step65_consistency_exempt("L1", False, False) is False
    assert _step65_consistency_exempt("L0", False, False) is False

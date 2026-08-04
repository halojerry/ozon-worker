"""skill 信封字段单测（v0.21 P1-1）：尺寸缺失时标记 dimensions_estimated，不再静默。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.cloud_probe import _validate_and_fix_product_data


def test_dimensions_missing_flagged_estimated():
    """1688 未提供尺寸 → 返回估算尺寸 + estimated=True。"""
    w, d, errs, est = _validate_and_fix_product_data(
        item_id="123", title="测试", cost_cny=10.0, images=[],
        weight_g=350, dimensions={"length": 0, "width": 0, "height": 0},
        variants=[], option_groups=[],
    )
    assert est is True
    assert d["length"] > 0 and d["width"] > 0 and d["height"] > 0
    assert errs == []


def test_dimensions_present_not_estimated():
    """1688 提供尺寸 → estimated=False。"""
    w, d, errs, est = _validate_and_fix_product_data(
        item_id="123", title="测试", cost_cny=10.0, images=[],
        weight_g=350, dimensions={"length": 100, "width": 80, "height": 50},
        variants=[], option_groups=[],
    )
    assert est is False
    assert d["length"] == 100

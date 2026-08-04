#!/usr/bin/env python3
"""尺寸/重量修复单测（v0.21 P2）— 挂脖风扇/工具套装/修车躺板三个实证案例。

覆盖：
1. 描述文本提取：带单位优先于无单位；mm 不乘 10
2. packaging 行 cm/mm 交叉判定（工具 260cm→260mm；躺板 110cm 保持）
3. density 兜底：商家有真实重量时不覆盖；无重量估算时加 cap

运行：
    cd skill && python3 tests/test_dimension_units.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.cloud_probe import (
    _validate_and_fix_product_data,
    extract_dimensions_from_texts,
    resolve_packaging_dimensions,
)


def test_extract_prefers_united_candidate():
    """风扇页：同时有 规格 8.5*6.5*11cm 与 外观尺寸 85*65*11（无单位）→ 取带单位值并 cm→mm。"""
    texts = ["外观尺寸 85*65*11", "规格 8.5*6.5*11cm", "包装体积 8.5*6.5*11cm"]
    out = extract_dimensions_from_texts(texts)
    assert out is not None
    assert out["unit"] == "cm"
    assert (out["length"], out["width"], out["height"]) == (85, 65, 110)


def test_extract_mm_unit_no_multiply():
    """工具页权威表：尺寸(mm) 260*80*35 → 260×80×35mm，不乘 10。"""
    out = extract_dimensions_from_texts(["产品规格 尺寸(mm) 260*80*35"])
    assert out is not None
    assert out["unit"] == "mm"
    assert (out["length"], out["width"], out["height"]) == (260, 80, 35)


def test_extract_unitless_is_conservative():
    """无单位尺寸不盲目当 cm 乘 10：按原值 mm 输出，标记 unit=unknown。"""
    out = extract_dimensions_from_texts(["外观尺寸 85*65*11"])
    assert out is not None
    assert out["unit"] == "unknown"
    assert (out["length"], out["width"], out["height"]) == (85, 65, 11)


def test_extract_rejects_absurd_dim():
    """单边 > 5000mm（5m）的候选视为脏数据拒绝。"""
    out = extract_dimensions_from_texts(["尺寸 26000*8000*3500"])
    assert out is None


def test_resolve_packaging_mm_when_cm_density_absurd():
    """工具套装：无单位 260/80/35 + 400g。按 cm 密度 0.0005 荒谬 → 判为 mm，输出 260×80×35。"""
    row = {
        "lengthText": "260", "widthText": "80", "heightText": "35",
        "weightText": "400", "weightGrams": 400,
    }
    out = resolve_packaging_dimensions(row, weight_g=400)
    assert out["unit_used"] == "mm"
    assert (out["length"], out["width"], out["height"]) == (260, 80, 35)


def test_resolve_packaging_keeps_cm_when_mm_absurd():
    """修车躺板：无单位 110/50/30 + 5200g。按 mm 密度 31 荒谬 → 保持 cm，输出 1100×500×300。"""
    row = {
        "lengthText": "110", "widthText": "50", "heightText": "30",
        "weightText": "5200", "weightGrams": 5200,
    }
    out = resolve_packaging_dimensions(row, weight_g=5200)
    assert out["unit_used"] == "cm"
    assert (out["length"], out["width"], out["height"]) == (1100, 500, 300)


def test_density_guard_keeps_merchant_weight():
    """挂脖风扇：850×650×110mm + 300g 商家重量 → 不放大，保留 300g。"""
    w, d, errs, est = _validate_and_fix_product_data(
        item_id="892889757604", title="风扇", cost_cny=11.3, images=["x"],
        weight_g=300, dimensions={"length": 850, "width": 650, "height": 110},
        variants=[], option_groups=[],
    )
    assert w == 300
    assert d["length"] == 850
    assert errs == []


def test_density_estimate_capped_when_no_merchant_weight():
    """无商家重量时估算重量，但封顶 30000g，不再输出 364kg 级离谱值。"""
    w, d, errs, est = _validate_and_fix_product_data(
        item_id="965229015779", title="工具套装", cost_cny=162.68, images=["x"],
        weight_g=0, dimensions={"length": 2600, "width": 800, "height": 350},
        variants=[], option_groups=[],
    )
    assert w <= 30000
    assert w > 0


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

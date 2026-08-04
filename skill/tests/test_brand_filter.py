#!/usr/bin/env python3
"""discover 品牌过滤单测（v0.22）— 知名品牌直接过滤，白牌保留。

运行：
    cd skill && python3 tests/test_brand_filter.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import _is_known_brand


def test_known_international_brands_filtered():
    for brand in ["Nike", "adidas", "Apple", "Samsung", "Xiaomi", "Philips", "Bosch",
                  "Dyson", "JBL", "Anker"]:
        assert _is_known_brand(brand) is True, f"{brand} 应被过滤"


def test_whitelabel_kept():
    """1688 白牌/小厂牌（fansen 风扇）保留，不误杀。"""
    for brand in ["fansen", "德力西", "", "无品牌", "fans", "通用"]:
        assert _is_known_brand(brand) is False, f"{brand!r} 不应被过滤"


def test_brand_with_suffix():
    """品牌名带后缀/大小写混合也能识别。"""
    assert _is_known_brand("NIKE SPORTS") is True
    assert _is_known_brand("Xiaomi Store") is True


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

#!/usr/bin/env python3
"""worker 自修复能力单测（v0.22）— 手工修复经验固化：
1. cm/mm 交叉判定（替代密度<1.0 无差别 /10）
2. price_out_of_range → repair_pricing
3. 字典值搜索语言链 ZH→RU→EN（RU 命中）

运行：
    cd worker && PYTHONPATH=src python3 tests/test_retry_self_repair.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.validation_retry_loop import REPAIR_STRATEGY, _resolve_dimension_units


# ── 1. cm/mm 交叉判定 ─────────────────────────────────────────────────────

def test_resolve_mm_when_cm_density_absurd():
    """工具套装：2600/800/350 + 400g。cm 密度 0.0005 荒谬 → 判为 mm 260×80×35。"""
    d, w, h, unit = _resolve_dimension_units(2600, 800, 350, 400)
    assert unit == "mm"
    assert (d, w, h) == (260, 80, 35)


def test_resolve_keeps_cm_when_mm_absurd():
    """修车躺板：1100/500/300 + 5200g。mm 密度 31 荒谬 → 保持 cm 不 /10。"""
    d, w, h, unit = _resolve_dimension_units(1100, 500, 300, 5200)
    assert unit == "cm"
    assert (d, w, h) == (1100, 500, 300)


def test_resolve_small_item_keeps_cm():
    """正常小件：85/65/110 + 300g。cm 密度 0.49 合理 → 保持。"""
    d, w, h, unit = _resolve_dimension_units(85, 65, 110, 300)
    assert unit == "cm"
    assert (d, w, h) == (85, 65, 110)


def test_resolve_does_not_shrink_estimated_defaults():
    """缺失尺寸从重量推算的默认值（200×160×120/100g，密度天然低）不能被误切 mm。"""
    d, w, h, unit = _resolve_dimension_units(200, 160, 120, 100)
    assert unit == "cm"
    assert (d, w, h) == (200, 160, 120)


# ── 2. price_out_of_range → repair_pricing ────────────────────────────────

def test_price_out_of_range_routes_to_repair_pricing():
    """price_out_of_range 必须走重定价（LLM 修不了价格）。"""
    assert REPAIR_STRATEGY.get("price_out_of_range") == "repair_pricing"


# ── 3. 字典值搜索语言链 ZH→RU→EN ─────────────────────────────────────────

def test_search_chain_prefers_ru_when_zh_miss():
    """ZH 搜不到 → 换 RU（Ozon 字典值是俄语），RU 命中即返回，不浪费 EN。"""
    calls: list[str] = []

    def fake_search(cid, key, aid, cat, tp, value, lang):
        calls.append(lang)
        if lang == "ZH_HANS":
            return []
        if lang == "RU":
            return [{"id": 148495146, "value": "Hand Fan"}]
        return []

    from graphs.validation_retry_loop import _search_dictionary_values_chain
    out = _search_dictionary_values_chain(
        "1", "k", 8229, "17028743", "148495146", ["вентилятор"], fake_search
    )
    assert out is not None
    assert out["id"] == 148495146
    assert calls == ["ZH_HANS", "RU"]


def test_search_chain_zh_hit_no_ru():
    """ZH 命中直接返回，不调 RU。"""
    calls: list[str] = []

    def fake_search(cid, key, aid, cat, tp, value, lang):
        calls.append(lang)
        return [{"id": 61571, "value": "Белый"}] if lang == "ZH_HANS" else []

    from graphs.validation_retry_loop import _search_dictionary_values_chain
    out = _search_dictionary_values_chain("1", "k", 8229, "1", "1", ["白色"], fake_search)
    assert out["id"] == 61571
    assert calls == ["ZH_HANS"]


# ── 4. P2b: repair_pricing 无定价信息阻断（v0.22 审查修复）───────────────

def test_repair_pricing_blocks_without_pricing_info():
    """price_out_of_range 但 state.pricing_info 无 price → 阻断，不用 999 兜底。"""
    from graphs.validation_retry_loop import repair_pricing_node
    from types import SimpleNamespace
    state = SimpleNamespace(
        error_code="price_out_of_range",
        pricing_info={},
        ozon_payload={"items": [{"price": "25290"}]},
        retry_count=0,
    )
    out = repair_pricing_node(state)
    assert "PRICING_FAILED" in out.error_message
    assert out.ozon_payload["items"][0]["price"] != "999"


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

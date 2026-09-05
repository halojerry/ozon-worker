#!/usr/bin/env python3
"""discover 信封不再兜底注入 margin_rate 0.25 / commission_rate 0.10 回归（v0.65）。

背景：build_envelope_from_discovery 降级组装段此前把 store_profile 缺失的
margin_rate 兜底成 0.25、commission_rate 兜底成 0.10——①显式 0.25 让 worker
pricing_node 判定「显式 margin_rate 无 floor/anchor → 旧单档」→ 三档默认永远
不生效；②显式 0.10 压过 worker commission_resolver（v0.59 佣金链：
explicit > 缓存表 > segments > 0.10），真实佣金永远读不到。

worker 侧三档默认规则已改为「margin 键全缺 → 自动三档」+ 佣金解析链已接真实
费率——skill 不该再兜底填 0.25/0.10。本测试锁定：未配置 → 不注入任何定价键
（只留 follow_sell）；配置了 → 只注入配置的非零键；0 值不注入。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_envelope_pricing_defaults.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402


def _mk_candidate(**overrides) -> ProductCandidate:
    """构造降级路径所需最小 ProductCandidate（fallback 只用基础字段）。"""
    base = dict(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
        match_1688_url="https://detail.1688.com/offer/980815374096.html",
        competing_sellers=4,
    )
    base.update(overrides)
    c = ProductCandidate(
        ozon_product_id=base["ozon_product_id"],
        ozon_title=base["ozon_title"],
        ozon_price=base["ozon_price"],
    )
    c.match_1688_url = base["match_1688_url"]
    c.competing_sellers = base["competing_sellers"]
    c.weight_g = 0
    c.dimensions_mm = {}
    c.ozon_category = {}
    return c


def _build_fallback(cand: ProductCandidate, store_profile: dict) -> dict | None:
    """走 build_envelope_from_discovery 的降级组装段（retry 失败 → 本地兜底）。"""
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=None), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value=store_profile):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def test_empty_store_profile_no_pricing_keys():
    """store_profile 空/未配置 → extensions 只有 follow_sell，无定价兜底键。"""
    result = _build_fallback(_mk_candidate(), {})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert ext["follow_sell"] is True
    assert "margin_rate" not in ext
    assert "commission_rate" not in ext
    assert "fx_buffer" not in ext


def test_configured_store_injects_only_configured():
    """store_profile 配 margin/commission → 只注入配置的非零键，无 fx_buffer 兜底。"""
    result = _build_fallback(_mk_candidate(), {"margin_rate": 0.3, "commission_rate": 0.12})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert ext["margin_rate"] == 0.3
    assert ext["commission_rate"] == 0.12
    assert "fx_buffer" not in ext


def test_zero_values_not_injected():
    """store_profile 里 margin_rate=0 / fx_buffer=0 → 不注入（留空让 worker 默认）。"""
    result = _build_fallback(_mk_candidate(),
                             {"margin_rate": 0, "fx_buffer": 0, "commission_rate": 0.10})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert "margin_rate" not in ext
    assert "fx_buffer" not in ext
    assert ext["commission_rate"] == 0.10


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

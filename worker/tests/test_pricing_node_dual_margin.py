# -*- coding: utf-8 -*-
"""pricing_node v0.60 三档双价格体系接入测试（T3，TDD RED→GREEN）。

三档参数（用户拍板 2026-08-21，T2 已进 compute_price）：
- margin_rate=1.5（日常价） / margin_anchor=2.0（划线原价） / margin_floor=0.6（促销底线）
- variable_cost_rate=0.155（日常变动成本） / promo_variable_cost_rate=0.245（促销变动成本）
- 利润口径 = 销售净利率

本测试锁定 pricing_node 的「接线」行为：
  a. 三档启用时 pricing_info 输出日常/划线/促销三价 + old_price ≥ ceil(price×1.2)
  b. 只传 margin_rate（无 floor/anchor）→ 旧单档行为（无 promo_price 键）
  c. 变体循环走 compute_price（对齐单 SKU 公式）+ variant_prices 含 promo_price

演算基准（total_cost=17.5, RUB rate=12, fx=0.05, 佣金 fallback 0.10）：
- daily_divisor = 1-0.10-0.155 = 0.745，promo_divisor = 1-0.10-0.245 = 0.655
- price = ceil(17.5×2.5×1.05/0.745×12) = 740
- old   = ceil(17.5×3.0×1.05/0.745×12) = 888（≥price×1.2 = 888）
- promo = ceil(17.5×1.6×1.05/0.655×12) = 539

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_node_dual_margin.py -q
"""
from __future__ import annotations

import math
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import pricing_node as pn
from utils.pricing_estimate import compute_price  # 独立已知正确的公式（验证变体同源）


def _make_state(draft=None, extensions=None, currency="RUB", dc_id="17028830"):
    """构造 pricing_node 可直接消费的 state（SimpleNamespace，无 variants 属性 → 走 draft.variants 兜底）。"""
    return SimpleNamespace(
        draft=draft
        or {
            "cost_cny": 5.5,
            "purchase_cost": 5.5,
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        extensions=extensions or {},
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code=currency,
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id=dc_id,
        task_id="",
        tenant_id="",
        token="sk-test",
    )


class _DummyRuntime:
    """pricing_node 只读 `runtime.context`（不消费内容），给个空对象即可。"""

    class _DummyContext:
        pass

    context = _DummyContext()


def _call_pricing(monkeypatch, state, logistics_cost=10.0, rate=12.0, cat_commission=None):
    """mock 重依赖后调用 pricing_node（纯内存，无 PG/HTTP）。"""
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (logistics_cost, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: rate)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: cat_commission, raising=False)
    monkeypatch.setattr(pn, "ozon_post", lambda *a, **k: {}, raising=False)
    return pn.pricing_node(state, None, _DummyRuntime())


# ── a. 三档启用：日常/划线/促销三价 + old_price ≥ price×1.2 ──
def test_dual_margin_three_tiers(monkeypatch):
    out = _call_pricing(
        monkeypatch,
        _make_state(extensions={
            "margin_rate": 1.5,
            "margin_floor": 0.6,
            "margin_anchor": 2.0,
        }),
    )
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    assert pi["price"] == 740, f"日常价应=740，实际 {pi['price']}"
    assert pi["old_price"] == 888, f"划线原价应=888，实际 {pi['old_price']}"
    assert pi["promo_price"] == 539, f"促销底线应=539，实际 {pi['promo_price']}"
    assert pi["old_price"] >= math.ceil(pi["price"] * 1.2), "划线价须 ≥ 日常价×1.2"
    # 组装键
    assert pi["margin_anchor"] == 2.0
    assert pi["margin_floor"] == 0.6
    assert pi["variable_cost_rate"] == 0.155
    # 销售净利率口径（净利/售价）
    assert pi["profit_estimation"]["profit_rate"] > 0


# ── b. 向后兼容：只传 margin_rate（无 floor/anchor）→ 旧单档行为 ──
def test_legacy_when_only_margin_rate(monkeypatch):
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    # 旧公式: ceil(17.5×1.25×1.05/0.90×12) = 307；old = ceil(307×1.2) = 369
    assert pi["price"] == 307, f"旧单档 price 应=307，实际 {pi['price']}"
    assert pi["old_price"] == 369, f"旧单档 old_price 应=price×1.2=369，实际 {pi['old_price']}"
    assert "promo_price" not in pi, "单档旧行为不产生 promo_price 键"
    assert "margin_anchor" not in pi, "单档旧行为不产生 margin_anchor 键"
    assert "margin_floor" not in pi, "单档旧行为不产生 margin_floor 键"


# ── c. 变体循环走 compute_price（对齐单 SKU）+ variant_prices 含 promo_price ──
def test_variant_prices_use_compute_price_with_promo(monkeypatch):
    draft = {
        "cost_cny": 5.5,
        "purchase_cost": 5.5,
        "weight": 227,
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "variants": [
            {"sku_id": "v1", "color": "黑色", "price": 8.0},
            {"sku_id": "v2", "color": "白色", "price": 12.0},
        ],
    }
    out = _call_pricing(
        monkeypatch,
        _make_state(draft=draft, extensions={
            "margin_rate": 1.5,
            "margin_floor": 0.6,
            "margin_anchor": 2.0,
        }),
    )
    assert out.error_message == "", f"不应失败: {out.error_message}"
    vp = out.pricing_info["variant_prices"]
    assert len(vp) == 2, "两个变体都应计算"
    for var, var_cost in zip(vp, (8.0, 12.0)):
        assert "promo_price" in var, f"变体 {var['sku_id']} 必须含 promo_price"
        # 与单 SKU 同公式：compute_price 独立演算同一 var_total_cost
        ref = compute_price(
            var_cost + 10.0 + 2.0, 1.5, 0.10, 0.05, "RUB", 12.0,
            margin_anchor=2.0, margin_floor=0.6,
            variable_cost_rate=0.155, promo_variable_cost_rate=0.245,
        )
        assert var["price"] == ref["price"], f"{var['sku_id']} price 应与单 SKU 公式一致"
        assert var["old_price"] == ref["old_price"], f"{var['sku_id']} old_price 应与单 SKU 公式一致"
        assert var["promo_price"] == ref["promo_price"], f"{var['sku_id']} promo_price 应与单 SKU 公式一致"
        assert ref["promo_price"] is not None
    # 显式演算值（v1: total=20 → price=846, old=1016(≥846×1.2), promo=616）
    assert vp[0]["price"] == 846
    assert vp[0]["old_price"] == 1016
    assert vp[0]["promo_price"] == 616

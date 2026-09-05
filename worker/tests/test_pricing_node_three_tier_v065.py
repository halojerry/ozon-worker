# -*- coding: utf-8 -*-
"""v0.65: pricing_node 三档定价默认激活测试（TDD RED→GREEN）。

背景（v0.60 三档从未默认生效）：pricing_node 原 dual_margin 判定只认信封显式传的
margin_floor/margin_anchor → 裸信封（extensions 无 margin 键）恒 false → 走旧单档公式
（margin 0.25 成本利润率），promo_price 从未产生。

v0.65 用户决策：
  - 未配置店铺自动三档（margin_rate 默认 1.5 / anchor 2.0 / floor 0.6 / vcr 0.155 / pvcr 0.245）；
  - 显式配了 margin_rate 的店保持旧单档（存量显式配置向后兼容，缺省 margin 0.25）。
判定规则与 estimate_service.estimate_from_envelope 对齐：floor/anchor 在场或 margin 键全缺
→ 三档；显式 margin_rate 且无 floor/anchor → 旧单档。

演算基准（total_cost=17.5 = 采购5.5 + mock物流10 + 包装2，RUB rate=12, fx=0.05, 佣金 fallback 0.10）：
- daily_divisor  = 1-0.10-0.155 = 0.745
- promo_divisor  = 1-0.10-0.245 = 0.655
- price = ceil(17.5×2.5×1.05/0.745×12) = 740（日常价）
- old   = ceil(17.5×3.0×1.05/0.745×12) = 888（划线原价，anchor=2.0；≥price×1.2=888）
- promo = ceil(17.5×1.6×1.05/0.655×12) = 539（促销底线）
单档（显式 margin_rate=0.25）：price = ceil(17.5×1.25×1.05/0.90×12) = 307，old = ceil(307×1.2) = 369。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_node_three_tier_v065.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import pricing_node as pn
from services import estimate_service  # 与 pricing_node 判定/公式同源对照


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


def _envelope(draft=None, extensions=None):
    """构造 estimate_service 消费的三层信封（与 _make_state 同一裸信封）。"""
    return {
        "draft": draft
        or {
            "cost_cny": 5.5,
            "purchase_cost": 5.5,
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        "extensions": extensions or {},
    }


class _DummyRuntime:
    """pricing_node 只读 `runtime.context`（不消费内容），给个空对象即可。"""

    class _DummyContext:
        pass

    context = _DummyContext()


def _call_pricing(monkeypatch, state, logistics_cost=10.0, rate=12.0, cat_commission=None):
    """mock 重依赖后调用 pricing_node（纯内存，无 PG/HTTP），风格同 test_pricing_node_commission.py。"""
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
    return pn.pricing_node(state, None, _DummyRuntime())


def _call_estimate(monkeypatch, envelope, rate=12.0, logistics_cost=10.0):
    """mock 重依赖后调用 estimate_service（其模块级 import 在加载时绑定，须打 estimate_service 命名空间）。"""
    monkeypatch.setattr(
        estimate_service, "query_logistics_cost",
        lambda *a, **k: (logistics_cost, "mock_channel", {}),
    )
    monkeypatch.setattr(estimate_service, "get_category_commission", lambda *a, **k: None)
    return estimate_service.estimate_from_envelope(
        envelope, exchange_rate=rate, currency_code="RUB"
    )


# ── 1. 裸信封（无 margin 键）→ 默认三档 ──
def test_no_margin_keys_defaults_three_tier(monkeypatch):
    """extensions={}（无任何 margin 键）→ 三档：promo_price>0、margin_rate==1.5、anchor==2.0、floor==0.6。"""
    out = _call_pricing(monkeypatch, _make_state())
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    assert pi["price"] == 740, f"三档日常价应=740，实际 {pi['price']}"
    assert pi["old_price"] == 888, f"划线原价应=888，实际 {pi['old_price']}"
    assert pi["promo_price"] == 539, f"促销底线应=539，实际 {pi['promo_price']}"
    assert pi["promo_price"] > 0, "默认三档必须产生 promo_price"
    assert pi["margin_rate"] == 1.5, "三档 margin_rate 默认应=1.5"
    assert pi["margin_anchor"] == 2.0, "margin_anchor 默认应=2.0"
    assert pi["margin_floor"] == 0.6, "margin_floor 默认应=0.6"
    assert pi["variable_cost_rate"] == 0.155
    assert pi["promo_price"] < pi["price"], "促销底线必须低于日常价（打折空间）"


# ── 2. 显式 margin_rate（无 floor/anchor）→ 旧单档向后兼容 ──
def test_explicit_margin_rate_keeps_legacy(monkeypatch):
    """extensions={"margin_rate":0.25} → 旧单档：无 promo_price、price=ceil(307) 旧公式逐字一致。"""
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    # 旧公式: ceil(17.5×1.25×1.05/0.90×12) = 307；old = ceil(307×1.2) = 369
    assert pi["price"] == 307, f"旧单档 price 应=307，实际 {pi['price']}"
    assert pi["old_price"] == 369, f"旧单档 old_price 应=369，实际 {pi['old_price']}"
    assert "promo_price" not in pi, "旧单档不产生 promo_price 键"
    assert "margin_anchor" not in pi, "旧单档不产生 margin_anchor 键"
    assert "margin_floor" not in pi, "旧单档不产生 margin_floor 键"
    assert pi["margin_rate"] == 0.25, "显式 margin_rate 须保留"


# ── 3. 显式 margin_rate + margin_floor → 三档且用显式 margin ──
def test_explicit_floor_enables_three_tier(monkeypatch):
    """extensions={"margin_rate":1.5,"margin_floor":0.6} → 三档，margin_rate 用显式值 1.5。"""
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 1.5, "margin_floor": 0.6}))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    assert pi["promo_price"] == 539, f"floor 在场即三档，promo 应=539，实际 {pi['promo_price']}"
    assert pi["margin_rate"] == 1.5, "显式 margin_rate=1.5 三档须保留"
    assert pi["margin_anchor"] == 2.0, "anchor 未显式 → 默认 2.0"
    assert pi["margin_floor"] == 0.6


# ── 4. 裸信封：pricing_node 与 estimate_service 同价（默认三档判定一致）──
def test_bare_envelope_matches_estimate_service(monkeypatch):
    """同一裸信封（extensions 无 margin 键）分别过 pricing_node 与 estimate_from_envelope → price 相等。"""
    out = _call_pricing(monkeypatch, _make_state())
    est = _call_estimate(monkeypatch, _envelope())
    assert out.error_message == "", f"pricing_node 不应失败: {out.error_message}"
    assert est["price"] == out.pricing_info["price"], (
        f"estimate({est['price']}) 与 pricing_node({out.pricing_info['price']}) 应同价"
    )
    assert est["old_price"] == out.pricing_info["old_price"]
    assert est.get("promo_price") == out.pricing_info.get("promo_price"), (
        f"estimate promo={est.get('promo_price')} vs pricing promo={out.pricing_info.get('promo_price')}"
    )
    assert est.get("margin_anchor") == out.pricing_info.get("margin_anchor")


# ── 5. 显式 margin_rate：pricing_node 与 estimate_service 同为旧单档 ──
def test_explicit_margin_rate_matches_estimate_service(monkeypatch):
    """显式 margin_rate=0.25（无 floor/anchor）→ 两端都判定单档、price 相等、无 promo。"""
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}))
    est = _call_estimate(monkeypatch, _envelope(extensions={"margin_rate": 0.25}))
    assert out.error_message == "", f"pricing_node 不应失败: {out.error_message}"
    assert "promo_price" not in out.pricing_info, "pricing_node 单档无 promo_price"
    assert "promo_price" not in est, "estimate_service 单档无 promo_price"
    assert est["price"] == out.pricing_info["price"], (
        f"estimate({est['price']}) 与 pricing_node({out.pricing_info['price']}) 应同价"
    )

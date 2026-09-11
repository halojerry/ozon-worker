# -*- coding: utf-8 -*-
"""BL-01: pricing_node 汇率源集成测试（fallback_12 marks + source 留痕）。

背景：pricing 兜底恒 12.0 无告警。集成 fx_rate_service 后：
- source=fallback_12 → pricing_info 带 exchange_rate_source + exchange_rate_fallback=True
- source=pg_cache/live_fetch → 只带 exchange_rate_source（无 fallback 键）
- 定价公式零改动（compute_price 唯一入口纪律）：rate=12.0 时价格与 v0.65 基线逐字一致
- CNY 路径（rate=1.0）行为逐字保持：无 marks 键
- 既有测试族直接 mock pn._get_exchange_rate（float 契约）→ 无 marks，向后兼容

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_fx_source.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import pricing_node as pn
from utils import fx_rate_service as fx


@pytest.fixture(autouse=True)
def _clear_fx_source_channel():
    """清线程键来源通道（防前序用例残留串扰；正常路径写后即被调用点消费）。"""
    pn._FX_SOURCE_BY_THREAD.clear()
    yield
    pn._FX_SOURCE_BY_THREAD.clear()


def _make_state(draft=None, extensions=None, currency="RUB", dc_id="17028830"):
    """构造 pricing_node 可直接消费的 state（风格同 test_pricing_node_three_tier_v065.py）。"""
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


def _call_pricing(monkeypatch, state, resolved=(12.0, "fallback_12"), logistics_cost=10.0):
    """mock 物流/佣金后调 pricing_node（纯内存，无 PG/HTTP）。

    与 v065 测试的关键差异：不打 pn._get_exchange_rate 的桩，而是 mock
    fx service 的 resolve——真 _get_exchange_rate 走 fx 三级链（BL-01 主路径）。
    返回 (PricingOutput, resolve 调用次数)。
    """
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (logistics_cost, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None)

    resolve_calls: list = []

    def _fake_resolve(*a, **k):
        resolve_calls.append(1)
        return resolved

    monkeypatch.setattr(fx, "resolve_cny_rub_rate", _fake_resolve)
    out = pn.pricing_node(state, None, _DummyRuntime())
    return out, len(resolve_calls)


# ── 1. fallback_12 → 双 marks + 价格与 v0.65 基线逐字一致 ──
def test_fallback_12_marks_and_formula_unchanged(monkeypatch):
    out, calls = _call_pricing(monkeypatch, _make_state(), resolved=(12.0, "fallback_12"))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    assert calls == 1, "fallback 路径应经 fx service resolve 恰好一次"
    pi = out.pricing_info
    assert pi["exchange_rate_source"] == "fallback_12"
    assert pi["exchange_rate_fallback"] is True
    assert pi["exchange_rate"] == 12.0
    # 定价公式零改动：rate=12.0 时与 test_pricing_node_three_tier_v065 基线逐字一致
    # （total_cost=17.5 = 采购5.5 + mock物流10 + 包装2，三档默认 margin 1.5/2.0/0.6）
    assert pi["price"] == 740, f"三档日常价应=740，实际 {pi['price']}"
    assert pi["old_price"] == 888
    assert pi["promo_price"] == 539


# ── 2. live_fetch → 只带 source，无 fallback 键 ──
def test_live_fetch_source_no_fallback_key(monkeypatch):
    out, _ = _call_pricing(monkeypatch, _make_state(), resolved=(12.5, "live_fetch"))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    assert pi["exchange_rate_source"] == "live_fetch"
    assert "exchange_rate_fallback" not in pi
    assert pi["exchange_rate"] == 12.5, "live 汇率必须真实进定价"


# ── 3. pg_cache → 只带 source，无 fallback 键 ──
def test_pg_cache_source_no_fallback_key(monkeypatch):
    out, _ = _call_pricing(monkeypatch, _make_state(), resolved=(11.8, "pg_cache"))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    pi = out.pricing_info
    assert pi["exchange_rate_source"] == "pg_cache"
    assert "exchange_rate_fallback" not in pi
    assert pi["exchange_rate"] == 11.8


# ── 4. CNY 路径行为逐字保持：rate=1.0、无 marks、不经 resolve ──
def test_cny_path_no_marks_and_skips_resolve(monkeypatch):
    out, calls = _call_pricing(monkeypatch, _make_state(currency="CNY"),
                               resolved=(12.0, "fallback_12"))
    assert out.error_message == "", f"不应失败: {out.error_message}"
    assert calls == 0, "CNY 路径短路返回 1.0，不得调 resolve"
    pi = out.pricing_info
    assert pi["exchange_rate"] == 1.0
    assert "exchange_rate_source" not in pi
    assert "exchange_rate_fallback" not in pi


# ── 5. 既有 mock seam 兼容：直接 mock pn._get_exchange_rate → 无 marks ──
def test_legacy_get_exchange_rate_mock_seam_no_marks(monkeypatch):
    """5 个既有测试族按 float 契约 mock pn._get_exchange_rate——此时拿不到来源，
    pricing_info 不得出现 marks（消费语义保证无残留串扰）。"""
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None)
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 12.0)
    out = pn.pricing_node(_make_state(), None, _DummyRuntime())
    assert out.error_message == "", f"不应失败: {out.error_message}"
    assert "exchange_rate_source" not in out.pricing_info
    assert "exchange_rate_fallback" not in out.pricing_info
    assert out.pricing_info["price"] == 740, "旧 mock seam 下价格行为不变"

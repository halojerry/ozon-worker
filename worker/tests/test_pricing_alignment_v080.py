"""v0.80 定价双实现对齐测试（docs/ARCHITECTURE/09-findings.md Top10 #10 收敛锁定）。

此前三处分叉：
  1. 三档判定：pricing_node 认「extensions 键存在」、estimate_service 认「floor 值
     非 None」→ margin_rate+margin_anchor（无 floor）的信封 graph 三档 / /estimate 单档。
     修法：判定唯一口径 utils.pricing_estimate.is_dual_margin，两处都走它。
  2. provisional band pass：pricing_node 临时价不传三档 kwargs（分母 1-c）、
     estimate_service 传（分母 1-c-vcr）→ 两处选出的佣金价格段可能不同。
     修法：pricing_node 临时价与最终价同参。
  3. 中性档：pick_price_band(None)→leq_1500（自称最保守）vs pricing_node/
     learning_record 无价手工 leq_5000。修法：resolver 无价默认统一 leq_5000。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_alignment_v080.py -q
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import pricing_node as pn
from services import estimate_service
from utils.commission_resolver import resolve_commission_rate_detail
from utils.pricing_estimate import is_dual_margin


# ── 0. 共享判定纯函数真值表（键存在语义）──
def test_is_dual_margin_truth_table():
    # floor/anchor 任一键在场 → 三档
    assert is_dual_margin(margin_floor_present=False, margin_anchor_present=False, has_margin_rate=False) is True
    assert is_dual_margin(margin_floor_present=True, margin_anchor_present=False, has_margin_rate=True) is True
    assert is_dual_margin(margin_floor_present=False, margin_anchor_present=True, has_margin_rate=True) is True
    assert is_dual_margin(margin_floor_present=True, margin_anchor_present=True, has_margin_rate=False) is True
    # 仅显式 margin_rate（floor/anchor 均不在场）→ 单档 legacy
    assert is_dual_margin(margin_floor_present=False, margin_anchor_present=False, has_margin_rate=True) is False


def _make_state(draft=None, extensions=None, currency="RUB", dc_id="17028830"):
    """构造 pricing_node 可直接消费的 state（SimpleNamespace，风格同 test_pricing_node_dual_margin.py）。"""
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


def _mock_heavy(monkeypatch, cat_commission=None, logistics_cost=10.0, rate=12.0):
    """同时 mock pricing_node 与 estimate_service 的重依赖（物流/汇率/佣金缓存表）。"""
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
    monkeypatch.setattr(
        estimate_service, "query_logistics_cost",
        lambda *a, **k: (logistics_cost, "mock_channel", {}),
    )
    monkeypatch.setattr(estimate_service, "get_category_commission", lambda *a, **k: cat_commission)


# ── 1. 三档判定两处一致：同一 extensions 输入 → pricing_node 与 estimate_service 同档同价 ──
@pytest.mark.parametrize(
    "extensions",
    [
        {},  # 全缺省 → 三档（v0.65 默认激活）
        {"margin_rate": 0.25},  # 仅显式 margin_rate → 单档 legacy
        {"margin_rate": 1.5, "margin_floor": 0.6},  # floor 在场 → 三档
        {"margin_anchor": 2.0},  # 仅 anchor（无 margin_rate）→ 三档
        {"margin_rate": 0.25, "margin_anchor": 2.0},  # ★ 修复目标：margin_rate+anchor 无 floor → 两边都三档
        {"margin_rate": 0.25, "margin_floor": 0.6, "margin_anchor": 2.0},  # 全键 → 三档
    ],
)
def test_dual_margin_verdict_consistent(monkeypatch, extensions):
    _mock_heavy(monkeypatch)
    out = pn.pricing_node(_make_state(extensions=dict(extensions)), None, _DummyRuntime())
    est = estimate_service.estimate_from_envelope(
        _envelope(extensions=dict(extensions)), exchange_rate=12.0, currency_code="RUB"
    )
    assert out.error_message == "", f"pricing_node 不应失败: {out.error_message}"
    pi = out.pricing_info
    # 档位一致：promo_price 有无是三档/单档的唯一外部可见标志
    assert ("promo_price" in pi) == ("promo_price" in est), (
        f"extensions={extensions}: pricing_node promo={'有' if 'promo_price' in pi else '无'}"
        f" vs estimate promo={'有' if 'promo_price' in est else '无'}（档位判定分叉）"
    )
    # 同价：判定一致 + 公式同源 → 三价必须相等
    assert est["price"] == pi["price"], f"extensions={extensions}: estimate({est['price']}) vs graph({pi['price']})"
    assert est["old_price"] == pi["old_price"], f"extensions={extensions}: old_price 分叉"
    if "promo_price" in est:
        assert est["promo_price"] == pi["promo_price"], f"extensions={extensions}: promo_price 分叉"


# ── 2. estimate_service 请求级显式 margin_floor 优先语义保持不变 ──
def test_estimate_request_floor_override_still_three_tier(monkeypatch):
    """请求级 margin_floor 在场 → 即使 extensions 只带 margin_rate 也三档（原语义不动）。"""
    _mock_heavy(monkeypatch)
    est = estimate_service.estimate_from_envelope(
        _envelope(extensions={"margin_rate": 0.25}),
        exchange_rate=12.0,
        currency_code="RUB",
        margin_floor=0.6,
    )
    assert "promo_price" in est, "请求级显式 margin_floor 必须激活三档"
    # 请求级 margin_rate（无 floor/anchor）+ 裸 extensions → 仍单档（原语义不动）
    est_single = estimate_service.estimate_from_envelope(
        _envelope(extensions={}),
        exchange_rate=12.0,
        currency_code="RUB",
        margin_rate=0.25,
    )
    assert "promo_price" not in est_single, "请求级 margin_rate 无 floor/anchor 应保持单档"


# ── 3. provisional band pass 对齐：临时价三档分母 → 选档与 estimate_service 一致 ──
# 演算：total_cost=80（68+10+2），margin_rate=0.25、anchor 2.0（dual）。
# 旧 pricing_node 临时价（单档分母 0.9）：ceil(80×1.25×1.05/0.9×12)=1400 → 档 leq_1500 → 佣 8%
# 新 pricing_node 临时价（三档分母 0.745）：ceil(80×1.25×1.05/0.745×12)=1692 → 档 leq_5000 → 佣 25%
# estimate_service 一直传三档 kwargs（同 1692/25%）→ 修复后两端同佣金同价。
_BAND_CACHE_ROW = {
    "fbs_leq_1500": 8.0,
    "fbs_leq_5000": 25.0,
    "fbs_gt_5000": 30.0,
    "source": "what_to_sell",
    "updated_at": time.time(),  # BL-24: 新鲜行（无 updated_at 视同超龄降级）
}

_DUAL_EXT = {"margin_rate": 0.25, "margin_anchor": 2.0}


def test_provisional_band_pass_aligned(monkeypatch):
    _mock_heavy(monkeypatch, cat_commission=_BAND_CACHE_ROW)
    out = pn.pricing_node(_make_state(
        draft={"cost_cny": 68.0, "weight": 227,
               "dimensions": {"length": 120, "width": 80, "height": 60}},
        extensions=dict(_DUAL_EXT),
    ), None, _DummyRuntime())
    est = estimate_service.estimate_from_envelope(
        _envelope(draft={"cost_cny": 68.0, "weight": 227,
                         "dimensions": {"length": 120, "width": 80, "height": 60}},
                  extensions=dict(_DUAL_EXT)),
        exchange_rate=12.0,
        currency_code="RUB",
    )
    assert out.error_message == "", f"pricing_node 不应失败: {out.error_message}"
    pi = out.pricing_info
    # 三档分母下的临时价 1692 落 leq_5000 → 佣 25%（旧实现落 leq_1500 → 8%，两端分叉）
    assert pi["commission_rate"] == pytest.approx(0.25), (
        f"pricing_node 临时价应按三档分母选 leq_5000 段（25%），实际 {pi['commission_rate']}"
    )
    assert est["commission_rate"] == pytest.approx(0.25)
    # 终价一致（临时价选档一致 → 真实佣金一致 → 终价一致）：ceil(80×1.25×1.05/0.595×12)=2118
    assert pi["price"] == 2118, f"price 应=2118，实际 {pi['price']}"
    assert est["price"] == pi["price"], f"estimate({est['price']}) vs graph({pi['price']}) 应同价"


# ── 4. 中性档统一：无价（None/≤0）→ resolver 与 pricing_node 手工兜底同为 leq_5000 ──
def test_pick_price_band_neutral_default_leq_5000():
    # resolver 层：无价不再落 leq_1500 低佣段（此前与两调用方手工 leq_5000 分叉）
    detail = resolve_commission_rate_detail(
        description_category_id=123,
        price_rub=None,
        explicit_commission=0,
        get_category_commission_fn=lambda _dc: _BAND_CACHE_ROW,
    )
    assert detail == {"rate": 0.25, "source": "cache:leq_5000", "stale": False}, (
        f"无价场景应选 leq_5000 段（中性档），实际 {detail}"
    )
    detail_zero = resolve_commission_rate_detail(
        description_category_id=123,
        price_rub=0,
        explicit_commission=0,
        get_category_commission_fn=lambda _dc: _BAND_CACHE_ROW,
    )
    assert detail_zero["source"] == "cache:leq_5000"


def test_pricing_node_no_price_uses_neutral_band(monkeypatch):
    """CNY 店无有效汇率（_price_rub=None）→ 佣金按 leq_5000 段解析（端到端锁中性档）。"""
    # CNY 路径 _get_exchange_rate 真实现恒返 1.0（不触网）→ _price_rub=None
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: _BAND_CACHE_ROW)
    out = pn.pricing_node(_make_state(extensions=dict(_DUAL_EXT), currency="CNY"), None, _DummyRuntime())
    assert out.error_message == "", f"pricing_node 不应失败: {out.error_message}"
    assert out.pricing_info["commission_rate"] == pytest.approx(0.25), (
        f"CNY 无价场景应走 leq_5000 段 25%，实际 {out.pricing_info['commission_rate']}"
    )

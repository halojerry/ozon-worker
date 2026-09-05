"""pricing_node 佣金三重 bug 修复测试（任务 1.3，TDD）。

背景（已确认）：pricing_node 旧佣金逻辑（155-174 行）三重 bug：
  1. `price_resp.get("result", {}).get("commissions", {})` —— 顶层无 result，实际是 items[0].commissions
  2. `{"filter": {"offer_id": []}}` 空数组 → 查不到数据
  3. 结果 commission_rate 恒 0 → fallback 0.10

修复：删除 /v5/product/info/prices 空 filter 调用，改走共享解析模块
`utils/commission_resolver.resolve_commission_rate`（explicit > 缓存表 band 选段 >
extensions segments > 0.10），并用 provisional-price band pass 解决
「佣金档位依赖售价、售价依赖佣金」的鸡生蛋问题。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_node_commission.py -q
"""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import pricing_node as pn

# 独立推导的期望值（不依赖被测实现）：
# total_cost = 5.5(采购) + 10.0(mock物流) + 2.0(包装) = 17.5 CNY
# RUB(rate=12) 公式: ceil(total × 1.25 × 1.05 / (1-佣金) × 12)
#   佣金 0.10 → ceil(306.25) = 307（同时是 provisional 临时价 → 档 leq_1500）
#   佣金 0.18 → ceil(336.13) = 337
TOTAL_COST = 17.5


def _make_state(draft=None, extensions=None, currency="RUB", dc_id="17028830"):
    """构造 pricing_node 可直接消费的 state（SimpleNamespace，含 description_category_id）。"""
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
    """mock 重依赖后调用 pricing_node（纯内存，无 PG/HTTP）。

    - query_logistics_cost/get_store_logistics_config：utils.logistics_quote 模块级
    - _get_exchange_rate：pricing_node 模块级
    - get_category_commission：pricing_node 模块级（新代码 import 自 commission_resolver）
    - ozon_post：旧代码残留 API 调用点（raising=False —— 新代码已删除该 import，无此属性）
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
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: rate)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: cat_commission, raising=False)
    monkeypatch.setattr(pn, "ozon_post", lambda *a, **k: {}, raising=False)
    return pn.pricing_node(state, None, _DummyRuntime())


# ── 1. 空 filter API 调用已删除 ──
def test_pricing_node_no_empty_filter():
    """旧代码的 `{"filter": {"offer_id": []}}` 空 filter 调用必须删除（查不到数据）。"""
    src = inspect.getsource(pn)
    assert '"offer_id": []' not in src, "pricing_node 不得再调用空 offer_id filter"
    assert "/v5/product/info/prices" not in src, "pricing_node 不得再调 /v5/product/info/prices（移回 learning_record 回填）"


# ── 2. 引用共享佣金解析模块 ──
def test_pricing_node_references_resolver():
    """pricing_node 必须引用 commission_resolver.resolve_commission_rate（共享解析链）。"""
    src = inspect.getsource(pn)
    assert "resolve_commission_rate" in src, "pricing_node 必须调用 resolve_commission_rate"


# ── 3. 无任何配置 → fallback 0.10 ──
def test_pricing_node_fallback_010(monkeypatch):
    """extensions={} + 缓存表无记录 → fallback 0.10（不得抛错，价格仍计算）。"""
    out = _call_pricing(monkeypatch, _make_state(), cat_commission=None)
    assert out.error_message == "", f"不应失败: {out.error_message}"
    assert out.pricing_info["commission_rate"] == 0.10


# ── 4. extensions.commission_rate 显式配置最高优先 ──
def test_pricing_node_explicit_commission_wins(monkeypatch):
    """extensions.commission_rate=0.15 → 即使缓存表有记录也用 0.15（explicit 最高优先）。"""
    cat_commission = {"fbs_leq_1500": 8.0, "fbs_leq_5000": 12.0, "fbs_gt_5000": 18.0, "source": "what_to_sell"}
    out = _call_pricing(
        monkeypatch,
        _make_state(extensions={"commission_rate": 0.15}),
        cat_commission=cat_commission,
    )
    assert out.pricing_info["commission_rate"] == 0.15
    assert out.pricing_info["commission_rate"] != 0.10


# ── 5. 缓存表佣金流入最终价格（provisional-price band pass）──
def test_pricing_node_cache_flows_to_price(monkeypatch):
    """缓存表 fbs_leq_1500=18% + 临时价(0.10 算)≤1500 → 用 0.18，price 反映 /(1-0.18)。"""
    cat_commission = {
        "fbs_leq_1500": 18.0,
        "fbs_leq_5000": 25.0,
        "fbs_gt_5000": 30.0,
        "source": "what_to_sell",
    }
    # ✅ v0.65: 显式 margin_rate → 旧单档路径（bare 信封现默认三档，price 不再是 337）。
    # 本测试锁「佣金缓存流入最终价」，单档路径用显式 margin_rate=0.25 保持原 337 期望。
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}), cat_commission=cat_commission)
    assert out.error_message == "", f"不应失败: {out.error_message}"
    assert out.pricing_info["commission_rate"] == pytest.approx(0.18)
    assert out.pricing_info["commission_rate"] != 0.10, "不再恒 fallback 0.10"
    # 最终价 = ceil(17.5 × 1.25 × 1.05 / 0.82 × 12) = 337（若仍用 0.10 会是 307）
    assert out.pricing_info["price"] == 337, f"price 应反映 /(1-0.18) = 337，实际 {out.pricing_info['price']}"

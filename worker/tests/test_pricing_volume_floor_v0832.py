"""v0.83.2 定价体积重兜底：卡面/物流/定价三者同口径（低密度件）回归锁。

背景：prepare 上架链对低密度件按 ``ensure_volume_weight_floor``（0.40 g/cm³，只上调、
cap 原值×3）抬重后声明卡面并据此计费；PR #99 曾把 estimate 向下对齐 pricing_node 的
「未抬重」链（仅显示面），但定价若按兜底前重量算 → 低密度单系统性少收运费差。用户拍板
「价格按公式来」，终态：pricing_node ≡ estimate ≡ prepare 三处同一条重量链
（normalize→reconcile→floor）。

覆盖：
1. 低密度件：pricing_node 计费重量 == prepare 上架链 final weight（同一体积兜底口径）；
2. 低密度件：estimate ≡ pricing_node 逐字段（体积兜底开启态）；
3. 高密度件（兜底不触发）：开启/关闭兜底输出逐字相等（价格零变化回归锁）；
4. 兜底审计键 ``volume_floor_applied`` 留痕 {from,to,density_before}，未触发省略键。

运行：cd worker && PGDATABASE_URL="postgresql://postgres:ozon123@localhost:5433/ozon" \\
       PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_volume_floor_v0832.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import pricing_node as pn
from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions
from services import estimate_service
from utils import pricing_core as pc

# ── fixture：低密度（0.331 g/cm³，兜底 100→121）/ 高密度（2.5 g/cm³，兜底不触发）──
_LOW_DENSITY = {"cost_cny": 3.37, "weight": 100,
                "dimensions": {"length": 96, "width": 70, "height": 45}}
_HIGH_DENSITY = {"cost_cny": 3.37, "weight": 1200,
                 "dimensions": {"length": 100, "width": 80, "height": 60}}
_SINGLE_EXT = {"margin_rate": 0.25, "commission_rate": 0.10}


class _DummyRuntime:
    class _DummyContext:
        pass

    context = _DummyContext()


def _make_state(draft, extensions, currency="CNY"):
    return SimpleNamespace(
        draft=dict(draft), extensions=extensions, supabase_url="http://s.local", supabase_key="k",
        currency_code=currency, ozon_client_id="1", ozon_api_key="k",
        description_category_id="17028830", task_id="", tenant_id="", token="sk-test", variants=None,
    )


def _weight_rate_cost(weight, depth_cm, width_cm, height_cm, tpl="RETS", svc="Standard"):
    """重量相关物流费率（对齐 PG 行 RETS/Standard）：3.12 + 0.0364×计费重量。"""
    return (3.12 + 0.0364 * float(weight), "mock_channel", {"weight": weight})


def _mock_deps(monkeypatch):
    from utils import logistics_quote

    monkeypatch.setattr(logistics_quote, "query_logistics_cost", _weight_rate_cost)
    monkeypatch.setattr(estimate_service, "query_logistics_cost", _weight_rate_cost)
    monkeypatch.setattr(logistics_quote, "get_store_logistics_config", lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(estimate_service, "get_store_logistics_config", lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 1.0)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(estimate_service, "get_category_commission", lambda *a, **k: None)


def _capture_node_audit(monkeypatch):
    """包装 compute_pricing_core 捕获 pricing_node 的 audit_out。"""
    captured: dict = {}
    orig = pc.compute_pricing_core

    def _wrap(*a, **k):
        res = orig(*a, **k)
        audit = k.get("audit_out")
        if isinstance(audit, dict):
            captured.update(audit)
        return res

    monkeypatch.setattr(pc, "compute_pricing_core", _wrap)
    return captured


# ── 1. 低密度件：定价计费重量 == prepare 上架链 final weight ──
def test_low_density_pricing_weight_equals_prepare_final_weight(monkeypatch):
    """pricing_node 计费重量 == prepare `_resolve_weight_dimensions` 的 final weight。

    两者同序 normalize→reconcile→floor：100g（密度 0.331）→ 121g。若定价侧漏兜底
    （PR #99 的 False），计费重量会停在 100g → 少收运费差。
    """
    _mock_deps(monkeypatch)
    audit = _capture_node_audit(monkeypatch)

    prep_weight, prep_d, prep_w, prep_h = _resolve_weight_dimensions(dict(_LOW_DENSITY), None)
    assert prep_weight == 121, f"prepare final weight 应=121，实际 {prep_weight}"

    out = pn.pricing_node(_make_state(_LOW_DENSITY, _SINGLE_EXT), None, _DummyRuntime())
    assert out.error_message == "", out.error_message

    assert audit["weight_g"] == prep_weight == 121, (
        f"定价计费重量({audit['weight_g']}) != prepare final weight({prep_weight})"
    )
    # 物流按 121g 计费（不是 100g）
    assert out.pricing_info["logistics_cost_cny"] == pytest.approx(3.12 + 0.0364 * 121)
    # 结构化审计键留痕（风格对齐 prepare 的 weight_adjusted_for_volume）
    assert audit["volume_floor_applied"] == {"from": 100, "to": 121, "density_before": 0.331}
    assert out.pricing_info["wd_audit"]["volume_floor_applied"] == audit["volume_floor_applied"]


# ── 2. 低密度件：estimate ≡ pricing_node 逐字段（兜底开启态）──
def test_low_density_estimate_node_parity_floor_on(monkeypatch):
    """同一低密度信封分别过 pricing_node / estimate_from_envelope → 价格逐字段相等。"""
    _mock_deps(monkeypatch)

    out = pn.pricing_node(_make_state(_LOW_DENSITY, _SINGLE_EXT), None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    pi = out.pricing_info
    est = estimate_service.estimate_from_envelope(
        {"draft": dict(_LOW_DENSITY), "extensions": dict(_SINGLE_EXT)}, currency_code="CNY",
    )

    assert pi["logistics_cost_cny"] == pytest.approx(3.12 + 0.0364 * 121)
    assert est["logistics_cost_cny"] == round(pi["logistics_cost_cny"], 2)
    assert est["price"] == pi["price"] == 18, (est["price"], pi["price"])
    assert est["old_price"] == pi["old_price"]
    assert est["profit_cny"] == pi["profit_estimation"]["profit_cny"]
    assert est["profit_rate"] == pi["profit_estimation"]["profit_rate"]
    assert est["commission_rate"] == pi["commission_rate"]


# ── 3. 高密度件（兜底不触发）：价格逐字不变（回归锁）──
def test_high_density_floor_flag_is_noop():
    """高密度件 apply_volume_floor True/False 输出逐字相等——兜底不触发 → 零影响。"""
    draft = dict(_HIGH_DENSITY)
    common = dict(
        currency_code="CNY", tpl_provider="RETS", service_level="Standard",
        query_logistics_cost_fn=_weight_rate_cost, get_category_commission_fn=lambda *a, **k: None,
    )
    off = pc.compute_pricing_core(draft, dict(_SINGLE_EXT), apply_volume_floor=False, **common)
    on = pc.compute_pricing_core(draft, dict(_SINGLE_EXT), apply_volume_floor=True, **common)
    assert off == on, "高密度件（兜底不触发）开启兜底后输出必须逐字不变"


def test_high_density_node_price_unchanged(monkeypatch):
    """高密度件 1200g/100×80×60mm 价格锁定（兜底不触发 → 与修复前逐字一致）。"""
    _mock_deps(monkeypatch)
    audit = _capture_node_audit(monkeypatch)

    out = pn.pricing_node(_make_state(_HIGH_DENSITY, _SINGLE_EXT), None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    pi = out.pricing_info
    assert audit["weight_g"] == 1200
    assert audit["volume_floor_applied"] is None
    assert "volume_floor_applied" not in pi["wd_audit"]
    # total_cost = 3.37 + (3.12+0.0364×1200) + 2 = 52.17 → ceil(52.17×1.25/0.9) = 73
    assert pi["logistics_cost_cny"] == pytest.approx(3.12 + 0.0364 * 1200)
    assert pi["price"] == 73, pi["price"]


# ── 4. 低密度件：兜底开启确实抬价（证明开关生效，非静默）──
def test_low_density_floor_flag_raises_price():
    draft = dict(_LOW_DENSITY)
    common = dict(
        currency_code="CNY", tpl_provider="RETS", service_level="Standard",
        query_logistics_cost_fn=_weight_rate_cost, get_category_commission_fn=lambda *a, **k: None,
    )
    off = pc.compute_pricing_core(draft, dict(_SINGLE_EXT), apply_volume_floor=False, **common)
    on = pc.compute_pricing_core(draft, dict(_SINGLE_EXT), apply_volume_floor=True, **common)
    assert off["price"] == 17 and on["price"] == 18, (off["price"], on["price"])
    assert on["logistics_cost_cny"] > off["logistics_cost_cny"]

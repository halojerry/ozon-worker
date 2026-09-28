"""v0.83 批①：两个隐藏消费者回归锁 —— 采集箱预估价 + 店铺分析（语义不静默漂移）。

- ``ai_field_service._estimate_pricing``（采集箱 draft.estimated_pricing 展示价）：
  RUB 口径 + PG 汇率，走 estimate_service（现共享 pricing_core）。
- ``store_analysis_service``：单品利润率一律经 estimate_service.estimate_from_envelope
  （反漂移：禁止内联定价公式）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_estimate_consumers_v083.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest


_ENVELOPE = {
    "draft": {
        "purchase_cost": 8.5,
        "weight": 120,
        "dimensions": {"length": 150, "width": 90, "height": 60},
    },
    "extensions": {"margin_rate": 1.5, "margin_floor": 0.6, "margin_anchor": 2.0},
}


def _mock_deps(monkeypatch):
    from services import ai_field_service, estimate_service

    monkeypatch.setattr(
        estimate_service, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(estimate_service, "get_category_commission", lambda *a, **k: None)
    monkeypatch.setattr(ai_field_service, "_get_cny_rub_rate", lambda: 12.0, raising=False)
    monkeypatch.setattr(
        "utils.fx_rate_service.resolve_cny_rub_rate", lambda: (12.0, "pg_cache"), raising=False
    )


def test_ai_field_estimate_pricing_uses_estimate_service(monkeypatch):
    """采集箱预估价 = estimate_from_envelope(RUB, 12.0) 的三档投影（不内联公式）。"""
    _mock_deps(monkeypatch)
    from services import ai_field_service, estimate_service

    out = ai_field_service._estimate_pricing(_ENVELOPE)
    ref = estimate_service.estimate_from_envelope(
        _ENVELOPE, currency_code="RUB", exchange_rate=12.0
    )
    assert out is not None
    assert out["price"] == round(float(ref["price"]), 2)
    assert out["old_price"] == round(float(ref["old_price"]), 2)
    assert out["promo_price"] == round(float(ref["promo_price"]), 2)
    # 三档键在场（extensions 带 floor/anchor）
    assert isinstance(out["price"], float)


def test_ai_field_estimate_pricing_failure_returns_none(monkeypatch):
    _mock_deps(monkeypatch)
    from services import ai_field_service

    def _boom(*a, **k):
        raise RuntimeError("boom")

    # ai_field_service 顶层 `from ... import estimate_from_envelope` → patch 其命名空间
    monkeypatch.setattr(ai_field_service, "estimate_from_envelope", _boom)
    assert ai_field_service._estimate_pricing(_ENVELOPE) is None


def test_store_analysis_references_estimate_service():
    """反漂移：店铺分析利润率一律经 estimate_service.estimate_from_envelope（禁内联公式）。"""
    from services import store_analysis_service

    src = inspect.getsource(store_analysis_service)
    assert "estimate_from_envelope" in src
    assert "compute_price(" not in src, "store_analysis_service 不得内联 compute_price"

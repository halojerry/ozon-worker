#!/usr/bin/env python3
"""discover 佣金分段透传回归（v0.58 佣金分段同源链路）。

背景：_to_rate_segments（ozon_seller_analytics.py:262）解析出三段佣金
{"leq_1500","leq_5000","gt_5000"}，_extract_metrics 已暴露
commission_fbp_segments / commission_rfbs_segments——但 ProductCandidate
无字段、apply_analytics_to_candidate 不写入、build_envelope_from_discovery
不注入信封，worker 定价拿不到分段费率（只有中段标量）。

本测试锁定三段链路：
1. ProductCandidate 有 segments 字段（默认空 dict）
2. apply_analytics_to_candidate 把 metrics segments 写入 candidate
3. build_envelope_from_discovery 注入 extensions.commission_segments
   {"fbs": rfbs_segments, "fbo": fbp_segments}（rfbs→fbs / fbp→fbo 映射）
4. 无 segments 时不加该键

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_commission_segments.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts.lib.ozon_seller_analytics import apply_analytics_to_candidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402

RFBS_SEG = {"leq_1500": 8.0, "leq_5000": 6.0, "gt_5000": 4.0}
FBP_SEG = {"leq_1500": 10.0, "leq_5000": 8.0, "gt_5000": 5.0}


def _mk_candidate(**overrides) -> ProductCandidate:
    """构造 ProductCandidate（含佣金分段字段）。"""
    base = dict(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
        match_1688_url="https://detail.1688.com/offer/980815374096.html",
        competing_sellers=4,
        weight_g=0,
        dimensions_mm={},
        ozon_category={},
        commission_rfbs_segments={},
        commission_fbp_segments={},
    )
    base.update(overrides)
    c = ProductCandidate(
        ozon_product_id=base["ozon_product_id"],
        ozon_title=base["ozon_title"],
        ozon_price=base["ozon_price"],
    )
    c.match_1688_url = base["match_1688_url"]
    c.competing_sellers = base["competing_sellers"]
    c.weight_g = base["weight_g"]
    c.dimensions_mm = base["dimensions_mm"]
    c.ozon_category = base["ozon_category"]
    c.commission_rfbs_segments = base["commission_rfbs_segments"]
    c.commission_fbp_segments = base["commission_fbp_segments"]
    return c


def _mock_envelope() -> dict:
    """mock build_graph_envelope_with_retry 返回的信封。"""
    return {
        "token": "sk-test",
        "ozon_client_id": "123",
        "ozon_api_key": "key",
        "envelope": {
            "draft": {"item_id": "980815374096", "title": "x", "images": [], "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0}},
            "source": {"purchase_url": "u", "purchase_cost": 1.0},
            "extensions": {"margin_rate": 0.25, "commission_rate": 0.10, "fx_buffer": 0.05},
        },
    }


def _build(cand: ProductCandidate) -> dict:
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        return cloud_probe.build_envelope_from_discovery(cand, {"client_id": "123", "api_key": "key"})


def test_product_candidate_has_segments_fields():
    """ProductCandidate 默认有 commission_rfbs_segments / commission_fbp_segments（空 dict）。"""
    c = ProductCandidate(ozon_product_id="1", ozon_title="t", ozon_price=100.0)
    assert c.commission_rfbs_segments == {}
    assert c.commission_fbp_segments == {}


def test_apply_analytics_writes_segments():
    """metrics 含 segments → apply_analytics_to_candidate 写入 candidate。"""
    c = ProductCandidate(ozon_product_id="1", ozon_title="t", ozon_price=100.0)
    metrics = {
        "commission_rfbs_segments": dict(RFBS_SEG),
        "commission_fbp_segments": dict(FBP_SEG),
    }
    ok = apply_analytics_to_candidate(c, metrics)
    assert ok is True
    assert c.commission_rfbs_segments == RFBS_SEG
    assert c.commission_fbp_segments == FBP_SEG


def test_envelope_injects_commission_segments():
    """候选有 segments → extensions["commission_segments"] == {"fbs": rfbs, "fbo": fbp}。"""
    cand = _mk_candidate(
        commission_rfbs_segments=dict(RFBS_SEG),
        commission_fbp_segments=dict(FBP_SEG),
    )
    result = _build(cand)
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert ext["commission_segments"] == {
        "fbs": RFBS_SEG,
        "fbo": FBP_SEG,
    }


def test_envelope_omits_when_empty():
    """候选无 segments → 不注入 commission_segments 键。"""
    cand = _mk_candidate()
    result = _build(cand)
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert "commission_segments" not in ext


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{4 - failed}/4 passed")
    sys.exit(1 if failed else 0)

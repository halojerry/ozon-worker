#!/usr/bin/env python3
"""discover 信封注入竞品重量/尺寸/权威类目（worker 兜底链 C2）回归。

背景：build_envelope_from_discovery 此前只注入 ozon_category，未注入
候选品 what_to_sell 的竞品重量(4497)/尺寸(9454/9455/9456)——worker 端
_resolve_weight_dimensions（prepare_ozon_upload_node.py:1373）期望
extensions.competitor_weight_g / competitor_dimensions_mm 兜底，
缺失时退到 100g/300×200×50mm 硬编码，1688 缺数据时上品尺寸不准。

运行：
    cd skill && python3.12 tests/test_discover_envelope_competitor.py
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402


def _mk_candidate(**overrides) -> ProductCandidate:
    """构造 ProductCandidate（含竞品数据字段）。"""
    base = dict(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
        match_1688_url="https://detail.1688.com/offer/980815374096.html",
        competing_sellers=4,
        weight_g=0,
        dimensions_mm={},
        ozon_category={},
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
    return c


def _mock_envelope(result_override=None) -> dict:
    """mock build_graph_envelope_with_retry 返回的信封。"""
    env = {
        "token": "sk-test",
        "ozon_client_id": "123",
        "ozon_api_key": "key",
        "envelope": {
            "draft": {"item_id": "980815374096", "title": "x", "images": [], "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0}},
            "source": {"purchase_url": "u", "purchase_cost": 1.0},
            "extensions": {"margin_rate": 0.25, "commission_rate": 0.10, "fx_buffer": 0.05},
        },
    }
    if result_override:
        env["envelope"].update(result_override)
    return env


def test_injects_competitor_weight_and_dims():
    """候选有竞品重量/尺寸 → 注入 extensions.competitor_weight_g / competitor_dimensions_mm。"""
    cand = _mk_candidate(weight_g=227, dimensions_mm={"length": 120, "width": 80, "height": 60})
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(cand, {"client_id": "123", "api_key": "key"})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert ext["competitor_weight_g"] == 227
    assert ext["competitor_dimensions_mm"] == {"length": 120, "width": 80, "height": 60}


def test_injects_authoritative_category():
    """候选有权威类目（Seller 空间 category2_id/3_id）→ 注入 draft.ozon_category。"""
    cand = _mk_candidate(ozon_category={
        "description_category_id": "17028929",
        "type_id": "504866264",
        "language": "RU",
        "category_path": "宠物用品",
    })
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(cand, {"client_id": "123", "api_key": "key"})
    assert result is not None
    assert result["envelope"]["draft"]["ozon_category"]["description_category_id"] == "17028929"
    assert result["envelope"]["draft"]["ozon_category"]["type_id"] == "504866264"


def test_no_competitor_data_leaves_extensions_unpolluted():
    """候选无竞品数据（weight=0/dims 空）→ 不注入（避免污染）。"""
    cand = _mk_candidate()
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(cand, {"client_id": "123", "api_key": "key"})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert "competitor_weight_g" not in ext
    assert "competitor_dimensions_mm" not in ext


def test_follow_sell_flag_preserved():
    """有竞品 → follow_sell=True 且 ozon_product_id 保留（回归）。"""
    cand = _mk_candidate(competing_sellers=4)
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(cand, {"client_id": "123", "api_key": "key"})
    assert result["envelope"]["extensions"]["follow_sell"] is True
    assert result["envelope"]["draft"]["ozon_product_id"] == "4767514314"


def test_round_trip_json_serializable():
    """信封 JSON 可序列化（提交 Worker 契约）。"""
    cand = _mk_candidate(weight_g=227, dimensions_mm={"length": 120, "width": 80, "height": 60})
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(cand, {"client_id": "123", "api_key": "key"})
    json.dumps(result, ensure_ascii=False)  # 不抛异常即通过


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
    print(f"\n{6 - failed}/6 passed")
    sys.exit(1 if failed else 0)

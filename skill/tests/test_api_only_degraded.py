#!/usr/bin/env python3
"""Q2 (Lane B): build_graph_envelope api_only 降级透传回归测试。

背景：`enrich_product_with_cdp` 在浏览器探测完全失败时返回 source='api_only'
（data 中仍带 1688 API 的 title/price/images）。`build_graph_envelope` 此前
在 cloud_probe.py:1235-1239 直接 `raise RuntimeError`——即使 API 数据齐全也
必然失败，且 `build_graph_envelope_with_retry` 会带着 15s+ backoff 空转 3 次。

修复：api_only 不再 raise，降级透传组装 API 数据信封；数据质量仍由
`_validate_and_fix_product_data` 校验门把关（weight=0 → 50g 软兜底；
images/price/title 全空 → ProductValidationError 硬阻断）。

运行：
    cd skill && .venv314/bin/python tests/test_envelope_api_only_passthrough.py
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_envelope_api_only_passthrough.py -v
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402

ITEM_ID = "980815374096"
DETAIL_URL = f"https://detail.1688.com/offer/{ITEM_ID}.html"
ALICDN_IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"


def _api_enriched(**overrides) -> dict:
    """构造 enrich_product_with_cdp 的 api_only 返回（data 仅含 API 数据）。"""
    data = {
        "title": "宠物自动饮水器 2L",
        "price": "5.50",
        "brand": "",
        "seller": "",
        "images": [ALICDN_IMG],
        "weight_grams": None,
        "packaging_rows": [],
        "shipping": {},
        "description": "",
        "sku_details": [],
        "attributes": [],
        "option_groups": [],
        "category_id": "",
    }
    data.update(overrides.pop("data", {}))
    base = {
        "ok": False,
        "degraded": True,
        "degraded_reason": "浏览器探测失败: mock api_only",
        "user_action": None,
        "data": data,
        "source": "api_only",
    }
    base.update(overrides)
    return base


def _build(enriched: dict, api_data: dict | None = None, **kw) -> dict:
    """以 api_only enrich 结果调用 build_graph_envelope（mock 全部外部依赖）。"""
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: api_data or {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=enriched), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "123", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"):
        return cloud_probe.build_graph_envelope(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            poll_category=False,
            **kw,
        )


def test_api_only_builds_envelope_no_raise():
    """api_only（API 数据齐全）→ 不再 raise，组装完整 GraphInput 信封。"""
    graph = _build(_api_enriched())
    assert graph is not None
    assert graph["token"] == "sk-test"
    assert graph["ozon_client_id"] == "123"
    draft = graph["envelope"]["draft"]
    assert draft["item_id"] == ITEM_ID
    assert draft["title"] == "宠物自动饮水器 2L"
    assert draft["purchase_cost"] == 5.5
    assert draft["images"] == [ALICDN_IMG]
    assert graph["envelope"]["source"]["purchase_url"] == DETAIL_URL
    # Q2 acceptance: degraded 标记置位
    assert graph["envelope"]["extensions"].get("cdp_degraded") is True


def test_api_only_weight_zero_soft_fallback():
    """api_only 无重量/尺寸 → _validate_and_fix_product_data 软兜底 50g + 估算尺寸。"""
    graph = _build(_api_enriched())
    draft = graph["envelope"]["draft"]
    assert draft["weight"] == 50, f"重量缺失应软兜底 50g，实际 {draft['weight']}"
    d = draft["dimensions"]
    assert d["length"] > 0 and d["width"] > 0 and d["height"] > 0
    assert draft.get("dimensions_estimated") is True


def test_api_only_empty_data_still_gated():
    """api_only 但 API 数据全空（无图片/价格/标题）→ ProductValidationError 硬阻断。"""
    enriched = _api_enriched(data={
        "title": "", "price": "", "images": [],
    })
    try:
        _build(enriched)
    except cloud_probe.ProductValidationError as e:
        assert "图片为空" in str(e) or "采购价格" in str(e) or "标题为空" in str(e)
        return
    raise AssertionError("api_only 空数据应被 ProductValidationError 阻断，实际未抛异常")


def test_with_retry_returns_api_only_envelope_without_retry():
    """build_graph_envelope_with_retry：api_only 不再抛 RuntimeError → 首次即返回，不空转重试。"""
    real_build = cloud_probe.build_graph_envelope
    calls = {"n": 0}

    def _spy(*_a, **_kw):
        calls["n"] += 1
        with mock.patch.object(cloud_probe, "build_graph_envelope", real_build):
            return _build(_api_enriched())

    with mock.patch.object(cloud_probe, "build_graph_envelope", side_effect=_spy):
        graph = cloud_probe.build_graph_envelope_with_retry(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            max_retries=3,
            retry_delay=0.0,
        )
    assert graph is not None
    assert calls["n"] == 1, f"api_only 不再 raise → 不应重试，实际调用 {calls['n']} 次"
    assert graph["envelope"]["draft"]["title"] == "宠物自动饮水器 2L"


def test_with_retry_integration_real_build():
    """真实 build_graph_envelope（api_only enrich）跑在 with_retry 内 → 首次成功返回信封。"""
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=_api_enriched()), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "123", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"):
        graph = cloud_probe.build_graph_envelope_with_retry(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            max_retries=3,
            retry_delay=0.0,
        )
    assert graph is not None
    draft = graph["envelope"]["draft"]
    assert draft["title"] == "宠物自动饮水器 2L"
    assert draft["purchase_cost"] == 5.5


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

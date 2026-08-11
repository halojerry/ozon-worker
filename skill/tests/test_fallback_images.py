#!/usr/bin/env python3
"""P4: build_graph_envelope fallback_images 兜底。

背景：1688 api_only 降级时图片可能为空 → _validate_and_fix_product_data 硬阻断
「产品图片为空」→ follow 链路 envelope 构建失败。跟卖时 Ozon 竞品主图是安全兜底。

修复：build_graph_envelope 新增 fallback_images 参数，get_best_product_images 结果
为空且提供 fallback_images 时用兜底图（放行 images 校验门）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_fallback_images.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402

ITEM_ID = "980815374096"
DETAIL_URL = f"https://detail.1688.com/offer/{ITEM_ID}.html"
FALLBACK = ["https://x/1.jpg"]


def _api_enriched(**overrides) -> dict:
    """api_only enrich 结果（1688 图片为空 → 校验门会因图片为空而阻断）。"""
    data = {
        "title": "宠物自动饮水器 2L",
        "price": "5.50",
        "brand": "",
        "seller": "",
        "images": [],
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


def _build(enriched: dict, fallback_images: list[str] | None) -> dict:
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=enriched), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "123", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        return cloud_probe.build_graph_envelope(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            poll_category=False,
            fallback_images=fallback_images,
        )


def test_fallback_images_used_when_1688_images_empty():
    """1688 图片为空 + fallback_images → draft.images == fallback_images（放行校验门）。"""
    graph = _build(_api_enriched(), fallback_images=FALLBACK)
    draft = graph["envelope"]["draft"]
    assert draft["images"] == FALLBACK, draft["images"]


def test_no_fallback_still_raises_validation_error():
    """1688 图片为空且无 fallback_images → 仍 ProductValidationError（行为不变）。"""
    try:
        _build(_api_enriched(), fallback_images=None)
    except cloud_probe.ProductValidationError as e:
        assert "图片为空" in str(e), str(e)
        return
    raise AssertionError("无 fallback_images 时应 ProductValidationError，实际未抛")


def test_fallback_ignored_when_1688_images_present():
    """1688 有真实图片 → 不用 fallback（真实图片优先）。"""
    from tests.test_api_only_degraded import ALICDN_IMG

    enriched = _api_enriched(data={"images": [ALICDN_IMG]})
    graph = _build(enriched, fallback_images=FALLBACK)
    draft = graph["envelope"]["draft"]
    assert draft["images"] == [ALICDN_IMG], draft["images"]


if __name__ == "__main__":
    import traceback

    failed = total = 0
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

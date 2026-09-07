#!/usr/bin/env python3
"""串图修复回归：1688 图空必须阻断，绝不用兜底图放行（fix/image-ref-pollution）。

背景（旧 P4 行为已废除）：1688 api_only 降级时图片为空 → fallback_images 用
Ozon 竞品主图放行「产品图片为空」校验门 → 竞品图进 draft.images 做生图参考
（AI 重绘出竞品样子）+ E1 兜底直上（竞品原图上卡）——线上「产品A卡片出现
产品B图」根因之一。

新行为：1688 图空即 ProductValidationError 阻断，宁不出单不上错图；
follow 竞品主图只进 extensions.competitor_ref_images（识别用，不进 draft.images）。

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


def _build(enriched: dict) -> dict:
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
        )


def test_empty_images_blocked_not_fallback():
    """1688 图片为空 → ProductValidationError 阻断（P4 竞品图兜底已废除）。"""
    try:
        _build(_api_enriched())
    except cloud_probe.ProductValidationError as e:
        assert "图片为空" in str(e), str(e)
        return
    raise AssertionError("1688 图空应 ProductValidationError 阻断，实际未抛")


def test_real_images_pass_through():
    """1688 有真实图片 → draft.images == 真实图片（正常路径不回归）。"""
    from tests.test_api_only_degraded import ALICDN_IMG

    enriched = _api_enriched(data={"images": [ALICDN_IMG]})
    graph = _build(enriched)
    draft = graph["envelope"]["draft"]
    assert draft["images"] == [ALICDN_IMG], draft["images"]


def test_build_graph_envelope_has_no_fallback_param():
    """build_graph_envelope/with_retry 不再接受 fallback_images（防止竞品图兜底回归）。"""
    import inspect
    for fn in (cloud_probe.build_graph_envelope,
               cloud_probe.build_graph_envelope_with_retry):
        assert "fallback_images" not in inspect.signature(fn).parameters, \
            f"{fn.__name__} 不得恢复 fallback_images 参数（串图根因之一）"


def _main() -> int:
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


if __name__ == "__main__":
    _main()

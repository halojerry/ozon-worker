#!/usr/bin/env python3
"""串图修复回归：禁止「别家商品图」兜底进货源图（fix/image-ref-pollution）。

背景：线上多单「产品A卡片出现产品B图」——
  L2 毒源: get_product_details 在 AK API 详情无图时用标题搜索
  search_results[0] 的别家商品图顶数；多商品标题解析失败 → 同 query 命中
  ak_search 磁盘缓存 → 跨任务逐字节相同的别家 310x310 缩略图进信封 →
  生图参考（AI 重绘别家）/ E1 兜底（别家原图直上）双出口上卡。

修复原则：宁缺毋滥——详情无图就空 images，绝拿别家商品图顶；
无图由上层校验门（skill「产品图片为空」）拦截，不组装信封。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_no_foreign_image_fallback.py -q
    cd skill && .venv314/bin/python tests/test_no_foreign_image_fallback.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.ak_1688_client  # noqa: F401  isort:skip


def _enter(mocks):
    stack = ExitStack()
    for m in mocks:
        stack.enter_context(m)
    return stack


def _detail_mocks(post_result, cache_dir=None):
    """get_product_details 通用 mock：认证 + offer_detail 响应受控 + 缓存重定向。"""
    return [
        mock.patch("scripts.lib.config_store._require_auth", return_value=None),
        mock.patch("scripts.lib.ak_1688_client._post_1688", return_value=dict(post_result)),
        mock.patch("scripts.lib.cache.CACHE_DIR", cache_dir or Path(tempfile.mkdtemp()) / "cache"),
    ]


# offer_detail 响应：all_info 不含任何图片 URL（模拟 API 无图/解析退化的 offer）
NO_IMAGE_BIZ = {
    "model": {"bizData": {
        "123456": {"all_info": "标题：加厚瑜伽垫\n价格：25.00\n起批量：2件"},
    }},
}
# offer_detail 响应：raw 带正规主图列表（正常路径）
WITH_IMAGE_BIZ = {
    "model": {"bizData": {
        "123456": {
            "all_info": "标题：加厚瑜伽垫",
            "images": ["https://cbu01.alicdn.com/img/ibank/O1CNxx_!!123-0-cib.jpg"],
        },
    }},
}
# 标题搜索返回的「别家商品」候选（旧兜底逻辑会取它的图）
FOREIGN_MATCH = [{
    "product_id": "999888",
    "title": "完全无关的别家爆款商品",
    "image_url": "https://cbu01.alicdn.com/img/ibank/O1CNforeign_!!2208080228339-0-cib.310x310.jpg",
}]


def test_detail_no_image_never_search_fallback():
    """详情无图 → images 返回空，绝不调 search_products 拿别家商品图顶。"""
    from scripts.lib.ak_1688_client import get_product_details
    search_calls = []

    def _fake_search(*args, **kwargs):
        search_calls.append(args)
        return [dict(m) for m in FOREIGN_MATCH]

    mocks = _detail_mocks(NO_IMAGE_BIZ)
    mocks.append(mock.patch("scripts.lib.ak_1688_client.search_products",
                            side_effect=_fake_search))
    with _enter(mocks):
        details = get_product_details(["123456"])
    assert details["123456"]["images"] == [], \
        f"详情无图应返回空 images，实际: {details['123456']['images']}"
    assert not search_calls, \
        f"详情无图不得触发标题搜索兜底（会把别家商品图串进信封），实际搜索 {len(search_calls)} 次"


def test_detail_with_images_unchanged():
    """raw 带正规主图 → images 正常提取（正常路径不回归）。"""
    from scripts.lib.ak_1688_client import get_product_details
    mocks = _detail_mocks(WITH_IMAGE_BIZ)
    mocks.append(mock.patch("scripts.lib.ak_1688_client.search_products"))
    with _enter(mocks):
        details = get_product_details(["123456"])
    imgs = details["123456"]["images"]
    assert len(imgs) == 1 and "alicdn.com" in imgs[0]


def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())

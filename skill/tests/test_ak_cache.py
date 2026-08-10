#!/usr/bin/env python3
"""AK 搜索缓存单测（Q9：search_by_image + search_products 磁盘缓存）。

Q9: 防 follow/discover 各自重复调用 1688 AK API 耗配额——
  - search_by_image（imageUrl 模式）: cache_get/cache_set("ak_img_search", image_url, ttl=21600)
  - search_products: cache_get/cache_set("ak_search", query+规范化params, ttl=86400)
  - 均仅缓存非空结果（空列表/瞬时失败不写缓存）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_ak_cache.py -q
    cd skill && .venv314/bin/python tests/test_ak_cache.py
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


def _search_mocks(post_side_effect, cache_dir=None):
    """通用 mock：认证 + _post_1688 网络层受控 + 缓存目录重定向到临时目录。"""
    return [
        mock.patch("scripts.lib.config_store._require_auth", return_value=None),
        mock.patch("scripts.lib.ak_1688_client._post_1688", side_effect=post_side_effect),
        mock.patch("scripts.lib.cache.CACHE_DIR", cache_dir or Path(tempfile.mkdtemp()) / "cache"),
    ]


# 模拟 1688 API 返回的单条商品（_parse_product_item 消费 itemId/title/imageUrl/currentPrice/score）
SAMPLE_ITEM = {
    "itemId": "123456",
    "title": "测试商品",
    "imageUrl": "https://img.example.com/1.jpg",
    "currentPrice": "10.00",
    "score": 0.9,
}
NON_EMPTY_RESULT = {"data": {"data": [SAMPLE_ITEM]}}
EMPTY_RESULT = {"data": {"data": []}}


# ═══════════════════════════════════════════════════════════════════════
# search_products（关键词）缓存
# ═══════════════════════════════════════════════════════════════════════

def test_search_products_same_key_cached():
    """同 query+规范化参数 → 二次调用不触发网络请求（_post_1688 只调一次）。"""
    from scripts.lib.ak_1688_client import search_products
    calls = []

    def post(path, body):
        calls.append(dict(body))
        return dict(NON_EMPTY_RESULT)

    with _enter(_search_mocks(post)):
        first = search_products("宠物饮水机", page=1, page_size=20)
        second = search_products("宠物饮水机", page=1, page_size=20)
    assert first == second
    assert len(first) == 1
    assert len(calls) == 1, f"同 key 二次调用应命中缓存，实际网络请求 {len(calls)} 次"


def test_search_products_diff_params_different_key():
    """不同 page/参数 → 各自独立缓存，网络各调一次。"""
    from scripts.lib.ak_1688_client import search_products
    calls = []

    def post(path, body):
        calls.append(dict(body))
        return dict(NON_EMPTY_RESULT)

    with _enter(_search_mocks(post)):
        search_products("宠物饮水机", page=1)
        search_products("宠物饮水机", page=2)
    assert len(calls) == 2, "不同 page 应分别请求网络"


def test_search_products_empty_not_cached():
    """空结果不缓存 → 二次调用重新请求网络（防缓存污染）。"""
    from scripts.lib.ak_1688_client import search_products
    calls = []

    def post(path, body):
        calls.append(dict(body))
        return dict(EMPTY_RESULT)

    with _enter(_search_mocks(post)):
        first = search_products("无结果关键词")
        second = search_products("无结果关键词")
    assert first == [] and second == []
    assert len(calls) == 2, "空结果不应写缓存"


# ═══════════════════════════════════════════════════════════════════════
# search_by_image（以图搜款）缓存
# ═══════════════════════════════════════════════════════════════════════

def test_search_by_image_url_cached():
    """同 image_url → 二次调用不触发网络请求（_post_1688 只调一次）。"""
    from scripts.lib.ak_1688_client import search_by_image
    calls = []

    def post(path, body):
        calls.append(dict(body))
        return dict(NON_EMPTY_RESULT)

    with _enter(_search_mocks(post)):
        first = search_by_image(image_url="https://img.example.com/search.jpg")
        second = search_by_image(image_url="https://img.example.com/search.jpg")
    assert first == second
    assert len(first) == 1
    assert len(calls) == 1, f"同 image_url 二次调用应命中缓存，实际网络请求 {len(calls)} 次"


def test_search_by_image_empty_not_cached():
    """空结果不缓存 → 二次调用重新请求网络。"""
    from scripts.lib.ak_1688_client import search_by_image
    calls = []

    def post(path, body):
        calls.append(dict(body))
        return dict(EMPTY_RESULT)

    with _enter(_search_mocks(post)):
        first = search_by_image(image_url="https://img.example.com/empty.jpg")
        second = search_by_image(image_url="https://img.example.com/empty.jpg")
    assert first == [] and second == []
    assert len(calls) == 2, "空结果不应写缓存"


def test_search_by_image_local_path_not_cached():
    """本地图片（image_path）模式不按 URL 缓存 → 两次均走网络。"""
    from scripts.lib.ak_1688_client import search_by_image
    calls = []

    def post(path, body):
        calls.append(dict(body))
        return dict(NON_EMPTY_RESULT)

    mocks = _search_mocks(post)
    mocks += [
        mock.patch("scripts.lib.image_preprocessor.preprocess_image",
                   return_value={"type": "local", "path": "/tmp/fake.jpg", "converted": False}),
        mock.patch("scripts.lib.image_preprocessor.image_to_base64", return_value="aGVsbG8="),
    ]
    with _enter(mocks):
        search_by_image(image_path="/tmp/fake.jpg")
        search_by_image(image_path="/tmp/fake.jpg")
    assert len(calls) == 2, "本地图片模式无法复用 URL 缓存，应每次都请求网络"


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

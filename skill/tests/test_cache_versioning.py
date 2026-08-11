#!/usr/bin/env python3
"""缓存版本指纹单测（Task: cache-version fingerprint）。

背景：升级 skill 后旧版本缓存的抓取/翻译/图搜结果可能污染新版本逻辑
（"cache poisoning after code upgrades"）。cache.py 引入模块级
_CACHE_VERSION 指纹——_cache_path 对 key 哈希前拼上版本号，
版本一变 → sha256 全变 → 全部 14 个命名空间一次性失效。

关键点：
  - 版本源是 skill/VERSION（当前 0.37.0），**不是** _const.SKILL_VERSION
    （0.4.0，已过期残留）。
  - 测试用 mock.patch.object(cache, "_cache_version", ...) 控制版本，
    不写仓库 VERSION 文件。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_cache_versioning.py -q
    cd skill && .venv314/bin/python tests/test_cache_versioning.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_cache_path_differs_across_versions():
    """同 (ns, key) 在不同版本指纹下 → 哈希路径不同（版本变更即缓存失效）。"""
    from scripts.lib import cache

    ns, key = "probe1688", "https://detail.1688.com/offer/980815374096.html"
    with mock.patch.object(cache, "_cache_version", return_value="0.37.0"):
        p1 = cache._cache_path(ns, key)
    with mock.patch.object(cache, "_cache_version", return_value="0.38.0"):
        p2 = cache._cache_path(ns, key)
    assert p1 != p2, f"不同版本应产生不同缓存路径，实际 {p1} == {p2}"
    assert p1.parent == p2.parent, "仅文件名应变化，命名空间目录不变"


def test_cache_miss_after_version_bump():
    """cache_set 后同版本 cache_get 命中；版本 bump → cache_get 返回 None。"""
    from scripts.lib import cache

    ns, key = "slug_cn", "палочки-от-комаров"
    with tempfile.TemporaryDirectory() as td, \
         mock.patch("scripts.lib.cache.CACHE_DIR", Path(td)), \
         mock.patch.object(cache, "_cache_version", return_value="0.37.0"):
        cache.cache_set(ns, key, {"cn": "驱蚊棒"})
        assert cache.cache_get(ns, key) == {"cn": "驱蚊棒"}, "同版本应命中"
    with mock.patch.object(cache, "_cache_version", return_value="0.38.0"):
        assert cache.cache_get(ns, key) is None, "版本变更后旧缓存应 miss"


def test_cache_version_reads_repo_version_not_stale_const():
    """_cache_version() 读 skill/VERSION（0.37.0），与过期 SKILL_VERSION（0.4.0）不同。"""
    from scripts._const import SKILL_ROOT, SKILL_VERSION
    from scripts.lib import cache

    cache._CACHE_VERSION = None  # 重置惰性缓存，强制真实读文件
    try:
        v = cache._cache_version()
    finally:
        cache._CACHE_VERSION = None
    real = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert v == real == "0.37.0", f"应读 skill/VERSION，实际 {v!r}，文件 {real!r}"
    assert v != SKILL_VERSION, f"禁止用过期的 _const.SKILL_VERSION（{SKILL_VERSION}）"


def test_cache_version_fallback_on_read_error():
    """VERSION 读取失败 → 兜底 "0"（不抛异常，缓存退化为无指纹）。"""
    from scripts.lib import cache

    cache._CACHE_VERSION = None
    try:
        with mock.patch("pathlib.Path.read_text", side_effect=OSError("boom")):
            assert cache._cache_version() == "0", "读取异常应兜底 '0'"
    finally:
        cache._CACHE_VERSION = None


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

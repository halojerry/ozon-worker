#!/usr/bin/env python3
"""N3 (P1-3) + N7 (P2-7): cleanup 命令回归测试（TDD）。

① _cleanup_profile_cache：白名单可再生目录被删、登录态（Cookies/Local Storage/
   Login Data/Preferences）保留；Chrome 运行时跳过（返回 skipped_chrome_running=1）。
② --temp：.json.tmp 孤儿文件删除，正常 .json 保留。
③ --old-results：早于 N 天的文件删除，近期保留（os.utime mock mtime）。
④ --cache：cache_clear(None) 被调用。
⑤ cache.py safe_unlink 修复：Windows 锁定文件 safe_unlink 失败 → cache_get
   优雅返回 None，不崩溃。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_cleanup.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── ① _cleanup_profile_cache ──

def test_profile_cache_removes_whitelisted_keeps_login():
    """白名单可再生目录全部删除；登录态文件（Cookies/Local Storage/Login Data/Preferences）保留。"""
    from scripts import cli

    tmp = Path(tempfile.mkdtemp(prefix="cleanup_profile_"))
    default = tmp / "Default"
    default.mkdir()
    for rel in cli._PROFILE_CACHE_DIRS:
        d = tmp / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "junk.bin").write_bytes(b"x" * 16)
    # 登录态文件（必须保留）
    (default / "Cookies").write_bytes(b"cookie")
    (default / "Local Storage").mkdir()
    (default / "Local Storage" / "leveldb").mkdir()
    (default / "Login Data").write_bytes(b"login")
    (default / "Preferences").write_bytes(b"{}")

    with mock.patch("scripts.lib.chrome_launcher._find_chrome_processes", return_value=[]):
        r = cli._cleanup_profile_cache(str(tmp))

    assert r["removed"] == len(cli._PROFILE_CACHE_DIRS), f"应删除全部白名单目录: {r}"
    assert r["errors"] == 0, r
    assert r["skipped_chrome_running"] == 0, r
    for rel in cli._PROFILE_CACHE_DIRS:
        assert not (tmp / rel).exists(), f"可再生目录应被删除: {rel}"
    assert (default / "Cookies").read_bytes() == b"cookie"
    assert (default / "Local Storage").is_dir()
    assert (default / "Login Data").read_bytes() == b"login"
    assert (default / "Preferences").read_bytes() == b"{}"


def test_profile_cache_skipped_when_chrome_running():
    """Chrome 进程运行中 → 跳过清理（缓存被锁定），白名单目录保持不动。"""
    from scripts import cli

    tmp = Path(tempfile.mkdtemp(prefix="cleanup_profile_running_"))
    cache_dir = tmp / "Default" / "Cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "junk.bin").write_bytes(b"x")

    with mock.patch("scripts.lib.chrome_launcher._find_chrome_processes",
                    return_value=[{"pid": 1234, "cmd": "Google Chrome", "port": None}]):
        r = cli._cleanup_profile_cache(str(tmp))

    assert r["skipped_chrome_running"] == 1, r
    assert r["removed"] == 0, r
    assert (tmp / "Default" / "Cache").is_dir(), "Chrome 运行时不应删除缓存"


# ── ② --temp ──

def test_cleanup_temp_removes_orphan_tmp_keeps_json():
    """孤儿 .json.tmp 删除；正常 .json 保留。"""
    from scripts import cli

    tmp = Path(tempfile.mkdtemp(prefix="cleanup_tmp_"))
    cache_ns = tmp / "cache" / "probe1688"
    cache_ns.mkdir(parents=True)
    orphan = cache_ns / "abc123.json.tmp"
    orphan.write_text("{}")
    keep = cache_ns / "def456.json"
    keep.write_text('{"value": 1}')

    with mock.patch("scripts.lib.task_paths.cleanup_old_files",
                    return_value={"deleted": 0, "bytes_freed": 0, "errors": 0}):
        r = cli._cleanup_temp_files(scan_dirs=[tmp])

    assert r["removed"] == 1, r
    assert not orphan.exists(), "孤儿 .json.tmp 应被删除"
    assert keep.exists(), "正常 .json 应保留"


# ── ③ --old-results ──

def test_cleanup_old_results_removes_old_keeps_recent():
    """早于 N 天的文件删除；近期文件保留。"""
    from scripts import cli

    tmp = Path(tempfile.mkdtemp(prefix="cleanup_results_"))
    old = tmp / "old.csv"
    old.write_text("a,b\n")
    recent = tmp / "recent.csv"
    recent.write_text("c,d\n")
    now = time.time()
    os.utime(old, (now - 40 * 86400, now - 40 * 86400))
    os.utime(recent, (now - 1000, now - 1000))

    r = cli._cleanup_old_results(30, scan_dirs=[tmp])

    assert r["removed"] == 1, r
    assert r["errors"] == 0, r
    assert not old.exists(), "过期文件应被删除"
    assert recent.exists(), "近期文件应保留"


# ── ④ --cache ──

def test_cleanup_cache_calls_cache_clear():
    """--cache → cache_clear(None)（全部命名空间）。"""
    from scripts import cli

    with mock.patch("scripts.lib.cache.cache_clear", return_value=42) as m_clear:
        r = cli._cleanup_cache()
    m_clear.assert_called_once_with(None)
    assert r["removed"] == 42


# ── ⑤ cache_get safe_unlink（Windows 锁定不崩溃）──

def test_cache_get_locked_file_still_returns_none():
    """safe_unlink 失败（Windows 沙箱锁定）→ cache_get 优雅返回 None，绝不 raise。"""
    from scripts.lib import cache

    tmp = Path(tempfile.mkdtemp(prefix="cache_locked_"))
    expired = tmp / "expired.json"
    expired.write_text(json.dumps({"value": 1, "created_at": 0, "expires_at": 0}), encoding="utf-8")

    with mock.patch("scripts.lib.cache._cache_path", return_value=expired), \
         mock.patch("scripts.lib.cache.safe_unlink", return_value=False) as m_unlink:
        result = cache.cache_get("ns", "key")

    assert result is None, "锁定文件无法删除 → 返回 None 而非崩溃"
    m_unlink.assert_called_once_with(expired)


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

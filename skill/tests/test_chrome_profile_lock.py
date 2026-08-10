#!/usr/bin/env python3
"""Q15: chrome_launcher per-profile 启动并发锁回归测试。

背景：原 ensure_chrome_cdp 用 tempdir 全局锁 + flock LOCK_NB（非阻塞）——
第二个并发进程拿不到锁直接抛 BlockingIOError（未被捕获 → ensure_chrome_cdp
崩溃），且锁与 profile 无关（不同 profile 无谓串行）。

修复：per-profile 锁（data/browser/.profile-{name}.lock，仿 updater.py
data/.update.lock 模式）+ 阻塞等待带超时 + 超时优雅降级（返回错误信息而非抛异常）。

运行：
    cd skill && .venv314/bin/python tests/test_chrome_profile_lock.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import chrome_launcher as cl  # noqa: E402


def _lock_path_for(prof: Path) -> Path:
    """测试用锁路径：放在 profile 同目录下（不写真实 data/browser/）。"""
    return prof / ".test-profile.lock"


def test_profile_lock_path_standard_structure():
    """标准结构 data/browser/profiles/<name>/<sub> → data/browser/.profile-<name>.lock。"""
    p = cl._profile_lock_path(Path("/x/data/browser/profiles/1688/default"))
    assert p.name == ".profile-1688.lock"
    assert p.parent.name == "browser"


def test_profile_lock_path_custom_dir_unique():
    """非标准路径取末两段（父目录-目录名），不同 profile 互不冲突。"""
    a = cl._profile_lock_path(Path("/tmp/prof_a/run"))
    b = cl._profile_lock_path(Path("/tmp/prof_b/run"))
    assert a != b
    assert a.name.startswith(".profile-")


def test_acquire_release_roundtrip():
    """获取 → 释放 → 可再次获取。"""
    prof = Path(tempfile.mkdtemp(prefix="lock_a_"))
    fd1 = cl._try_acquire_lock(_lock_path_for(prof), timeout=5)
    assert fd1 is not None
    cl._release_lock(fd1)
    fd2 = cl._try_acquire_lock(_lock_path_for(prof), timeout=5)
    assert fd2 is not None
    cl._release_lock(fd2)


def test_second_acquire_times_out_gracefully():
    """锁被占用 → 超时返回 None（不抛异常，原 LOCK_NB 代码直接崩）。"""
    prof = Path(tempfile.mkdtemp(prefix="lock_b_"))
    fd1 = cl._try_acquire_lock(_lock_path_for(prof), timeout=5)
    assert fd1 is not None
    t0 = time.monotonic()
    fd2 = cl._try_acquire_lock(_lock_path_for(prof), timeout=0.3)
    elapsed = time.monotonic() - t0
    assert fd2 is None  # 不抛 BlockingIOError
    assert elapsed < 5  # 按时超时返回，非无限等待
    cl._release_lock(fd1)


def test_per_profile_isolation():
    """不同 profile 的锁互不阻塞。"""
    prof_a = Path(tempfile.mkdtemp(prefix="lock_c_"))
    prof_b = Path(tempfile.mkdtemp(prefix="lock_d_"))
    fd1 = cl._try_acquire_lock(_lock_path_for(prof_a), timeout=5)
    assert fd1 is not None
    fd2 = cl._try_acquire_lock(_lock_path_for(prof_b), timeout=1)
    assert fd2 is not None  # profile B 不被 profile A 阻塞
    cl._release_lock(fd1)
    cl._release_lock(fd2)


def test_ensure_chrome_cdp_lock_timeout_graceful():
    """锁获取超时 → ensure_chrome_cdp 返回 (False, 提示) 而非抛异常。"""
    tmp = tempfile.mkdtemp(prefix="lock_e_")
    with mock.patch("scripts.lib.chrome_launcher._try_acquire_lock", return_value=None), \
         mock.patch("scripts.lib.chrome_launcher._is_cdp_available", return_value=False), \
         mock.patch("scripts.lib.chrome_launcher._find_chrome_executable", return_value="/usr/bin/fake-chrome"):
        ok, msg = cl.ensure_chrome_cdp(port=9299, profile_dir=tmp)
    assert ok is False
    assert ("锁" in msg) or ("超时" in msg)


def test_ensure_chrome_cdp_uses_per_profile_lock():
    """ensure_chrome_cdp 必须用 per-profile 锁路径（Q15 契约，替代全局 tempdir 锁）。"""
    tmp = tempfile.mkdtemp(prefix="lock_f_")
    captured: dict = {}

    def fake_acquire(lock_path, timeout=30.0):
        captured["path"] = lock_path
        return None

    with mock.patch("scripts.lib.chrome_launcher._try_acquire_lock", side_effect=fake_acquire), \
         mock.patch("scripts.lib.chrome_launcher._is_cdp_available", return_value=False), \
         mock.patch("scripts.lib.chrome_launcher._find_chrome_executable", return_value="/usr/bin/fake-chrome"):
        cl.ensure_chrome_cdp(port=9299, profile_dir=tmp)
    assert captured["path"] == cl._profile_lock_path(Path(tmp))


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

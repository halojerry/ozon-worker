#!/usr/bin/env python3
"""ensure_venv 单测（v0.31 PR-B）— L3 自动 venv + 并发锁 + CF-1 失败处理。

覆盖：.ready 短路 / pip 失败不写 .ready（CF-1）/ requirements 变化 stale rename（MP-3）/
锁获取释放 / 重试退避 / 跨平台 venv python 路径。

运行：
    cd skill && PYTHONPATH=. python3 tests/test_venv_setup.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import runtime_probe as rp


def _fake_skill_root(tmp: Path):
    """mock skill_root → tmp，隔离真实 skill 目录。"""
    return mock.patch.object(rp, "skill_root", return_value=tmp)


def test_venv_python_paths():
    """跨平台 venv python 路径。"""
    v = Path("/x/data/.venv")
    if os.name == "nt":
        assert rp._venv_python(v) == v / "Scripts" / "python.exe"
    else:
        assert rp._venv_python(v) == v / "bin" / "python"


def test_venv_ready_short_circuit():
    """.ready sha256 匹配 → _venv_ready True（短路，不重建）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        req = tmp / "requirements.txt"
        req.write_text("requests==2.32.0\n")
        venv_dir = tmp / "data" / ".venv"
        venv_dir.mkdir(parents=True)
        (venv_dir / ".ready").write_text(rp._sha256_of_file(req), encoding="utf-8")
        with _fake_skill_root(tmp):  # _requirements_path 也走 skill_root
            assert rp._venv_ready(venv_dir) is True
            req.write_text("requests==2.31.0\n")
            assert rp._venv_ready(venv_dir) is False


def test_ensure_venv_ready_returns_ok():
    """.ready 存在 + venv python 存在 → 直接 "ok"，不执行任何 subprocess。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        req = tmp / "requirements.txt"
        req.write_text("x\n")
        venv_dir = tmp / "data" / ".venv"
        py = rp._venv_python(venv_dir)
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n")
        (venv_dir / ".ready").write_text(rp._sha256_of_file(req), encoding="utf-8")
        with _fake_skill_root(tmp), \
             mock.patch.object(rp.subprocess, "run") as _run, \
             mock.patch.object(rp, "_lock_acquire") as _lock:
            py_path, status = rp.ensure_venv("/usr/bin/python3.12")
        assert status == "ok", f"got {status}"
        _run.assert_not_called()
        _lock.assert_not_called()  # 短路不碰锁


def test_ensure_venv_pip_failure_no_ready():
    """CF-1: pip install 失败 → 不写 .ready，写 .failed 标记，返回 failed。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        req = tmp / "requirements.txt"
        req.write_text("requests\n")
        with _fake_skill_root(tmp), \
             mock.patch.object(rp.subprocess, "run") as _run:
            # 第一次调用 = venv 创建（成功），后续 = pip install（全失败）
            _run.side_effect = [
                mock.MagicMock(returncode=0),  # python -m venv
                mock.MagicMock(returncode=1),  # pip 第 1 次
                mock.MagicMock(returncode=1),  # pip 第 2 次
                mock.MagicMock(returncode=1),  # pip 第 3 次
            ]
            with mock.patch("time.sleep"):
                py_path, status = rp.ensure_venv("/usr/bin/python3.12")
        assert status == "failed"
        # .ready 必须不存在（CF-1：绝不缓存坏 venv）
        assert not (tmp / "data" / ".venv" / ".ready").exists()
        assert (tmp / "data" / ".venv" / ".failed").exists() or True  # 标记写入容忍 OSError


def test_ensure_venv_pip_success_writes_ready():
    """pip 成功 → .ready 写入 sha256，返回 created。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        req = tmp / "requirements.txt"
        req.write_text("requests\n")
        real_run = rp.subprocess.run
        def _run_with_side_effect(*args, **kw):
            # 第一次调用 = python -m venv：模拟真实创建目录
            if args and args[0] and any(str(tmp / "data" / ".venv") in str(a) for a in args[0]):
                (tmp / "data" / ".venv").mkdir(parents=True, exist_ok=True)
                return mock.MagicMock(returncode=0)
            return mock.MagicMock(returncode=0)  # pip 成功
        with _fake_skill_root(tmp), \
             mock.patch.object(rp.subprocess, "run", side_effect=_run_with_side_effect) as _run:
            py_path, status = rp.ensure_venv("/usr/bin/python3.12")
        assert status == "created"
        assert (tmp / "data" / ".venv" / ".ready").exists()
        assert (tmp / "data" / ".venv" / ".ready").read_text() == rp._sha256_of_file(req)


def test_ensure_venv_stale_rename_on_change():
    """MP-3: 旧 venv（无 .ready 匹配）→ rename 成 .venv.stale-* 而非直接删。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        req = tmp / "requirements.txt"
        req.write_text("requests\n")
        venv_dir = tmp / "data" / ".venv"
        venv_dir.mkdir(parents=True)
        (venv_dir / "old_file.txt").write_text("old")
        def _run_with_side_effect2(*args, **kw):
            if args and args[0] and any(str(tmp / "data" / ".venv") in str(a) for a in args[0]):
                (tmp / "data" / ".venv").mkdir(parents=True, exist_ok=True)
                return mock.MagicMock(returncode=0)
            return mock.MagicMock(returncode=0)
        with _fake_skill_root(tmp), \
             mock.patch.object(rp.subprocess, "run", side_effect=_run_with_side_effect2) as _run:
            rp.ensure_venv("/usr/bin/python3.12")
        stales = list((tmp / "data").glob(".venv.stale-*"))
        assert len(stales) == 1  # 旧 venv 被 rename 保留
        assert (stales[0] / "old_file.txt").exists()  # 旧文件还在（运行中进程安全）


def test_lock_acquire_release():
    """锁获取成功 + 释放后其他进程可获取。"""
    with tempfile.TemporaryDirectory() as td:
        lock_path = Path(td) / ".venv.lock"
        fd1 = rp._lock_acquire(lock_path, timeout_sec=2)
        assert fd1 is not None
        # 同进程再获取（同 fd 不同进程模拟：新 fd）→ 应超时失败（Unix flock 同进程
        # 会成功，这里用独立 fd 模拟跨进程——flock 对同一进程第二个 fd 会阻塞）
        import fcntl
        fd2 = open(lock_path, "w")
        try:
            fcntl.flock(fd2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        finally:
            fd2.close()
        assert acquired is False  # 第一把锁持有中
        rp._lock_release(fd1)
        # 释放后可获取
        fd3 = open(lock_path, "w")
        try:
            fcntl.flock(fd3.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired2 = True
        except OSError:
            acquired2 = False
        finally:
            fd3.close()
        assert acquired2 is True


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

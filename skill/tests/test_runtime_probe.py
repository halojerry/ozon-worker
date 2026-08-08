#!/usr/bin/env python3
"""runtime_probe.py 单测（v0.31 PR-A）— 多解释器发现 + re-exec。

覆盖：候选优先级 / 双边界验证（CF-2 3.14 拒绝）/ SKILL_NO_VENV 短路（CF-3）/
env 白名单清洗 / 哨兵防递归 / 坏解释器 timeout。

运行：
    cd skill && PYTHONPATH=. python3 tests/test_runtime_probe.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import runtime_probe as rp


def test_version_ok_bounds():
    """双边界：3.12 通过，3.11/3.13/3.14 拒绝（CF-2：只接受 3.12.x）。"""
    assert rp._version_ok((3, 12, 0)) is True
    assert rp._version_ok((3, 12, 99)) is True
    assert rp._version_ok((3, 13, 0)) is False  # cpython-313 ≠ cpython-312
    assert rp._version_ok((3, 11, 0)) is False
    assert rp._version_ok((3, 14, 0)) is False  # 3.14 ABI 不匹配，拒绝
    assert rp._version_ok((2, 7, 0)) is False
    assert rp._version_ok(None) is False


def test_parse_version_formats():
    assert rp._parse_version("Python 3.12.1") == (3, 12, 1)
    assert rp._parse_version("3.12.1") == (3, 12, 1)
    assert rp._parse_version("3.12") == (3, 12, 0)
    assert rp._parse_version("garbage") is None


def test_resolve_python_current_ok():
    """当前解释器是 3.12 → 直接返回，零扫描开销。"""
    with mock.patch.object(rp.sys, "version_info", (3, 12, 1, "final", 0)):
        cmd, is_current = rp.resolve_python()
    assert is_current is True
    assert cmd == rp.sys.executable


def test_resolve_python_scans_and_finds_312():
    """当前 3.10 + PATH 有 python3.12 → 返回它 + is_current=False。"""
    with mock.patch.object(rp.sys, "version_info", (3, 10, 0, "final", 0)), \
         mock.patch.object(rp.shutil, "which", side_effect=lambda name: f"/usr/local/bin/{name}" if name == "python3.12" else None), \
         mock.patch.object(rp.subprocess, "run") as _run:
        _run.return_value = mock.MagicMock(returncode=0, stdout="Python 3.12.3\n")
        cmd, is_current = rp.resolve_python()
    assert is_current is False
    assert "python3.12" in cmd


def test_resolve_python_rejects_314_first():
    """PATH 有 python3.14 但无 3.12/3.13 → 不选 3.14（CF-2：ABI 不匹配）。"""
    with mock.patch.object(rp.sys, "version_info", (3, 10, 0, "final", 0)), \
         mock.patch.object(rp.shutil, "which",
                           side_effect=lambda name: "/usr/bin/python3.14" if name == "python3.14" else None), \
         mock.patch.object(rp.subprocess, "run") as _run:
        _run.return_value = mock.MagicMock(returncode=0, stdout="Python 3.14.0\n")
        cmd, is_current = rp.resolve_python()
    assert is_current is True  # 没找到可用 3.12/3.13 → 回退当前，报错提示
    assert "3.14" not in cmd


def test_resolve_python_skips_bad_candidate():
    """候选存在但版本验证失败（坏解释器）→ 跳过继续找下一个。"""
    with mock.patch.object(rp.sys, "version_info", (3, 10, 0, "final", 0)), \
         mock.patch.object(rp.shutil, "which",
                           side_effect=lambda name: f"/bin/{name}" if name in rp._CANDIDATE_CMDS else None), \
         mock.patch.object(rp.subprocess, "run") as _run:
        _run.return_value = mock.MagicMock(returncode=1, stdout="")  # python3.12 坏了
        cmd, is_current = rp.resolve_python()
    assert is_current is True  # 全部候选失败 → 回退当前


def test_resolve_python_skip_no_venv_escape():
    """SKILL_NO_VENV=1 → 短路：不扫描，直接返回当前（CF-3 回退现状）。"""
    with mock.patch.dict(os.environ, {"SKILL_NO_VENV": "1"}), \
         mock.patch.object(rp.sys, "version_info", (3, 10, 0, "final", 0)), \
         mock.patch.object(rp, "_scan_candidates") as _scan:
        cmd, is_current = rp.resolve_python()
    assert is_current is True
    _scan.assert_not_called()  # 逃生舱下不扫描


def test_re_exec_cleans_env_and_sets_sentinel():
    """re-exec env：清 PYTHONPATH/PIP_*，保留用户变量，置哨兵。"""
    env = {
        "PATH": "/usr/bin",
        "PYTHONPATH": "/evil/path",
        "PYTHONHOME": "/evil/home",
        "PIP_INDEX_URL": "http://evil",
        "VIRTUAL_ENV": "/evil/venv",
        "WORKER_URL": "http://worker",
        "SKILL_AUTO_UPDATE": "0",
    }
    with mock.patch.dict(os.environ, env), \
         mock.patch.object(rp.os, "execve") as _execve:
        rp.re_exec_if_needed("/usr/bin/python3.12", "/app/cli.py", ["graph", "--url", "x"])
    args, kwargs = _execve.call_args
    clean_env = args[2]  # os.execve(path, argv, env) — env 是第 3 个位置参数
    assert "PYTHONPATH" not in clean_env
    assert "PYTHONHOME" not in clean_env
    assert "PIP_INDEX_URL" not in clean_env
    assert "VIRTUAL_ENV" not in clean_env
    assert clean_env["WORKER_URL"] == "http://worker"
    assert clean_env["SKILL_AUTO_UPDATE"] == "0"
    assert clean_env[rp._SENTINEL] == "1"
    assert clean_env["SKILL_RUNTIME_PY"] == "/usr/bin/python3.12"


def test_re_exec_noop_same_interpreter():
    """目标 == 当前 → 不 exec。"""
    with mock.patch.object(rp.sys, "executable", "/usr/bin/python3.12"), \
         mock.patch.object(rp.os, "execve") as _execve:
        rp.re_exec_if_needed("/usr/bin/python3.12", "/app/cli.py", [])
    _execve.assert_not_called()


def test_re_exec_argv_preserved():
    """execve argv = [python, script, *原 argv]（含参数透传）。"""
    with mock.patch.object(rp.os, "execve") as _execve:
        rp.re_exec_if_needed("/opt/py312/bin/python", "/app/cli.py", ["follow", "--ozon-url", "u"])
    argv = _execve.call_args.args[1]
    assert argv == ["/opt/py312/bin/python", "/app/cli.py", "follow", "--ozon-url", "u"]


def test_probe_candidate_timeout_returns_none():
    """坏解释器挂起 → timeout 返回 None（不阻塞 agent）。"""
    with mock.patch.object(rp.subprocess, "run",
                           side_effect=rp.subprocess.TimeoutExpired(cmd="x", timeout=10)):
        assert rp._probe_candidate("/bin/python3.12") is None


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

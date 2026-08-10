#!/usr/bin/env python3
"""Q12: compile.py dist data/browser 禁止打包断言回归测试。

背景：data/browser/ 含 Chrome profile（登录态）——打包分发会泄露用户登录态。
compile.py PR-A 完整性断言区需拦截「dist 含 data/browser」的情况（SystemExit 非零退出）。

运行：
    cd skill && .venv314/bin/python tests/test_compile_no_profile.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_COMPILE_PATH = Path(__file__).resolve().parent.parent / "compile.py"


def _load_compile():
    """加载 compile.py（独立脚本，非包模块，用 spec 加载避免包依赖）。"""
    spec = importlib.util.spec_from_file_location("skill_compile", _COMPILE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


compile_mod = _load_compile()


def _fake_dist() -> Path:
    """空 dist（runtime_probe 检查先于 data/.venv/browser，测试需自带明文产物）。"""
    return Path(tempfile.mkdtemp(prefix="compile_dist_test_"))


def _dist_with_runtime_probe() -> Path:
    dist = _fake_dist()
    (dist / "scripts").mkdir(parents=True)
    (dist / "scripts" / "runtime_probe.py").write_text("", encoding="utf-8")
    return dist


def test_dist_with_browser_raises():
    """dist 含 data/browser（Chrome profile 登录态）→ SystemExit 非零退出。"""
    dist = _dist_with_runtime_probe()
    (dist / "data" / "browser").mkdir(parents=True)
    try:
        compile_mod._assert_dist_safety(dist)
    except SystemExit as e:
        assert "data/browser" in str(e)
        assert "登录态" in str(e)
    else:
        raise AssertionError("应抛出 SystemExit（禁止打包登录态）")


def test_dist_with_venv_still_raises():
    """data/.venv 检查保持（回归：用户态 venv 不得打包）。"""
    dist = _dist_with_runtime_probe()
    (dist / "data" / ".venv").mkdir(parents=True)
    try:
        compile_mod._assert_dist_safety(dist)
    except SystemExit as e:
        assert "data/.venv" in str(e)
    else:
        raise AssertionError("应抛出 SystemExit")


def test_dist_missing_runtime_probe_still_raises():
    """runtime_probe.py 缺失检查保持（回归：明文产物完整性）。"""
    dist = _fake_dist()
    try:
        compile_mod._assert_dist_safety(dist)
    except SystemExit as e:
        assert "runtime_probe.py" in str(e)
    else:
        raise AssertionError("应抛出 SystemExit")


def test_normal_dist_passes():
    """正常 dist（runtime_probe.py 明文 + 无 data/.venv/browser）→ 不抛异常。"""
    dist = _dist_with_runtime_probe()
    compile_mod._assert_dist_safety(dist)  # 不抛 = 通过


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

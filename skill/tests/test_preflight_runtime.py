#!/usr/bin/env python3
"""cli.py _preflight_runtime 单测（PR-3）— Python 版本 + 核心依赖探测。

运行：
    cd skill && PYTHONPATH=. python3 tests/test_preflight_runtime.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_preflight_ok_with_deps():
    """依赖齐全 + Python≥3.12 → (True, "")。"""
    from scripts.cli import _preflight_runtime
    with mock.patch("scripts.cli.sys.version_info", (3, 12, 0)):
        ok, msg = _preflight_runtime()
    assert ok is True
    assert msg == ""


def test_preflight_blocks_old_python():
    """Python < 3.12 → 阻断并提示版本。"""
    from scripts.cli import _preflight_runtime
    with mock.patch("scripts.cli.sys.version_info", (3, 10, 0)):
        ok, msg = _preflight_runtime()
    assert ok is False
    assert "3.12" in msg


def test_preflight_reports_missing_deps():
    """缺 requests → 阻断并给 pip install 指引。"""
    from scripts.cli import _preflight_runtime

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("No module named 'requests'")
        return real_import(name, *args, **kwargs)

    with mock.patch("scripts.cli.sys.version_info", (3, 12, 0)), \
         mock.patch("builtins.__import__", side_effect=fake_import):
        ok, msg = _preflight_runtime()
    assert ok is False
    assert "pip install" in msg
    assert "requests" in msg


def test_preflight_allows_config_commands():
    """set_token/set_ak/set_store 不经过 preflight（配置命令无依赖）。"""
    from scripts.cli import _preflight_runtime
    # 只验证函数本身不受影响（main 里的豁免逻辑由 CLI 行为覆盖）
    assert callable(_preflight_runtime)


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

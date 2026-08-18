#!/usr/bin/env python3
"""W5.7 (I-10) 回归测试：编译 stub 特征校验 —— 旧 .so 缺 search_by_image_aibuy
时生成的 stub 必须包含显式 warning 检查（不静默降级 CDP）。

运行：
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_compile_stub_feature.py -v
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
    spec = importlib.util.spec_from_file_location("skill_compile", _COMPILE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


compile_mod = _load_compile()


def _gen_stub(module_file: str) -> str:
    with tempfile.TemporaryDirectory(prefix="stub_feat_") as td:
        tmp = Path(td)
        (tmp / "scripts" / "lib").mkdir(parents=True)
        compile_mod._generate_import_stubs(tmp, [module_file])
        stem = Path(module_file).stem
        return (tmp / "scripts" / "lib" / f"{stem}.py").read_text(encoding="utf-8")


def test_ozon_image_search_stub_has_aibuy_feature_check():
    """ozon_image_search stub 含 search_by_image_aibuy 存在性校验 + warnings。"""
    stub = _gen_stub("scripts/lib/ozon_image_search.py")
    assert 'if not hasattr(_mod, "search_by_image_aibuy")' in stub
    assert "import warnings as _w" in stub
    assert "RuntimeWarning" in stub
    assert "过旧" in stub


def test_other_module_stub_has_no_feature_check():
    """无特征要求的模块（cdp_client）stub 不含 aibuy 检查（不冗余）。"""
    stub = _gen_stub("scripts/lib/cdp_client.py")
    assert "search_by_image_aibuy" not in stub
    assert "hasattr(_mod" not in stub


def test_feature_check_inside_load_block():
    """特征检查块在 `if _spec and _spec.loader:` 块内（_mod 已定义才访问）。"""
    stub = _gen_stub("scripts/lib/ozon_image_search.py")
    lines = stub.splitlines()
    loader_idx = next(i for i, l in enumerate(lines) if "if _spec and _spec.loader:" in l)
    check_idx = next(i for i, l in enumerate(lines) if 'hasattr(_mod, "search_by_image_aibuy")' in l)
    assert check_idx > loader_idx
    assert lines[check_idx].startswith("        "), "检查块必须缩进在 loader 块内"


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

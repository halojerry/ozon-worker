#!/usr/bin/env python3
"""compile.py P6 编译清单回归测试：COMPILE_FILES 从 8 扩到 14。

三个断言锁定：
① COMPILE_FILES 恰好 14 个模块（含 6 个新晋编译的 discover/CDP 链路模块）
② COMPILE_FILES ∩ AUX_FILES == ∅（AUX 复制在 stub 生成之后，模块两属会
   明文覆盖 stub —— 编译保护失效）
③ COMPILE_FILES ∩ COPY_FILES == ∅（同理由）

运行：
    cd skill && .venv314/bin/python tests/test_compile_lists.py
    cd skill && .venv314/bin/python -m pytest tests/test_compile_lists.py -q
"""
from __future__ import annotations

import importlib.util
import os
import sys
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


def test_compile_files_has_14_modules():
    """COMPILE_FILES 必须恰好 14 个（8 原有 + 6 新晋：ozon_seller_analytics、
    analytics_upload、ozon_fission、ozon_discovery、ozon_seller、cdp_client）。"""
    expected = {
        "scripts/lib/ak_1688_client.py",
        "scripts/lib/ak_callback.py",
        "scripts/lib/config_store.py",
        "scripts/lib/image_preprocessor.py",
        "scripts/lib/ozon_scraper.py",
        "scripts/lib/ozon_image_search.py",
        "scripts/lib/reference_images.py",
        "scripts/lib/ozon_api.py",
        "scripts/lib/ozon_seller_analytics.py",
        "scripts/lib/analytics_upload.py",
        "scripts/lib/ozon_fission.py",
        "scripts/lib/ozon_discovery.py",
        "scripts/lib/ozon_seller.py",
        "scripts/lib/cdp_client.py",
    }
    assert len(compile_mod.COMPILE_FILES) == 14, (
        f"COMPILE_FILES 应为 14 个，实际 {len(compile_mod.COMPILE_FILES)}"
    )
    assert set(compile_mod.COMPILE_FILES) == expected, (
        f"COMPILE_FILES 清单不一致:\n"
        f"  缺: {sorted(expected - set(compile_mod.COMPILE_FILES))}\n"
        f"  多: {sorted(set(compile_mod.COMPILE_FILES) - expected)}"
    )


def test_compile_files_disjoint_from_aux_files():
    """编译模块绝不能同时出现在 AUX_FILES——AUX 复制在 stub 生成后执行，
    会把 stub 覆盖回明文，源码保护静默失效。"""
    overlap = set(compile_mod.COMPILE_FILES) & set(compile_mod.AUX_FILES)
    assert not overlap, f"COMPILE_FILES 与 AUX_FILES 重叠: {sorted(overlap)}"


def test_compile_files_disjoint_from_copy_files():
    """编译模块绝不能同时出现在 COPY_FILES（同 AUX 覆盖理由）。"""
    overlap = set(compile_mod.COMPILE_FILES) & set(compile_mod.COPY_FILES)
    assert not overlap, f"COMPILE_FILES 与 COPY_FILES 重叠: {sorted(overlap)}"


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

#!/usr/bin/env python3
"""
Skill 核心库 Cython 编译脚本

跨平台统一包：
  dist/scripts/lib/
    _loader.py          # 平台感知的 import loader
    _native/            # 编译后的二进制（按平台分目录）
      darwin/           # macOS .so
      win32/            # Windows .pyd
      linux/            # Linux .so

Python 会优先加载编译后的二进制文件，保护源码。
"""
from __future__ import annotations

import os
import sys
import shutil
import platform
from pathlib import Path

# 需要编译的核心文件（保护源码）
COMPILE_FILES = [
    "scripts/lib/ak_1688_client.py",
    "scripts/lib/ak_callback.py",
    "scripts/lib/chrome_launcher.py",
    "scripts/lib/config_store.py",
    "scripts/lib/image_preprocessor.py",
    "scripts/lib/ozon_scraper.py",
    "scripts/lib/ozon_image_search.py",
    "scripts/lib/reference_images.py",
    "scripts/lib/ozon_discovery.py",   # 选品发现引擎（蓝海评分）
    "scripts/lib/ozon_api.py",         # Ozon API 封装（类目搜索/属性匹配）
    "scripts/cloud_probe.py",          # 信封组装 + 管线编排
    "scripts/capabilities/browser_probe/service.py",  # CDP 浏览器探针
    "scripts/capabilities/browser_probe/stealth.py",
]

# 复制但不编译的文件（入口脚本，无需保护源码）
COPY_FILES = [
    "scripts/cli.py",
    "scripts/batch_test.py",
]

# 辅助文件（必须复制，否则 import 会失败）
AUX_FILES = [
    "scripts/__init__.py",
    "scripts/_const.py",
    "scripts/_errors.py",
    "scripts/lib/__init__.py",
    "scripts/lib/task_paths.py",
    "scripts/lib/logging_utils.py",
    "scripts/lib/cdp_client.py",     # CDP 客户端（替代 Playwright）
    "scripts/lib/utils.py",          # 共享工具函数（parse_price 等）
    "scripts/lib/cache.py",          # 通用磁盘缓存（JSON + TTL + SHA256 key）
    "scripts/lib/ozon_seller.py",    # Ozon Seller API 客户端（佣金/属性）
    "scripts/lib/ozon_widget.py",    # Ozon Widget API 客户端（产品/跟卖）
    "scripts/lib/ozon_seller_analytics.py",  # 运营指标借道（Discover v2 新增）
    "scripts/capabilities/__init__.py",
    "scripts/capabilities/browser_probe/__init__.py",
]

# 参考文件（客户端文档 + 依赖）
DOC_FILES = [
    "SKILL.md",
    "envelope_example.json",
    "field_mapping.md",
    "requirements.txt",
]

# 平台映射
PLATFORM_MAP = {
    "Darwin": "darwin",
    "Windows": "win32",
    "Linux": "linux",
}


def _get_platform_dir() -> str:
    """Get platform directory name (e.g., darwin-arm64, win32)."""
    plat = PLATFORM_MAP.get(platform.system(), platform.system().lower())
    if plat == "darwin":
        return f"darwin-{platform.machine()}"  # darwin-arm64 or darwin-x86_64
    return plat


def _find_compiled_file(build_dir: Path, stem: str) -> Path | None:
    """Find compiled .so/.pyd file, supporting multiple Python versions."""
    py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
    patterns = [
        f"{stem}.cpython-{py_ver}-{platform.machine()}.so",
        f"{stem}.cpython-{py_ver}.so",
        f"{stem}.cpython-{py_ver}-win_amd64.pyd",
        f"{stem}.cpython-{py_ver}-win32.pyd",
        f"{stem}.so",
        f"{stem}.pyd",
    ]
    for pattern in patterns:
        for f in build_dir.glob(pattern):
            return f
    for f in build_dir.glob(f"{stem}.*"):
        if f.suffix in ('.so', '.pyd'):
            return f
    return None


def compile_file(py_file: str, build_dir: str) -> bool:
    """编译单个 .py 文件为 .so/.pyd"""
    import subprocess
    try:
        # Normalize paths to forward slashes (Windows compatibility)
        py_file_posix = Path(py_file).as_posix()
        build_dir_posix = Path(build_dir).as_posix()
        stem = Path(py_file).stem

        setup_content = (
            'from setuptools import setup, Extension\n'
            'from Cython.Build import cythonize\n'
            f'setup(ext_modules=cythonize([Extension("{stem}", ["{py_file_posix}"])]))\n'
        )
        setup_file = Path(py_file).parent / "_setup_temp.py"
        setup_file.write_text(setup_content)

        old_cwd = os.getcwd()
        os.chdir(str(Path(py_file).parent.parent))

        result = subprocess.run(
            [sys.executable, str(setup_file), "build_ext",
             f"--build-lib={build_dir_posix}", f"--build-temp={build_dir_posix}/temp"],
            capture_output=True, text=True, timeout=120
        )

        setup_file.unlink(missing_ok=True)
        os.chdir(old_cwd)

        if result.returncode == 0:
            return True
        else:
            print(f"  ❌ {result.stderr.split(chr(10))[-2] if result.stderr else 'unknown error'}")
            return False
    except Exception as e:
        print(f"  ❌ 编译失败: {e}")
        return False


def _generate_loader(dist_lib_dir: Path) -> None:
    """Generate _loader.py for platform-aware imports."""
    loader_content = '''#!/usr/bin/env python3
"""Platform-aware native module loader.

Automatically loads the correct binary (.so/.pyd) for the current platform
from _native/{darwin,win32,linux}/ directory.
"""
from __future__ import annotations

import importlib
import importlib.util
import platform
import sys
from pathlib import Path

_NATIVE_DIR = Path(__file__).resolve().parent / "_native"

_PLATFORM_MAP = {
    "Darwin": "darwin",
    "Windows": "win32",
    "Linux": "linux",
}

# Cache for loaded modules
_loaded: dict[str, object] = {}


def _get_platform_dir():
    """Get platform-architecture directory name (e.g., darwin-arm64, win32)."""
    plat = _PLATFORM_MAP.get(platform.system(), platform.system().lower())
    machine = platform.machine()
    if plat == "darwin":
        return f"darwin-{machine}"  # darwin-arm64 or darwin-x86_64
    return plat  # win32 or linux


def load_native(module_name: str):
    """Load a native module from the platform-specific directory.

    Usage:
        from scripts.lib._loader import load_native
        config_store = load_native("config_store")
        config_store.get_store("my_store")
    """
    if module_name in _loaded:
        return _loaded[module_name]

    plat_dir_name = _get_platform_dir()
    plat_dir = _NATIVE_DIR / plat_dir_name

    if not plat_dir.is_dir():
        # Fallback: try generic platform dir (darwin, win32)
        plat = _PLATFORM_MAP.get(platform.system(), platform.system().lower())
        plat_dir = _NATIVE_DIR / plat

    if not plat_dir.is_dir():
        raise ImportError(
            f"No native modules for '{plat_dir_name}'. "
            f"Available: {[d.name for d in _NATIVE_DIR.iterdir() if d.is_dir()]}"
        )

    # Find the binary file
    py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
    candidates = [
        f"{module_name}.cpython-{py_ver}-{platform.machine()}",
        f"{module_name}.cpython-{py_ver}",
        f"{module_name}",
    ]

    binary_file = None
    for candidate in candidates:
        for suffix in ['.so', '.pyd']:
            f = plat_dir / f"{candidate}{suffix}"
            if f.exists():
                binary_file = f
                break
        if binary_file:
            break

    if not binary_file:
        # Fallback: try to import as regular Python module
        try:
            mod = importlib.import_module(f"scripts.lib.{module_name}")
            _loaded[module_name] = mod
            return mod
        except ImportError:
            raise ImportError(
                f"Cannot find native module '{module_name}' for platform '{plat}' "
                f"in {plat_dir}"
            )

    # Load the binary module
    spec = importlib.util.spec_from_file_location(
        f"scripts.lib.{module_name}",
        str(binary_file),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {binary_file}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"scripts.lib.{module_name}"] = mod
    spec.loader.exec_module(mod)
    _loaded[module_name] = mod
    return mod
'''
    (dist_lib_dir / "_loader.py").write_text(loader_content, encoding='utf-8')
    print(f"  📎 scripts/lib/_loader.py")


def _generate_import_stubs(dist_dir: Path, compile_files: list[str]) -> None:
    """Generate import stubs that directly load native binaries.

    Stubs are placed at the same relative path as the original source file,
    so import paths like ``scripts.capabilities.browser_probe.stealth`` resolve
    correctly.  The native binary is always loaded from
    ``dist/scripts/lib/_native/{platform}/`` regardless of stub location.
    """
    dist_lib_dir = dist_dir / "scripts" / "lib"
    for py_file in compile_files:
        stem = Path(py_file).stem
        # Stub is at its original package location, native binary is always in lib/_native/
        # Compute relative path from stub to _native dir
        stub_parent = dist_dir / Path(py_file).parent  # e.g. dist/scripts/capabilities/browser_probe
        native_rel = os.path.relpath(dist_lib_dir / "_native", stub_parent)  # e.g. ../../lib/_native
        # Use string concatenation to avoid f-string escaping issues
        # Use sysconfig.EXT_SUFFIX for correct platform suffix (e.g., .cpython-312-darwin.so)
        stub_content = (
            '#!/usr/bin/env python3\n'
            '"""Auto-generated stub — loads native binary for current platform."""\n'
            'import importlib.util as _ilu\n'
            'import platform as _pm\n'
            'import sys as _sys\n'
            'import sysconfig\n'
            'from pathlib import Path as _Path\n'
            '\n'
            '_plat = {"Darwin": "darwin", "Windows": "win32", "Linux": "linux"}.get(\n'
            '    _pm.system(), _pm.system().lower()\n'
            ')\n'
            '# macOS: architecture-specific dir (darwin-arm64, darwin-x86_64)\n'
            'if _plat == "darwin":\n'
            '    _plat_name = f"darwin-{_pm.machine()}"\n'
            'else:\n'
            '    _plat_name = _plat\n'
            '_native_dir = _Path(__file__).resolve().parent / "' + native_rel.replace('\\', '/') + '" / _plat_name\n'
            '# Fallback to generic platform dir if arch-specific not found\n'
            'if not _native_dir.is_dir():\n'
            '    _native_dir = _Path(__file__).resolve().parent / "' + native_rel.replace('\\', '/') + '" / _plat\n'
            '_ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")\n'
            '_binary = None\n'
            '# Try exact EXT_SUFFIX first (e.g., .cpython-312-darwin.so)\n'
            '_f = _native_dir / ("' + stem + '" + _ext_suffix)\n'
            'if _f.exists():\n'
            '    _binary = _f\n'
            'else:\n'
            '    # Fallback: search for ABI-compatible file only\n'
            '    _py_tag = f"cpython-{_sys.version_info.major}{_sys.version_info.minor}"\n'
            '    _bare_fallback = None\n'
            '    for _p in _native_dir.glob("' + stem + '.*"):\n'
            '        if _p.suffix not in (".so", ".pyd"):\n'
            '            continue\n'
            '        _name = _p.name\n'
            '        if _py_tag in _name:\n'
            '            _binary = _p\n'
            '            break\n'
            '        # Bare .so/.pyd with no cpython tag — last resort\n'
            '        if "cpython" not in _name and _bare_fallback is None:\n'
            '            _bare_fallback = _p\n'
            '    if _binary is None and _bare_fallback is not None:\n'
            '        _binary = _bare_fallback\n'
            '\n'
            'if _binary:\n'
            '    _spec = _ilu.spec_from_file_location(__name__, str(_binary))\n'
            '    if _spec and _spec.loader:\n'
            '        _mod = _ilu.module_from_spec(_spec)\n'
            '        _spec.loader.exec_module(_mod)\n'
            '        for _n in dir(_mod):\n'
            '            if not _n.startswith("__"):\n'
            '                globals()[_n] = getattr(_mod, _n)\n'
            'else:\n'
            '    raise ImportError(f"No native binary for ' + stem + ' on {_plat_name}")\n'
        )
        stub_path = stub_parent / f"{stem}.py"
        stub_path.parent.mkdir(parents=True, exist_ok=True)
        stub_path.write_text(stub_content, encoding='utf-8')
        print(f"  📄 {Path(py_file).parent}/{stem}.py (stub → native)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Skill 核心库 Cython 编译")
    parser.add_argument("--clean", action="store_true", help="清理 build/dist 目录")
    args = parser.parse_args()

    plat_dir_name = _get_platform_dir()

    print("🔨 Skill 核心库 Cython 编译")
    print(f"   系统: {platform.system()} {platform.machine()} → {plat_dir_name}")
    print(f"   Python: {sys.version.split()[0]}")
    print()

    skill_dir = Path(__file__).parent
    build_dir = skill_dir / "build"
    dist_dir = skill_dir / "dist"

    if args.clean:
        for d in [build_dir, dist_dir]:
            if d.exists():
                shutil.rmtree(d)
                print(f"  🗑️  已删除 {d}")
        print("✅ 清理完成")
        return

    try:
        import Cython
        print(f"   Cython: {Cython.__version__}")
    except ImportError:
        print("❌ 请先安装 Cython: pip3 install cython setuptools")
        return

    if build_dir.exists():
        shutil.rmtree(build_dir)
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    build_dir.mkdir()
    dist_dir.mkdir()

    # 编译
    success = 0
    failed = 0
    for py_file in COMPILE_FILES:
        full_path = skill_dir / py_file
        if not full_path.exists():
            print(f"  ⏭️  跳过（不存在）: {py_file}")
            continue
        print(f"  🔧 编译: {py_file}")
        if compile_file(str(full_path), str(build_dir)):
            success += 1
        else:
            failed += 1

    # 创建平台目录
    native_plat_dir = dist_dir / "scripts" / "lib" / "_native" / plat_dir_name
    native_plat_dir.mkdir(parents=True, exist_ok=True)

    # 复制编译产物到平台目录
    print(f"\n📦 复制编译产物到 _native/{plat_dir_name}/")
    for compile_src in COMPILE_FILES:
        file_stem = Path(compile_src).stem
        compiled = _find_compiled_file(build_dir, file_stem)
        if compiled:
            dst = native_plat_dir / compiled.name
            shutil.copy2(compiled, dst)
            print(f"  ✅ {compiled.name}")
        else:
            print(f"  ⚠️ 未找到编译产物: {file_stem}")

    # 生成 loader + stubs
    dist_lib_dir = dist_dir / "scripts" / "lib"
    _generate_loader(dist_lib_dir)
    _generate_import_stubs(dist_dir, COMPILE_FILES)

    # 复制入口脚本
    print(f"\n📄 复制入口脚本")
    for copy_file in COPY_FILES:
        src = skill_dir / copy_file
        if src.exists():
            dst = dist_dir / copy_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  📄 {copy_file}")

    # 复制辅助文件
    print(f"\n📎 复制辅助文件")
    for aux_file in AUX_FILES:
        src = skill_dir / aux_file
        if src.exists():
            dst = dist_dir / aux_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  📎 {aux_file}")

    # 复制参考文件
    print(f"\n📚 复制参考文档")
    for doc_file in DOC_FILES:
        src = skill_dir / doc_file
        if src.exists():
            shutil.copy2(src, dist_dir / doc_file)
            print(f"  📚 {doc_file}")

    # 配置目录（生成空模板，不泄露真实凭证）
    import json as _json
    config_dir = dist_dir / "data" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    print(f"  📁 data/config/")
    settings_template = {"mxou_token": "", "ali_1688_ak": ""}
    stores_template = {"default": "", "stores": {}}
    (config_dir / "settings.json").write_text(
        _json.dumps(settings_template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (config_dir / "stores.json").write_text(
        _json.dumps(stores_template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  📝 data/config/settings.json (空模板)")
    print(f"  📝 data/config/stores.json (空模板)")

    # 清理 dist 中的 __pycache__ / .pyc（编译时 import 会生成，避免污染发布包）
    cleaned = 0
    for cache_dir in dist_dir.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
        cleaned += 1
    for pyc in dist_dir.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)
        cleaned += 1
    if cleaned:
        print(f"\n🧹 已清理 {cleaned} 个 __pycache__/.pyc（发布包保持干净）")

    print(f"\n✅ 编译完成: {success} 成功, {failed} 失败")
    print(f"   平台: {plat_dir_name}")
    print(f"   输出目录: {dist_dir}")
    print(f"\n💡 跨平台分发:")
    print(f"   1. 在 macOS 上运行: python3.12 compile.py")
    print(f"   2. 在 Windows 上运行: python3.12 compile.py")
    print(f"   3. 将两次编译的 _native/ 目录合并")
    print(f"   4. 打包分发")


if __name__ == "__main__":
    main()

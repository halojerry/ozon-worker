#!/usr/bin/env python3
"""
Skill 核心库 Cython 编译脚本
将 .py 编译为 .so (macOS/Linux) 或 .pyd (Windows)
Python 会优先加载编译后的二进制文件，保护源码
"""
from __future__ import annotations

import os
import sys
import shutil
import platform
from pathlib import Path

# 需要编译的核心文件（保护源码）
# 包含 Ozon 抓取和以图搜款
COMPILE_FILES = [
    "scripts/lib/ak_1688_client.py",
    "scripts/lib/chrome_launcher.py",
    "scripts/lib/config_store.py",
    "scripts/lib/ozon_scraper.py",
    "scripts/lib/ozon_image_search.py",
]

# 复制但不编译的文件（有复杂依赖或需要直接查看）
COPY_FILES = [
    "scripts/cli.py",
    "scripts/cloud_probe.py",
    "scripts/batch_test.py",
]


def compile_file(py_file: str, build_dir: str) -> bool:
    """编译单个 .py 文件为 .so/.pyd"""
    import subprocess
    try:
        # 创建临时 setup.py
        setup_content = f'''
from setuptools import setup, Extension
from Cython.Build import cythonize
setup(ext_modules=cythonize([Extension("{Path(py_file).stem}", ["{py_file}"])]))
'''
        setup_file = Path(py_file).parent / "_setup_temp.py"
        setup_file.write_text(setup_content)

        old_cwd = os.getcwd()
        os.chdir(str(Path(py_file).parent.parent))  # skill/ 目录

        result = subprocess.run(
            [sys.executable, str(setup_file), "build_ext", f"--build-lib={build_dir}", f"--build-temp={build_dir}/temp"],
            capture_output=True, text=True, timeout=60
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


def main():
    print("🔨 Skill 核心库 Cython 编译")
    print(f"   系统: {platform.system()} {platform.machine()}")
    print(f"   Python: {sys.version.split()[0]}")
    print()

    # 检查 Cython
    try:
        import Cython
        print(f"   Cython: {Cython.__version__}")
    except ImportError:
        print("❌ 请先安装 Cython: pip3 install cython setuptools")
        return

    skill_dir = Path(__file__).parent
    build_dir = skill_dir / "build"
    dist_dir = skill_dir / "dist"

    # 清理旧构建
    if build_dir.exists():
        shutil.rmtree(build_dir)
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    build_dir.mkdir()
    dist_dir.mkdir()

    # 编译每个文件
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

    # 复制未编译的文件
    print(f"\n📦 复制文件到 dist/")
    for copy_file in COPY_FILES:
        src = skill_dir / copy_file
        if src.exists():
            dst = dist_dir / copy_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  📄 {copy_file}")

    # 复制编译后的 .so/.pyd 文件到正确的目录
    for compile_src in COMPILE_FILES:
        file_stem = Path(compile_src).stem
        parent_dir = Path(compile_src).parent  # scripts/lib 或 scripts
        # 找到编译产物
        for ext in ['.cpython-39-darwin.so', '.cpython-39-win_amd64.pyd', '.so', '.pyd']:
            src = build_dir / f"{file_stem}{ext}"
            if src.exists():
                dst = dist_dir / parent_dir / f"{file_stem}{ext}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"  ✅ {parent_dir}/{file_stem}{ext}")
                break

    # 复制配置文件
    for config_file in ["SKILL.md", "requirements.txt", ".env.example", "install.py"]:
        src = skill_dir / config_file
        if src.exists():
            shutil.copy2(src, dist_dir / config_file)
            print(f"  📄 {config_file}")

    print(f"\n✅ 编译完成: {success} 成功, {failed} 失败")
    print(f"   输出目录: {dist_dir}")


if __name__ == "__main__":
    main()

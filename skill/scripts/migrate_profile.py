#!/usr/bin/env python3
"""PR-4: Chrome profile 迁移脚本 — 旧 data/browser/profile → data/browser/profiles/1688/default。

背景：v0.28.6 工具 Chrome 用 data/browser/profile，PR-4 统一为
data/browser/profiles/1688/default（与 cli/service 一致）。已用旧路径的用户
登录态（1688/Ozon cookie）在旧目录，直接切换会丢登录 → 需要一次性迁移。

策略：只复制不删除（安全），dry-run 默认开启；--apply 才实际复制。
用法：
    python3 scripts/migrate_profile.py            # dry-run 预览
    python3 scripts/migrate_profile.py --apply    # 实际迁移
    python3 scripts/migrate_profile.py --check    # 检测是否已在新路径（幂等）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

OLD_PROFILE = Path(__file__).resolve().parent.parent / "data" / "browser" / "profile"
NEW_PROFILE = Path(__file__).resolve().parent.parent / "data" / "browser" / "profiles" / "1688" / "default"


def _profile_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def cmd_check() -> int:
    """检测迁移状态（幂等）。"""
    if NEW_PROFILE.exists() and any(NEW_PROFILE.iterdir()):
        print(f"✅ 新 profile 已存在且有内容: {NEW_PROFILE}")
        if OLD_PROFILE.exists():
            print(f"  ⚠️ 旧 profile 仍存在（可手动删除）: {OLD_PROFILE}")
        return 0
    if OLD_PROFILE.exists() and any(OLD_PROFILE.iterdir()):
        size_mb = _profile_size(OLD_PROFILE) / 1024 / 1024
        print(f"⚠️ 旧 profile 存在（{size_mb:.1f} MB），未迁移: {OLD_PROFILE}")
        print("  → 运行 `python3 scripts/migrate_profile.py --apply` 迁移登录态")
        return 1
    print("ℹ️ 未发现旧 profile，无需迁移")
    return 0


def cmd_migrate(apply: bool) -> int:
    """迁移旧 profile → 新路径（默认 dry-run）。"""
    if not OLD_PROFILE.exists() or not any(OLD_PROFILE.iterdir()):
        print("ℹ️ 旧 profile 不存在或为空，无需迁移")
        return 0

    if NEW_PROFILE.exists() and any(NEW_PROFILE.iterdir()):
        print(f"⚠️ 新 profile 已存在且有内容，跳过迁移（避免覆盖）: {NEW_PROFILE}")
        return 0

    size_mb = _profile_size(OLD_PROFILE) / 1024 / 1024
    print(f"📦 待迁移: {OLD_PROFILE} ({size_mb:.1f} MB) → {NEW_PROFILE}")

    if not apply:
        print("  [dry-run] 未执行复制。加 --apply 执行迁移。")
        return 0

    NEW_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(OLD_PROFILE, NEW_PROFILE)
    except FileExistsError:
        print("❌ 新路径已存在，迁移中止（保留原文件）")
        return 1
    except Exception as exc:
        print(f"❌ 迁移失败（旧 profile 保留）: {exc}")
        return 1

    print(f"✅ 迁移完成: {NEW_PROFILE}")
    print("  ⚠️ 旧 profile 未删除（确认登录态正常后可手动删除）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Chrome profile 路径迁移（PR-4）")
    parser.add_argument("--apply", action="store_true", help="实际执行迁移（默认 dry-run）")
    parser.add_argument("--check", action="store_true", help="仅检测迁移状态")
    args = parser.parse_args()

    if args.check:
        return cmd_check()
    return cmd_migrate(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

"""文档同步检查（v0.40 协同规范第一步）。

对比 cli.py 实际子命令 vs SKILL.md 命令表，diff 非空 → 退出码 1（CI 阻断）。

用法:
    python3.12 scripts/check_doc_sync.py [--fix]

- 默认只检查并报告差异（退出码 1 表示有脱节）
- --fix 自动向 SKILL.md 追加缺失命令行（保守：只补不删，防误删人工内容）
- 支持单独指定 SKILL.md 路径（默认 skill/SKILL.md）

背景：SKILL.md 命令表靠人肉维护，实测已脱节（CLI 有 category/cleanup 但
文档缺；文档有 migrate_profile 但 CLI 无）。本脚本是第一步自动化校验，
后续演进为 yaml 单一事实源 + 生成器（见 AGENTS.md 协同规范方案）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys


def extract_cli_commands(cli_path: str) -> set[str]:
    """从 cli.py 提取 add_parser 注册的全部子命令。"""
    try:
        src = open(cli_path, "r", encoding="utf-8").read()
    except FileNotFoundError:
        return set()
    return set(re.findall(r'add_parser\(\s*["\']([a-z_]+)["\']', src))


def extract_doc_commands(skill_md_path: str) -> set[str]:
    """从 SKILL.md 命令表提取已记录的命令名。

    匹配 `| `code` | 行（命令表行）；支持 `batch_test.py` 这类带扩展名条目。
    """
    try:
        lines = open(skill_md_path, "r", encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return set()
    cmds: set[str] = set()
    for line in lines:
        m = re.match(r"^\|\s*`([a-zA-Z0-9_.-]+)`\s*\|", line.strip())
        if m:
            cmds.add(m.group(1))
    return cmds


def standalone_scripts(scripts_dir: str) -> set[str]:
    """scripts/ 下可独立运行的 .py（batch_test.py / migrate_profile.py 等）。

    这些是独立入口（非 cli.py 子命令），SKILL.md 记录它们是合法的，不算 ghost。
    返回同时含 `batch_test` 和 `batch_test.py` 两种形式（SKILL.md 两种写法都有）。
    """
    try:
        files = os.listdir(scripts_dir)
    except FileNotFoundError:
        return set()
    out: set[str] = set()
    for f in files:
        if f.endswith(".py") and not f.startswith("_") and f != "cli.py":
            out.add(f)
            out.add(f[:-3])
    return out


def sync_missing_to_doc(missing: set[str], skill_md_path: str) -> int:
    """向 SKILL.md 命令表追加缺失命令（保守：只补不删）。

    在命令表最后一个 `|` 行后插入。找不到命令表 → 不修改。
    """
    lines = open(skill_md_path, "r", encoding="utf-8").read().splitlines()
    last_table_idx = -1
    for i, line in enumerate(lines):
        if re.match(r"^\|\s*`[a-z_]+`\s*\|", line.strip()):
            last_table_idx = i
    if last_table_idx < 0:
        return 0
    added = 0
    for cmd in sorted(missing):
        lines.insert(last_table_idx + 1 + added,
                     f"| `{cmd}` | （待补充说明） |")
        added += 1
    if added:
        open(skill_md_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="SKILL.md 命令表同步检查")
    parser.add_argument("--fix", action="store_true", help="自动补齐缺失命令到 SKILL.md")
    parser.add_argument("--cli", default=None, help="cli.py 路径（默认自动定位）")
    parser.add_argument("--skill-md", default=None, help="SKILL.md 路径（默认自动定位）")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # skill/
    cli_path = args.cli or os.path.join(root, "scripts", "cli.py")
    skill_md_path = args.skill_md or os.path.join(root, "SKILL.md")

    cli_cmds = extract_cli_commands(cli_path)
    doc_cmds = extract_doc_commands(skill_md_path)
    standalone = standalone_scripts(os.path.join(root, "scripts"))

    if not cli_cmds:
        print(f"⚠️ 无法从 {cli_path} 提取命令（路径错误？）")
        return 2

    missing = sorted(cli_cmds - doc_cmds)            # CLI 子命令但文档缺
    ghost = sorted((doc_cmds - cli_cmds) - standalone)  # 文档有但 CLI 无且非独立脚本

    if not missing and not ghost:
        print(f"✅ 文档同步 OK（CLI {len(cli_cmds)} 命令全部在 SKILL.md 中）")
        return 0

    if missing:
        print(f"❌ SKILL.md 缺 {len(missing)} 个命令: {', '.join(missing)}")
    if ghost:
        print(f"⚠️ SKILL.md 有 {len(ghost)} 个命令但 CLI 无（可能已删除）: {', '.join(ghost)}")

    if args.fix and missing:
        added = sync_missing_to_doc(set(missing), skill_md_path)
        print(f"  --fix: 已向 SKILL.md 追加 {added} 个命令（补说明后提交）")
        return 0 if not ghost else 1

    print("  修复: python3.12 scripts/check_doc_sync.py --fix（或手动补 SKILL.md 命令表）")
    return 1


if __name__ == "__main__":
    sys.exit(main())

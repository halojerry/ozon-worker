#!/usr/bin/env python3
"""compile.py Q10/Q13 Wave2 回归测试（Task 8）：
① DOC_FILES 含 3 个新 references 文件
② 复制 SKILL.md 后用 skill/VERSION 覆写 dist/SKILL.md frontmatter version 字段

运行：
    cd skill && .venv314/bin/python tests/test_compile_frontmatter.py
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_compile_frontmatter.py -v
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


# ── ① DOC_FILES 含 3 个新 references 文件 ──

def test_doc_files_include_new_references():
    """DOC_FILES 必须包含 anti-patterns / discover-fission / trend-selection 三文件。"""
    docs = compile_mod.DOC_FILES
    for f in ("references/anti-patterns.md",
              "references/discover-fission.md",
              "references/trend-selection.md"):
        assert f in docs, f"DOC_FILES 缺少 {f}"


# ── ② frontmatter version 覆写 ──

def test_rewrite_skill_frontmatter_version():
    """SKILL.md frontmatter version 被 VERSION 内容覆写（引号保留）。"""
    skill_md = (
        "---\n"
        "name: pounding-ozon-probe\n"
        'version: "0.30.0"\n'
        "agent_created: true\n"
        "---\n"
        "\n"
        "# 正文\n"
    )
    out = compile_mod._rewrite_skill_frontmatter_version(skill_md, "0.35.0")
    assert 'version: "0.35.0"' in out, f"frontmatter 未覆写: {out}"
    assert 'version: "0.30.0"' not in out, "旧版本号应被替换"
    assert "# 正文" in out, "正文应保留"


def test_rewrite_skill_frontmatter_version_missing_noop():
    """无 frontmatter version 字段 → 原样返回（不崩溃、不插入）。"""
    skill_md = "---\nname: x\n---\n# 正文\n"
    assert compile_mod._rewrite_skill_frontmatter_version(skill_md, "0.35.0") == skill_md


def test_rewrite_skill_frontmatter_version_only_first():
    """只替换 frontmatter 中的 version（count=1），正文里的 version 不动。"""
    skill_md = (
        "---\nname: x\n"
        'version: "0.30.0"\n'
        "---\n"
        '正文 version: "1.2.3"\n'
    )
    out = compile_mod._rewrite_skill_frontmatter_version(skill_md, "9.9.9")
    assert out.count('version: "9.9.9"') == 1, f"应只替换 frontmatter 1 处: {out}"
    assert 'version: "1.2.3"' in out, "正文 version 不应被替换"


def test_doc_copy_overwrites_dist_skill_frontmatter():
    """复制 SKILL.md 到 dist 后 frontmatter version 被 VERSION 覆写（端到端）。"""
    with tempfile.TemporaryDirectory(prefix="compile_fm_") as td:
        tmp = Path(td)
        # 模拟 skill 目录：SKILL.md + VERSION
        skill_md_src = tmp / "SKILL.md"
        skill_md_src.write_text(
            "---\nname: pounding-ozon-probe\n"
            'version: "0.30.0"\n'
            "---\n# 正文\n",
            encoding="utf-8",
        )
        version_src = tmp / "VERSION"
        version_src.write_text("0.35.0", encoding="utf-8")

        dist = tmp / "dist"
        dist.mkdir()
        dst = dist / "SKILL.md"
        # 模拟 compile.py 的复制逻辑：拷贝后立即覆写 frontmatter
        dst.write_text(skill_md_src.read_text(encoding="utf-8"), encoding="utf-8")
        dst.write_text(
            compile_mod._rewrite_skill_frontmatter_version(
                dst.read_text(encoding="utf-8"),
                version_src.read_text(encoding="utf-8").strip(),
            ),
            encoding="utf-8",
        )
        out = dst.read_text(encoding="utf-8")
        assert 'version: "0.35.0"' in out, f"dist/SKILL.md frontmatter 应覆写为 VERSION: {out}"
        assert "# 正文" in out


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

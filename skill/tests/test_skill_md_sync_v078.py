#!/usr/bin/env python3
"""批B7: SKILL.md 速查表/最短路径与 CLI 实际 flag 同步锁（先例：test_compile_frontmatter）。

锁定批B 新增面不被文档漂移丢掉：
- 命令速查表含 --wait（合并语义：闸排队 + 终态轮询）、--min-margin、check --logs
- 「最短路径」三条 recipe 存在（graph/follow --wait + discover 无人值守）
- 「长任务后台」小节引导 job_* 四件套
- --yes / --non-interactive 别名说明（--non-interactive 为实际 flag 名）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_skill_md_sync_v078.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SKILL_MD = os.path.join(os.path.dirname(__file__), "..", "SKILL.md")


def _read() -> str:
    with open(SKILL_MD, encoding="utf-8") as f:
        return f.read()


def test_quick_reference_mentions_wait_with_semantics():
    """①速查表/关键规则含 --wait 且说明双重语义（闸排队 + 提交后轮询终态）。"""
    doc = _read()
    assert "--wait" in doc
    assert "终态" in doc, "必须说明 --wait 的提交后轮询终态语义（合并 v0.76 排队语义）"


def test_quick_reference_mentions_min_margin_for_graph_follow():
    """②速查表含 graph/follow 的 --min-margin（预估利润率拦截）。"""
    doc = _read()
    assert "--min-margin" in doc
    assert "预估利润率" in doc


def test_quick_reference_mentions_check_logs():
    """③速查表含 check --logs（只读日志通道）。"""
    doc = _read()
    assert "check" in doc and "--logs" in doc


def test_shortest_path_recipes_present():
    """④「最短路径」三条 recipe：graph --wait / follow --wait / discover 无人值守。"""
    doc = _read()
    assert "最短路径" in doc
    assert "graph" in doc and "--wait" in doc
    assert "follow" in doc and "--ozon-url" in doc
    assert "discover" in doc and "--auto-submit" in doc
    # --non-interactive 是真实 flag 名；--yes 若提及须注明别名关系
    assert "--non-interactive" in doc


def test_background_jobs_section_guides_job_tools():
    """⑤「长任务后台」小节：引导 job_status/job_result/job_list/job_cancel。"""
    doc = _read()
    assert "长任务后台" in doc
    for tool in ("job_status", "job_result", "job_list", "job_cancel"):
        assert tool in doc, f"缺少 {tool} 引导"


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
    sys.exit(1 if failed else 0)

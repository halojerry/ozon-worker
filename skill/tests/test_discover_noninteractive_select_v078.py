"""discover --non-interactive 挑选腿自动全选（0.78.0 实机验证补缺）。

实机验证发现：--non-interactive 下 _interactive_select 仍弹 input("挑选: ")，
非 tty EOF 被当「已取消」→ exit 0 静默不出货（P7 只修了提交确认腿，挑选腿漏了）。
本文件锁定：非交互自动全选可分析产品（ok/uncertain，等价交互模式「回车全选可挑」），
且 cmd_discover 内非交互分支先于交互弹窗（源码序锁，防回归）。
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.cli import (  # noqa: E402
    _auto_select_noninteractive,
    _interactive_select,
)


class _Cand:
    """最小 ProductCandidate 形状（本分支只读 status）。"""

    def __init__(self, status: str):
        self.status = status


def test_auto_select_keeps_only_analyzable_statuses(capsys):
    cands = [_Cand("ok"), _Cand("uncertain"), _Cand("error"), _Cand("rejected"),
             _Cand("ok")]
    picked = _auto_select_noninteractive(cands)
    assert [c.status for c in picked] == ["ok", "uncertain", "ok"]
    out = capsys.readouterr().out
    assert "非交互模式" in out and "3/5" in out


def test_auto_select_empty_returns_empty(capsys):
    assert _auto_select_noninteractive([_Cand("error")]) == []
    assert "0/1" in capsys.readouterr().out


def test_interactive_select_still_prompts_and_eof_cancels(monkeypatch):
    """交互路径行为零变化：仍然 input()，EOF → None。"""
    prompts: list[str] = []

    def _fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        raise EOFError

    monkeypatch.setattr("builtins.input", _fake_input)
    assert _interactive_select([_Cand("ok")]) is None
    assert prompts, "交互挑选必须仍走 input()（非交互分支才自动全选）"


def test_cmd_discover_noninteractive_branch_precedes_interactive():
    """源码序锁：cmd_discover 内非交互自动全选分支必须先于交互弹窗判定。

    回归背景：非交互模式下先弹 input 再判 EOF-cancel = 静默取消（本次事故）。
    """
    src = (Path(__file__).resolve().parents[1] / "scripts" / "cli.py").read_text(
        encoding="utf-8")
    auto_idx = src.find("_auto_select_noninteractive(candidates)")
    interactive_idx = src.find("selected = _interactive_select(candidates)")
    assert auto_idx != -1, "cmd_discover 缺非交互自动全选分支"
    assert interactive_idx != -1, "cmd_discover 交互挑选分支丢失"
    assert auto_idx < interactive_idx, "非交互分支必须先于交互弹窗（先判 args.non_interactive）"
    # 自动全选与「回车全选可挑」同一状态口径（ok/uncertain），防口径漂移
    auto_block = src[src.find("def _auto_select_noninteractive"):auto_idx]
    assert re.search(r'"ok",\s*"uncertain"', auto_block) or re.search(
        r"'ok',\s*'uncertain'", auto_block)

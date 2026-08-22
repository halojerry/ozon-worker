"""冒烟测试：验证 run_skill_command 的参数映射与 server 工具注册。

不依赖真实 Chrome/worker，只测：
1. run_skill_command 的 argv 组装逻辑（用 check 命令，或 mock subprocess）
2. server 里 19 个工具都能正确导入 + 调用
"""

from __future__ import annotations

import pytest

from pounding_mcp.server import mcp
from pounding_mcp import skill_runner


def test_run_skill_command_builds_argv(monkeypatch):
    """验证参数映射：位置参数 + 布尔 flag + 下划线转连字符。"""
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    skill_runner.run_skill_command("search", "关键词", page_size=5, sort="sold_desc")
    argv = captured["argv"]
    assert argv[0] == skill_runner.SKILL_PYTHON
    assert argv[-2:] == ["--sort", "sold_desc"]
    assert "--page-size" in argv and "5" in argv
    assert "关键词" in argv  # 位置参数
    assert "search" in argv


def test_run_skill_command_bool_flag(monkeypatch):
    """布尔 True → 单 flag；False → 跳过。"""
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    skill_runner.run_skill_command("graph", url="u", no_submit=True, notify=False)
    argv = captured["argv"]
    assert "--no-submit" in argv          # True → flag
    assert "--notify" not in argv         # False → 跳过


def test_all_tools_registered():
    """21 个工具都注册到 FastMCP。"""
    import asyncio

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    expected = {
        "check", "list_stores", "set_store", "set_token", "set_ak", "get_ak",
        "search", "probe", "image_search", "category", "follow", "discover",
        "discover_multi", "seller", "queries", "graph", "query", "update", "cleanup",
        "analyze_store", "run_store_action",
    }
    missing = expected - names
    assert not missing, f"未注册的工具: {missing}"
    assert len(names) == 21, f"工具数应为 21，实际 {len(names)}"

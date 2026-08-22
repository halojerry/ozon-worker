"""锁定 search 命令的 query 位置参数映射（防回退：`unrecognized arguments: --query`）。

skill CLI 的 `search` 是纯位置参数（cli.py:1955 `sp.add_argument("query", ...)`，
**无 `--query` flag**）。因此所有把 query 传给 `search` 的调用链都必须把 query
转成位置参数（经 `_exec` / `run_skill_command("search", query, **flags)`），
否则 `_build_argv` 会把残留的 `query` key 映射成 `--query <值>` 传给 CLI →
argparse 报 `unrecognized arguments: --query`。

本测试锁定：
1. `_POSITIONAL["search"] == ["query"]`（映射声明存在）。
2. `_exec("search", {"query": ..., ...})` 构造的 argv 不含 `--query`，query 是位置参数。
3. 空 query 被跳过，不生成 flag。
4. category 工具（显式位置参数，正确模式）不被破坏。
"""

from __future__ import annotations

from unittest import mock

from pounding_mcp import tasks
from pounding_mcp.skill_runner import _build_argv


def test_positional_declared_for_search():
    """_POSITIONAL 必须声明 search 的 query 为位置参数。"""
    assert tasks._POSITIONAL.get("search") == ["query"]


def test_exec_search_positions_query_not_flag():
    """_exec('search', {'query': ...}) → run_skill_command 收到位置参数 query，无 --query flag。"""
    captured = {}

    def fake_run(cmd, *positional, **flags):
        captured["cmd"] = cmd
        captured["positional"] = positional
        captured["flags"] = flags
        return {"ok": True}

    with mock.patch.object(tasks, "run_skill_command", fake_run):
        tasks._exec("search", {"query": "手机壳", "page_size": 3})

    assert captured["cmd"] == "search"
    assert captured["positional"] == ("手机壳",)  # 位置参数
    assert "query" not in captured["flags"]  # 没把 query 残留成 flag
    assert captured["flags"].get("page_size") == 3


def test_exec_search_argv_has_no_query_flag():
    """完整链路：_exec → run_skill_command → _build_argv，argv 不含 --query。"""
    argv_captured = {}

    def fake_run(cmd, *positional, **flags):
        argv_captured["argv"] = _build_argv(cmd, positional, flags)
        return {"ok": True}

    with mock.patch.object(tasks, "run_skill_command", fake_run):
        tasks._exec("search", {"query": "手机壳", "page_size": 3, "sort": "sold_desc"})

    argv = argv_captured["argv"]
    assert "--query" not in argv
    assert "手机壳" in argv  # 位置参数
    assert "--page-size" in argv and "3" in argv
    assert "--sort" in argv and "sold_desc" in argv


def test_empty_query_skipped_not_flag():
    """空 query 不占位置参数，也不生成 --query flag（_build_argv 跳过空字符串值）。"""
    argv_captured = {}

    def fake_run(cmd, *positional, **flags):
        argv_captured["argv"] = _build_argv(cmd, positional, flags)
        return {"ok": True}

    with mock.patch.object(tasks, "run_skill_command", fake_run):
        tasks._exec("search", {"query": "", "page_size": 3})

    assert "--query" not in argv_captured["argv"]  # 绝不生成 --query
    assert "--page-size" in argv_captured["argv"] and "3" in argv_captured["argv"]


def test_category_positional_not_broken():
    """category 工具走显式位置参数（run_skill_command('category', query, ...)），必须保持。"""
    argv = _build_argv("category", ("护手霜",), {"lang": "ZH_HANS"})
    assert "--query" not in argv
    assert "护手霜" in argv
    assert "--lang" in argv and "ZH_HANS" in argv

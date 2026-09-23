"""v0.79 agent 人体工学（PLAN-agent-ergonomics-v1 Task C1）：

命令出口 NEXT 行——把「下一步做什么」从 agent 推断变成工具告知。
锁定点：①``_print_next`` 固定前缀；②``_wait_task_terminal`` 四终态各带 NEXT；
③``_print_query_result`` 终态/中间态 NEXT；④错误恢复路径文案存在性。
纯 stdout 断言，零业务 mock 之外的依赖。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cli  # noqa: E402


# ── ① helper 固定前缀 ──────────────────────────────────────────────

def test_print_next_prefix_and_flush(capsys):
    cli._print_next("测试动作")
    out = capsys.readouterr().out
    assert out.startswith("👉 NEXT: 测试动作")
    assert out.endswith("\n")


def test_print_next_empty_string_still_prefix(capsys):
    """空文案也必须有前缀行（接线一致性——绝不出空行假 NEXT）。"""
    cli._print_next("")
    assert "👉 NEXT:" in capsys.readouterr().out


# ── ② _wait_task_terminal 四终态 ───────────────────────────────────

def _patch_poll(status: str, extra: dict | None = None):
    ret = {"status": status}
    ret.update(extra or {})
    return mock.patch("scripts.cloud_probe.poll_task_status", return_value=ret)


def test_wait_terminal_completed_has_next(capsys):
    with _patch_poll("completed", {"result_json": {"product_id": "12345"}}):
        cli._wait_task_terminal("T-1")
    out = capsys.readouterr().out
    assert "✅ 任务完成 task_id=T-1 product_id=12345" in out
    assert "👉 NEXT:" in out
    assert "汇报" in out


def test_wait_terminal_failed_has_recovery_next(capsys):
    with _patch_poll("failed", {"error_message": "VALUE_MAX_LIMIT 超上限\n第二行"}):
        cli._wait_task_terminal("T-2")
    out = capsys.readouterr().out
    assert "❌ 任务失败 task_id=T-2" in out
    assert "👉 NEXT:" in out
    assert "error-codes.md" in out
    assert "report" in out


def test_wait_terminal_timeout_has_query_next(capsys):
    with _patch_poll("timeout"):
        cli._wait_task_terminal("T-3")
    out = capsys.readouterr().out
    assert "⏱️ 等待超时" in out
    assert "query T-3" in out
    assert "勿秒级轮询" in out


def test_wait_terminal_other_status_has_next(capsys):
    with _patch_poll("cancelled"):
        cli._wait_task_terminal("T-4")
    assert "👉 NEXT:" in capsys.readouterr().out


# ── ③ _print_query_result 出口 ─────────────────────────────────────

def test_query_result_completed_next(capsys):
    cli._print_query_result("Q-1", {"ok": True, "result_json": {"product_summary": []},
                                     "status": "completed"})
    out = capsys.readouterr().out
    assert "✅ 任务完成(无产品明细)" in out
    assert "output-schema" in out


def test_query_result_processing_next_teaches_watch(capsys):
    cli._print_query_result("Q-2", {"ok": False, "status": "processing"})
    out = capsys.readouterr().out
    assert "query Q-2 --watch" in out
    assert "勿秒级轮询" in out


def test_query_result_failed_next_mentions_report(capsys):
    cli._print_query_result("Q-3", {"ok": False, "status": "failed",
                                     "error_message": "boom"})
    out = capsys.readouterr().out
    assert "❌ 错误: boom" in out
    assert "error-codes.md" in out


def test_query_result_not_found_next(capsys):
    cli._print_query_result("Q-4", {"ok": False, "status": "not_found"})
    assert "核对 task_id" in capsys.readouterr().out


def test_query_result_worker_unreachable_next(capsys):
    cli._print_query_result("Q-5", {"ok": False, "status": "worker_unreachable"})
    assert "WORKER_URL" in capsys.readouterr().out


# ── ④ 接线存在性（防回归：出口不许丢 NEXT）────────────────────────

@pytest.mark.parametrize("needle", [
    "展示模式（--no-submit）",          # graph 展示态
    "展示模式（未带 --auto-submit）",   # follow 展示态
    "重跑本命令并加 --wait 排队",       # 锁占用 exit 4
    "按上方指引补配置",                # preflight 失败
    "按上方 ❌ 项逐个补齐",             # check 失败汇总
    "批量入箱完成",                    # discover/search/discover-task 入箱出口
    "本次为干跑",                      # discover-task 干跑出口
    "环境就绪",                        # check 成功出口
])
def test_next_wiring_needles_present(needle):
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert needle in src, f"NEXT 接线缺失：{needle}"

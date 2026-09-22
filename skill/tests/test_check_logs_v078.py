#!/usr/bin/env python3
"""批B5: check --logs（只读日志通道）纯 mock 回归。

- `check --logs <task_id>`：read_task_log 复活（全仓此前零调用），打印 JSONL 事件
- `check --logs`（不带值）：列 data/logs/ 最近 5 个日志文件
- 不带 --logs：None → 正常环境诊断路径（本文件不触发 CDP）
- argparse：nargs="?" 三态（None / "" / task_id）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_check_logs_v078.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402
from scripts.lib import logging_utils  # noqa: E402


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _run_print_logs(task_id, tmp_path):
    monkey_logs = mock.patch.object(logging_utils, "LOGS_DIR", tmp_path)
    const_logs = mock.patch("scripts._const.LOGS_DIR", tmp_path)
    with ExitStack() as stack:
        stack.enter_context(monkey_logs)
        stack.enter_context(const_logs)
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli._print_logs(task_id)
    return rc, out.getvalue()


def test_print_logs_task_id_outputs_jsonl(tmp_path):
    """①--logs <task_id>：逐行输出 JSONL 事件（复活 read_task_log）。"""
    recs = [
        {"ts": 1, "task_id": "t-1", "comp": "ak1688", "stage": "search",
         "level": "info", "msg": "Fetched"},
        {"ts": 2, "task_id": "t-1", "comp": "cdp", "stage": "probe",
         "level": "warn", "msg": "CDP degraded", "data": {"source": "api_only"}},
    ]
    _write_jsonl(tmp_path / "t-1.jsonl", recs)
    rc, out = _run_print_logs("t-1", tmp_path)
    assert rc == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["msg"] == "Fetched"
    assert json.loads(lines[1])["level"] == "warn"


def test_print_logs_missing_task_returns_1(tmp_path):
    """②task_id 不存在 → 人话提示 + exit 1。"""
    rc, out = _run_print_logs("nope", tmp_path)
    assert rc == 1
    assert "nope" in out


def test_print_logs_without_task_lists_recent_five(tmp_path):
    """③--logs 不带值：列最近 5 个日志文件（mtime 降序），多余的不列。"""
    for i in range(7):
        p = tmp_path / f"run_2026010{i}_x.log" if i % 2 else tmp_path / f"t-{i}.jsonl"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (time.time() + i, time.time() + i))  # i 越大越新
    rc, out = _run_print_logs("", tmp_path)
    assert rc == 0
    assert str((tmp_path / "t-6.jsonl").name) in out, "最新文件在列"
    assert str((tmp_path / "t-0.jsonl").name) not in out, "只列最近 5 个"
    listed = [ln for ln in out.splitlines() if ln.strip().startswith(("run_", "t-"))]
    assert len(listed) == 5


def test_print_logs_empty_dir(tmp_path):
    """④空日志目录 → 人话提示不崩。"""
    rc, out = _run_print_logs("", tmp_path)
    assert rc == 0
    assert "暂无" in out or "无" in out


def test_check_logs_argparse_three_states():
    """⑤argparse 三态：缺省 None / 裸 --logs "" / --logs t1。"""
    parser = cli.build_arg_parser()
    assert parser.parse_args(["check"]).logs is None
    assert parser.parse_args(["check", "--logs"]).logs == ""
    assert parser.parse_args(["check", "--logs", "t1"]).logs == "t1"


def test_check_cmd_short_circuits_to_logs(tmp_path, monkeypatch):
    """⑥cmd_check 收到 --logs 先走日志通道并短路，不进环境诊断（零 Chrome/网络）。"""
    monkeypatch.setattr(logging_utils, "LOGS_DIR", tmp_path)
    monkeypatch.setattr("scripts._const.LOGS_DIR", tmp_path)
    _write_jsonl(tmp_path / "t-9.jsonl",
                 [{"ts": 1, "task_id": "t-9", "msg": "hello"}])
    args = cli.build_arg_parser().parse_args(["check", "--logs", "t-9"])
    called = {}

    def _spy(task_id=""):
        called["task_id"] = task_id
        return 0

    # 诊断入口第一站 check_config 若被触达 → 证明未短路（测试失败信号清晰）
    def _no_diag(*a, **kw):
        raise AssertionError("cmd_check --logs 未短路，仍进了环境诊断")

    with mock.patch.object(cli, "_print_logs", side_effect=_spy), \
            mock.patch("scripts.lib.config_store.check_config",
                       side_effect=_no_diag), \
            mock.patch("sys.stdout", io.StringIO()):
        rc = cli.cmd_check(args)
    assert rc == 0
    assert called == {"task_id": "t-9"}


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

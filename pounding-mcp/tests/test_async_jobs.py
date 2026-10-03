#!/usr/bin/env python3
"""后台任务注册表回归（惰性收割 / 终态粘性 / job_* 工具）。

核心承诺：
① 孤儿任务（server 重启/watch 线程死）由 get()/list() 惰性收割按日志定案；
② 同步路径 run_and_record 行为不变；
③ 终态粘性：completed 落定后 _finish(failed) 不得翻盘；
④ 注册表原子写合并（盘上他进程的任务不被整文件互踩）。

注（2026-10 死代码清扫）：v0.70 旧后台路径 CollectTaskManager.start_background
（连同 _watch / _spawn_detach_kwargs / 单飞闸）已删除——v0.83 批④ 起后台统一走
skill `--detach`（server._start_skill_job，`skill/data/jobs/` 注册表单一事实源）。

运行：
    cd pounding-mcp && .venv/bin/python -m pytest tests/test_async_jobs.py -q
"""
from __future__ import annotations

import json
import subprocess
import time

import pytest

from pounding_mcp import tasks as tasks_mod
from pounding_mcp.server import _run_or_background
from pounding_mcp.tasks import CollectTaskManager


@pytest.fixture()
def mgr(tmp_path):
    """独立 store 的 manager。"""
    return CollectTaskManager(store_path=tmp_path / "tasks.json", max_tasks=50)


def test_reap_orphan_from_log(mgr, tmp_path):
    """孤儿收割：registry running + pid 已死 + 日志有尾部 JSON → completed。"""
    log = tmp_path / "orphan.log"
    log.write_text("阶段 1/3\n[2/5] x\n{\"ok\": 9, \"candidates\": [1]}\n", encoding="utf-8")
    dead = subprocess.Popen(["true"])
    dead.wait()

    mgr._tasks["orphan1"] = {
        "id": "orphan1", "kind": "discover", "label": "选品采集",
        "params": {}, "source": "agent", "status": "running",
        "created_at": "x", "started_at": time.time() - 60, "finished_at": None,
        "summary": None, "error": None,
        "pid": dead.pid, "log": str(log),
    }
    t = mgr.get("orphan1")
    assert t["status"] == "completed", t.get("error")
    assert t["summary"]
    assert mgr.extract_worker_task_ids("orphan1") == []   # 无 task_id 键


def test_reap_orphan_interrupted(mgr, tmp_path):
    """孤儿收割：pid 死 + 日志空 → interrupted（如实区分于正常失败）。"""
    log = tmp_path / "empty.log"
    log.write_text("", encoding="utf-8")
    dead = subprocess.Popen(["true"])
    dead.wait()
    mgr._tasks["orphan2"] = {
        "id": "orphan2", "kind": "discover", "label": "选品采集",
        "params": {}, "source": "agent", "status": "running",
        "created_at": "x", "started_at": time.time(), "finished_at": None,
        "summary": None, "error": None, "pid": dead.pid, "log": str(log),
    }
    t = mgr.get("orphan2")
    assert t["status"] == "interrupted"


def test_reap_skips_unverifiable_sync_tasks(mgr):
    """无 pid 无日志（他进程同步路径）不误判——保持 running 原状。"""
    mgr._tasks["sync1"] = {
        "id": "sync1", "kind": "search", "label": "关键词采集",
        "params": {}, "source": "agent", "status": "running",
        "created_at": "x", "started_at": time.time(), "finished_at": None,
        "summary": None, "error": None,
    }
    assert mgr.get("sync1")["status"] == "running"


def test_run_or_background_sync_path_unchanged(mgr, monkeypatch):
    """background=False（v0.83 批④ 起为显式同步 opt-out）走同步 run_and_record。"""
    monkeypatch.setattr("pounding_mcp.server.get_manager", lambda: mgr)
    monkeypatch.setattr(tasks_mod, "run_skill_command",
                        lambda *a, **k: {"ok": True, "result": "sync"})
    out = _run_or_background("discover", {"keyword": "x"}, background=False)
    assert out == {"ok": True, "result": "sync"}
    items = mgr.list(10)
    assert items and items[0]["status"] == "completed"


def test_store_merge_keeps_foreign_tasks(mgr, tmp_path):
    """原子写合并：盘上他进程的任务在本进程 _save 后保留（防整文件互踩）。"""
    store = tmp_path / "tasks.json"
    foreign = {"id": "foreign", "kind": "discover", "status": "completed",
               "started_at": 1}
    store.write_text(json.dumps({"foreign": foreign}), encoding="utf-8")
    m2 = CollectTaskManager(store_path=store, max_tasks=50)
    m2._register("search", {"query": "q"}, source="agent")
    m2._save()
    merged = json.loads(store.read_text(encoding="utf-8"))
    assert "foreign" in merged and any(v.get("kind") == "search"
                                       for v in merged.values())


def test_export_param_passthrough(mgr, tmp_path, monkeypatch):
    """discover_task 的 export / discover 的 export+output 落参数映射（--export）。"""
    captured: dict = {}
    monkeypatch.setattr(tasks_mod, "run_skill_command",
                        lambda cmd, *a, **flags: captured.update(cmd=cmd, flags=flags)
                        or {"ok": True})
    monkeypatch.setattr("pounding_mcp.server.get_manager",
                        lambda: CollectTaskManager(store_path=tmp_path / "t.json"))
    _run_or_background("discover_task",
                       {"keyword": "x", "export": "/tmp/out.csv"}, background=False)
    assert captured["flags"]["export"] == "/tmp/out.csv"
    _run_or_background("discover",
                       {"keyword": "x", "export": "csv", "output": "/tmp/o"},
                       background=False)
    assert captured["flags"]["export"] == "csv" and captured["flags"]["output"] == "/tmp/o"


def test_cli_command_alias_translation():
    """工具名（下划线）→ CLI 命令名（discover-task 连字符）显式映射。"""
    from pounding_mcp.skill_runner import _build_argv

    argv = _build_argv("discover_task", (), {"keyword": "手套"})
    assert argv[2] == "discover-task" and "--keyword" in argv
    assert _build_argv("discover_multi", ())[2] == "discover-multi"
    assert _build_argv("search", ("词",))[2] == "search"   # 下划线命名 CLI 不受影响


def test_cli_accepts_translated_commands():
    """真 CLI 校验：翻译后的命令名必须被 argparse 接受（防 mock 盲区回归）。"""
    import subprocess

    from pounding_mcp.skill_runner import _CLI, SKILL_PYTHON, _CLI_COMMAND_ALIASES

    for tool, cmd in _CLI_COMMAND_ALIASES.items():
        r = subprocess.run([SKILL_PYTHON, str(_CLI), cmd, "--help"],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"CLI 不接受 {cmd}（来自工具 {tool}）: {r.stderr[:200]}"


def test_terminal_status_sticky(mgr):
    """终态粘性：completed 落定后 _finish(failed) 不得翻盘。

    竞品上品帮实证（2026-08-16 win32 日志）：清理阶段窗口崩溃把已 completed
    的任务覆盖成 failed，采满 327 个商品的状态全丢——此处锁死该缺陷不可发生。
    """
    task = mgr._register("discover", {"keyword": "手套"}, source="agent")
    mgr._finish(task["id"], "completed", summary={"candidates": 1},
                started=task["started_at"])
    assert mgr.get(task["id"])["status"] == "completed"

    # 模拟迟到的失败回调（竞态/重复收割）
    mgr._finish(task["id"], "failed", error="迟到的窗口异常关闭")
    after = mgr.get(task["id"])
    assert after["status"] == "completed"
    assert not after.get("error")

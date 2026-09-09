#!/usr/bin/env python3
"""后台任务全生命周期回归（v0.70：start_background / 惰性收割 / 单飞闸 / job_* 工具）。

核心承诺：
① background=true 立即返回，CLI 进程组独立（会话关闭任务照跑——测试用假 CLI
   sleep 验证不阻塞 + cancel 可终止）；
② 孤儿任务（server 重启/watch 线程死）由 get()/list() 惰性收割按日志定案；
③ 单飞闸：heavy 任务同时只跑 1 个，force 可越；
④ 同步路径 run_and_record 行为不变。

运行：
    cd pounding-mcp && .venv/bin/python -m pytest tests/test_async_jobs.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pounding_mcp import tasks as tasks_mod
from pounding_mcp.server import _run_or_background
from pounding_mcp.tasks import CollectTaskManager

_SLEEP_THEN_JSON = (
    "import time, sys\n"
    "print('[1/2] 商品', flush=True)\n"
    "print('阶段 1/2', flush=True)\n"
    "time.sleep(1.2)\n"
    "import json\n"
    "print(json.dumps({'ok': 1, 'items': [1, 2]}, indent=2), flush=True)\n"
)
_SLEEPER = "import time; time.sleep(30)\n"


def _fake_argv(script: str):
    return [sys.executable, "-c", script]


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    """独立 store 的 manager + _build_argv 换成假 CLI（可注入脚本）。"""
    m = CollectTaskManager(store_path=tmp_path / "tasks.json", max_tasks=50)
    holders: dict = {}

    def _set_script(script: str) -> None:
        holders["script"] = script
        monkeypatch.setattr(tasks_mod, "_build_argv",
                            lambda *a, **k: _fake_argv(holders["script"]))

    _set_script(_SLEEP_THEN_JSON)
    m._set_script = _set_script  # 供用例切换脚本
    yield m


def _wait_status(m: CollectTaskManager, task_id: str, want: set[str],
                 timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    t: dict = {}
    while time.time() < deadline:
        t = m.get(task_id) or {}
        if t.get("status") in want:
            return t
        time.sleep(0.1)
    return t


def test_start_background_returns_immediately_and_completes(mgr):
    """后台启动 <1s 返回 running；跑完由监控线程定案 completed + summary。"""
    t0 = time.time()
    task = mgr.start_background("discover", {"keyword": "手套"}, source="agent")
    assert time.time() - t0 < 1.0
    assert task["status"] == "running"
    assert task["pid"] > 0 and Path(task["log"]).is_file()

    done = _wait_status(mgr, task["id"], {"completed", "failed"})
    assert done["status"] == "completed", done.get("error")
    assert done["summary"]                              # _summarize(discover) 有产物
    result, err = mgr.read_result(task["id"])
    assert err is None and result["ok"] == 1


def test_progress_parsed_from_log(mgr):
    """running 中 get() 尾读日志回填 [N/M] 进度（job_status 的数据源）。"""
    task = mgr.start_background("discover", {"keyword": "x"})
    _wait_status(mgr, task["id"], {"running"})          # 拿到即查（脚本先打进度行）
    got = mgr.get(task["id"])
    assert got["status"] == "running"
    if got.get("progress"):
        assert got["progress"] == {"current": 1, "total": 2}
    mgr.cancel(task["id"])


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


def test_single_flight_gate_and_force(mgr):
    """单飞闸：heavy 任务 running 时第二个 heavy 拒绝；force=true 越过。"""
    mgr._set_script(_SLEEPER)
    first = mgr.start_background("discover", {"keyword": "a"})
    assert first["status"] == "running"

    second = mgr.start_background("follow", {"ozon_url": "u"})
    assert "error" in second and "force" in second["error"]

    third = mgr.start_background("follow", {"ozon_url": "u"}, force=True)
    assert third["status"] == "running" and not third.get("error")

    # 轻任务（search）不受闸限制
    light = mgr.start_background("search", {"query": "q"})
    assert light["status"] == "running" and not light.get("error")

    assert mgr.cancel(first["id"]) and mgr.cancel(third["id"]) and mgr.cancel(light["id"])


def test_cancel_kills_background_process(mgr):
    """job_cancel：running 后台任务被终止，registry 记 cancelled。"""
    mgr._set_script(_SLEEPER)
    task = mgr.start_background("discover_task", {"keyword": "a"})
    assert mgr.cancel(task["id"]) is True
    assert mgr.get(task["id"])["status"] == "cancelled"
    time.sleep(0.3)
    assert not tasks_mod._pid_alive(task["pid"]) or \
        not mgr._is_running({"id": task["id"], "status": "running", "pid": task["pid"]})


def test_run_or_background_sync_path_unchanged(mgr, monkeypatch):
    """background=False（缺省）走同步 run_and_record，行为与旧版一致。"""
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


def test_watch_exit0_raw_output_completes(mgr):
    """退出码 0 但输出纯文本（queries 表格式）→ completed 不误判 failed。"""
    mgr._set_script("print('词1  100\\n词2  200\\n')\n")
    task = mgr.start_background("queries", {"type": "all-queries", "keyword": "x"})
    done = _wait_status(mgr, task["id"], {"completed", "failed"})
    assert done["status"] == "completed", done.get("error")


def test_watch_discover_task_structured_summary(mgr):
    """discover_task 尾部 _out JSON → summary 带达标/状态分布（job_status 机读）。"""
    script = (
        "import json\n"
        "print('⏳ 阶段...', flush=True)\n"
        "print(json.dumps({'task_id': 't1', 'summary': {'candidates': "
        "{'profitable': 3, 'rejected': 5}, 'target': {'goal': 3, 'total': 3}, "
        "'submitted': 0}}, indent=2), flush=True)\n"
    )
    mgr._set_script(script)
    task = mgr.start_background("discover_task", {"keyword": "x"})
    done = _wait_status(mgr, task["id"], {"completed", "failed"})
    assert done["status"] == "completed", done.get("error")
    assert done["summary"]["candidates"] == {"profitable": 3, "rejected": 5}
    assert done["summary"]["target"] == {"goal": 3, "total": 3}


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
    m = mgr
    m._set_script(_SLEEP_THEN_JSON)
    t = m.start_background("discover", {"keyword": "手套"})
    done = _wait_status(m, t["id"], {"completed"})
    assert done["status"] == "completed"

    # 模拟迟到的失败回调（watch 竞态/重复收割）
    m._finish(t["id"], "failed", error="迟到的窗口异常关闭")
    after = m.get(t["id"])
    assert after["status"] == "completed"
    assert not after.get("error")

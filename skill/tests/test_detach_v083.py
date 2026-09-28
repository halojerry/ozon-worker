#!/usr/bin/env python3
"""v0.83 批④：skill ``--detach`` 后台化单测（方案 §3④）。

覆盖：①job_id 形态与注册表读写；②孤儿收割（running+死 pid → interrupted）；
③``spawn_detached``（--wait 互斥 / 闸占 fail-fast / fork 参数与环境变量）；
④``finish_job`` 终态回写 + run_id 提取 + 终态粘性；⑤``jobs``/``job-status``/
``job-result`` 出口（尾 JSON + NEXT 行）；⑥六重命令 ``--detach`` flag 接线。

运行：cd skill && .venv314/bin/python -m pytest tests/test_detach_v083.py -q
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock  # noqa: F401  （保留给后续用例扩展）

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cli  # noqa: E402
from scripts.lib import detach  # noqa: E402
from scripts.lib import lock_utils  # noqa: E402


@pytest.fixture()
def jobs_tmp(tmp_path, monkeypatch):
    """隔离注册表目录 + 闸锁路径（绝不碰仓库 data/）。"""
    monkeypatch.setenv("OZON_SKILL_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(detach, "HEAVY_LOCK_PATH", tmp_path / "heavy.lock")
    return tmp_path / "jobs"


def _ns(**kw) -> argparse.Namespace:
    base = {"command": "discover", "detach": True, "wait": False, "force": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _write_job(jid: str, **over) -> dict:
    detach._ensure_dir()
    data = {"schema_version": "skill.job.v1", "job_id": jid, "kind": "discover",
            "status": "running", "pid": None, "started_at": 1.0,
            "finished_at": None, "exit_code": None, "run_id": "", "session_path": "",
            "error": None, "log": str(detach.job_log_path(jid))}
    data.update(over)
    detach._atomic_write(detach.job_path(jid), data)
    return data


# ── ① job_id 形态 ───────────────────────────────────────────────────

def test_job_id_shape_and_validation():
    jid = detach.new_job_id()
    assert detach.is_valid_job_id(jid)
    # 本地时间戳坑：裸 YYYYMMDD_HHMMSS 不是 job_id（先例 extract_worker_task_ids）
    assert not detach.is_valid_job_id("20260928_120000")
    assert not detach.is_valid_job_id("disc_260928_120000_ab12cd")
    assert not detach.is_valid_job_id("../evil")
    assert not detach.is_valid_job_id("")


# ── ② 注册表读写 + 孤儿收割 ──────────────────────────────────────────

def test_registry_roundtrip_and_list(jobs_tmp):
    jid = detach.new_job_id()
    _write_job(jid, pid=os.getpid())
    job = detach.read_job(jid)
    assert job and job["kind"] == "discover"
    assert [j["job_id"] for j in detach.list_jobs(10)] == [jid]
    assert detach.read_job("20260101_000000_abcdef") is None


def test_orphan_reap_marks_interrupted_and_persists(jobs_tmp):
    dead = subprocess.Popen(["true"])
    dead.wait()
    jid = detach.new_job_id()
    _write_job(jid, pid=dead.pid)
    assert detach.read_job(jid)["status"] == "interrupted"
    # 落盘生效（幂等）
    assert json.loads(detach.job_path(jid).read_text(encoding="utf-8"))["status"] == "interrupted"


def test_running_with_alive_or_missing_pid_not_reaped(jobs_tmp):
    jid_alive = detach.new_job_id()
    _write_job(jid_alive, pid=os.getpid())
    assert detach.read_job(jid_alive)["status"] == "running"
    # pid 尚未回填（spawn 与 reader 竞态窗口）不得误判
    jid_none = detach.new_job_id()
    _write_job(jid_none, pid=None)
    assert detach.read_job(jid_none)["status"] == "running"


def test_terminal_status_not_reaped(jobs_tmp):
    jid = detach.new_job_id()
    _write_job(jid, status="completed", pid=None)
    assert detach.read_job(jid)["status"] == "completed"


def test_extract_tail_json_skips_next_line():
    text = ("progress line\n{\n  \"run_id\": \"disc_260928_120000_ab12cd\",\n"
            "  \"n\": 3\n}\n👉 NEXT: do x\n")
    assert detach.extract_tail_json(text)["n"] == 3
    assert detach.extract_tail_json("no json here") == {}


# ── ③ spawn_detached ────────────────────────────────────────────────

def test_spawn_mutual_exclusion_with_wait(jobs_tmp):
    res = detach.spawn_detached(_ns(wait=True))
    assert res["code"] == 2 and not res["job_id"] and "互斥" in res["error"]
    assert detach.list_jobs(10) == []


def test_spawn_gate_busy_fast_fail(jobs_tmp):
    fd = lock_utils.try_acquire(detach.HEAVY_LOCK_PATH, timeout=0)
    assert fd is not None
    try:
        res = detach.spawn_detached(_ns())
        assert res["code"] == 4 and not res["job_id"]
        assert "闸被占" in res["error"]
    finally:
        lock_utils.release(fd)


def test_spawn_force_skips_gate_probe(jobs_tmp, monkeypatch):
    fd = lock_utils.try_acquire(detach.HEAVY_LOCK_PATH, timeout=0)
    assert fd is not None
    class _P:
        pid = os.getpid()
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **k: _P())
    monkeypatch.setattr(sys, "argv", ["cli.py", "seller", "--seller-id", "1", "--detach"])
    try:
        res = detach.spawn_detached(_ns(command="seller", force=True))
        assert res["code"] == 0 and res["job_id"]
    finally:
        lock_utils.release(fd)


def test_spawn_detached_forks_and_registers(jobs_tmp, monkeypatch):
    captured: dict = {}

    class _P:
        pid = os.getpid()

    def fake_popen(argv, **kw):
        captured["argv"] = list(argv)
        captured["env"] = dict(kw.get("env") or {})
        captured["cwd"] = kw.get("cwd")
        return _P()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sys, "argv",
                        ["cli.py", "discover", "--keyword", "手套", "--detach", "--to-box"])
    res = detach.spawn_detached(_ns())

    assert res["code"] == 0 and detach.is_valid_job_id(res["job_id"])
    argv = captured["argv"]
    assert "--detach" not in argv                      # 子进程不含 --detach
    assert argv[2] == "discover" and "--keyword" in argv and "--to-box" in argv
    assert captured["env"]["OZON_SKILL_JOB_ID"] == res["job_id"]
    assert captured["cwd"]  # skill 根目录
    job = detach.read_job(res["job_id"])
    assert job["status"] == "running" and job["pid"] == os.getpid()
    assert Path(job["log"]).is_file()
    assert job["kind"] == "discover"


# ── ④ finish_job 终态回写 ───────────────────────────────────────────

def test_finish_job_terminal_run_id_and_sticky(jobs_tmp):
    jid = detach.new_job_id()
    _write_job(jid, pid=os.getpid())
    detach.job_log_path(jid).write_text(
        "progress\n{\n  \"run_id\": \"disc_260928_120000_ab12cd\",\n"
        "  \"session_path\": \"/tmp/sess.json\"\n}\n👉 NEXT: n\n", encoding="utf-8")
    detach.finish_job(jid, 0)
    job = detach.read_job(jid)
    assert job["status"] == "completed" and job["exit_code"] == 0
    assert job["run_id"] == "disc_260928_120000_ab12cd"
    assert job["session_path"] == "/tmp/sess.json"
    # 终态粘性：迟到失败回调不翻盘
    detach.finish_job(jid, 1)
    assert detach.read_job(jid)["status"] == "completed"


def test_finish_job_failed_carries_tail(jobs_tmp):
    jid = detach.new_job_id()
    _write_job(jid, pid=os.getpid())
    detach.job_log_path(jid).write_text("❌ 出错了\n", encoding="utf-8")
    detach.finish_job(jid, 3)
    job = detach.read_job(jid)
    assert job["status"] == "failed" and job["exit_code"] == 3 and job["error"]


def test_finish_job_noop_without_job_id(jobs_tmp):
    detach.finish_job("", 0)  # 不抛
    assert detach.list_jobs(10) == []


def test_claim_tracking_job_id_pops_env(monkeypatch):
    """子进程认领 job_id 即清 env——嵌套 spawn 的 CLI 不会误认领同一 job。"""
    monkeypatch.setattr(detach, "_CLAIMED_JOB_ID", "")
    monkeypatch.setenv("OZON_SKILL_JOB_ID", "20260928_120000_abcdef")
    assert detach.claim_tracking_job_id() == "20260928_120000_abcdef"
    assert "OZON_SKILL_JOB_ID" not in os.environ
    assert detach.tracking_job_id() == "20260928_120000_abcdef"  # 仍可回写终态


# ── ⑤ 出口命令（jobs / job-status / job-result）+ NEXT ──────────────

def test_cmd_jobs_empty_has_next(jobs_tmp, capsys):
    rc = cli.cmd_jobs(argparse.Namespace(limit=10))
    out = capsys.readouterr().out
    assert rc == 0 and '"total": 0' in out
    assert "👉 NEXT:" in out


def test_cmd_job_status_and_result(jobs_tmp, capsys):
    jid = detach.new_job_id()
    _write_job(jid, status="completed", pid=os.getpid(),
               run_id="disc_260928_120000_ab12cd", session_path="/p.json")
    detach.job_log_path(jid).write_text(
        "{\n  \"run_id\": \"disc_260928_120000_ab12cd\",\n  \"candidates_count\": 5\n}\n"
        "👉 NEXT: x\n", encoding="utf-8")

    assert cli.cmd_job_status(argparse.Namespace(job_id=jid, log_tail=5)) == 0
    out = capsys.readouterr().out
    assert jid in out and "next_action" in out
    assert "disc_260928_120000_ab12cd" in out
    assert "👉 NEXT:" in out

    assert cli.cmd_job_result(argparse.Namespace(job_id=jid)) == 0
    out2 = capsys.readouterr().out
    assert '"candidates_count": 5' in out2 and "👉 NEXT:" in out2


def test_cmd_job_status_missing_has_next(jobs_tmp, capsys):
    rc = cli.cmd_job_status(argparse.Namespace(job_id="20260101_000000_abcdef", log_tail=5))
    out = capsys.readouterr().out
    assert rc == 1 and "任务不存在" in out and "👉 NEXT:" in out


def test_cmd_job_result_missing_has_next(jobs_tmp, capsys):
    rc = cli.cmd_job_result(argparse.Namespace(job_id="20260101_000000_abcdef"))
    out = capsys.readouterr().out
    assert rc == 1 and "👉 NEXT:" in out


def test_cmd_job_result_running_no_structured(jobs_tmp, capsys):
    jid = detach.new_job_id()
    _write_job(jid, status="running", pid=os.getpid())
    detach.job_log_path(jid).write_text("just progress\n", encoding="utf-8")
    rc = cli.cmd_job_result(argparse.Namespace(job_id=jid))
    out = capsys.readouterr().out
    assert rc == 1 and "未终态" in out and "👉 NEXT:" in out


# ── ⑤b _handle_detach 出口 ──────────────────────────────────────────

def test_handle_detach_success_prints_handle(jobs_tmp, capsys, monkeypatch):
    monkeypatch.setattr(detach, "spawn_detached", lambda args, heavy=True: {
        "code": 0, "job_id": "20260928_120000_abcdef", "log": "/tmp/l.log", "error": ""})
    rc = cli._handle_detach(_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "job_id=20260928_120000_abcdef" in out
    assert '"job_id": "20260928_120000_abcdef"' in out
    assert "👉 NEXT:" in out and "job-status" in out


def test_handle_detach_mutex_exit2(jobs_tmp, capsys):
    rc = cli._handle_detach(_ns(wait=True))
    out = capsys.readouterr().out
    assert rc == 2 and "互斥" in out and "👉 NEXT:" in out


def test_handle_detach_gate_busy_exit4(jobs_tmp, capsys):
    fd = lock_utils.try_acquire(detach.HEAVY_LOCK_PATH, timeout=0)
    assert fd is not None
    try:
        rc = cli._handle_detach(_ns())
    finally:
        lock_utils.release(fd)
    out = capsys.readouterr().out
    assert rc == 4 and "串行闸被占" in out and "👉 NEXT:" in out


# ── ⑥ --detach flag 接线（六重命令）─────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["discover", "--keyword", "x"],
    ["discover-multi", "--keywords", "a,b"],
    ["discover-task", "--keyword", "x"],
    ["follow", "--ozon-url", "https://www.ozon.ru/product/1/"],
    ["graph", "--url", "https://detail.1688.com/offer/1.html"],
    ["seller", "--seller-id", "1"],
])
def test_detach_flag_parses_on_six_heavy_commands(argv):
    args = cli.build_arg_parser().parse_args([*argv, "--detach"])
    assert args.detach is True


def test_queries_detach_is_light_no_gate_probe(jobs_tmp, monkeypatch):
    """queries（第 7 个 background 工具）--detach 不持重闸 → 闸被占也能启动。"""
    class _P:
        pid = os.getpid()

    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **k: _P())
    monkeypatch.setattr(sys, "argv", ["cli.py", "queries", "--type", "all-queries", "--detach"])
    fd = lock_utils.try_acquire(detach.HEAVY_LOCK_PATH, timeout=0)
    assert fd is not None
    try:
        res = detach.spawn_detached(
            argparse.Namespace(command="queries", detach=True, wait=False), heavy=False)
        assert res["code"] == 0 and res["job_id"]
    finally:
        lock_utils.release(fd)


def test_queries_detach_flag_parses_and_is_light():
    args = cli.build_arg_parser().parse_args(["queries", "--type", "all-queries", "--detach"])
    assert args.detach is True
    assert not hasattr(args, "force")  # light：无重闸参数（_handle_detach 据此判 heavy）


def test_next_needles_present_for_job_exits():
    src = Path(cli.__file__).read_text(encoding="utf-8")
    for needle in ("后台任务在跑", "串行闸被占——加 --wait", "job-status", "job-result"):
        assert needle in src, f"jobs 族 NEXT 接线缺失：{needle}"

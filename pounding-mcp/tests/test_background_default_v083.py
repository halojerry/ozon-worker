#!/usr/bin/env python3
"""v0.83 批④：pounding-mcp 后台默认翻转 + skill 注册表读取。

覆盖：①七工具 ``background`` 缺省 True（显式 False = 同步 opt-out）；
②``_run_or_background(background=True)`` 经 skill ``--detach``（argv 带 --detach）；
③``ALLOWED_PARAM_KEYS`` detach 透传；④``_parse_output`` 剥尾部 NEXT 行；
⑤``extract_discovery_run_id`` 跳过本地时间戳坑；⑥``job_status``/``job_result``
优先读 ``SKILL_JOBS_DIR`` 注册表并附 run_id/session_path。

运行：cd pounding-mcp && .venv/bin/python -m pytest tests/test_background_default_v083.py -q
"""
from __future__ import annotations

import inspect
import json
import os
import time

import pytest

from pounding_mcp import server, skill_jobs
from pounding_mcp.skill_runner import ALLOWED_PARAM_KEYS, _build_argv, _parse_output

_HEAVY = ("discover", "discover_multi", "discover_task", "follow", "seller",
          "queries", "graph")
_JID = "20260928_120000_abcdef"


# ── ① 默认翻转 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", _HEAVY)
def test_background_default_true(name):
    sig = inspect.signature(getattr(server, name))
    assert sig.parameters["background"].default is True
    assert sig.parameters["force"].default is False


@pytest.mark.parametrize("name", _HEAVY)
def test_detach_allowed_for_heavy(name):
    assert "detach" in ALLOWED_PARAM_KEYS[name]


def test_detach_not_allowed_for_light():
    for name in ("search", "probe", "image_search", "get_ak"):
        assert "detach" not in ALLOWED_PARAM_KEYS[name]


def test_build_argv_emits_detach_flag():
    argv = _build_argv("discover_task", (), {"keyword": "x", "detach": True})
    assert "--detach" in argv
    argv_q = _build_argv("queries", (), {"type": "all-queries", "detach": True})
    assert "--detach" in argv_q
    argv2 = _build_argv("graph", (), {"url": "u"})
    assert "--detach" not in argv2  # 缺省 False/None 不进 argv


# ── ② 后台路径经 skill --detach ─────────────────────────────────────

def test_run_or_background_uses_skill_detach(monkeypatch):
    captured: dict = {}

    def fake(cmd, *a, **flags):
        captured["cmd"] = cmd
        captured["flags"] = flags
        return {"job_id": _JID, "status": "running", "log": "/tmp/x.log"}

    monkeypatch.setattr(server, "run_skill_command", fake)
    out = server._run_or_background("discover", {"keyword": "手套"}, background=True)
    assert out["job_id"] == _JID and out["id"] == _JID
    assert out["status"] == "running" and out["next_poll_s"] == 20
    assert captured["cmd"] == "discover"
    assert captured["flags"]["detach"] is True


def test_run_or_background_sync_opt_out(monkeypatch):
    called: dict = {}

    class _M:
        def run_and_record(self, kind, params, source="agent"):
            called["sync"] = (kind, source)
            return {"ok": True}

    monkeypatch.setattr(server, "get_manager", lambda: _M())
    out = server._run_or_background("discover", {"keyword": "x"}, background=False)
    assert out == {"ok": True} and called["sync"][0] == "discover"


def test_start_skill_job_force_and_error(monkeypatch):
    captured: dict = {}

    def fake(cmd, *a, **flags):
        captured["flags"] = flags
        return {"job_id": _JID}

    monkeypatch.setattr(server, "run_skill_command", fake)
    server._start_skill_job("graph", {"url": "u"}, force=True)
    assert captured["flags"]["force"] is True

    from pounding_mcp.skill_runner import SkillError

    def boom(cmd, *a, **flags):
        raise SkillError("闸被占")

    monkeypatch.setattr(server, "run_skill_command", boom)
    out = server._start_skill_job("discover", {"keyword": "x"})
    assert "error" in out and "闸被占" in out["error"]


# ── ③ _parse_output 剥 NEXT 行 ──────────────────────────────────────

def test_parse_output_strips_trailing_next_line():
    stdout = ('progress\n{\n  "run_id": "disc_260928_120000_ab12cd",\n'
              '  "candidates_count": 7\n}\n👉 NEXT: 汇报下一步\n')
    parsed = _parse_output(stdout, "")
    assert parsed["candidates_count"] == 7
    assert parsed["run_id"] == "disc_260928_120000_ab12cd"


# ── ④ extract_discovery_run_id ──────────────────────────────────────

def test_extract_discovery_run_id_cases():
    from pounding_mcp.tasks import extract_discovery_run_id as f

    assert f({"run_id": "disc_260928_120000_ab12cd"}) == "disc_260928_120000_ab12cd"
    assert f({"session_run_id": "disc_260928_120000_ab12cd"}) == "disc_260928_120000_ab12cd"
    # 本地时间戳 id 坑：不是 run_id
    assert f({"task_id": "20260928_120000"}) == ""
    assert f({"run_id": "20260928_120000"}) == ""
    # 嵌套一层
    assert f({"envelope": {"extensions": {"discovery_meta": {}}}}) == ""
    assert f({"extensions": {"discovery_meta": {"run_id": "disc_260928_120000_ab12cd"}}}) \
        == "disc_260928_120000_ab12cd"
    assert f(None) == ""


# ── ⑤ job_status / job_result 优先读 skill 注册表 ───────────────────

@pytest.fixture()
def skill_jobs_tmp(tmp_path, monkeypatch):
    d = tmp_path / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(skill_jobs._ENV_JOBS_DIR, str(d))
    return d


def _write_skill_job(d, jid=_JID, **over):
    job = {"schema_version": "skill.job.v1", "job_id": jid, "kind": "discover",
           "status": "completed", "pid": None, "started_at": time.time(),
           "run_id": "", "session_path": "", "error": None}
    job.update(over)
    (d / f"{jid}.json").write_text(json.dumps(job), encoding="utf-8")
    return job


def test_get_job_orphan_reap(skill_jobs_tmp):
    import subprocess

    dead = subprocess.Popen(["true"])
    dead.wait()
    _write_skill_job(skill_jobs_tmp, jid="20260928_120000_aaaaaa",
                     status="running", pid=dead.pid)
    job = skill_jobs.get_job("20260928_120000_aaaaaa")
    assert job["status"] == "interrupted"
    assert skill_jobs.get_job("20260101_000000_bbbbbb") is None


def test_job_status_reads_skill_registry(skill_jobs_tmp, monkeypatch):
    jid = "20260928_120000_cccccc"
    _write_skill_job(skill_jobs_tmp, jid=jid)
    (skill_jobs_tmp / f"{jid}.log").write_text(
        '{\n  "run_id": "disc_260928_120000_ab12cd",\n'
        '  "session_path": "/tmp/sess.json",\n  "candidates_count": 9\n}\n'
        "👉 NEXT: n\n", encoding="utf-8")

    def _boom():
        raise AssertionError("skill 注册表命中时不应回落 pounding manager")

    monkeypatch.setattr(server, "get_manager", _boom)
    t = server.job_status(jid)
    assert t["id"] == jid and t["run_id"] == "disc_260928_120000_ab12cd"
    assert t["session_path"] == "/tmp/sess.json"
    assert "next_action" in t and "log_tail" in t


def test_job_result_reads_skill_registry(skill_jobs_tmp, monkeypatch):
    jid = "20260928_120000_dddddd"
    _write_skill_job(skill_jobs_tmp, jid=jid)
    (skill_jobs_tmp / f"{jid}.log").write_text(
        '{\n  "run_id": "disc_260928_120000_ab12cd",\n  "candidates_count": 4\n}\n'
        "👉 NEXT: n\n", encoding="utf-8")
    monkeypatch.setattr(server, "get_manager",
                        lambda: (_ for _ in ()).throw(AssertionError("不应回落")))
    r = server.job_result(jid)
    assert r["candidates_count"] == 4 and r["run_id"] == "disc_260928_120000_ab12cd"


def test_job_list_merges_skill_jobs(skill_jobs_tmp):
    _write_skill_job(skill_jobs_tmp, jid="20260928_120000_eeeeee")
    items = server.job_list(10)["items"]
    assert any(i.get("id") == "20260928_120000_eeeeee" for i in items)


def test_job_cancel_skill_registry(skill_jobs_tmp):
    import subprocess

    jid = "20260928_120000_ffffff"
    # 用已死 pid（kill 是 no-op）——绝不拿当前进程 pid（killpg 会连测试进程一起杀）
    dead = subprocess.Popen(["true"])
    dead.wait()
    _write_skill_job(skill_jobs_tmp, jid=jid, status="running", pid=dead.pid)
    out = server.job_cancel(jid)
    assert out["ok"] is True
    assert skill_jobs.get_job(jid)["status"] == "cancelled"

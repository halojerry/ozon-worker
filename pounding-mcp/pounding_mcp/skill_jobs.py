"""skill 后台任务注册表读取（v0.83 批④）——pounding-mcp 侧薄读。

**单一事实源在 skill**：``skill/data/jobs/{job_id}.json``（skill ``--detach`` 落盘，
MCP ``background`` 路径即经它）。本模块**只读**（+ 显式取消时写终态）；``job_status`` /
``job_result`` / ``job_list`` 优先读它，旧 pounding ``data/tasks/`` 注册表仅作向后兼容
回退（已有用户的旧任务）。

env ``SKILL_JOBS_DIR`` 覆写目录（默认 ``SKILL_DIR/data/jobs``）。

孤儿收割（只读版）：status=running 且 pid 已死 → 响应里如实报 ``interrupted``
（落盘由 skill ``job-status``/``jobs`` 侧完成——本模块不改写运行中进程的文件）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .skill_runner import SKILL_DIR
from .tasks import CollectTaskManager, _pid_alive

_ENV_JOBS_DIR = "SKILL_JOBS_DIR"
_DEFAULT_JOBS_DIR = SKILL_DIR / "data" / "jobs"

_LOG_TAIL_BYTES = 262_144


def jobs_dir() -> Path:
    override = os.environ.get(_ENV_JOBS_DIR, "").strip()
    return Path(override) if override else _DEFAULT_JOBS_DIR


def job_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def job_log_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.log"


def _read(job_id: str) -> dict | None:
    try:
        data = json.loads(job_path(job_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and data.get("job_id"):
        return data
    return None


def _reap(job: dict) -> dict:
    """孤儿收割（只读）：running + pid 已死 → interrupted（响应内定案，不落盘）。"""
    if job.get("status") != "running":
        return job
    pid = job.get("pid")
    if pid and not _pid_alive(pid):
        return {**job, "status": "interrupted",
                "error": job.get("error") or "进程已终止且无终态（会话中断/机器重启）"}
    return job


def get_job(job_id: str) -> dict | None:
    """读单个 skill 后台任务（含孤儿收割）；不存在 → None。"""
    job = _read(job_id)
    return _reap(job) if job else None


def list_jobs(limit: int = 20) -> list[dict]:
    """列出 skill 后台任务（started_at 降序，含孤儿收割）。"""
    d = jobs_dir()
    try:
        files = list(d.glob("*.json"))
    except OSError:
        return []
    out: list[dict] = []
    for f in files:
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(job, dict) and job.get("job_id"):
            out.append(_reap(job))
    out.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return out[:limit]


def log_tail(job_id: str, n: int = 40) -> str:
    if n <= 0:
        return ""
    try:
        with open(job_log_path(job_id), "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _read_tail_text(job_id: str) -> str:
    try:
        with open(job_log_path(job_id), "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _LOG_TAIL_BYTES))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def extract_tail_json(text: str) -> dict:
    """从混合输出提尾部 JSON（跳过出口末行 NEXT 提示）。"""
    lines = (text or "").split("\n")
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("👉 NEXT:")):
        lines.pop()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "{":
            try:
                obj = json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return {}


def read_result(job_id: str) -> dict:
    """读任务结果（尾 JSON）；无结构化结果返回 {}。"""
    return extract_tail_json(_read_tail_text(job_id))


def cancel(job_id: str) -> bool:
    """取消 skill 后台任务：杀进程组 + 写 cancelled 终态（best-effort）。

    子进程自身的 finish_job 有终态粘性，不会把 cancelled 翻回。
    """
    job = _read(job_id)
    if not job or job.get("status") != "running":
        return False
    pid = job.get("pid")
    if pid:
        try:
            CollectTaskManager._kill_pid(int(pid))
        except Exception:  # noqa: BLE001
            pass
    try:
        job = {**job, "status": "cancelled", "finished_at": time.time(),
               "error": job.get("error")}
        tmp = job_path(job_id).with_name(f"{job_path(job_id).name}.tmpc")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, job_path(job_id))
    except OSError:
        pass
    return True


# ── 结果字段提取 ─────────────────────────────────────────────────────────

def extract_discovery_run_id(result: dict) -> str:
    """从结果 dict 提取 discover canonical run_id（复用 tasks 唯一实现）。"""
    from .tasks import extract_discovery_run_id as _f

    return _f(result)


def extract_worker_task_ids(result: dict) -> list[str]:
    """从结果 dict 递归提取 worker 云任务 id（复用 tasks 唯一实现）。"""
    from .tasks import extract_worker_task_ids as _f

    return _f(result)

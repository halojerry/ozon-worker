#!/usr/bin/env python3
"""后台任务（detach）唯一实现 —— v0.83 批④（方案 docs/PLAN-v083-quality-campaign-v1.md §3④）。

**改本模块前先读方案该批节。** 背景：agent 平台抱怨「重命令同步阻塞、agent 全程干等」，
本模块让六重命令（discover/discover-multi/discover-task/follow/graph/seller）支持
``--detach``——父进程 fork 脱离会话子进程后立即返回 job 句柄，子进程内照常走完整流程
（含 ``_heavy_gate`` 串行闸），产出物（session 落盘 / 尾 JSON / 采集箱）与前台逐字一致。

**单事实源**：注册表落 ``skill/data/jobs/{job_id}.json``（env ``OZON_SKILL_JOBS_DIR``
可覆写），pounding-mcp ``job_*`` 读同目录——**不出第二套 id**。job_id 形如
``<yyyymmdd_HHMMSS>_<6hex>``，带 hex 后缀，与 ``^\\d{8}_\\d{6}$`` 本地时间戳 id 不同，
不会被 ``extract_worker_task_ids`` 误当本地 id 跳过。

锁纪律：``--detach`` 父进程**不持锁**——只做一次非阻塞占用探针（拿到即释放，fail-fast
给占用方信息）；真正的 ``_heavy_gate`` 持锁发生在子进程内（``lock_utils`` 同一实现）。

孤儿收割：读注册表时若 status=running 且 pid 已死且无终态写入 → 定案 ``interrupted``
（会话中断/机器重启），先例 ``pounding-mcp/pounding_mcp/tasks.py`` 的 ``_reap``。

红线（gitleaks）：本模块只序列化命令元数据与状态，**绝不写 cookie/token 明文**；
日志是子进程 stdout/stderr（``_out`` 已脱敏），与既有 ``data/logs/run_*.log`` 同性质。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from scripts._const import HEAVY_LOCK_PATH  # noqa: F401  （探针用；真持锁在子进程）
from scripts.lib import lock_utils

logger = logging.getLogger(__name__)

# 注册表目录（env 动态读，测试可 import 后改 env）
_DEFAULT_JOBS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "jobs"
_ENV_JOB_ID = "OZON_SKILL_JOB_ID"
_ENV_JOBS_DIR = "OZON_SKILL_JOBS_DIR"

# 终态集合：写入粘性（terminal 落定后不再改写）
_TERMINAL = frozenset({"completed", "failed", "interrupted", "cancelled"})

_LOG_TAIL_BYTES = 262_144  # 读尾 256KB（discover 全量候选日志可能很大）
_LOCK_TIMEOUT = 5.0

# 本进程认领的 job_id（子进程启动时从 env 认领并清除，防嵌套 CLI 误认领）
_CLAIMED_JOB_ID = ""


# ─────────────────────────────────────────────────────────────────────────
# 目录 / id / 路径
# ─────────────────────────────────────────────────────────────────────────

def jobs_dir() -> Path:
    """注册表目录（env ``OZON_SKILL_JOBS_DIR`` 优先，默认 ``skill/data/jobs``）。"""
    override = os.environ.get(_ENV_JOBS_DIR, "").strip()
    return Path(override) if override else _DEFAULT_JOBS_DIR


def _ensure_dir() -> Path:
    d = jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_job_id(now: time.struct_time | None = None) -> str:
    """生成 ``<yyyymmdd_HHMMSS>_<6hex>``（不可枚举：随机段 16^6）。"""
    ts = time.strftime("%Y%m%d_%H%M%S", now or time.localtime())
    return f"{ts}_{secrets.token_hex(3)}"


def is_valid_job_id(job_id: str) -> bool:
    """``<8digits>_<6digits>_<6hex>`` 形态校验（防路径注入）。"""
    rid = (job_id or "").strip()
    parts = rid.split("_")
    if len(parts) != 3:
        return False
    d, t, hx = parts
    if len(d) != 8 or len(t) != 6 or not (d + t).isdigit():
        return False
    return len(hx) == 6 and all(c in "0123456789abcdef" for c in hx)


def job_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def job_log_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.log"


def job_lock_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.lock"


# ─────────────────────────────────────────────────────────────────────────
# 进程存活 / 锁探针
# ─────────────────────────────────────────────────────────────────────────

def pid_alive(pid: Any) -> bool:
    """跨进程 pid 存活判定（与 pounding-mcp tasks._pid_alive 同口径）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但无权发信号
    except OSError:
        return False


def _holder_text() -> str:
    """占用方人话描述（读锁文件内容；读不到如实降级）。"""
    raw = lock_utils.read_holder_info(HEAVY_LOCK_PATH)
    if not raw:
        return "未知占用者（锁文件无占用方信息）"
    try:
        info = json.loads(raw)
        return f"{info.get('cmd', '?')} (PID {info.get('pid', '?')})"
    except (json.JSONDecodeError, TypeError):
        return raw[:200]


def probe_heavy_lock() -> str | None:
    """非阻塞占用探针：空闲 → None；被占 → 占用方文案。

    ⚠️ 拿到锁立即释放（本函数**不持锁**）——真正持锁在子进程 ``_heavy_gate``。
    仅用于 ``--detach`` fail-fast：闸不空时不 spawn（否则子进程秒退 exit 4，
    父进程已报「已启动」，体验错乱）。
    """
    try:
        HEAVY_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "锁目录不可创建（检查权限/磁盘）"
    fd = lock_utils.try_acquire(HEAVY_LOCK_PATH, timeout=0.0)
    if fd is None:
        return _holder_text()
    lock_utils.release(fd)
    return None


# ─────────────────────────────────────────────────────────────────────────
# 注册表读写（原子写 + 文件锁 RMW）
# ─────────────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_raw(job_id: str) -> dict | None:
    try:
        data = json.loads(job_path(job_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("job_id") else None


def _locked_update(job_id: str, mutate: Callable[[dict], dict | None]) -> dict | None:
    """文件锁内读-改-写（mutate 返回 None = 不改写，原样返回现状）。

    父进程回填 pid 与子进程写终态可能并发，故所有写路径统一走本函数；
    拿不到锁（极端）不静默改写，返回现状防半写。
    """
    try:
        _ensure_dir()
    except OSError:
        return _read_raw(job_id)
    with lock_utils.file_lock(job_lock_path(job_id), timeout=_LOCK_TIMEOUT) as fd:
        if fd is None:
            return _read_raw(job_id)
        cur = _read_raw(job_id)
        if cur is None:
            return None
        new = mutate(cur)
        if new is None:
            return cur
        try:
            _atomic_write(job_path(job_id), new)
        except OSError:
            return cur
        return new


def reap_if_stale(job: dict) -> dict:
    """孤儿收割：running + pid 已死且无终态 → interrupted（并落盘）。"""
    if job.get("status") != "running":
        return job
    pid = job.get("pid")
    if not pid or pid_alive(pid):
        return job
    updated = _locked_update(job["job_id"], lambda j: (
        {**j, "status": "interrupted", "finished_at": time.time(),
         "error": "进程已终止且无终态（会话中断/机器重启）"}
        if j.get("status") == "running" else None))
    return updated or job


def read_job(job_id: str, *, reap: bool = True) -> dict | None:
    job = _read_raw(job_id)
    if job is None:
        return None
    return reap_if_stale(job) if reap else job


def list_jobs(limit: int = 20) -> list[dict]:
    """列出注册表任务（started_at 降序，含孤儿收割）。"""
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
            out.append(reap_if_stale(job))
    out.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return out[:limit]


# ─────────────────────────────────────────────────────────────────────────
# 日志 / 结果读取
# ─────────────────────────────────────────────────────────────────────────

def log_tail(job_id: str, n: int = 40) -> str:
    """读任务日志尾部 n 行（n<=0 或无文件 → 空串）。"""
    if n <= 0:
        return ""
    path = job_log_path(job_id)
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _read_tail_text(job_id: str) -> str:
    path = job_log_path(job_id)
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _LOG_TAIL_BYTES))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def extract_tail_json(text: str) -> dict:
    """从混合输出里提取尾部结构化 JSON（跳过 NEXT 提示行）。

    skill 出口形如 ``... 进度行 ... _out({...})  👉 NEXT: ...``——NEXT 是末行且
    非 JSON，先剥尾部 NEXT/空行，再从下往上找 ``{`` 独占行拼接解析。
    """
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


def read_result(job_id: str) -> tuple[dict | None, str | None]:
    """读任务结果（尾 JSON）。返回 (结果 dict, 错误说明)。"""
    path = job_log_path(job_id)
    if not path.is_file():
        return None, "日志文件缺失"
    text = _read_tail_text(job_id)
    if not text.strip():
        return None, ""
    parsed = extract_tail_json(text)
    if parsed:
        return parsed, None
    return None, "无结构化结果（任务未终态或输出无尾 JSON）"


# ─────────────────────────────────────────────────────────────────────────
# spawn / 终态回写
# ─────────────────────────────────────────────────────────────────────────

def _spawn_kwargs() -> dict:
    """进程脱离会话的 Popen 参数：POSIX 独立会话组；Windows 分离进程组。"""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _child_argv() -> list[str]:
    """构造子进程 argv：与当前命令逐字一致，仅去掉 ``--detach``。

    用 ``sys.argv`` 保留原始 flag 形态（含位置参数/空格参数），``sys.executable``
    保证与父进程同解释器（preflight re-exec 后即为 3.12）。
    """
    cli_path = Path(__file__).resolve().parent.parent / "cli.py"
    rest = [a for a in sys.argv[1:] if a != "--detach"]
    return [sys.executable, str(cli_path), *rest]


def spawn_detached(args, heavy: bool = True) -> dict:
    """父进程侧 ``--detach``：fork 脱离会话子进程后立即返回句柄。

    ``heavy=True``（六重命令，带 --wait/--force）时先做串行闸占用探针（闸不空
    exit 4 不启动）；``heavy=False``（queries 等非重命令）跳过探针——light 命令
    不持重闸，不该被重闸拦。

    返回 ``{"code": int, "job_id": str, "log": str, "error": str}``——打印由 cli 负责
    （``_out`` 句柄 JSON + ``_print_next`` NEXT 行）。
    """
    if getattr(args, "wait", False):
        return {"code": 2, "job_id": "", "log": "",
                "error": "--detach 与 --wait 互斥：--detach 立即返回句柄（job-status 轮询），"
                         "--wait 阻塞等终态——二者只可选一"}

    cmd = str(getattr(args, "command", "") or "")
    if heavy and not getattr(args, "force", False):
        holder = probe_heavy_lock()
        if holder is not None:
            return {"code": 4, "job_id": "", "log": "",
                    "error": f"重采集串行闸被占：{holder}\n"
                             f"   → --detach 未启动（闸不空，避免子进程秒退）；"
                             f"加 --wait 排队（同步等锁）或 --force 强制并行（慎用）"}

    try:
        _ensure_dir()
    except OSError as exc:
        return {"code": 1, "job_id": "", "log": "", "error": f"任务目录不可创建: {exc}"}

    job_id = new_job_id()
    log = job_log_path(job_id)
    argv = _child_argv()
    _atomic_write(job_path(job_id), {
        "schema_version": "skill.job.v1",
        "job_id": job_id,
        "kind": cmd,
        "status": "running",
        "pid": None,
        "argv": argv,
        "log": str(log),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "run_id": "",
        "session_path": "",
        "error": None,
    })
    try:
        with open(log, "w", encoding="utf-8") as fh:
            env = dict(os.environ)
            env[_ENV_JOB_ID] = job_id
            proc = subprocess.Popen(
                argv, stdout=fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env, cwd=str(Path(argv[1]).resolve().parent.parent),
                **_spawn_kwargs())
    except Exception as error:  # noqa: BLE001
        msg = f"启动失败: {error}"
        _locked_update(job_id, lambda j: (
            {**j, "status": "failed", "finished_at": time.time(), "error": msg}
            if j.get("status") not in _TERMINAL else None))
        return {"code": 1, "job_id": job_id, "log": str(log), "error": msg}

    # 只在仍 running 时回填 pid——子进程可能已极快结束并写终态，不得覆盖
    _locked_update(job_id, lambda j: (
        {**j, "pid": proc.pid} if j.get("status") == "running" and not j.get("pid") else None))
    return {"code": 0, "job_id": job_id, "log": str(log), "error": ""}


def tracking_job_id() -> str:
    """子进程内：本进程认领的 job_id（非 detach 子进程为空串）。

    优先返回 ``claim_tracking_job_id`` 认领的值——认领会把环境变量清掉，
    防嵌套 spawn 的 CLI 子进程误认领同一 job。
    """
    return _CLAIMED_JOB_ID or os.environ.get(_ENV_JOB_ID, "").strip()


def claim_tracking_job_id() -> str:
    """子进程启动时认领 job_id 并从环境清除（幂等；非 detach 进程返回空串）。

    ⚠️ 必须在「会 spawn 子 CLI 的那段逻辑」之前调用，且要在 ``_preflight_runtime``
    的 os.execve 之后（re-exec 前的清除会让新进程丢掉 job_id）。
    """
    global _CLAIMED_JOB_ID
    if _CLAIMED_JOB_ID:
        return _CLAIMED_JOB_ID
    jid = os.environ.pop(_ENV_JOB_ID, "").strip()
    _CLAIMED_JOB_ID = jid
    return jid


def finish_job(job_id: str, exit_code: int) -> None:
    """子进程出口回写终态（cli.main 调；幂等——terminal 落定不翻盘）。"""
    if not job_id:
        return
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        code = 1
    status = "completed" if code == 0 else "failed"
    result = read_result(job_id)[0] or {}
    run_id = str(result.get("run_id") or "")
    session_path = str(result.get("session_path") or "")
    error = None
    if status == "failed":
        error = (log_tail(job_id, 8)[-300:] or f"退出码 {code}")
    _locked_update(job_id, lambda j: (
        {**j, "status": status, "finished_at": time.time(), "exit_code": code,
         "run_id": run_id or j.get("run_id", ""),
         "session_path": session_path or j.get("session_path", ""),
         "error": error}
        if j.get("status") not in _TERMINAL else None))

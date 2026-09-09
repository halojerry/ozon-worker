"""采集任务管理器——统一记录「采集类 skill 命令」为任务，供任务中心展示 + agent 后台轮询。

agent（MCP 工具）和手动（任务中心 UI）触发都会走这里：
- 同步路径 run_and_record：MCP 工具缺省（兼容旧行为），等 skill 命令跑完返回结果
- 后台路径 start_background（v0.70）：CLI 进程**脱离会话独立运行**——输出写日志
  文件（非管道）、进程组独立（POSIX setsid / Windows DETACHED_PROCESS），dsh
  会话关闭 / MCP server 退出任务照跑；完成状态由监控线程或惰性收割（get/list）
  定案，registry 是唯一事实源
- 任务注册表落盘 JSON（原子写 + 按 id 合并，防 stdio server 与 8902 tasks_server
  两个进程整文件互踩），供 tasks_server.py 跨进程查询；get/list 前重读盘
- 实时进度：解析 skill 输出的 `[N/M]`（商品进度）与 `阶段 X/Y` 行
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .skill_runner import (
    run_skill_command, _build_argv, _parse_output, SkillError, SKILL_DIR,
    _wake_browser, _done_browser,
)

# 采集类命令 → 任务类型中文标签
COLLECT_KINDS: dict[str, str] = {
    "search": "关键词采集",
    "probe": "详情采集",
    "image_search": "图搜采集",
    "follow": "跟卖采集",
    "discover": "选品采集",
    "discover_multi": "多词选品",
    "discover_task": "任务式选品",
    "seller": "店铺分析",
    "queries": "热销榜查询",
    "graph": "上架组装",
    "get_ak": "获取 AK",
}

# 重任务（CDP/图搜/长时）：单飞闸范围——同时只允许 1 个 running（Chrome tab 打架防护）
_HEAVY_KINDS = frozenset({
    "discover", "discover_multi", "discover_task", "follow", "seller", "graph",
})

# 终态集合：_finish 粘性保护（落定后任何后续收割/回调不得翻盘）
_TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled"})

# skill CLI 中作为「位置参数」的命令参数（其余一律 --flag 传）
_POSITIONAL: dict[str, list[str]] = {
    "search": ["query"],
    "category": ["query"],
}

# 进度行解析：商品进度 `[3/20]`、阶段 `阶段 1/3`、达标进度 `[12/50] 达标进度`
_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_STAGE_RE = re.compile(r"阶段\s*(\d+)/(\d+)")


def _exec(kind: str, params: dict) -> dict:
    """执行 skill 命令，正确处理位置参数（search 的 query 等）。"""
    p = dict(params or {})
    positional = [p.pop(n) for n in _POSITIONAL.get(kind, []) if n in p and p[n] not in (None, "")]
    return run_skill_command(kind, *positional, **p)

_DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "tasks.json"
_TASKS_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "tasks"


def _log_path(task_id: str) -> Path:
    return _TASKS_LOG_DIR / f"{task_id}.log"


def _spawn_detach_kwargs() -> dict:
    """进程脱离会话的 Popen 参数：POSIX 独立会话组；Windows 分离进程组。"""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _pid_alive(pid: int | None) -> bool:
    """跨进程 pid 存活判定（后台任务可能属于已退出的旧 server 进程）。"""
    if not pid or pid <= 0:
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


class CollectTaskManager:
    """线程安全的采集任务注册表，落盘 JSON。"""

    def __init__(self, store_path: Path | None = None, max_tasks: int = 200) -> None:
        self._store_path = store_path or _DEFAULT_STORE
        self._max_tasks = max_tasks
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._store_path.is_file():
                data = json.loads(self._store_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._tasks = data
        except Exception:
            self._tasks = {}

    def _save(self) -> None:
        """落盘：原子写（tmp+os.replace）+ 读盘按 id 合并。

        合并语义：本进程内存为准覆盖同 id；盘上多出的任务（他进程新建）保留——
        stdio server 与 8902 tasks_server 是两个独立进程，整文件覆写会互踩丢任务。
        """
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            merged: dict = {}
            try:
                if self._store_path.is_file():
                    disk = json.loads(self._store_path.read_text(encoding="utf-8"))
                    if isinstance(disk, dict):
                        merged = disk
            except Exception:
                pass
            merged.update(self._tasks)
            if len(merged) > self._max_tasks:
                for old in sorted(merged.values(), key=lambda t: t.get("started_at") or 0)[: len(merged) - self._max_tasks]:
                    merged.pop(old.get("id"), None)
            tmp = self._store_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self._store_path)
        except Exception:
            pass

    def create(self, kind: str, params: dict, source: str = "manual") -> str:
        """创建一个采集任务并在后台线程执行 skill 命令。返回 task_id。"""
        task = self._register(kind, params, source)
        task_id = task["id"]
        threading.Thread(target=self._run, args=(task_id, kind, params), daemon=True).start()
        return task_id

    def _register(self, kind: str, params: dict, source: str) -> dict:
        """创建任务记录（状态 running），不执行。返回 task dict。"""
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id,
            "kind": kind,
            "label": COLLECT_KINDS.get(kind, kind),
            "params": {k: v for k, v in (params or {}).items() if v not in (None, "")},
            "source": source,  # manual（手动）/ agent（对话）
            "status": "running",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "started_at": time.time(),
            "finished_at": None,
            "summary": None,
            "error": None,
        }
        with self._lock:
            self._tasks[task_id] = task
            # 裁剪：只保留最近 max 条（按 created 序）
            if len(self._tasks) > self._max_tasks:
                for old in sorted(self._tasks.values(), key=lambda t: t["started_at"])[: len(self._tasks) - self._max_tasks]:
                    self._tasks.pop(old["id"], None)
            self._save()
        return task

    def run_and_record(self, kind: str, params: dict, source: str = "agent") -> dict:
        """同步执行采集命令并记录任务状态（agent 触发用）。返回 skill 结果 dict。"""
        task = self._register(kind, params, source)
        task_id = task["id"]
        started = time.time()
        try:
            result = _exec(kind, params)
            summary = self._summarize(kind, result)
            with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t["status"] = "completed"
                    t["finished_at"] = time.time()
                    t["summary"] = summary
                    t["elapsed"] = round(time.time() - started, 1)
                    self._save()
            return result
        except SkillError as exc:
            with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t["status"] = "failed"
                    t["finished_at"] = time.time()
                    t["error"] = str(exc)[:300]
                    self._save()
            raise
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t["status"] = "failed"
                    t["finished_at"] = time.time()
                    t["error"] = f"{type(exc).__name__}: {exc}"[:300]
                    self._save()
            raise

    # ── v0.70 后台路径：脱离会话独立运行 ─────────────────────────────

    def start_background(self, kind: str, params: dict, source: str = "agent",
                         force: bool = False) -> dict:
        """后台启动 skill 命令，立即返回 task dict（agent 不阻塞）。

        - CLI 进程组独立（POSIX setsid / Windows DETACHED_PROCESS），stdout/stderr
          写 data/tasks/{id}.log 文件（非管道）——dsh 会话关闭 / 本 MCP server 退出
          任务照跑（根治「关会话任务终止」）；
        - 完成状态由 _watch 监控线程定案；线程随 server 死掉的孤儿任务由
          get()/list() 惰性收割按日志定案；
        - 单飞闸：heavy 任务（_HEAVY_KINDS）同时只跑 1 个，防 Chrome tab 打架；
          冲突返回 {"error": ...}（force=True 越过）。
        """
        if kind in _HEAVY_KINDS and not force:
            busy = [t for t in self.list()
                    if t.get("kind") in _HEAVY_KINDS and self._is_running(t)]
            if busy:
                b = busy[0]
                return {"error": f"已有重采集任务运行中（{b.get('kind')}/{b.get('id')}），"
                                 f"完成前不启动新任务——job_status 查进度，或 force=true 强制并行"}
        task = self._register(kind, params, source)
        task_id = task["id"]
        log = _log_path(task_id)
        p = dict(params or {})
        positional = [p.pop(n) for n in _POSITIONAL.get(kind, []) if n in p and p[n] not in (None, "")]
        argv = _build_argv(kind, tuple(positional), p)
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "w", encoding="utf-8") as fh:
                proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, cwd=str(SKILL_DIR),
                                        **_spawn_detach_kwargs())
        except Exception as exc:  # noqa: BLE001
            self._finish(task_id, "failed", error=f"启动失败: {exc}",
                         started=task["started_at"])
            return dict(self._tasks.get(task_id) or task)
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t["pid"] = proc.pid
                t["log"] = str(log)
                self._save()
        self._procs[task_id] = proc
        _wake_browser()
        threading.Thread(target=self._watch, args=(task_id, proc), daemon=True).start()
        return dict(self._tasks.get(task_id) or task)

    def _watch(self, task_id: str, proc: subprocess.Popen) -> None:
        """后台任务监控线程：等进程退出按真实退出码定案。

        本线程随 MCP server 进程死亡——孤儿任务（server 先死、CLI 独立跑完）
        由下次 get()/list() 的 _reap 按日志定案。"""
        code = proc.wait()
        self._procs.pop(task_id, None)
        _done_browser()
        t = self._tasks.get(task_id) or {}
        started = t.get("started_at")
        if code == 0:
            # 真实退出码 0 即完成——纯文本输出（queries 表格等无尾部 JSON）不算失败；
            # 孤儿收割（_reap）拿不到退出码才需要严格以结构化 JSON 为完成判据
            result, _ = self._result_from_log(task_id)
            self._finish(task_id, "completed",
                         summary=self._summarize(t.get("kind", ""), result or {}),
                         started=started)
        else:
            tail = self.log_tail(task_id, 8)
            self._finish(task_id, "failed",
                         error=(tail[-300:] or f"退出码 {code}"), started=started)

    def _result_from_log(self, task_id: str,
                         log_path: str | None = None) -> tuple[dict | None, str | None]:
        """日志尾解析任务结果（复用 _parse_output 的尾部 JSON 提取）。

        返回 (结果 dict, 错误信息)；无输出/无文件返回 (None, 原因)。
        只取尾部 256KB（discover 全量候选日志可能很大）。
        log_path 缺省查全局 data/tasks/{id}.log；registry 记录了自定义路径则优先。"""
        path = Path(log_path) if log_path else _log_path(task_id)
        if not path.is_file():
            return None, "日志文件缺失"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return None, f"日志读取失败: {exc}"
        if not text.strip():
            return None, ""
        parsed = _parse_output(text[-262144:], "")
        # 孤儿定案没有退出码，只能看产物：真实结果 dict（非纯文本 {"raw"}）才算完成
        if set(parsed.keys()) - {"raw", "_stderr"}:
            return parsed, None
        return parsed, None if set(parsed.keys()) - {"raw"} else "无结构化结果（进程退出码未知）"

    def _reap(self, t: dict) -> dict:
        """惰性收割：running 孤儿任务（pid 已死且无监控线程）按日志定案；
        pid 存活 → 尾读日志回填实时进度。跨 server 重启后任务状态仍可信。"""
        if t.get("status") != "running":
            return t
        if not self._procs.get(t["id"]) and not _pid_alive(t.get("pid")):
            result, err = self._result_from_log(t["id"], t.get("log"))
            if err == "日志文件缺失" and not t.get("pid"):
                # 同步路径且非本进程发起（无 pid 无日志可查证死活）——不误判，保持原状
                return t
            started = t.get("started_at")
            if err is None:
                self._finish(t["id"], "completed",
                             summary=self._summarize(t.get("kind", ""), result or {}),
                             started=started)
            elif err in ("", "日志文件缺失"):
                self._finish(t["id"], "interrupted",
                             error="进程已终止且无输出（会话中断/机器重启）",
                             started=started)
            else:
                self._finish(t["id"], "failed", error=err,
                             summary=self._summarize(t.get("kind", ""), result or {}),
                             started=started)
            return self._tasks.get(t["id"], t)
        self._progress_from_log(t)
        return t

    def _is_running(self, t: dict) -> bool:
        """任务是否真在跑：进程内 Popen 活着，或 registry pid 跨进程存活。"""
        if t.get("status") != "running":
            return False
        if t["id"] in self._procs:
            return True
        return _pid_alive(t.get("pid"))

    def _progress_from_log(self, t: dict) -> None:
        """pid 存活的 running 任务：尾读日志最后一屏，回填 stage/progress。"""
        path = Path(t.get("log") or str(_log_path(t["id"])))
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 8192))
                tail = fh.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return
        for line in tail.splitlines():
            self._maybe_progress(t["id"], line.rstrip())

    def log_tail(self, task_id: str, n: int = 40) -> str:
        """读任务日志尾部 n 行（job_status 展示用；文件缺失返回空串）。"""
        try:
            with open(_log_path(task_id), "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 65536))
                lines = fh.read().decode("utf-8", "replace").splitlines()
            return "\n".join(lines[-n:])
        except Exception:  # noqa: BLE001
            return ""

    def read_result(self, task_id: str) -> tuple[dict | None, str | None]:
        """公开读结果接口（job_result 工具用）：返回 (结果 dict, 错误说明)。"""
        return self._result_from_log(task_id)

    def extract_worker_task_ids(self, task_id: str) -> list[str]:
        """从任务结果提取 worker 云任务 id（graph/discover 提交后 agent 可直接 query）。

        跳过 discovery 本地任务号（YYYYMMDD_HHMMSS 形态）——那是 --resume 用的
        本地 id，state_path 里已有，别混进 worker 语义。"""
        import re

        result, _ = self._result_from_log(task_id)
        found: list[str] = []

        def _walk(o) -> None:
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("task_id", "taskId") and isinstance(v, str) \
                            and 6 <= len(v) <= 64 and not re.match(r"^\d{8}_\d{6}$", v):
                        found.append(v)
                    else:
                        _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)

        if isinstance(result, dict):
            _walk(result)
        return list(dict.fromkeys(found))[:10]

    def _run(self, task_id: str, kind: str, params: dict) -> None:
        """后台执行 skill 命令（Popen 逐行解析实时进度），完成后更新状态。"""
        started = time.time()
        _wake_browser()  # 手动任务也唤醒浏览器宿主
        p = dict(params or {})
        positional = [p.pop(n) for n in _POSITIONAL.get(kind, []) if n in p and p[n] not in (None, "")]
        argv = _build_argv(kind, tuple(positional), p)
        out_lines: list[str] = []
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=str(SKILL_DIR),
            )
        except Exception as exc:  # noqa: BLE001
            self._finish(task_id, "failed", error=f"启动失败: {exc}", started=started)
            return
        self._procs[task_id] = proc
        code = -1
        try:
            for line in proc.stdout:
                line = line.rstrip()
                out_lines.append(line)
                self._maybe_progress(task_id, line)
            proc.wait()
            code = proc.returncode
        except Exception as exc:  # noqa: BLE001
            self._finish(task_id, "failed", error=f"执行异常: {exc}", started=started)
            self._procs.pop(task_id, None)
            return
        self._procs.pop(task_id, None)
        _done_browser()  # 命令完成 → 浏览器延迟静默

        stdout = "\n".join(out_lines)
        if code == 0:
            try:
                result = _parse_output(stdout, "")
                summary = self._summarize(kind, result)
                self._finish(task_id, "completed", summary=summary, started=started)
            except Exception as exc:  # noqa: BLE001
                self._finish(task_id, "failed", error=f"结果解析失败: {exc}", started=started)
        else:
            self._finish(task_id, "failed", error=(stdout[-300:] or f"退出码 {code}"), started=started)

    def _maybe_progress(self, task_id: str, line: str) -> None:
        """解析 skill 输出行里的进度（[N/M] 商品进度 / 阶段 X/Y），更新任务。"""
        if not line:
            return
        m = _PROGRESS_RE.search(line)
        if not m:
            sm = _STAGE_RE.search(line)
            if sm:
                with self._lock:
                    t = self._tasks.get(task_id)
                    if t and t.get("status") == "running":
                        t["stage"] = f"阶段 {sm.group(1)}/{sm.group(2)}"
                        self._save()
            return
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.get("status") == "running":
                t["progress"] = {"current": int(m.group(1)), "total": int(m.group(2))}
                self._save()

    def _finish(self, task_id: str, status: str, summary: dict | None = None,
                error: str | None = None, started: float | None = None) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            # 终态粘性：completed/failed/interrupted/cancelled 落定后不覆盖
            # （竞品上品帮实证：清理阶段崩溃把已 completed 的任务翻成 failed，采满的数据状态全丢）
            if t.get("status") in _TERMINAL_STATUSES:
                if started is not None:
                    t["elapsed"] = round(time.time() - started, 1)
                    self._save()
                return
            t["status"] = status
            t["finished_at"] = time.time()
            if summary is not None:
                t["summary"] = summary
            if error is not None:
                t["error"] = error
            if started is not None:
                t["elapsed"] = round(time.time() - started, 1)
            self._save()

    def cancel(self, task_id: str) -> bool:
        """取消运行中的任务（终止子进程；后台任务按 pid 杀整个进程组）。"""
        proc = self._procs.get(task_id)
        pid = None
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.get("status") != "running":
                return False
            t["status"] = "cancelled"
            t["finished_at"] = time.time()
            pid = t.get("pid")
            self._save()
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
        elif pid:
            self._kill_pid(pid)
        return True

    @staticmethod
    def _kill_pid(pid: int) -> None:
        """杀脱离会话的后台任务：POSIX 按进程组 SIGTERM（CLI 可能自起子进程；
        Chrome 是独立 app 不受进程组影响）；Windows taskkill。"""
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

    def _summarize(self, kind: str, result: dict) -> dict:
        """从 skill 结果提取展示摘要。"""
        if kind == "discover_task":
            # cmd_discover_task 尾部 _out JSON（v0.70）：summary 带 达标/状态分布
            s = result.get("summary")
            if isinstance(s, dict):
                return {"candidates": s.get("candidates"), "target": s.get("target"),
                        "submitted": s.get("submitted"),
                        "skipped": s.get("skipped"), "failed": s.get("failed")}
            cands = result.get("candidates") or []
            return {"count": len(cands) if isinstance(cands, list) else 0}
        if kind == "search" or kind == "image_search":
            products = result.get("products") or []
            return {"count": len(products), "hits": result.get("total_results") or result.get("count")}
        if kind == "probe":
            return {"title": str(result.get("title") or "")[:40], "images": result.get("images")}
        if kind in ("discover", "discover_multi", "seller"):
            cands = result.get("candidates") or result.get("products") or result.get("items") or []
            return {"count": len(cands) if isinstance(cands, list) else cands}
        if kind == "queries":
            raw = result.get("raw") or ""
            return {"lines": len([ln for ln in raw.splitlines() if ln]) - 1 if raw else 0}
        return {"ok": True}

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            self._load()
            tasks = sorted(self._tasks.values(), key=lambda t: t.get("started_at") or 0, reverse=True)
            snapshot = [dict(t) for t in tasks[:limit]]
        # 收割在锁外（_finish 会再拿锁，threading.Lock 不可重入）
        return [self._reap(t) for t in snapshot]

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            self._load()
            t = self._tasks.get(task_id)
            snapshot = dict(t) if t else None
        return self._reap(snapshot) if snapshot else None


# 单例（进程内共享）
_manager: CollectTaskManager | None = None


def get_manager() -> CollectTaskManager:
    global _manager
    if _manager is None:
        _manager = CollectTaskManager()
    return _manager

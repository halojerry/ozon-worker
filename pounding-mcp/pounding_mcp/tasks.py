"""采集任务管理器——统一记录「采集类 skill 命令」为任务，供任务中心展示。

agent（MCP 工具）和手动（任务中心 UI）触发都会走这里：
- 采集类工具调用前 create → 任务状态 running
- skill 命令在后台子进程执行，完成后 update → completed/failed
- 任务注册表落盘 JSON，供独立 REST 服务（tasks_server.py）跨进程查询

进度：MVP 记录状态 + 结果摘要（商品数/耗时）；实时进度（current/total）后续
由 skill 命令输出进度行 + 本管理器解析增强。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from .skill_runner import run_skill_command, SkillError

# 采集类命令 → 任务类型中文标签
COLLECT_KINDS: dict[str, str] = {
    "search": "关键词采集",
    "probe": "详情采集",
    "image_search": "图搜采集",
    "follow": "跟卖采集",
    "discover": "选品采集",
    "discover_multi": "多词选品",
    "seller": "店铺分析",
    "queries": "热销榜查询",
    "get_ak": "获取 AK",
}

# skill CLI 中作为「位置参数」的命令参数（其余一律 --flag 传）
_POSITIONAL: dict[str, list[str]] = {
    "search": ["query"],
    "category": ["query"],
}


def _exec(kind: str, params: dict) -> dict:
    """执行 skill 命令，正确处理位置参数（search 的 query 等）。"""
    p = dict(params or {})
    positional = [p.pop(n) for n in _POSITIONAL.get(kind, []) if n in p and p[n] not in (None, "")]
    return run_skill_command(kind, *positional, **p)

_DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "tasks.json"


class CollectTaskManager:
    """线程安全的采集任务注册表，落盘 JSON。"""

    def __init__(self, store_path: Path | None = None, max_tasks: int = 200) -> None:
        self._store_path = store_path or _DEFAULT_STORE
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
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
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(
                json.dumps(self._tasks, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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
            if len(self._tasks) > 200:
                for old in sorted(self._tasks.values(), key=lambda t: t["started_at"])[: len(self._tasks) - 200]:
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

    def _run(self, task_id: str, kind: str, params: dict) -> None:
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
        except SkillError as exc:
            with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t["status"] = "failed"
                    t["finished_at"] = time.time()
                    t["error"] = str(exc)[:300]
                    self._save()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t["status"] = "failed"
                    t["finished_at"] = time.time()
                    t["error"] = f"{type(exc).__name__}: {exc}"[:300]
                    self._save()

    def _summarize(self, kind: str, result: dict) -> dict:
        """从 skill 结果提取展示摘要。"""
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
            return {"lines": len([l for l in raw.splitlines() if l]) - 1 if raw else 0}
        return {"ok": True}

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.get("started_at") or 0, reverse=True)
            return tasks[:limit]

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            return self._tasks.get(task_id)


# 单例（进程内共享）
_manager: CollectTaskManager | None = None


def get_manager() -> CollectTaskManager:
    global _manager
    if _manager is None:
        _manager = CollectTaskManager()
    return _manager

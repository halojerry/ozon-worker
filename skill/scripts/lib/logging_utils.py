"""Structured audit logging for pounding-ozon-hybrid.

Every operation — 1688 API, CDP probe, Ozon API, category resolution, envelope
build, submission — writes a JSON record to ``data/logs/{task_id}.jsonl``.

CLI ``check --logs`` reads these files for full traceability.

Usage:
    from scripts.lib.logging_utils import AuditLogger
    audit = AuditLogger(task_id="task-xxx")
    audit.log("ak1688", "details", "info", "Fetched product details", {"item_id": "123"})
    audit.log("cdp", "probe", "warn", "CDP degraded", {"source": "api_only"})

Run logging (v0.78 批B1/B2):
    from scripts.lib.logging_utils import setup_run_logging, log_stage
    setup_run_logging("graph")   # stderr INFO + 文件 DEBUG 双通道，幂等
    with log_stage("阶段 1/3：采集"):
        ...
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
import time
from typing import Any

from scripts._const import LOGS_DIR

# Terminal logger — human-readable, real-time feedback
_terminal = logging.getLogger("pounding-ozon")

# 幂等标记（挂 root logger 对象上；"" = 文件创建失败但已初始化）
_RUN_LOG_ATTR = "_pounding_run_log_path"

# 沿用 cloud_probe basicConfig 的现格式（v0.78 批B1 前唯一日志配置点）
_RUN_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_RUN_LOG_DATEFMT = "%H:%M:%S"


def _is_test_capture_handler(h: logging.Handler) -> bool:
    """pytest LogCaptureHandler 探测——它挂在 root 上但不是真实 stderr 出口，
    不算「已有 stderr 通道」（否则 pytest 下 setup_run_logging 永远不装自己的
    handler，测试无法断言；生产行为零影响：该类只在 pytest 存在）。"""
    return type(h).__name__.startswith("LogCapture")


def setup_run_logging(cmd: str) -> str:
    """统一运行日志入口（v0.78 批B1）——root logger 双通道，幂等。

    - stderr handler：INFO（沿用 cloud_probe 现格式 ``%(asctime)s [%(levelname)s]
      %(name)s: %(message)s``，datefmt ``%H:%M:%S``）；已有 StreamHandler 时不叠加
      （防 cloud_probe basicConfig 先行导致重复输出）。
    - 文件 handler：DEBUG → ``data/logs/run_{YYYYmmdd_HHMMSS}_{cmd}.log``；
      root 提到 DEBUG 让各模块 debug() 进文件（stderr 仍被 handler 挡在 INFO）。
    - 返回日志文件路径；文件创建失败返回 ""（stderr 通道仍就位，绝不抛异常）。
    - 幂等：以 root 上的 ``_pounding_run_log_path`` 属性为标记，重复调用直接返回。

    与 cloud_probe 头部 basicConfig 的关系：cli main() 在任何重 import 之前先调
    本函数 → root 已有 handler → cloud_probe 的「仅 root 无 handler 时」兜底
    basicConfig 自然失效（不覆盖、不叠加）。
    """
    root = logging.getLogger()
    existing = getattr(root, _RUN_LOG_ATTR, None)
    if existing is not None:
        return existing

    formatter = logging.Formatter(_RUN_LOG_FORMAT, datefmt=_RUN_LOG_DATEFMT)

    # stderr 通道：root 上还没有（真实）StreamHandler 时才加（幂等 + 防重复输出）
    has_stream = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and not _is_test_capture_handler(h)
        for h in root.handlers)
    if not has_stream:
        stream_h = logging.StreamHandler(sys.stderr)
        stream_h.setLevel(logging.INFO)
        stream_h.setFormatter(formatter)
        root.addHandler(stream_h)

    # 文件通道：DEBUG 全量（含各模块 debug()）；失败静默降级为仅 stderr
    safe_cmd = re.sub(r"[^A-Za-z0-9_.-]", "_", str(cmd or "cli"))[:60]
    file_path = LOGS_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}_{safe_cmd}.log"
    path_str = ""
    try:
        file_h = logging.FileHandler(file_path, encoding="utf-8")
        file_h.setLevel(logging.DEBUG)
        file_h.setFormatter(formatter)
        root.addHandler(file_h)
        path_str = str(file_path)
    except Exception:
        path_str = ""

    root.setLevel(logging.DEBUG)
    setattr(root, _RUN_LOG_ATTR, path_str)
    return path_str


@contextlib.contextmanager
def log_stage(name: str):
    """阶段计时 contextmanager（v0.78 批B2）——enter/exit 各一条 INFO，exit 带耗时秒。

    走 ``pounding.run`` logger（propagate 到 root 的 setup_run_logging 双通道）；
    块内抛异常也保证 exit 行（finally 语义），异常本身照常上抛。
    用法：``with log_stage("阶段 2/3：1688 货源匹配"): ...``
    """
    lg = logging.getLogger("pounding.run")
    t0 = time.time()
    lg.info("▶️ %s ...", name)
    try:
        yield
    finally:
        lg.info("✅ %s 完成（耗时 %.1fs）", name, time.time() - t0)


class AuditLogger:
    """Writes structured JSON log records to ``data/logs/{task_id}.jsonl``.

    Each record has:
        ts       — Unix timestamp (int)
        task_id  — task identifier
        comp     — component: ak1688 / cdp / ozon / cloud / cli
        stage    — operation: search / details / probe / category / build / submit / check
        level    — info / warn / error
        msg      — human-readable message (English)
        data     — optional dict with metrics (elapsed, counts, urls, errors...)

    Also prints a formatted line to the terminal logger for real-time feedback.
    """

    def __init__(self, task_id: str = "unknown") -> None:
        self.task_id = task_id

    # ── component abbreviations ──────────────────────────────────────────

    @staticmethod
    def _label(comp: str, stage: str) -> str:
        return f"[{comp}/{stage}]"

    # ── public API ───────────────────────────────────────────────────────

    def log(
        self,
        comp: str,
        stage: str,
        level: str,
        msg: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Write a structured log record."""
        record = {
            "ts": int(time.time()),
            "task_id": self.task_id,
            "comp": comp,
            "stage": stage,
            "level": level,
            "msg": msg,
        }
        if data:
            record["data"] = data

        # 1. Terminal output (human-readable)
        label = self._label(comp, stage)
        log_fn = {
            "info": _terminal.info,
            "warn": _terminal.warning,
            "error": _terminal.error,
        }.get(level, _terminal.info)
        log_fn("%s %s %s", label, self.task_id, msg)

        # 2. File output (JSONL, machine-readable)
        try:
            log_path = LOGS_DIR / f"{self.task_id}.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  # logging must never crash the main flow

    def info(self, comp: str, stage: str, msg: str, data: dict[str, Any] | None = None) -> None:
        self.log(comp, stage, "info", msg, data)

    def warn(self, comp: str, stage: str, msg: str, data: dict[str, Any] | None = None) -> None:
        self.log(comp, stage, "warn", msg, data)

    def error(self, comp: str, stage: str, msg: str, data: dict[str, Any] | None = None) -> None:
        self.log(comp, stage, "error", msg, data)


def read_task_log(task_id: str) -> list[dict[str, Any]]:
    """Read all log entries for a task. Used by CLI ``check --logs``."""
    log_path = LOGS_DIR / f"{task_id}.jsonl"
    if not log_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries

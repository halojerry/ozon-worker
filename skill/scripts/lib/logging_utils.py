"""Structured audit logging for pounding-ozon-hybrid.

Every operation — 1688 API, CDP probe, Ozon API, category resolution, envelope
build, submission — writes a JSON record to ``data/logs/{task_id}.jsonl``.

CLI ``check --logs`` reads these files for full traceability.

Usage:
    from scripts.lib.logging_utils import AuditLogger
    audit = AuditLogger(task_id="task-xxx")
    audit.log("ak1688", "details", "info", "Fetched product details", {"item_id": "123"})
    audit.log("cdp", "probe", "warn", "CDP degraded", {"source": "api_only"})
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from scripts._const import LOGS_DIR

# Terminal logger — human-readable, real-time feedback
_terminal = logging.getLogger("pounding-ozon")


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

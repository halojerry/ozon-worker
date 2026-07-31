#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from scripts._const import DATA_DIR

logger = logging.getLogger(__name__)


def current_task_id(default: str = 'default-task') -> str:
    value = str(os.environ.get('POUNDING_OZON_TASK_ID', '')).strip()
    return value or default


def task_dir(task_id: str | None = None) -> Path:
    tid = str(task_id or current_task_id()).strip() or 'default-task'
    path = DATA_DIR / 'ozon' / 'tasks' / tid
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_media_dir(kind: str, task_id: str | None = None) -> Path:
    path = task_dir(task_id) / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_old_files(max_age_days: int = 7, *, dry_run: bool = False) -> dict[str, int]:
    """Delete probe artifacts and browser sessions older than max_age_days.

    Returns {'deleted': count, 'bytes_freed': total_bytes, 'errors': count}
    """
    tasks_root = DATA_DIR / 'ozon' / 'tasks'
    sessions_dir = DATA_DIR / 'browser' / 'sessions'
    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0
    bytes_freed = 0
    errors = 0

    for scan_dir in (tasks_root, sessions_dir):
        if not scan_dir.is_dir():
            continue
        for f in scan_dir.rglob('*'):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    size = f.stat().st_size
                    if not dry_run:
                        f.unlink()
                    deleted += 1
                    bytes_freed += size
            except OSError:
                errors += 1

    if deleted > 0:
        logger.info(
            'cleanup: removed %d old files (%d MB freed), %d errors (dry_run=%s)',
            deleted, bytes_freed // (1024 * 1024), errors, dry_run,
        )

    return {'deleted': deleted, 'bytes_freed': bytes_freed, 'errors': errors}

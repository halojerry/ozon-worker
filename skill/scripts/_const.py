#!/usr/bin/env python3
"""Constants for pounding-ozon-probe."""
from __future__ import annotations

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
DATA_DIR = SKILL_ROOT / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = DATA_DIR / 'config'
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PROFILE = os.environ.get('POUNDING_OZON_STORE', '').strip() or 'default'
CONFIG_FILE = CONFIG_DIR / f'runtime_config.{CONFIG_PROFILE}.json'
LEGACY_CONFIG_FILE = CONFIG_DIR / 'runtime_config.json'


def get_config_profile() -> str:
    return (
        os.environ.get('POUNDING_OZON_STORE', '').strip()
        or os.environ.get('UNIFIED_1688_OZON_STORE', '').strip()
        or 'default'
    )


DEFAULT_OZON_CURRENCY = 'RUB'
DEFAULT_CACHE_TTL_SECONDS = 86400
CLOUD_API_BASE = os.environ.get('WORKER_URL', 'https://worker.mxou.cn').rstrip('/')
LOGS_DIR = DATA_DIR / 'logs'
LOGS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = DATA_DIR / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
# 重采集串行闸锁（v0.83 批④：从 cli.py 上提——detach 占用探针与闸共用同一路径，
# 单一事实源，防两处字面量漂移）
LOCKS_DIR = DATA_DIR / 'locks'
LOCKS_DIR.mkdir(parents=True, exist_ok=True)
HEAVY_LOCK_PATH = LOCKS_DIR / 'heavy_cdp.lock'
# 后台任务注册表（v0.83 批④：--detach 落盘；pounding-mcp job_* 读同目录）
JOBS_DIR = DATA_DIR / 'jobs'
SKILL_NAME = 'pounding-ozon-probe'

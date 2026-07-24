#!/usr/bin/env python3
"""Constants for pounding-ozon-probe."""
from __future__ import annotations

import os
import json as _json
from pathlib import Path

SKILL_VERSION = '0.1.0'
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


def _read_pounding_config(key_path: str) -> str | None:
    config_path = Path.home() / ".pounding" / "config.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path) as f:
            cfg = _json.load(f)
        for part in key_path.split("."):
            if isinstance(cfg, dict):
                cfg = cfg.get(part)
            else:
                return None
        return str(cfg) if cfg is not None else None
    except Exception:
        return None


DEFAULT_OZON_CURRENCY = 'RUB'
DEFAULT_CACHE_TTL_SECONDS = 86400
CLOUD_API_BASE = 'https://worker.mxou.cn'
LOGS_DIR = DATA_DIR / 'logs'
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SKILL_NAME = 'pounding-ozon-probe'

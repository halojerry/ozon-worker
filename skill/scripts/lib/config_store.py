#!/usr/bin/env python3
"""Unified configuration store for pounding-ozon-probe.

All config lives in skill/data/config/:
  stores.json   — Ozon multi-store credentials (client_id + api_key)
  settings.json — 1688 AK + MXOU_TOKEN + other settings

No .env fallback. Single source of truth.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from scripts._const import CONFIG_DIR, SKILL_ROOT

# Config file paths
STORES_FILE = CONFIG_DIR / 'stores.json'
SETTINGS_FILE = CONFIG_DIR / 'settings.json'

# Ensure config directory exists
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# stores.json — Ozon multi-store management
# ═══════════════════════════════════════════════════════════════════════════

def _load_stores_file() -> dict[str, Any]:
    """Load stores.json. Returns {"default": "...", "stores": {...}}."""
    if STORES_FILE.is_file():
        try:
            return json.loads(STORES_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {"default": "", "stores": {}}


def _save_stores_file(data: dict[str, Any]) -> None:
    """Save stores.json."""
    STORES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def list_stores() -> dict[str, dict[str, str]]:
    """Return all configured stores. Keys are store names, values have client_id/api_key/currency."""
    data = _load_stores_file()
    stores = data.get("stores", {})
    return {k: v for k, v in stores.items() if isinstance(v, dict)}


def get_store(store_id: str = "") -> dict[str, str] | None:
    """Get a specific store by name. Returns None if not found.

    If store_id is empty, returns the default store.
    """
    data = _load_stores_file()
    stores = data.get("stores", {})
    if not store_id:
        store_id = data.get("default", "")
    if not store_id:
        # Return first store if no default
        for sid, profile in stores.items():
            if isinstance(profile, dict):
                return profile
        return None
    store = stores.get(str(store_id))
    if isinstance(store, dict):
        return store
    return None


def set_store(store_id: str, client_id: str, api_key: str,
              currency: str = "", shipping_provider: str = "", shipping_service: str = "") -> dict[str, Any]:
    """Upsert a store profile."""
    data = _load_stores_file()
    if "stores" not in data or not isinstance(data.get("stores"), dict):
        data["stores"] = {}

    store = data["stores"].get(str(store_id), {})
    if not isinstance(store, dict):
        store = {}

    store["client_id"] = client_id
    store["api_key"] = api_key
    if currency:
        store["currency"] = currency
    if shipping_provider:
        store["shipping_provider"] = shipping_provider
    if shipping_service:
        store["shipping_service"] = shipping_service

    data["stores"][str(store_id)] = store

    # Set as default if it's the first store
    if not data.get("default") or len(data["stores"]) == 1:
        data["default"] = str(store_id)

    _save_stores_file(data)
    return store


def remove_store(store_id: str) -> bool:
    """Remove a store. Returns True if removed."""
    data = _load_stores_file()
    stores = data.get("stores", {})
    if str(store_id) in stores:
        del stores[str(store_id)]
        # Clear default if it was the removed store
        if data.get("default") == str(store_id):
            data["default"] = next(iter(stores), "") if stores else ""
        _save_stores_file(data)
        return True
    return False


def set_default_store(store_id: str) -> bool:
    """Set the default store. Returns True if set."""
    data = _load_stores_file()
    if str(store_id) in data.get("stores", {}):
        data["default"] = str(store_id)
        _save_stores_file(data)
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# settings.json — AK + MXOU_TOKEN + other settings
# ═══════════════════════════════════════════════════════════════════════════

def _load_settings_file() -> dict[str, Any]:
    """Load settings.json."""
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings_file(data: dict[str, Any]) -> None:
    """Save settings.json."""
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value by key."""
    return _load_settings_file().get(key, default)


def set_setting(key: str, value: Any) -> None:
    """Set a setting value."""
    data = _load_settings_file()
    data[key] = value
    _save_settings_file(data)


def remove_setting(key: str) -> bool:
    """Remove a setting. Returns True if removed."""
    data = _load_settings_file()
    if key in data:
        del data[key]
        _save_settings_file(data)
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Credential resolution
# ═══════════════════════════════════════════════════════════════════════════

def get_ali_1688_ak() -> str:
    """Get 1688 AK from settings.json."""
    return str(get_setting("ali_1688_ak", "")).strip()


def set_ali_1688_ak(ak: str) -> None:
    """Save 1688 AK to settings.json."""
    set_setting("ali_1688_ak", ak)


def get_mxou_token() -> str:
    """Get MXOU_TOKEN. Try ~/.pounding/config.json first, then settings.json."""
    # 1. Try ~/.pounding/config.json (auto-read, no user action needed)
    pounding = _load_pounding_config()
    api_section = pounding.get("api", {}) if isinstance(pounding.get("api"), dict) else {}
    token = str(api_section.get("key", "")).strip()
    if token:
        # Auto-save to our settings.json for future use
        current = get_setting("mxou_token", "")
        if current != token:
            set_setting("mxou_token", token)
        return token

    # 2. Try settings.json
    token = str(get_setting("mxou_token", "")).strip()
    return token


def set_mxou_token(token: str) -> None:
    """Save MXOU_TOKEN to settings.json."""
    set_setting("mxou_token", token)


def get_ozon_credentials(store_id: str = "") -> dict[str, str] | None:
    """Get Ozon credentials for a specific store.

    Returns {"client_id": "...", "api_key": "..."} or None if not configured.
    If store_id is empty, uses the default store.
    """
    store = get_store(store_id)
    if store and store.get("client_id") and store.get("api_key"):
        return {"client_id": store["client_id"], "api_key": store["api_key"]}
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Config check — diagnostics for `check` command
# ═══════════════════════════════════════════════════════════════════════════

def check_config() -> dict[str, Any]:
    """Check which required config values are present/missing.

    Returns:
        {"missing": [...], "present": [...], "stores": {...},
         "user_action": str | None,
         "cdp": {...}}
    """
    missing: list[str] = []
    present: list[str] = []

    # Check MXOU_TOKEN
    if get_mxou_token():
        present.append("MXOU_TOKEN")
    else:
        missing.append("MXOU_TOKEN")

    # Check 1688 AK
    if get_ali_1688_ak():
        present.append("ALI_1688_AK")
    else:
        missing.append("ALI_1688_AK")

    # Check Ozon stores
    stores = list_stores()
    if stores:
        present.append(f"OZON_STORES({len(stores)})")
    else:
        missing.append("OZON_STORES")

    # CDP browser probe
    cdp_status: dict[str, Any] = {
        'browser_available': False,
        'browser_path': None,
        'cdp_running': False,
        'cdp_url': None,
        'login_required': True,
        'auto_launch': False,
        'user_action': None,
    }
    try:
        from scripts.capabilities.browser_probe.service import check_cdp_prerequisites
        prereqs = check_cdp_prerequisites()
        cdp_status['browser_available'] = prereqs.get('browser_available', False)
        cdp_status['browser_path'] = prereqs.get('browser_path')
        cdp_status['cdp_running'] = prereqs.get('session_available', False)
        cdp_status['cdp_url'] = prereqs.get('cdp_url')
        cdp_status['login_required'] = prereqs.get('login_required', True)
        cdp_status['auto_launch'] = (
            cdp_status['browser_available']
            and (not cdp_status['cdp_running'] or cdp_status['login_required'])
        )
        issues = prereqs.get('issues', [])
        suggestions = prereqs.get('suggestions', [])
        if issues:
            cdp_status['user_action'] = (
                '🖥️ 浏览器探针未就绪：\n'
                + '\n'.join(f'  • {s}' for s in issues)
                + '\n'
                + '\n'.join(f'  → {s}' for s in suggestions)
            )
    except Exception as e:
        logger.debug('CDP probe status check failed: %s', e)

    # Build user_action
    user_action = None
    if missing:
        lines = ["请配置以下凭证：", ""]
        for k in missing:
            if k == "MXOU_TOKEN":
                lines.append("  python3 scripts/cli.py set_token --token <你的token>")
            elif k == "ALI_1688_AK":
                lines.append("  python3 scripts/cli.py get_ak  # 自动获取")
                lines.append("  # 或手动设置:")
                lines.append("  python3 scripts/cli.py set_ak --ak <你的AK>")
            elif k == "OZON_STORES":
                lines.append('  python3 scripts/cli.py set_store --name "店铺名" --client-id <ID> --api-key <KEY>')
        user_action = "\n".join(lines)

    return {
        'missing': missing,
        'present': present,
        'stores': {name: {"has_client_id": bool(s.get("client_id")), "has_api_key": bool(s.get("api_key"))}
                   for name, s in stores.items()},
        'user_action': user_action,
        'cdp': cdp_status,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════════════════════════

def _load_pounding_config() -> dict[str, Any]:
    """Load ~/.pounding/config.json (for auto-reading MXOU_TOKEN only)."""
    pounding_path = Path.home() / '.pounding' / 'config.json'
    if pounding_path.is_file():
        try:
            return json.loads(pounding_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_store_profile(store_id: str = "") -> dict[str, str]:
    """Get store-specific config (currency, shipping) from stores.json.

    Returns dict with keys: currency, shipping_provider, shipping_service.
    Returns empty dict if store not configured.
    """
    store = get_store(store_id)
    if not store:
        return {}
    return {k: v for k, v in store.items()
            if k in ('currency', 'shipping_provider', 'shipping_service') and isinstance(v, str)}


# ═══════════════════════════════════════════════════════════════════════════
# Sentry (best-effort error tracking)
# ═══════════════════════════════════════════════════════════════════════════

_SENTRY_INITIALIZED = False


def init_sentry() -> bool:
    """Initialize Sentry SDK. Reads DSN from settings.json."""
    global _SENTRY_INITIALIZED
    if _SENTRY_INITIALIZED:
        return True

    dsn = str(get_setting("sentry_dsn", "")).strip()
    if not dsn:
        logger.debug('Sentry DSN not configured — error tracking disabled')
        return False

    try:
        import sentry_sdk  # type: ignore
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.1,
            environment=os.environ.get('APP_ENV', 'production'),
            _experiments={'continuous_profiling_auto_start': False},
        )
        _SENTRY_INITIALIZED = True
        return True
    except ImportError:
        return False
    except Exception:
        return False


def capture_exception(exc: BaseException | None = None, **extra: Any) -> None:
    """Report an exception to Sentry (best-effort, no-op if not initialized)."""
    if not _SENTRY_INITIALIZED:
        return
    try:
        import sentry_sdk
        if exc:
            sentry_sdk.capture_exception(exc)
        if extra:
            with sentry_sdk.push_scope() as scope:
                for k, v in extra.items():
                    scope.set_extra(k, v)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Backward compatibility aliases (will be removed in future)
# ═══════════════════════════════════════════════════════════════════════════

# These aliases exist to avoid breaking imports in files that haven't been updated yet.
# They will be removed once all callers are migrated.

def load_env_file() -> None:
    """No-op. Config is now in stores.json/settings.json."""
    pass


def get_required_keys() -> dict[str, dict[str, str]]:
    """Return credential metadata. Kept for backward compatibility."""
    return {
        'MXOU_TOKEN':        {'label': '平台 Token（云端认证）',       'tier': 'dist', 'source': 'settings.json'},
        'ALI_1688_AK':       {'label': '1688 AK（本地搜索用）',       'tier': 'user', 'source': 'settings.json'},
        'OZON_CLIENT_ID':    {'label': 'Ozon Client ID（上架用）',    'tier': 'user', 'source': 'stores.json'},
        'OZON_API_KEY':      {'label': 'Ozon API Key（上架用）',      'tier': 'user', 'source': 'stores.json'},
    }

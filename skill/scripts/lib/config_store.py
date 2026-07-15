#!/usr/bin/env python3
"""Local configuration store for pounding-ozon-hybrid.

Credential tiers:
  Tier 1 (dist) — distribution-level, bundled with the skill:
    MXOU_TOKEN → ~/.pounding/config.json (read-only, never written to .env)

  Tier 2 (user) — user-level, persisted across conversations:
    ALI_1688_AK, OZON_CLIENT_ID, OZON_API_KEY, MXOU_IMAGE_TOKEN
    → .env file or environment variables
    → When user provides them, write to .env so they survive context loss
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from scripts._const import CONFIG_DIR, CONFIG_FILE, LEGACY_CONFIG_FILE, SKILL_ROOT

# ---------------------------------------------------------------------------
# .env persistence (user credentials)
# ---------------------------------------------------------------------------
ENV_FILE = SKILL_ROOT / '.env'

# Keys that belong to the user tier (writable to .env)
_USER_TIER_KEYS = frozenset({'ALI_1688_AK', 'OZON_CLIENT_ID', 'OZON_API_KEY'})

# Keys that belong to the distribution tier (never written to .env)
# MXOU_TOKEN, MXOU_IMAGE_TOKEN — both come from ~/.pounding/config.json api.key
_DIST_TIER_KEYS = frozenset({'MXOU_TOKEN', 'MXOU_IMAGE_TOKEN'})


def load_env_file() -> None:
    """Load .env file into os.environ (idempotent — won't override existing vars).

    Called once at module-import time by cloud_client.py so that every
    subsequent os.environ.get() picks up persisted user credentials.
    """
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def write_env_file(key: str, value: str) -> None:
    """Upsert a KEY=VALUE pair into the .env file.

    Only user-tier keys are accepted.  Distribution-tier keys (MXOU_TOKEN)
    raise ValueError — they must live in ~/.pounding/config.json only.
    """
    if key in _DIST_TIER_KEYS:
        raise ValueError(
            f'{key} 是分发级凭证，只能通过 ~/.pounding/config.json 配置，不能写入 .env'
        )
    if key not in _USER_TIER_KEYS:
        raise ValueError(f'{key} 不是已知的用户级凭证，不能写入 .env')

    lines: list[str] = []
    found = False
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                lines.append(line)
                continue
            if '=' in stripped:
                k, _, _ = stripped.partition('=')
                if k.strip() == key:
                    lines.append(f'{key}={value}')
                    found = True
                    continue
            lines.append(line)

    if not found:
        lines.append(f'{key}={value}')

    ENV_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.environ[key] = value


# ---------------------------------------------------------------------------
# JSON config file (legacy + distribution tier)
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or CONFIG_FILE
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback to legacy config
    legacy = LEGACY_CONFIG_FILE
    if legacy.is_file():
        try:
            return json.loads(legacy.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(config: dict[str, Any], config_path: Path | None = None) -> None:
    path = config_path or CONFIG_FILE
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')


def get(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def set_key(key: str, value: Any) -> None:
    config = load_config()
    config[key] = value
    save_config(config)


# ---------------------------------------------------------------------------
# Credential introspection
# ---------------------------------------------------------------------------

def get_required_keys() -> dict[str, dict[str, str]]:
    """Return credential metadata keyed by env-var name.

    Each value is a dict with:
      label  — human-readable Chinese name
      tier   — 'dist' (distribution, read-only) or 'user' (persisted to .env)
      source — where the value is resolved from
    """
    return {
        'MXOU_TOKEN':        {'label': '平台 Token（云端认证）',       'tier': 'dist', 'source': '~/.pounding/config.json'},
        'ALI_1688_AK':       {'label': '1688 AK（本地搜索用）',       'tier': 'user', 'source': '.env 或环境变量'},
        'OZON_CLIENT_ID':    {'label': 'Ozon Client ID（上架用）',    'tier': 'user', 'source': '.env 或环境变量'},
        'OZON_API_KEY':      {'label': 'Ozon API Key（上架用）',      'tier': 'user', 'source': '.env 或环境变量'},
        'MXOU_IMAGE_TOKEN':  {'label': 'mxou 图片生成 Token（AI 生图，同 api.key）', 'tier': 'dist', 'source': '~/.pounding/config.json（api.key）'},
    }


def check_config() -> dict[str, Any]:
    """Check which required config values are missing.

    Resolution order (per credential):
      - dist tier: ~/.pounding/config.json (api.key) first, then env fallback
      - user tier: os.environ first, then .env file, then runtime_config.json fallback

    Returns:
        {"missing": [...], "present": [...], "by_tier": {"dist": {...}, "user": {...}},
         "user_action": str | None,
         "cdp": {"browser_available": bool, "cdp_running": bool,
                 "login_required": bool, "auto_launch": bool,
                 "user_action": str | None}}  ← CDP browser probe readiness
    """
    config = load_config()
    pounding_config = _load_pounding_config()
    required = get_required_keys()
    missing: list[str] = []
    present: list[str] = []
    by_tier: dict[str, dict[str, bool]] = {'dist': {}, 'user': {}}

    for key, meta in required.items():
        tier = meta['tier']
        if tier == 'dist':
            api_section = pounding_config.get('api', {}) if isinstance(pounding_config.get('api'), dict) else {}
            val = str(api_section.get('key', '')).strip()
            if not val:
                val = os.environ.get(key, '').strip()
        else:
            val = os.environ.get(key, config.get(key, '')).strip()

        if val:
            present.append(key)
            by_tier.setdefault(tier, {})[key] = True
        else:
            missing.append(key)
            by_tier.setdefault(tier, {})[key] = False

    # ═══════════════════════════════════════════════════════════════════════
    # CDP Browser Probe pre-check — tells agent whether it can extract
    # product images from 1688 detail pages.
    # ═══════════════════════════════════════════════════════════════════════
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

    # Build user_action — exactly what the user needs to do manually.
    # NEVER write keys programmatically (Claude Code sanitizes sk-* keys).
    user_action = None
    dist_missing = [k for k in missing if required[k]['tier'] == 'dist']
    user_missing = [k for k in missing if required[k]['tier'] == 'user']

    if dist_missing:
        lines = [
            "请手动设置平台 Token（在终端运行）：",
            "",
            '  export MXOU_TOKEN="sk-你的密钥"',
            "",
            "或者创建 ~/.pounding/config.json：",
            "",
            '  mkdir -p ~/.pounding',
            "  echo '{\"api\":{\"key\":\"sk-你的密钥\"}}' > ~/.pounding/config.json",
            "",
            "⚠️ 注意：MXOU_TOKEN 绝不能在对话中明文传递，请在终端手动操作。",
        ]
        user_action = "\n".join(lines)

    if user_missing:
        lines = [
            "请设置以下配置（在终端运行）：",
            "",
        ]
        for k in user_missing:
            label = required[k]['label']
            lines.append(f'  export {k}="你的{label}"')
        lines.append("")
        lines.append("或通过 AI 助手写入 .env 文件（非敏感配置）：'设置 {key}={value}'")
        if not dist_missing:
            user_action = "\n".join(lines)
        else:
            user_action = user_action + "\n\n" + "\n".join(lines)

    return {
        'missing': missing,
        'present': present,
        'by_tier': by_tier,
        'user_action': user_action,
        'cdp': cdp_status,
    }


# ---------------------------------------------------------------------------
# Sentry (best-effort error tracking for local skill)
# ---------------------------------------------------------------------------

_SENTRY_INITIALIZED = False

def init_sentry() -> bool:
    """Initialize Sentry SDK for local skill error tracking.

    Reads SENTRY_DSN from environment or ~/.pounding/config.json.
    Best-effort — silently returns False if sentry-sdk not installed
    or DSN not configured.
    """
    global _SENTRY_INITIALIZED
    if _SENTRY_INITIALIZED:
        return True

    dsn = os.environ.get('SENTRY_DSN', '').strip()
    if not dsn:
        pounding = _load_pounding_config()
        sentry_cfg = pounding.get('sentry', {}) if isinstance(pounding.get('sentry'), dict) else {}
        dsn = str(sentry_cfg.get('dsn', '')).strip()
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


def _sync_to_dotenv(env_path: Path, key: str, value: str) -> None:
    """Write or update a key=value line in .env file."""
    lines: list[str] = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding='utf-8').splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f'{key}=') or line.strip().startswith(f'# {key}='):
            lines[i] = f'{key}={value}'
            found = True
            break
    if not found:
        lines.append(f'{key}={value}')
    env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _load_pounding_config() -> dict[str, Any]:
    """Load ~/.pounding/config.json (distribution-tier credentials)."""
    pounding_path = Path.home() / '.pounding' / 'config.json'
    if pounding_path.is_file():
        try:
            return json.loads(pounding_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_store_profile(store_id: str = "") -> dict[str, str]:
    """Get store-specific config (currency, shipping) from ~/.pounding/config.json.

    Returns dict with keys: currency, shipping_provider, shipping_service.
    Returns empty dict if store not configured.
    If store_id is empty, returns the first store found.
    """
    cfg = _load_pounding_config()
    stores = cfg.get('stores', {})
    if not isinstance(stores, dict):
        return {}
    if store_id:
        return {k: v for k, v in stores.get(str(store_id), {}).items() if isinstance(v, str)}
    # Return first store if no specific ID given
    for sid, profile in stores.items():
        if isinstance(profile, dict):
            return {k: v for k, v in profile.items() if isinstance(v, str)}
    return {}


def write_store_profile(store_id: str, currency: str = "", shipping_provider: str = "", shipping_service: str = "") -> dict[str, Any]:
    """Upsert a store profile into ~/.pounding/config.json."""
    pounding_path = Path.home() / '.pounding' / 'config.json'
    pounding_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _load_pounding_config()
    if 'stores' not in cfg or not isinstance(cfg.get('stores'), dict):
        cfg['stores'] = {}
    store = cfg['stores'].get(str(store_id), {})
    if not isinstance(store, dict):
        store = {}
    if currency:
        store['currency'] = currency
    if shipping_provider:
        store['shipping_provider'] = shipping_provider
    if shipping_service:
        store['shipping_service'] = shipping_service
    cfg['stores'][str(store_id)] = store
    pounding_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    return store

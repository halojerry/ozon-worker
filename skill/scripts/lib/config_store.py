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
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from scripts._const import CONFIG_DIR

# Config file paths
STORES_FILE = CONFIG_DIR / 'stores.json'
SETTINGS_FILE = CONFIG_DIR / 'settings.json'

# Ensure config directory exists
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Atomic JSON write — 并发安全的原子写（Q15，与 cache.py v0.14 E3 同模式）
# ═══════════════════════════════════════════════════════════════════════════

def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写 JSON 文件：临时文件 + os.replace。

    ⚠️ Q15: 原实现直接 `Path.write_text()` 覆写——并发 CLI 进程（check/
    set_token/set_store 同开）同时写同一 JSON 时，读者可能读到半截文件 →
    JSONDecodeError、凭证丢失。os.replace 在**同目录**内是原子操作
    （临时文件用 with_suffix 生成，保证与目标同文件系统），Windows 上
    os.replace 可能因文件锁失败 → 短等待重试一次。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        os.replace(tmp_path, path)
    except OSError:
        # Windows 文件锁重试（cache.py E3 同款）
        time.sleep(0.05)
        os.replace(tmp_path, path)


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
    """Save stores.json (atomic, Q15)."""
    _atomic_write_json(STORES_FILE, data)


def list_stores() -> dict[str, dict[str, str]]:
    """Return all configured stores. Keys are store names, values have client_id/api_key/currency."""
    data = _load_stores_file()
    stores = data.get("stores", {})
    return {k: v for k, v in stores.items() if isinstance(v, dict)}


# ⚠️ P2-8 多店铺: stores.json 顶层 "default" 字段是**指针**（声明的默认店铺名），
# 若某店铺恰好也叫 "default"，该字段与店铺名歧义。解析规则：指针字段优先，
# 进程内只告警一次（避免一条命令多次 get_store 调用刷屏）。
_STORE_DEFAULT_AMBIGUITY_WARNED = False


def get_store(store_id: str = "") -> dict[str, str] | None:
    """Get a specific store by name. Returns None if not found.

    If store_id is empty, returns the default store.

    ⚠️ P2-8: 顶层 ``"default"`` 是指针字段（声明默认店铺名），**优先于**任何店铺名；
    若存在名为 ``"default"`` 的店铺，解析默认时告警一次（指针优先，不崩）。
    """
    global _STORE_DEFAULT_AMBIGUITY_WARNED
    data = _load_stores_file()
    stores = data.get("stores", {})
    if not store_id:
        default_pointer = data.get("default", "")
        _warn_default_ambiguity(default_pointer, stores)
        store_id = default_pointer
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


def _warn_default_ambiguity(default_pointer: Any, stores: dict[str, Any]) -> None:
    """P2-8: 默认店铺解析歧义告警（进程内一次，`_STORE_DEFAULT_AMBIGUITY_WARNED` 去重）。

    - 未声明默认（指针空）且多店铺 → 回退第一个，告警提示显式声明。
    - 存在名为 "default" 的店铺 → 指针字段与店铺名歧义，指针优先，告警提示重命名。
    """
    global _STORE_DEFAULT_AMBIGUITY_WARNED
    if _STORE_DEFAULT_AMBIGUITY_WARNED:
        return
    dict_stores = [k for k, v in stores.items() if isinstance(v, dict)]
    if not default_pointer:
        if len(dict_stores) > 1:
            _STORE_DEFAULT_AMBIGUITY_WARNED = True
            logger.warning(
                'stores.json 未声明默认店铺（"default" 指针为空），将使用第一个店铺。'
                '多店铺场景建议显式指定 --store 或设置默认店铺，避免歧义。'
            )
    elif "default" in dict_stores:
        # 指针字段与名为 "default" 的店铺同名歧义——指针是声明的默认，优先解析。
        _STORE_DEFAULT_AMBIGUITY_WARNED = True
        logger.warning(
            'stores.json: 店铺名为 "default" 与 "default" 指针字段同名歧义——'
            '按指针声明解析（默认店铺 = "%s"）。建议重命名该店铺，避免误读。',
            default_pointer,
        )


def set_store(store_id: str, client_id: str, api_key: str,
              currency: str = "", shipping_provider: str = "", shipping_service: str = "",
              margin_rate: float | None = None, commission_rate: float | None = None,
              fx_buffer: float | None = None) -> dict[str, Any]:
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
    if margin_rate is not None:
        store["margin_rate"] = margin_rate
    if commission_rate is not None:
        store["commission_rate"] = commission_rate
    if fx_buffer is not None:
        store["fx_buffer"] = fx_buffer

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
    """Save settings.json (atomic, Q15)."""
    _atomic_write_json(SETTINGS_FILE, data)


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


def get_store_profile(store_id: str = "") -> dict[str, Any]:
    """Get store-specific config from stores.json.

    Returns dict with keys: currency, shipping_provider, shipping_service,
    margin_rate, commission_rate, fx_buffer, fx_rate.
    Returns empty dict if store not configured.
    """
    store = get_store(store_id)
    if not store:
        return {}
    allowed = ('currency', 'shipping_provider', 'shipping_service',
               'margin_rate', 'commission_rate', 'fx_buffer', 'fx_rate')
    return {k: v for k, v in store.items() if k in allowed}


# ═══════════════════════════════════════════════════════════════════════════
# Sentry (best-effort error tracking)
# ═══════════════════════════════════════════════════════════════════════════

_SENTRY_INITIALIZED = False

# ⚠️ v0.37: skill 内置默认 Sentry DSN（用户零配置）。与 worker 同 org/project
# （pouding_ozon），用 environment 标签区分来源（skill）。DSN 的 public key
# 本就是设计为暴露的（SDK 初始化必需），硬编码是标准做法；上报内容仅异常
# 堆栈 + 非敏感 tags，绝不含 token/ak/api_key/client_id 凭证。
# 高级用户可用 settings.json `sentry_dsn` 覆盖（如自建 Sentry）。
DEFAULT_SENTRY_DSN = "https://a2491a4381126cbb40068fae5e79aee6@o4511410803441664.ingest.us.sentry.io/4511432541339648"


def _skill_version_tag() -> str:
    """读取 skill/VERSION 作为 Sentry release 标签（读不到回退 0.0.0）。"""
    try:
        from scripts._const import SKILL_ROOT
        v = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        return v or "0.0.0"
    except Exception:
        return "0.0.0"


def init_sentry() -> bool:
    """Initialize Sentry SDK. DSN: settings.json `sentry_dsn` → 内置默认。"""
    global _SENTRY_INITIALIZED
    if _SENTRY_INITIALIZED:
        return True

    dsn = str(get_setting("sentry_dsn", "")).strip() or DEFAULT_SENTRY_DSN
    if not dsn:
        logger.debug('Sentry DSN not configured — error tracking disabled')
        return False

    try:
        import sentry_sdk  # type: ignore
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.0,  # 仅错误事件，不上报性能 trace（省额度）
            environment="skill",
            release=_skill_version_tag(),
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

def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load config as a flat dict. Backward compatible with old API.

    Returns a dict with keys like 'ALI_1688_AK', 'OZON_CLIENT_ID', etc.
    Used by ak_1688_client._signature_headers().
    """
    result = {}
    # From settings.json
    settings = _load_settings_file()
    for k, v in settings.items():
        result[k.upper()] = v
    # From stores.json (first store)
    store = get_store()
    if store:
        result.setdefault("OZON_CLIENT_ID", store.get("client_id", ""))
        result.setdefault("OZON_API_KEY", store.get("api_key", ""))
    return result


def load_env_file() -> None:
    """No-op. Config is now in stores.json/settings.json."""


def get_required_keys() -> dict[str, dict[str, str]]:
    """Return credential metadata. Kept for backward compatibility."""
    return {
        'MXOU_TOKEN':        {'label': '平台 Token（云端认证）',       'tier': 'dist', 'source': 'settings.json'},
        'ALI_1688_AK':       {'label': '1688 AK（本地搜索用）',       'tier': 'user', 'source': 'settings.json'},
        'OZON_CLIENT_ID':    {'label': 'Ozon Client ID（上架用）',    'tier': 'user', 'source': 'stores.json'},
        'OZON_API_KEY':      {'label': 'Ozon API Key（上架用）',      'tier': 'user', 'source': 'stores.json'},
    }


# ═══════════════════════════════════════════════════════════════════════════
# Auth framework — 核心函数级鉴权
# ═══════════════════════════════════════════════════════════════════════════

import hashlib
import time as _time

AUTH_CACHE_FILE = CONFIG_DIR / 'auth_cache.json'
AUTH_CACHE_TTL = 86400  # 24 小时


class AuthError(Exception):
    """凭证缺失或无效。"""


def _load_auth_cache() -> dict[str, Any]:
    """Load auth cache from disk."""
    if AUTH_CACHE_FILE.is_file():
        try:
            return json.loads(AUTH_CACHE_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_auth_cache(token: str, expires_in: int) -> None:
    """Save auth verification result to disk (atomic, Q15)."""
    now = _time.time()
    cache = {
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "verified_at": now,
        "expires_at": now + expires_in,
    }
    _atomic_write_json(AUTH_CACHE_FILE, cache)


def is_auth_valid() -> bool:
    """Check if local auth cache is valid (not expired + token unchanged)."""
    cache = _load_auth_cache()
    if not cache:
        return False

    # Check if token changed
    current_token = get_mxou_token()
    if not current_token:
        return False
    token_hash = hashlib.sha256(current_token.encode()).hexdigest()
    if token_hash != cache.get("token_hash"):
        return False

    # Check expiry
    return _time.time() < cache.get("expires_at", 0)


def verify_with_worker(token: str, client_id: str = "", api_key: str = "") -> dict[str, Any]:
    """Call Worker /api/v1/auth/verify to validate credentials.

    Returns:
        {"valid": True, "reason": "ok", "expires_in": 86400} on success
        {"valid": False, "reason": "xxx"} on failure
    Raises:
        AuthError: Worker unreachable or auth endpoint not available
    """
    import requests

    from scripts._const import CLOUD_API_BASE
    url = f"{CLOUD_API_BASE}/api/v1/auth/verify"
    try:
        resp = requests.post(url, json={
            "token": token,
            "client_id": client_id,
            "api_key": api_key,
        }, timeout=15)
        if resp.status_code == 404:
            raise AuthError(
                "Worker 鉴权端点未部署。请更新 Worker 到最新版本。"
            )
        return resp.json()
    except AuthError:
        raise
    except Exception as e:
        raise AuthError(
            f"无法连接云端 Worker（{CLOUD_API_BASE}）。请检查网络或联系管理员。\n{e}"
        )


def fetch_mxou_balance(token: str) -> float | None:
    """查询 MXOU 平台真实余额(v0.29.3 与 Worker 统一来源)。

    调 OpenAI 兼容 /v1/dashboard/billing/subscription; token 自动补 sk- 前缀。
    返回 balance(负=欠费); 失败/网络异常 → None。
    """
    if not token:
        return None
    import requests

    _MXOU_API = "https://api.mxou.cn"  # 与 Worker mxou_api.py 一致
    tok = token if token.startswith("sk-") else f"sk-{token}"
    try:
        resp = requests.get(
            f"{_MXOU_API}/v1/dashboard/billing/subscription",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        balance = data.get("balance") if isinstance(data, dict) else None
        return float(balance) if balance is not None else None
    except Exception:
        return None


def _require_auth() -> None:
    """Core function auth guard. Raises AuthError if credentials are invalid.

    Check order:
    1. MXOU_TOKEN missing → AuthError with api.mxou.cn guidance
    2. Local cache valid → pass
    3. Cache expired → call Worker verify
    4. Verify passed → update cache → pass
    5. Verify failed → AuthError with reason
    """
    token = get_mxou_token()
    if not token:
        raise AuthError(
            "缺少 MXOU_TOKEN。请到 https://api.mxou.cn 注册获取，然后运行：\n"
            "  python3.12 scripts/cli.py set_token --token <你的token>"
        )

    if is_auth_valid():
        return

    # Cache expired or missing — verify with Worker
    result = verify_with_worker(token)
    if result.get("valid"):
        _save_auth_cache(token, result.get("expires_in", AUTH_CACHE_TTL))
        return

    # Verify failed
    reason = result.get("reason", "unknown")
    messages = {
        "token_invalid": "MXOU_TOKEN 无效。请到 https://api.mxou.cn 重新获取。",
        "balance_insufficient": "账户余额不足。请到 https://api.mxou.cn 充值。",
        "account_inactive": "账户未激活。请到 https://api.mxou.cn 激活。",
        "worker_unreachable": "无法连接云端 Worker。请检查网络。",
    }
    msg = messages.get(reason, f"鉴权失败（{reason}）")
    raise AuthError(f"{msg}\n然后运行: python3.12 scripts/cli.py set_token --token <新token>")


def get_auth_status() -> str:
    """Get human-readable auth status for check command.

    Returns:
        "✅ MXOU_TOKEN（已验证，下次验证: 2026-07-25 19:30）"
        "❌ MXOU_TOKEN（未配置）"
        "❌ MXOU_TOKEN（已过期，请重新验证）"
    """
    token = get_mxou_token()
    if not token:
        return "❌ MXOU_TOKEN（未配置）"

    cache = _load_auth_cache()
    if not cache:
        return "⚠️ MXOU_TOKEN（已配置，未验证）"

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if token_hash != cache.get("token_hash"):
        return "⚠️ MXOU_TOKEN（已变更，需重新验证）"

    expires_at = cache.get("expires_at", 0)
    if _time.time() >= expires_at:
        return "⚠️ MXOU_TOKEN（已过期，请重新验证）"

    # Format expiry time
    from datetime import datetime
    expiry_str = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M")
    return f"✅ MXOU_TOKEN（已验证，下次验证: {expiry_str}）"


def preflight_check(skip_store: bool = False) -> list[str]:
    """CLI-layer quick check for local config completeness (no cloud call).

    Returns list of missing credential names. Empty = all ready.
    """
    missing = []
    if not get_mxou_token():
        missing.append("MXOU_TOKEN")
    if not get_ali_1688_ak():
        missing.append("ALI_1688_AK")
    if not skip_store and not list_stores():
        missing.append("OZON_STORES")
    return missing


def print_setup_guide(missing: list[str]) -> None:
    """Print setup instructions for missing credentials."""
    print("❌ 缺少以下凭证，请先配置：\n")
    guides = {
        "MXOU_TOKEN": [
            "1. 访问 https://api.mxou.cn 注册并获取 API Token",
            "2. 设置: python3.12 scripts/cli.py set_token --token <你的token>",
        ],
        "ALI_1688_AK": [
            "1. 自动获取（需 Chrome）: python3.12 scripts/cli.py get_ak",
            "2. 或手动获取: 浏览器打开 https://clawhub.1688.com → 登录 → 复制 AK",
            "3. 设置: python3.12 scripts/cli.py set_ak --ak <你的AK>",
        ],
        "OZON_STORES": [
            "1. 从 Ozon 卖家后台获取: 设置 → API 密钥",
            "2. 设置: python3.12 scripts/cli.py set_store --name \"店铺名\" --client-id <ID> --api-key <KEY>",
        ],
    }
    for key in missing:
        print(f"  📌 {key}:")
        for line in guides.get(key, ["请联系管理员"]):
            print(f"     {line}")
        print()

#!/usr/bin/env python3
"""Cloud ingress client — pipeline submission + polling.

Architecture:
Local Skill (thin)          Cloud Pipeline (heavy lifting)
─────────────────────       ──────────────────────────────
search_1688 (browser/CDP)
match_supply → price calc
build_envelope
POST /webhook/follow-sell → import_by_sku → poll → update
POST /webhook/pipeline    → Build → Upload Ozon → Save SB
POST /webhook/stage-image-gen → 主图生成 → 跟随图 → 清单
poll status ← cloud storage      gateway_tasks CRUD

Local-only (must stay local):
- 1688 search/detail (browser + CDP login)
- 1688→Ozon category matching confirmation (human decision)
- Attribute collection from 1688 product detail
- Attribute resolution engine (complex matching logic)
- Ozon category tree search (when mapping not cached)

Cloud handles (stable, rarely changes):
- All Ozon API calls (import_by_sku, poll, update, upload)
- mxou image generation (prompt building, API calls, manifest assembly)
- COS asset mirror
- cloud storage read/write
- Follow-sell complete flow with copy_denied detection
- Category resolution from cloud storage cache
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from scripts._const import CLOUD_API_BASE, SKILL_VERSION, LOGS_DIR
from scripts._errors import (
    ERR_CLOUD_UNAVAILABLE,
    ERR_CLOUD_REJECTED,
    ERR_CLOUD_TIMEOUT,
    ERR_CLOUD_FAILED,
)
from scripts.lib.config_store import load_env_file, init_sentry, capture_exception
from scripts.lib.reference_images import get_best_product_images
from scripts.lib.task_paths import cleanup_old_files

import logging
import sys

# Configure root logger for agent observability.
# This ensures ALL logger.info/warning/error calls are visible to the agent
# without changing any existing log calls. DEBUG messages stay silent.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stderr,
    )

logger = logging.getLogger(__name__)

from scripts._const import _read_pounding_config

# Load persisted user credentials from .env at import time so every
# subsequent os.environ.get() picks them up.  Idempotent — existing
# env vars are never overwritten.
load_env_file()

# Initialize Sentry for local skill error tracking (best-effort)
init_sentry()

# Periodically clean up old probe artifacts (>7 days) to prevent
# unlimited disk growth.  Runs at most once per session (import-time).
try:
    cleanup_old_files(max_age_days=7)
except Exception as e:
    logger.debug('cleanup_old_files: %s', e)

# ── Skill update check (best-effort, non-blocking) ──
# Checks COS for a newer version of the skill.  Logs a warning if
# one is available.  Does NOT auto-download — user must explicitly
# run `pounding-ozon update`.  Controlled by POUNDING_SKIP_UPDATE_CHECK.
try:
    from scripts.lib.update import check_and_notify
    pass  # update check removed
except Exception as e:
    logger.debug('update check failed: %s', e)


# ═══════════════════════════════════════════════════════════════════════════
# Structured Task Logging — see scripts/lib/logging_utils.py
# ═══════════════════════════════════════════════════════════════════════════

from scripts.lib.logging_utils import AuditLogger, read_task_log as _read_task_log

# Backward-compatible wrapper
def _log_task(task_id: str, component: str, stage: str, level: str,
            msg: str, data: dict | None = None) -> None:
    """Write structured audit log. See AuditLogger in logging_utils.py."""
    AuditLogger(task_id).log(component, stage, level, msg, data)


def _load_path_registry() -> dict[str, str]:
    """Load webhook paths. Priority: cloud API discovery > registry file > hardcoded."""
    defaults = {
        "pipeline": "/webhook/pl-v3-304140",
        "ingest": "/webhook/v2-ingest-292201",
        "follow_sell": "/webhook/fs-v4-303992",
        "refresh": "/webhook/re-v2-304020",
        "image_gen": "/webhook/mx-bp2-377417",
        "pricing": "/webhook/pricing-v1",
        "attr_learn": "/webhook/attr-learn-v1",
        "task_status": "/webhook/task-status-v1",
        "cat_lookup": "/webhook/cat-lookup-v1",
    }

    # 1. Try file registry first (fast, offline)
    try:
        registry_path = Path(__file__).resolve().parent / "path_registry.json"
        if registry_path.exists():
            with open(registry_path) as f:
                data = json.load(f)
            for key in defaults:
                if key in data and data[key]:
                    defaults[key] = data[key]
    except Exception as e:
        logger.debug('path_registry.json load failed: %s', e)

    # 2. Try cloud API service discovery (tag-based, always current)
    try:
        _refresh_from__discovery_api(defaults)
    except Exception as e:
        logger.debug('cloud API discovery failed: %s', e)

    return defaults


def _refresh_from__discovery_api(paths: dict[str, str]) -> None:
    """Query cloud REST API for active workflows tagged 'pounding-ozon'.

    Any workflow tagged with 'pounding-ozon' + 'prod-{role}' will
    automatically update its webhook path in the registry.

    This means: deploy a new workflow, tag it, and the skill
    auto-discovers it without any code changes.
    """
    base = _get_api_base()
    _discovery_api = base.replace("webhook", "rest") if "/webhook" in base else f"{base.rstrip('/')}/rest"
    # Strip webhook base, use REST API
    if "worker.mxou.cn" in base:
        _discovery_api = "https://worker.mxou.cn/rest"
    else:
        return  # Only auto-discover on known server

    try:
        resp = requests.get(
            f"{_discovery_api}/workflows",
            params={"active": "true"},
            timeout=10,
        )
        resp.raise_for_status()
        workflows = resp.json().get("data", [])
    except Exception:
        return

    tag_role_map = {
        "prod-pipeline": "pipeline",
        "prod-ingest": "ingest",
        "prod-follow-sell": "follow_sell",
        "prod-refresh": "refresh",
        "prod-image-gen": "image_gen",
        "prod-attr-learn": "attr_learn",
        "prod-task-status": "task_status",
    }

    for wf in workflows:
        tags = wf.get("tags", [])
        wf_name = wf.get("name", "")
        for tag, role in tag_role_map.items():
            if tag in tags or tag in wf_name:
                # Extract webhook path from the webhook node
                for node in wf.get("nodes", []):
                    if "webhook" in node.get("type", ""):
                        wh_path = node.get("parameters", {}).get("path", "")
                        if wh_path:
                            paths[role] = f"/webhook/{wh_path}"
                            logger.info(
                                "🔍 cloud discovery: %s → %s (from workflow '%s')",
                                role, paths[role], wf_name
                            )

    # Save discovered paths back to registry for offline use
    try:
        registry_path = Path(__file__).resolve().parent / "path_registry.json"
        save_data = {k: v for k, v in paths.items() if not k.startswith("_")}
        with open(registry_path, "w") as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug('Failed to save discovered paths to registry: %s', e)

_paths = _load_path_registry()
PIPELINE_PATH = _paths["pipeline"]
FOLLOW_SELL_PATH = _paths["follow_sell"]
REFRESH_PATH = _paths["refresh"]
IMAGE_GEN_PATH = _paths["image_gen"]
ATTR_LEARN_PATH = _paths["attr_learn"]
TASK_STATUS_PATH = _paths["task_status"]


def _get_api_base() -> str:
    return os.environ.get("MXOU_API_BASE", "").strip() or CLOUD_API_BASE


def _get_token() -> str:
    """Resolve MXOU token: ENV → pounding config → prompt user."""
    try:
        from scripts.lib.config_store import resolve_credential
        return resolve_credential(
            "MXOU_TOKEN",
            pounding_path="api.key",
            prompt_label="MXOU API Key（用于 LLM 和图片生成）",
        )
    except ImportError:
        pass
    # Fallback: legacy resolution
    token = os.environ.get("MXOU_TOKEN", "").strip()
    if not token:
        token = _read_pounding_config("api.key") or ""
    if not token:
        try:
            from scripts.lib.config_store import get as _get
            token = _get("MXOU_TOKEN", "")
        except ImportError:
            pass
    return token


def _get_ozon_credentials(store_id: str | None = None) -> dict[str, str]:
    """Get Ozon credentials for a specific store.

    Priority: ENV > pounding config > prompt user.
    Supports multi-store via store_id parameter.

    Returns {"client_id": str, "api_key": str}
    """
    try:
        from scripts.lib.config_store import get_store
        store = get_store(store_id)
        if store:
            return {"client_id": store["client_id"], "api_key": store["api_key"]}
    except ImportError:
        pass

    # Fallback: legacy resolution
    cid = os.environ.get("OZON_CLIENT_ID", "").strip()
    akey = os.environ.get("OZON_API_KEY", "").strip()

    if not cid:
        cid = _read_pounding_config("ozon.client_id") or ""
    if not akey:
        akey = _read_pounding_config("ozon.api_key") or ""

    if not cid or not akey:
        try:
            from scripts.lib.config_store import get as _get
            cid = cid or _get("OZON_CLIENT_ID", "")
            akey = akey or _get("OZON_API_KEY", "")
        except ImportError:
            pass

    return {"client_id": cid, "api_key": akey}


def _cloud_post(url: str, body: dict[str, Any], *, timeout_sec: int = 60, headers: dict[str, str] | None = None, max_retries: int = 3) -> dict[str, Any]:
    """POST to cloud pipeline webhook, return parsed JSON or error envelope.

    Retries on ConnectionError (ECONNRESET, etc.) with exponential backoff.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=body, timeout=timeout_sec, headers=headers)
            if resp.status_code in (401, 403):
                return _error_envelope(body, "AUTH_FAILED", "认证失败", terminal=True)
            resp.raise_for_status()
            try:
                result = resp.json() if resp.text else {}
            except (json.JSONDecodeError, ValueError):
                return _error_envelope(body, ERR_CLOUD_REJECTED, f"云端返回非JSON响应 ({resp.status_code})")
            if isinstance(result, dict) and result.get('_auth_error'):
                return _error_envelope(body, result.get('error_code', 'AUTH_UNKNOWN'),
                                    result.get('message', 'Authentication failed'), terminal=True)
            # Version check: pipeline returns skill_version, compare with local
            if isinstance(result, dict):
                remote_ver = result.get('skill_version', '')
                if remote_ver and remote_ver != SKILL_VERSION:
                    result['update_available'] = True
                    result['current_version'] = SKILL_VERSION
                    result['latest_version'] = remote_ver
            return result if isinstance(result, dict) else {}
        except requests.ConnectionError as exc:
            last_error = exc
            if attempt < max_retries:
                wait = min(5 * attempt, 30)  # 5s, 10s, 15s backoff
                logger.warning("_cloud_post: connection error (attempt %d/%d), retrying in %ds: %s",
                            attempt, max_retries, wait, exc)
                time.sleep(wait)
                continue
            capture_exception(exc, url=url, phase='cloud_post', attempts=attempt)
            return _error_envelope(body, ERR_CLOUD_UNAVAILABLE, f"无法连接云端 ({url})", details=str(exc))
        except requests.Timeout as exc:
            last_error = exc
            if attempt < max_retries:
                wait = min(5 * attempt, 30)
                logger.warning("_cloud_post: timeout (attempt %d/%d), retrying in %ds: %s",
                            attempt, max_retries, wait, exc)
                time.sleep(wait)
                continue
            capture_exception(exc, url=url, phase='cloud_post', timeout=timeout_sec)
            return _error_envelope(body, ERR_CLOUD_TIMEOUT, f"云端请求超时 ({timeout_sec}s)", terminal=False, retryable=True, details=str(exc))
        except requests.HTTPError as exc:
            capture_exception(exc, url=url, phase='cloud_post', status=exc.response.status_code)
            detail = exc.response.text[:500]
            return _error_envelope(body, ERR_CLOUD_REJECTED, f"云端拒绝请求 ({exc.response.status_code}): {detail}", terminal=exc.response.status_code < 500, retryable=exc.response.status_code >= 500, details=detail)

    # Should not reach here, but be defensive
    return _error_envelope(body, ERR_CLOUD_UNAVAILABLE, f"无法连接云端 ({url})", details=str(last_error))


def _cloud_get(url: str, *, timeout_sec: int = 30, max_retries: int = 3) -> dict[str, Any] | None:
    """GET from cloud REST endpoint, return parsed JSON or None on failure.

    Used for lightweight queries (task status, etc.) via n8n webhooks.
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout_sec)
            if not resp.ok:
                logger.warning("_cloud_get: HTTP %d on %s (attempt %d/%d)", 
                            resp.status_code, url, attempt, max_retries)
                if attempt < max_retries:
                    time.sleep(min(3 * attempt, 15))
                    continue
                return None
            return resp.json() if resp.text else None
        except requests.ConnectionError:
            if attempt < max_retries:
                time.sleep(min(3 * attempt, 15))
                continue
            logger.warning("_cloud_get: connection error on %s", url)
            return None
        except Exception as exc:
            logger.warning("_cloud_get: error on %s: %s", url, exc)
            return None
    return None


# ── Envelope assembly ──


def build_envelope(
    *,
    source: dict[str, Any],
    assets: dict[str, Any] | None = None,
    draft: dict[str, Any] | None = None,
    extensions: dict[str, Any] | None = None,
    store_id: str = "",
) -> dict[str, Any]:
    """Build a simplified request envelope for n8n cloud submission.

    Only includes fields the n8n workflow actually reads — dead fields
    (version, project_id, subproject_id, request_id, mxou_base_url)
    are removed.
    """
    resolved_extensions = dict(extensions or {})
    # Auto-fill from pounding config / env / config_store
    ozon = _get_ozon_credentials()
    resolved_extensions.setdefault("ozon_client_id", ozon["client_id"])
    # ozon_api_key removed — Auth node looks up credentials from Supabase users table
    mxou_token = _read_pounding_config("api.key") or os.environ.get("MXOU_IMAGE_TOKEN", "")
    resolved_extensions.setdefault("mxou_token", mxou_token)
    if store_id:
        resolved_extensions.setdefault("store_id", store_id)

    # Build assets (image pipeline reads this for reference images)
    resolved_assets = dict(assets or {})
    draft_images = (draft or {}).get("images", []) if draft else []
    resolved_assets.setdefault("image_urls", draft_images[:10])

    # Shipping provider — prefer store profile, fall back to auto-detect (cached 1h)
    if not resolved_extensions.get("shipping_provider"):
        from scripts.lib.config_store import get_store_profile
        store = get_store_profile(store_id) if store_id else {}
        if store.get('shipping_provider'):
            resolved_extensions.setdefault("shipping_provider", store['shipping_provider'])
            resolved_extensions.setdefault("shipping_service", store.get('shipping_service', 'Standard'))
            if store.get('currency'):
                resolved_extensions.setdefault("ozon_currency", store['currency'])
        else:
            # Auto-detect from Ozon API (cached per-session, 1h TTL)
            _shipping_cache = getattr(build_envelope, '_shipping_cache', None)
            if _shipping_cache and _shipping_cache.get('expires', 0) > time.monotonic():
                resolved_extensions.setdefault("shipping_provider", _shipping_cache['provider'])
                resolved_extensions.setdefault("shipping_service", _shipping_cache['service'])
            else:
                import time as _time
                try:
                    from scripts.lib.ozon_api import _post as _ozon_post
                    delivery_resp = _ozon_post(
                        ozon["client_id"], ozon["api_key"],
                        "/v1/delivery-method/list",
                        {"filter": {"status": "ACTIVE"}},
                    )
                    methods = delivery_resp.get("result", []) or []
                    if methods:
                        provider = str(methods[0].get("provider", "") or "").strip()
                        service = str(methods[0].get("service", "") or "").strip()
                        if provider:
                            resolved_extensions.setdefault("shipping_provider", provider)
                        if service:
                            resolved_extensions.setdefault("shipping_service", service or "Standard")
                        build_envelope._shipping_cache = {
                            'provider': provider or 'RETS',
                            'service': service or 'Express',
                            'expires': _time.monotonic() + 3600,
                        }
                except Exception as e:
                    logger.debug('Shipping provider auto-detect failed: %s', e)
    # Fallback — user should configure their actual logistics provider
    resolved_extensions.setdefault("shipping_provider", "RETS")
    resolved_extensions.setdefault("shipping_service", "Standard")

    envelope: dict[str, Any] = {
        "source": source,
        "assets": resolved_assets,
        "draft": draft or {},
    }
    if resolved_extensions:
        envelope["extensions"] = resolved_extensions
    return envelope


# ── Submit / Poll ──

INGEST_PATH = _paths["ingest"]
CAT_LOOKUP_PATH = _paths.get("cat_lookup", "/webhook/cat-lookup-v1")


def lookup_category_webhook(keyword: str, *, min_confidence: float = 0.8) -> dict[str, Any]:
    """Query pipeline's Supabase for previously learned category mappings.

    Calls the ``cat-lookup`` webhook which reads ``category_mapping_verified``.
    Returns ``{found: true, mappings: [{description_category_id, type_id, confidence}]}``
    or ``{found: false}`` on miss/error.

    Only returns mappings with confidence >= min_confidence (A/B selection).
    """
    if not keyword or not keyword.strip():
        return {"found": False, "keyword": keyword}
    base = _get_api_base()
    token = _get_token()
    try:
        result = _cloud_post(
            f"{base.rstrip('/')}{CAT_LOOKUP_PATH}",
            {"keyword": keyword.strip(), "min_confidence": min_confidence},
            headers={"Authorization": f"Bearer {token}"},
            timeout_sec=15,
        )
        return result if isinstance(result, dict) else {"found": False}
    except Exception:
        return {"found": False, "keyword": keyword}


def submit_envelope(
    graph_input: dict[str, Any],
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Submit GraphInput envelope directly to Worker's /submit_task endpoint.

    POST {WORKER_URL}/submit_task with the full GraphInput body.
    Worker handles: auth → category → pricing → images → upload → learning.

    Args:
        graph_input: Either a full GraphInput dict {token, ozon_client_id, ozon_api_key, envelope},
                     or a raw envelope dict {draft, source, extensions} (legacy, auto-wrapped).

    Returns:
        {ok, task_id, message} on success, or {ok: False, error} on failure.
    """
    worker_url = os.environ.get("WORKER_URL", "http://localhost:8080").rstrip("/")
    url = f"{worker_url}/submit_task"

    # Auto-detect: full GraphInput vs raw envelope
    if "envelope" in graph_input and ("token" in graph_input or "ozon_client_id" in graph_input):
        body = graph_input  # Already a full GraphInput
    else:
        # Legacy: raw envelope dict, wrap into GraphInput
        ozon_creds = _get_ozon_credentials()
        body = {
            "token": _get_token(),
            "ozon_client_id": ozon_creds.get("client_id", ""),
            "ozon_api_key": ozon_creds.get("api_key", ""),
            "envelope": graph_input,
        }

    try:
        import requests
        resp = requests.post(url, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": f"Worker unreachable: {url}", "task_id": task_id}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"Worker timeout: {url}", "task_id": task_id}
    except Exception as e:
        return {"ok": False, "error": str(e), "task_id": task_id}


def submit_task(
    envelope: dict[str, Any],
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """[DEPRECATED] Use submit_envelope() only. Worker triggers pipeline from pending tasks.

    POST /webhook/pipeline — kept for backward compatibility.
    New code should use Worker-only trigger model: submit_envelope() → Worker → pipeline.
    """
    tid = task_id or f"task-{uuid.uuid4().hex[:12]}"
    base = _get_api_base()
    token = _get_token()
    body = {"task_id": tid, "envelope": envelope, "token": token}
    result = _cloud_post(
        f"{base.rstrip('/')}{PIPELINE_PATH}",
        body,
        headers={"Authorization": f"Bearer {token}"},
        timeout_sec=600,
    )
    result.setdefault("task_id", tid)
    return result


# ── GraphInput envelope (new pipeline format) ──


# Common Chinese color words for smart variant label extraction
_COLOR_WORDS = frozenset({
    "白色", "黑色", "红色", "蓝色", "绿色", "黄色", "粉色", "紫色",
    "灰色", "橙色", "青色", "棕色", "米色", "卡其", "银色", "金色",
    "浅兰色", "浅绿色", "浅紫色", "深灰色", "浅灰色", "米黄色", "黑灰色",
    "胡萝卜红", "草莓粉", "菠萝黄", "牛油果绿",
})

# ── Variant type detection patterns ──

# 尺码模式：S/M/L/XL/XXL，大/中/小号，数字+cm/mm，40*40cm 等
import re as _re_type
_SIZE_PATTERN = _re_type.compile(
    r'(?:^|[^a-zA-Z])'
    r'(?:S|M|L|XL|XXL|XXXL|XXXXL)'
    r'(?:$|[^a-zA-Z])'
    r'|[大小中]号|[大小中]码'
    r'|\d+\s*(?:cm|mm|厘米|毫米|см|мм)'
    r'|\d+\s*[xX×]\s*\d+\s*(?:cm|mm|厘米|毫米)?'
    r'|\d+\s*(?:英寸|inch)'
    r'|均码|通用码|универсальный'
)

# 数量模式：数字+PIC/个/只/件/条/张/瓶/包/盒/袋/套/pack/set/pcs
# 注意：长的匹配放前面（如"5只装"优先于"5只"）
_QUANTITY_PATTERN = _re_type.compile(
    r'\d+\s*(?:PIC|pic|Pcs|pcs|Pack|pack|Set|set|шт|ШТ)'
    r'|\d+\s*(?:个装|只装|件装|片装|瓶装|套装|袋装|包装|盒装)'
    r'|\d+\s*(?:个|只|件|条|张|瓶|包|盒|袋|套|片|卷|对|双|本|台|支|粒)'
    r'|[一二三四五六七八九十]\s*(?:个|只|件|条|张|瓶|包|盒|袋|套|片)'
    r'|[一二三四五六七八九十]\s*(?:个装|只装|件装|片装|套装)'
)

# 规格/型号关键词
_SPEC_KEYWORDS = frozenset({
    '款', '型', '版', '式', '代', '系',
    '基础', '升级', '豪华', '旗舰', '顶配',
    '加强', '加厚', '加长', '加宽',
    '标准', '高配', '低配', '经济',
    '普通', '高速', '低速', '静音',
    'USB', 'Type-C', 'Lightning', 'Micro', 'Mini',
    '可折叠', '不可折叠', '折叠', '便携',
    '防水', '不防水', '防摔', '防滑',
    '带灯', '不带灯', '带配件', '不带配件',
})

# ── SKU 过滤：引流/定制/客服关键词 ──
_SKU_SKIP_KEYWORDS: list[str] = [
    # 客服/咨询类 — 不可直接购买
    "联系客服", "咨询客服", "来电咨询", "询价", "询问客服",
    # 定制类 — 不是标准商品
    "定制", "定制款", "订制", "订做", "定做",
    "来图", "来样", "来图定制", "按需定制",
    "OEM", "ODM", "贴牌", "代工", "加工定制",
    "logo定制", "加印", "印logo", "改logo",
    # 引流/误导类
    "不含电池", "不含充电器", "不含配件",
    # 无效加购类
    "加包装", "加盒子", "加彩盒",  # 加包装是服务不是商品
]

# 低价引流检测：最低价 SKU 价格低于平均价的这个比例时标记
_BAIT_PRICE_RATIO_THRESHOLD: float = 0.3  # min_price < avg_price * 0.3
_BAIT_PRICE_GAP_MIN: float = 3.0  # max_price / min_price >= 3


def _is_skip_sku(sku_name: str) -> tuple[bool, str]:
    """Check if a SKU should be skipped (bait, custom, or service SKU).

    Returns (should_skip, reason).
    """
    if not sku_name:
        return False, ""
    for kw in _SKU_SKIP_KEYWORDS:
        if kw in sku_name:
            return True, kw
    return False, ""


def _filter_bait_and_custom_skus(
    variants: list[dict],
    drop_reason: str,
    dropped_skus: int,
) -> tuple[list[dict], str, int, list[dict]]:
    """Filter out bait, custom, and service SKUs from the variant list.

    Also detects low-price bait patterns (one SKU priced far below average).

    Returns (filtered_variants, updated_drop_reason, updated_dropped_skus, removed_skus).
    """
    if len(variants) <= 1:
        return variants, drop_reason, dropped_skus, []

    filtered: list[dict] = []
    removed: list[dict] = []
    reasons: list[str] = []

    # ── Price-based bait detection ──
    prices = [v.get("price", 0) for v in variants if v.get("price", 0) > 0]
    if len(prices) >= 2:
        min_p = min(prices)
        max_p = max(prices)
        avg_p = sum(prices) / len(prices)
        if min_p > 0 and max_p / min_p >= _BAIT_PRICE_GAP_MIN and min_p < avg_p * _BAIT_PRICE_RATIO_THRESHOLD:
            # Found potential bait: lowest price SKU is suspicious
            min_idx = prices.index(min_p)
            min_name = str(variants[min_idx].get("name", variants[min_idx].get("color", "")))
            # Verify it's not a legitimate quantity variant
            is_qty = any(
                kw in min_name for kw in
                ("片装", "个装", "只装", "件装", "PIC", "pic", "pack", "Pack")
            )
            if not is_qty:
                removed.append(variants[min_idx])
                reasons.append(f"低价引流: min=¥{min_p} < avg¥{avg_p:.0f}×{_BAIT_PRICE_RATIO_THRESHOLD}, ratio={max_p/min_p:.1f}x")
                # Only filter the bait one, keep the rest
                for i, v in enumerate(variants):
                    if i != min_idx:
                        filtered.append(v)
                        # Keyword check for remaining
                        skip, kw = _is_skip_sku(str(v.get("name", v.get("color", ""))))
                        if skip:
                            removed.append(v)
                            reasons.append(f"SKU关键词: {kw}")
                            continue
                # Update stats
                total_removed = len(removed)
                new_drop = drop_reason
                if reasons:
                    new_drop = (drop_reason + "; " if drop_reason else "") + "; ".join(reasons)
                return filtered, new_drop, dropped_skus + total_removed, removed

    # ── Keyword-based filtering ──
    for v in variants:
        name = str(v.get("name", v.get("color", "")))
        skip, kw = _is_skip_sku(name)
        if skip:
            removed.append(v)
            reasons.append(f"SKU关键词: {kw}")
        else:
            filtered.append(v)

    if reasons:
        new_drop = (drop_reason + "; " if drop_reason else "") + "; ".join(reasons)
        return filtered, new_drop, dropped_skus + len(removed), removed

    return variants, drop_reason, dropped_skus, removed


def _detect_variant_type(name: str) -> str:
    """Detect the variant type from a single SKU name.

    Returns one of: 'color', 'size', 'quantity', 'spec', 'unknown'

    Priority: quantity > size > color > spec > unknown

    >>> _detect_variant_type("白色")
    'color'
    >>> _detect_variant_type("1PIC")
    'quantity'
    >>> _detect_variant_type("S")
    'size'
    >>> _detect_variant_type("USB款")
    'spec'
    >>> _detect_variant_type("5只装3cm【白色】USB款")
    'quantity'  # 数量优先
    """
    if not name or not isinstance(name, str):
        return 'unknown'
    name = name.strip()
    if not name:
        return 'unknown'

    # 1. Check quantity (highest priority — "5只装" is fundamentally a quantity variant)
    if _QUANTITY_PATTERN.search(name):
        return 'quantity'

    # 2. Check size
    if _SIZE_PATTERN.search(name):
        return 'size'

    # 3. Check color
    for cw in sorted(_COLOR_WORDS, key=len, reverse=True):
        if cw in name:
            return 'color'

    # 4. Check spec keywords
    for kw in _SPEC_KEYWORDS:
        if kw in name:
            return 'spec'

    return 'unknown'


def _parse_variant_attributes(name: str) -> dict[str, str]:
    """Parse a variant name into structured attributes.

    Input:  "5只装3cm【白色】USB款"
    Output: {"颜色":"白色", "数量":"5只装", "尺寸":"3cm", "规格":"USB款"}

    >>> _parse_variant_attributes("5只装3cm【白色】USB款")
    {'颜色': '白色', '数量': '5只装', '尺寸': '3cm', '规格': 'USB款'}
    >>> _parse_variant_attributes("白色")
    {'颜色': '白色'}
    >>> _parse_variant_attributes("1PIC")
    {'数量': '1PIC'}
    """
    import re as _re

    attrs: dict[str, str] = {}
    name = str(name or '').strip()
    if not name:
        return attrs

    remaining = name

    # 1. Extract quantity
    qty_match = _QUANTITY_PATTERN.search(remaining)
    if qty_match:
        attrs['数量'] = qty_match.group(0)
        remaining = remaining.replace(qty_match.group(0), ' ').strip()

    # 2. Extract size
    size_match = _SIZE_PATTERN.search(remaining)
    if size_match:
        attrs['尺寸'] = size_match.group(0)
        remaining = remaining.replace(size_match.group(0), ' ').strip()

    # 3. Extract color (from brackets or embedded)
    bracket_matches = _re.findall(r"【(.+?)】", remaining)
    color_found = False
    if bracket_matches:
        for m in bracket_matches:
            if m in _COLOR_WORDS:
                attrs['颜色'] = m
                remaining = _re.sub(r"【" + _re.escape(m) + r"】", "", remaining)
                color_found = True
                break
    if not color_found:
        # Search for embedded color words (longest match first)
        for cw in sorted(_COLOR_WORDS, key=len, reverse=True):
            if cw in remaining:
                attrs['颜色'] = cw
                remaining = remaining.replace(cw, ' ', 1)
                color_found = True
                break

    # 4. Remaining text → spec (after cleaning)
    remaining = _re.sub(r"【.+?】", "", remaining)  # Remove any remaining brackets
    remaining = _re.sub(r"[-+]\s*", " ", remaining)
    remaining = _re.sub(r"\s+", " ", remaining).strip()
    # Remove common punctuation and separators
    remaining = _re.sub(r"[，,、。．.·•·]", "", remaining)
    if remaining and len(remaining) > 1:
        attrs['规格'] = remaining

    return attrs


def _detect_group_variant_type(values: list[dict]) -> str:
    """Detect the variant type for an option_group based on its values.

    Votes across all values and returns the majority type.
    """
    if not values:
        return 'unknown'

    counts: dict[str, int] = {}
    for v in values:
        vname = str(v.get('name', '')).strip()
        if not vname:
            continue
        vt = _detect_variant_type(vname)
        counts[vt] = counts.get(vt, 0) + 1

    if not counts:
        return 'unknown'

    # Return the type with the most votes (excluding 'unknown' if there are known types)
    known = {k: v for k, v in counts.items() if k != 'unknown'}
    if known:
        return max(known, key=lambda k: known[k])
    return 'unknown'


def _extract_variant_label(name: str) -> tuple[str, str]:
    """Parse a CDP variant name into (color, model).

    Examples:
        "高速款【白色】无叶涡轮" → ("白色", "高速款无叶涡轮")
        "普通款【粉色】"       → ("粉色", "普通款")
        "226白色可折叠风扇【英文彩盒】-100档" → ("白色", "226可折叠风扇-100档")
        "139胡萝卜红【英文彩盒】+底座"       → ("胡萝卜红", "139+底座")
    """
    import re as _re

    name = name.strip()

    # ── Pattern 1: text【color/feature】rest ──
    bracket_matches = _re.findall(r"【(.+?)】", name)
    if bracket_matches:
        # Find the bracket content that looks like a color
        color_candidate = ""
        for m in bracket_matches:
            if m in _COLOR_WORDS:
                color_candidate = m
                break
        if color_candidate:
            # Known color in brackets → use as color, rest is model
            model = _re.sub(r"【" + _re.escape(color_candidate) + r"】", "", name)
            model = _re.sub(r"\s+", "", model).strip()
            return (color_candidate, model)

        # Bracket content is NOT a color (e.g. "英文彩盒", "+底座")
        # Strip all brackets, then search for embedded color words in the remaining text
        clean = _re.sub(r"【.+?】", "", name)
        clean = _re.sub(r"[-+]\s*", " ", clean).strip()
        for cw in sorted(_COLOR_WORDS, key=len, reverse=True):
            if cw in clean:
                model = clean.replace(cw, "").strip()
                return (cw, model)
        # No color found — use cleaned name
        return (clean, "")

    # ── Pattern 2: no brackets — try to find embedded color word ──
    for cw in sorted(_COLOR_WORDS, key=len, reverse=True):
        if cw in name:
            model = name.replace(cw, "").strip()
            return (cw, model)

    # ── Fallback: use full name ──
    return (name, "")


class ProductValidationError(Exception):
    """产品数据校验不通过——不应重试，直接跳过该品。"""
    pass


def _validate_and_fix_product_data(
    item_id: str,
    title: str,
    cost_cny: float,
    images: list,
    weight_g: int,
    dimensions: dict,
    variants: list,
    option_groups: list,
) -> tuple[int, dict, list[str]]:
    """校验产品数据完整性，并应用软兜底默认值。

    返回 (weight_g, dimensions, errors)。
    errors 为空表示通过；非空表示硬阻断，应跳过该产品。
    """
    errors: list[str] = []

    # ── 软兜底：重量 ──
    if weight_g <= 0:
        weight_g = 50
        logger.warning(f"物品 {item_id} 重量缺失，使用默认值 50g")

    # ── 软兜底：尺寸 ──
    if dimensions.get("length", 0) <= 0 and dimensions.get("width", 0) <= 0 and dimensions.get("height", 0) <= 0:
        # 基于重量估算合理默认尺寸（假设密度 ~250 kg/m³，典型消费品）
        # 体积 = weight / density, 假设长方体各边比例 2:1.5:1
        # side = (volume * ratio)^(1/3)
        density = 250.0  # kg/m³
        volume_m3 = (weight_g / 1000.0) / density  # m³
        volume_mm3 = volume_m3 * 1e9  # mm³
        # 长方体比例 2:1.5:1
        ratio_product = 2.0 * 1.5 * 1.0  # = 3.0
        base_mm = (volume_mm3 / ratio_product) ** (1/3) if volume_mm3 > 0 else 50.0
        # 限制最小值（太小会被 Ozon ML 拒绝）
        base_mm = max(base_mm, 30.0)
        est_length = round(base_mm * 2.0)
        est_width = round(base_mm * 1.5)
        est_height = round(base_mm * 1.0)
        dimensions = {"length": est_length, "width": est_width, "height": est_height}
        logger.warning(
            f"物品 {item_id} 尺寸缺失，根据重量 {weight_g}g 估算: "
            f"{est_length}×{est_width}×{est_height}mm（密度={density}kg/m³）"
        )

    # ── 硬阻断：图片 ──
    if not images:
        errors.append("产品图片为空")

    # ── 硬阻断：价格 ──
    if not cost_cny or float(cost_cny) <= 0:
        errors.append(f"采购价格缺失或为0（{cost_cny}）")

    # ── 硬阻断：标题 ──
    if not title or not str(title).strip():
        errors.append("产品标题为空")

    # ── 硬阻断：SKU 完整性（根据 option_groups 动态校验）──
    if len(variants) > 1 and option_groups:
        for og in option_groups:
            og_name = og.get("name", "")
            og_values = [v.get("name", "").strip() for v in og.get("values", []) if v.get("name")]
            if not og_values:
                continue
            for vi, v in enumerate(variants):
                # 检查每个变体是否在这个 option_group 下有值
                v_color = str(v.get("color", "")).strip()
                v_model = str(v.get("model", "")).strip()
                v_size = str(v.get("size", "")).strip()
                # 在 option_group 的 values 中查找匹配
                combined = f"{v_color} {v_model} {v_size}".strip()
                matched = False
                for ov in og_values:
                    if ov in v_color or ov in v_model or ov in v_size or ov in combined:
                        matched = True
                        break
                if not matched and v_color not in ("default", "") and v_model not in ("default", ""):
                    # 最后兜底：变体只要有非空的 color/model/size 就算有值
                    if v_color or v_model or (v_size and v_size != "one size"):
                        matched = True
                if not matched:
                    errors.append(
                        f"SKU[{vi}] 缺少 {og_name} 属性值"
                        f"（color={v_color}, model={v_model}, size={v_size}）"
                    )

    if errors:
        logger.warning(f"❌ 物品 {item_id} 校验不通过: {'; '.join(errors)}")
    return weight_g, dimensions, errors


def build_graph_envelope(
    *,
    item_id: str,
    detail_url: str,
    category_query: str = "",
    store_id: str = "",
    title: str = "",
    poll_category: bool = True,
    max_skus: int | None = None,
) -> dict[str, Any]:
    """1688 API + CDP → GraphInput 格式 envelope。

    1. 1688 API get_product_details(item_id)
    2. CDP enrich_product_with_cdp(detail_url, api_data)
    3. Ozon category search (if poll_category=True)
    4. 组装为 {token, ozon_client_id, ozon_api_key, envelope}
    
    Args:
        max_skus: SKU 数量上限（None=使用默认值15，0=不限制）
    """
    from scripts.lib.ak_1688_client import get_product_details, enrich_product_with_cdp
    from scripts.lib.reference_images import get_best_product_images

    # ── 1. 1688 API ──
    try:
        details = get_product_details([str(item_id)])
        api_data = details.get(str(item_id), {})
    except Exception:
        api_data = {}
    if not api_data:
        api_data = {"title": title or "", "price": "", "images": []}

    # ── 2. CDP 浏览器富化（必须成功，不允许降级）──
    enriched = enrich_product_with_cdp(detail_url=detail_url, api_data=api_data)
    data = enriched.get("data", {})
    cdp_source = enriched.get("source", "?")

    # 完全失败：CDP 没拿到任何数据
    if cdp_source == "api_only":
        raise RuntimeError(
            f"CDP completely failed for {item_id}: "
            f"{enriched.get('degraded_reason', 'unknown')}"
        )

    # 图片质量校验：过滤 1688 API 兜底占位图和反爬追踪像素
    _BAD_IMAGE_PATTERNS = [
        "gtms04.alicdn.com/tps/i4/T1Sa1dFuJaXXaESgf7",  # API 占位图
        "tdum.alibaba.com/dss.js.jpg",                    # 反爬追踪像素
        "_____tmd_____/report.jpg",                       # 1688 上报像素
        "/dss.js.jpg",                                     # 通用追踪像素
    ]
    images = data.get("images", [])

    def _is_real_image(url: str) -> bool:
        for pat in _BAD_IMAGE_PATTERNS:
            if pat in url:
                return False
        # Must look like a product image (alicdn or 1688 CDN with image extension)
        if "alicdn.com" in url or "1688.com" in url:
            return True
        # Unknown domains — suspect, reject
        return False

    real_images = [img for img in images if _is_real_image(img)]
    has_attrs = bool(data.get("attributes") or data.get("option_groups"))
    has_supplier = bool(data.get("seller") or data.get("brand"))

    if cdp_source == "cdp_degraded":
        # 部分成功 — 必须有真实图片或（属性+供应商）
        if not real_images:
            raise RuntimeError(
                f"CDP degraded for {item_id} with no real images "
                f"(got {len(images)} anti-bot pixels/tracking images). "
                f"Reason: {enriched.get('degraded_reason', '?')}"
            )
        if not has_attrs and not has_supplier:
            raise RuntimeError(
                f"CDP degraded for {item_id}: {len(real_images)} real images "
                f"but 0 attributes and no supplier — anti-bot page detected. "
                f"Reason: {enriched.get('degraded_reason', '?')}"
            )
        logger.warning(
            "build_graph_envelope: %s — CDP degraded but got %d real images, "
            "attrs=%s supplier=%s, continuing",
            item_id, len(real_images), has_attrs, has_supplier,
        )

    if real_images:
        data["images"] = real_images

    # ── 3. Ozon 类目解析 ──
    ozon_creds = _get_ozon_credentials(store_id)
    category_name = ""
    ozon_category = {}
    if poll_category:
        search_text = category_query or title or (data.get("title") or "")
        if search_text:
            try:
                from scripts.lib.ozon_api import search_categories
                cats = search_categories(
                    ozon_creds["client_id"], ozon_creds["api_key"],
                    search_text, language="RU", max_results=1,
                )
                if cats:
                    best = cats[0]
                    ozon_category = {
                        "description_category_id": str(best["description_category_id"]),
                        "type_id": str(best["type_id"]),
                    }
                    category_name = best.get("type_name", "") or best.get("category_name", "")
            except Exception:
                pass

    # ── 4. 提取数据（必须来自 CDP，不使用硬编码默认值）──
    item_title = title or data.get("title", "")
    cost_cny = _parse_price(data.get("price", ""))

    # 重量：取 packaging_rows 第一个 SKU 的重量
    pkg_rows = data.get("packaging_rows") or []
    pkg_first = pkg_rows[0] if pkg_rows else {}
    weight_g = int(pkg_first.get("weightGrams", 0) or data.get("weight_grams") or 0)
    if not weight_g:
        weight_g = 0  # 管线定价需要真实重量，0 会让运费计算降到最低

    # 尺寸：取 CDP 探针解析的 packaging_rows 数据
    # 1688 使用 cm，Ozon 使用 mm → ×10
    def _parse_pkg_dim(pkg_row, key_text, key_cm, default=0):
        """Parse dimension from packaging row, converting cm→mm."""
        val_text = pkg_row.get(key_text)  # e.g. "19.20"
        if val_text:
            try:
                return int(float(str(val_text).strip()) * 10)  # cm → mm
            except (ValueError, TypeError):
                pass
        return default

    if pkg_first:
        dimensions = {
            "length": _parse_pkg_dim(pkg_first, "lengthText", "lengthCm"),
            "width": _parse_pkg_dim(pkg_first, "widthText", "widthCm"),
            "height": _parse_pkg_dim(pkg_first, "heightText", "heightCm"),
        }
    else:
        dimensions = {"length": 0, "width": 0, "height": 0}

    # 降级: 如果 packaging 表格没有尺寸，从 description 文本提取
    if (dimensions["length"] == 0 and dimensions["width"] == 0 and dimensions["height"] == 0):
        desc = data.get("description") or ""
        if desc:
            import re as _re
            # 匹配 L*W*H 格式: "34*25*2CM", "尺寸：30*9.5*4.5cm", "MEAS:51*35*42CM"
            dim_pat = _re.search(
                r'(?:尺寸|单个|产品尺寸|MEAS|meas|箱规)?[：:\s]*'
                r'(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*(?:cm|CM|mm)?',
                desc
            )
            if dim_pat:
                try:
                    l, w, h = float(dim_pat.group(1)), float(dim_pat.group(2)), float(dim_pat.group(3))
                    if 0 < l < 300 and 0 < w < 300 and 0 < h < 300:  # sanity: < 3m
                        dimensions = {
                            "length": int(l * 10),  # cm → mm
                            "width": int(w * 10),
                            "height": int(h * 10),
                        }
                except (ValueError, TypeError):
                    pass
            # 提取 weight: "净重：53g", "含包装：61.8g"
            if not weight_g:
                wt_pat = _re.search(r'(?:净重|重量|含包装|毛重)[：:\s]*(\d+\.?\d*)\s*(?:g|G|克)', desc)
                if wt_pat:
                    try:
                        weight_g = int(float(wt_pat.group(1)))
                    except (ValueError, TypeError):
                        pass

    # 物流信息
    shipping = data.get("shipping") or {}

    # 图片
    images = get_best_product_images(data.get("images", []), limit=10)

    # 属性（取前 15 个有效 kv）
    attrs: dict[str, str] = {}
    for a in data.get("attributes", [])[:15]:
        name = str(a.get("name", "")).strip()
        val = str(a.get("value", "")).strip()
        if name and val and len(name) < 30 and len(val) < 80:
            attrs[name] = val

    # 卖家
    seller_raw = data.get("seller", "") or ""
    supplier = seller_raw.split(" ")[0].split("关注")[0].strip()[:30] if seller_raw else ""

    # ── 5. 变体（类型感知 + 数量控制） ──
    variants: list[dict] = []
    option_groups_raw = data.get("option_groups", [])
    sku_details = data.get("sku_details", [])

    # 默认 SKU 上限（可通过参数覆盖）
    effective_max_skus: int = max_skus if (max_skus and max_skus > 0) else 15
    dropped_skus: int = 0
    drop_reason = ""

    # Parse all option groups with type detection
    parsed_groups: list[dict] = []  # [{name, type, values: [{name, image, price}]}]
    for og in option_groups_raw:
        og_name = str(og.get("name", "")).strip()
        if not og_name:
            continue
        og_values = [v for v in og.get("values", []) if v.get("name")]
        if not og_values:
            continue
        og_type = _detect_group_variant_type(og_values)
        parsed_groups.append({
            "name": og_name,
            "type": og_type,
            "values": og_values,
        })

    if parsed_groups:
        # Deduplicate values within each group
        for pg in parsed_groups:
            seen = set()
            deduped = []
            for v in pg["values"]:
                vname = str(v.get("name", "")).strip()
                if vname and vname not in seen:
                    seen.add(vname)
                    deduped.append(v)
            pg["values"] = deduped

        # Generate cartesian product of all option groups
        from itertools import product as _cartesian_product

        # Build list of value lists: [[v1, v2, ...], [w1, w2, ...]]
        value_lists = [pg["values"] for pg in parsed_groups]

        # Compute total SKU count
        total_combos = 1
        for vl in value_lists:
            total_combos *= len(vl)
        if total_combos == 0:
            total_combos = 1

        # Apply SKU count limit
        if total_combos > effective_max_skus:
            # Multi-dimension sampling: cap each dimension
            n_dims = len(value_lists)
            if n_dims > 1:
                # Allocate slots proportionally: aim for effective_max_skus^(1/n_dims) per dimension
                per_dim = max(1, int(effective_max_skus ** (1.0 / n_dims)))
                # Check if this keeps us under effective_max_skus
                while True:
                    sampled_count = 1
                    for vl in value_lists:
                        sampled_count *= min(len(vl), per_dim)
                    if sampled_count <= effective_max_skus:
                        break
                    per_dim = max(1, per_dim - 1)
                # Apply sampling
                for k in range(n_dims):
                    if len(value_lists[k]) > per_dim:
                        value_lists[k] = value_lists[k][:per_dim]
                new_total = 1
                for vl in value_lists:
                    new_total *= len(vl)
                dropped_skus = total_combos - new_total
                drop_reason = (
                    f"multi-dim sampling: {n_dims} dims, "
                    f"total={total_combos} > max_skus={effective_max_skus}, "
                    f"sampled to {new_total} ({per_dim} per dim)"
                )
            else:
                # Single dimension: truncate
                dropped_skus = total_combos - effective_max_skus
                value_lists[0] = value_lists[0][:effective_max_skus]
                drop_reason = (
                    f"single-dim truncation: total={total_combos} > max_skus={effective_max_skus}"
                )

        # Generate cartesian product
        variant_idx = 0
        for combo in _cartesian_product(*value_lists):
            # combo is a tuple of values (one from each option group)
            # Build combined name and attributes
            combined_names: list[str] = []
            all_attrs: dict[str, str] = {}
            variant_img = ""
            variant_price = cost_cny

            for pg, val in zip(parsed_groups, combo):
                vname = str(val.get("name", "")).strip()
                if not vname:
                    continue
                combined_names.append(vname)
                # Parse attributes from this value
                parsed = _parse_variant_attributes(vname)
                for k, v in parsed.items():
                    if v and v not in all_attrs.values():
                        all_attrs[k] = v
                # Use first available image
                if not variant_img and val.get("image"):
                    variant_img = str(val.get("image", "")).strip()

            # Match sku_details for price (search combined names)
            combined_name = " ".join(combined_names)
            for sd in sku_details:
                sd_name = str(sd.get("name", ""))
                for cn in combined_names:
                    if cn in sd_name or sd_name in cn:
                        variant_price = float(sd.get("price", cost_cny))
                        break
                if variant_price != cost_cny:
                    break

            # Determine overall variant_type
            group_types = [pg["type"] for pg in parsed_groups]
            if "quantity" in group_types:
                variant_type = "quantity"
            elif "color" in group_types and "size" in group_types:
                variant_type = "color_size"
            elif "color" in group_types and "spec" in group_types:
                variant_type = "color_spec"
            elif "color" in group_types:
                variant_type = "color"
            elif "size" in group_types:
                variant_type = "size"
            elif "spec" in group_types:
                variant_type = "spec"
            else:
                variant_type = "unknown"

            # Backward-compatible fields
            color_val = all_attrs.get("颜色", combined_names[0] if combined_names else "default")
            model_val = all_attrs.get("规格", all_attrs.get("尺寸", ""))
            size_val = all_attrs.get("尺寸", "one size")

            variants.append({
                "sku_id": f"{item_id}_{variant_idx}",
                "name": combined_name,
                "color": color_val,
                "model": model_val,
                "size": size_val,
                "image": variant_img,
                "price": variant_price,
                "original_price": variant_price,
                "stock": 100,
                "attributes": all_attrs,
                "variant_type": variant_type,
            })
            variant_idx += 1

    elif sku_details:
        # Fallback: no option_groups but have sku_details
        max_skus_applied = min(len(sku_details), effective_max_skus)
        if len(sku_details) > effective_max_skus:
            dropped_skus = len(sku_details) - effective_max_skus
            drop_reason = f"sku_details truncation: {len(sku_details)} > max_skus={effective_max_skus}"
            sku_details = sku_details[:max_skus]

        for i, sd in enumerate(sku_details):
            sd_price = float(sd.get("price", cost_cny))
            sd_name = str(sd.get("name", "default"))
            parsed = _parse_variant_attributes(sd_name)
            vt = _detect_variant_type(sd_name)
            variants.append({
                "sku_id": f"{item_id}_{i}",
                "name": sd_name,
                "color": parsed.get("颜色", sd_name),
                "model": parsed.get("规格", ""),
                "size": parsed.get("尺寸", "one size"),
                "image": sd.get("image") or "",
                "price": sd_price,
                "original_price": sd_price,
                "stock": 100,
                "attributes": parsed,
                "variant_type": vt,
            })

    if not variants:
        variants.append({
            "sku_id": str(item_id),
            "name": "default",
            "color": "default",
            "model": "",
            "size": "one size",
            "image": "",
            "price": cost_cny,
            "original_price": cost_cny,
            "stock": 100,
            "attributes": {},
            "variant_type": "single",
        })

    # ── 5.4 SKU 过滤：去除引流/定制/客服类 SKU ──
    filtered_skus_info: list[dict] = []
    if len(variants) > 1:
        variants, drop_reason, dropped_skus, removed = _filter_bait_and_custom_skus(
            variants, drop_reason, dropped_skus
        )
        if removed:
            filtered_skus_info = [
                {"name": str(r.get("name", r.get("color", "")))[:60], "price": r.get("price")}
                for r in removed
            ]
            logger.warning(
                "SKU过滤: 移除%d个引流/定制SKU (剩余%d个): %s",
                len(removed), len(variants),
                ", ".join(r["name"][:40] for r in filtered_skus_info),
            )

    # ── 5.5 校验门：硬阻断 + 软兜底 ──
    weight_g, dimensions, validation_errors = _validate_and_fix_product_data(
        item_id=str(item_id),
        title=item_title,
        cost_cny=cost_cny,
        images=images,
        weight_g=weight_g,
        dimensions=dimensions,
        variants=variants,
        option_groups=data.get("option_groups", []),
    )
    if validation_errors:
        raise ProductValidationError(
            f"产品 {item_id} 数据不完整，跳过: {'; '.join(validation_errors)}"
        )

    # ── 6. 组装 envelope (三层结构: draft / source / extensions) ──
    is_multi = len(variants) > 1

    # 提取 1688 类目面包屑（供 worker 类目匹配使用）
    source_categories = api_data.get("categories", [])
    if isinstance(source_categories, list) and source_categories:
        source_category_path = " > ".join(c["name"] for c in source_categories if c.get("name"))
        # 取最后两级（最具体的分类）
        names = [c["name"] for c in source_categories if c.get("name")]
        source_category_short = " > ".join(names[-2:]) if len(names) >= 2 else (names[0] if names else "")
    else:
        source_category_path = ""
        source_category_short = ""

    draft: dict[str, Any] = {
        "item_id": str(item_id),
        "title": item_title,
        "description": "",
        "currency": "CNY",
        "images": images,
        "attributes": attrs,
        "weight": weight_g,
        "dimensions": dimensions,
        "category": category_name or category_query,
        "purchase_url": detail_url,
        "purchase_cost": cost_cny,
        "supplier": supplier,
        "stock": 100,
    }
    if shipping:
        draft["shipping"] = shipping
    if ozon_category:
        draft["ozon_category"] = ozon_category
    if source_category_short:
        draft["source_category"] = source_category_short  # 1688 类目（最具体两级），供 worker 类目匹配

    if is_multi:
        draft["variants"] = variants
    else:
        v0 = variants[0]
        draft["sku_id"] = v0["sku_id"]
        draft["price"] = v0["price"]
        draft["original_price"] = v0["original_price"]

    envelope: dict[str, Any] = {
        "draft": draft,
        "source": {
            "purchase_url": detail_url,
            "purchase_cost": cost_cny,
            "source_category_path": source_category_path,  # 完整 1688 类目路径，供后续建立映射表
        },
        "extensions": {
            "max_skus": effective_max_skus,
            "dropped_skus": dropped_skus,
            "drop_reason": drop_reason or "",
            "filtered_skus": filtered_skus_info,
        },
    }

    # ── 7. 组装 GraphInput ──
    mxou_token = _read_pounding_config("api.key") or os.environ.get("MXOU_IMAGE_TOKEN", "") or _get_token()
    return {
        "token": mxou_token,
        "ozon_client_id": ozon_creds["client_id"],
        "ozon_api_key": ozon_creds["api_key"],
        "envelope": envelope,
    }


def _refresh_browser_session(profile: str = "default") -> None:
    """Force-refresh the CDP browser session for clean retry.

    Deletes the cached session file and kills the existing Chrome CDP
    process so the next CDP probe auto-launches a fresh browser.
    """
    try:
        from scripts.capabilities.browser_probe.service import (
            _session_file,
            _find_live_cdp_session_for_profile,
        )
        import subprocess, signal

        # 1. Find and kill the existing CDP Chrome process
        recovered = _find_live_cdp_session_for_profile(profile, {})
        cdp_url = (recovered or {}).get("cdp_url", "")
        if cdp_url:
            port = cdp_url.rsplit(":", 1)[-1] if ":" in cdp_url else "9222"
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f"tcp:{port}"],
                    capture_output=True, text=True, timeout=5,
                )
                pids = [pid for pid in result.stdout.strip().split("\n") if pid]
                for pid_str in pids:
                    try:
                        os.kill(int(pid_str), signal.SIGTERM)
                    except (ValueError, ProcessLookupError):
                        pass
                if pids:
                    logger.info("_refresh_browser_session: killed Chrome on port %s", port)
            except Exception:
                pass

        # 2. Delete stale session file
        _session_file(profile).unlink(missing_ok=True)
        logger.info("_refresh_browser_session: removed cached session for %s", profile)
    except Exception as exc:
        logger.debug("_refresh_browser_session: failed (non-fatal): %s", exc)


def build_graph_envelope_with_retry(
    *,
    item_id: str,
    detail_url: str,
    category_query: str = "",
    store_id: str = "",
    max_retries: int = 3,
    retry_delay: float = 15.0,
    max_skus: int | None = None,
) -> dict[str, Any]:
    """build_graph_envelope() with CDP retry on degradation.

    When CDP degrades (1688 anti-bot, CAPTCHA), waits with increasing
    backoff before retrying.  Does NOT kill Chrome (preserves cookies).
    1688 rate-limiting resets after 30-60s of inactivity.
    """
    import random as _random

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return build_graph_envelope(
                item_id=item_id,
                detail_url=detail_url,
                category_query=category_query,
                store_id=store_id,
                poll_category=False,
                max_skus=max_skus,
            )
        except RuntimeError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait = retry_delay * (attempt + 1) + _random.uniform(5, 15)
                logger.warning(
                    "build_graph_envelope_with_retry: attempt %d/%d for %s — "
                    "waiting %.0fs (1688 rate-limit cooldown): %s",
                    attempt + 1, max_retries, item_id, wait, exc,
                )
                time.sleep(wait)

    raise RuntimeError(
        f"CDP failed after {max_retries} retries for {item_id}: {last_error}"
    )


def _graph_envelope_to_ctx(graph_input: dict[str, Any]) -> "PipelineContext":
    """Convert a GraphInput envelope to a PipelineContext.

    Maps the external GraphInput format to the internal PipelineContext
    dataclass that run_pipeline() expects.
    """
    from scripts.lib.pipeline import PipelineContext

    env = (graph_input.get("envelope", {}) or {})
    # 兼容三层结构: 如果 envelope 有 draft key，从中提取字段
    if isinstance(env, dict) and "draft" in env:
        env = dict(env.get("draft", {}))
    dims = env.get("dimensions", {}) or {}
    cat = env.get("ozon_category", {}) or {}

    # Attributes: dict → list[dict]
    source_attrs: list[dict] = [
        {"name": str(k), "value": str(v)}
        for k, v in env.get("attributes", {}).items()
        if k and v
    ]

    # Variants: GraphInput format → PipelineContext format
    variants: list[dict] = []
    for v in env.get("variants", []) or []:
        variants.append({
            "sku_id": str(v.get("sku_id", "")),
            "name": str(v.get("color", "") or v.get("model", "")),
            "image": str(v.get("image", "") or ""),
            "price": float(v.get("price", 0)),
        })

    item_id = str(env.get("item_id", ""))
    sku_id = env.get("sku_id", "")
    if not sku_id and variants:
        sku_id = variants[0]["sku_id"]
    elif not sku_id:
        sku_id = item_id

    params: dict[str, Any] = {
        "task_id": f"task-{item_id}",
        "item_id": item_id,
        "title": str(env.get("title", "")),
        "description": str(env.get("description", "") or ""),
        "category_query": str(env.get("category", "")),
        "cost_cny": float(env.get("purchase_cost", 0)),
        "weight_g": int(env.get("weight", 0) or 500),
        "depth": int(dims.get("length", 0) or 60),
        "width": int(dims.get("width", 0) or 60),
        "height": int(dims.get("height", 0) or 80),
        "images": list(env.get("images", []) or []),
        "source_attrs": source_attrs,
        "selling_points": [],
        "variants": variants,
        "sku_id": str(sku_id),
        "ozon_client_id": str(graph_input.get("ozon_client_id", "")),
        "ozon_api_key": str(graph_input.get("ozon_api_key", "")),
        "mxou_token": str(graph_input.get("token", "")),
        "store_id": str(env.get("store_id", "") or "4718259"),
    }

    # Pre-resolved Ozon category
    if cat.get("description_category_id"):
        params["category"] = {
            "description_category_id": str(cat["description_category_id"]),
            "type_id": str(cat["type_id"]),
            "confidence": 0.9,
        }

    return PipelineContext(**params)


def publish_graph_envelope(
    graph_input: dict[str, Any],
    *,
    skip_images: bool = True,
    skip_self_learning: bool = False,
) -> dict[str, Any]:
    """Submit a GraphInput envelope to the local pipeline and upload to Ozon.

    Args:
        graph_input: The GraphInput dict (token + ozon_client_id + ozon_api_key + envelope).
        skip_images: Skip AI image generation (default True — reuse CDP images).
        skip_self_learning: Skip self-learning DB writes.

    Returns:
        {
            "ok": bool,
            "stage": str,
            "ozon_task_id": str,
            "product_id": int,
            "offer_id": str,
            "pipeline": {stages, errors, warnings, pricing, image_count},
        }
    """
    from scripts.lib.pipeline import run_pipeline

    ctx = _graph_envelope_to_ctx(graph_input)
    ctx.skip_images = skip_images

    result: dict[str, Any] = {
        "ok": False,
        "task_id": ctx.task_id,
        "stage": "init",
        "ozon_task_id": None,
        "product_id": None,
        "offer_id": None,
        "error": None,
    }

    try:
        ok = run_pipeline(ctx)
        result["ok"] = ok
        result["stage"] = ctx.stages.get("persist_status",
                        ctx.stages.get("Import",
                        ctx.stages.get("upload_to_ozon", "unknown")))
        result["ozon_task_id"] = ctx.ozon_task_id or None
        result["product_id"] = ctx.product_id or None
        result["offer_id"] = ctx.offer_id or None
        result["pipeline"] = {
            "status": result["stage"],
            "stages": ctx.stages,
            "errors": ctx.errors,
            "warnings": ctx.warnings,
            "ozon_task_id": ctx.ozon_task_id,
            "product_id": ctx.product_id,
            "pricing": ctx.pricing,
            "image_count": len(ctx.image_urls),
            "filled_attrs": len(ctx.filled_attrs),
        }
    except Exception as exc:
        logger.exception("publish_graph_envelope: pipeline failed: %s", exc)
        result["ok"] = False
        result["stage"] = "pipeline_error"
        result["error"] = str(exc)[:500]

    return result


# ── Category self-service (moved to cloud/category.py) ──
# Re-exported below for backward compatibility


# ── Error helpers ──


def _error_envelope(
    source_envelope: dict[str, Any],
    code: str,
    message: str,
    *,
    terminal: bool = True,
    retryable: bool = False,
    details: Any = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "task_id": None,
        "status": "rejected" if terminal else "failed",
        "terminal": terminal,
        "error": {"code": code, "message": message, "retryable": retryable},
    }
    if details is not None:
        env["error"]["details"] = details
    if extras:
        env.update(extras)
    return env


# ── Ozon helpers ──


def _parse_price(raw: str) -> float:
    """Parse 1688 price string like '¥38' or '¥16.50' to float."""
    if not raw:
        return 0.0
    try:
        cleaned = str(raw).replace("¥", "").replace("¥", "").replace(",", "").replace(" ", "").strip()
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def parse_ozon_url(url: str) -> dict[str, str] | None:
    """Parse Ozon product/shop URL, extract product_id or offer_id."""
    import re
    value = str(url or "").strip()
    if not value:
        return None
    # /product/xxx-name-123456789/
    m = re.search(r"/products?/(?:[^/]+-)?(\d{6,15})", value)
    if m:
        return {"product_id": m.group(1), "type": "product"}
    # /context/detail/id/123456789/
    m = re.search(r"/detail/id/(\d{6,15})", value)
    if m:
        return {"product_id": m.group(1), "type": "product"}
    # offer_id in query
    m = re.search(r"[?&]offer_id=([^&]+)", value)
    if m:
        return {"offer_id": m.group(1), "type": "offer"}
    # /seller/12345/
    m = re.search(r"/seller/(\d+)", value)
    if m:
        return {"seller_id": m.group(1), "type": "shop"}
    return None


def get_ozon_product_info(client_id: str, api_key: str, product_id: str) -> dict[str, Any] | None:
    """Get Ozon product info by product_id. Returns {name, offer_id, images, category_id, attributes, price}."""
    try:
        # Try cloud package first, then fallback to direct import
        try:
            from scripts.lib.ozon_api import list_product_infos, get_product_attributes_v4
        except ImportError:
            # Direct HTTP call as fallback
            import requests as _r
            resp = _r.post(
                "https://api-seller.ozon.ru/v3/product/info/list",
                headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
                json={"product_id": [str(product_id)]}, timeout=20
            )
            items = resp.json().get("items", [])
            if not items:
                return None
            item = items[0]
            return {
                "product_id": product_id,
                "offer_id": item.get("offer_id", ""),
                "name": item.get("name", ""),
                "images": item.get("images", []),
                "category_id": item.get("description_category_id", ""),
                "price": item.get("price", ""),
                "currency": item.get("currency_code", "RUB"),
                "barcode": item.get("barcode", ""),
                "attributes": [],
            }

        infos = list_product_infos(client_id, api_key, product_ids=[product_id])
        if not infos:
            return None
        item = infos[0]
        result = {
            "product_id": product_id,
            "offer_id": item.get("offer_id", ""),
            "name": item.get("name", ""),
            "images": item.get("images", []),
            "category_id": item.get("description_category_id", ""),
            "price": item.get("price", ""),
            "currency": item.get("currency_code", "RUB"),
            "barcode": item.get("barcode", ""),
        }
        try:
            attrs = get_product_attributes_v4(client_id, api_key, product_ids=[product_id])
            result["attributes"] = attrs.get("result", [])
        except Exception:
            result["attributes"] = []
        return result
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Worker C: refresh_product — submit to n8n refresh webhook
# ═══════════════════════════════════════════════════════════════════════════


def refresh_product(
    *,
    product_id: str,
    offer_id: str = "",
    name: str = "",
    price: str = "",
    images: list[str] | None = None,
    token: str = "",
    client_id: str = "",
    api_key: str = "",
) -> dict[str, Any]:
    """Worker C — submit refresh to n8n pipeline."""
    body: dict[str, Any] = {
        "product_id": str(product_id),
        "offer_id": offer_id or str(product_id),
        "name": name or "",
        "price": price or "",
        "images": images or [],
        "token": token or _get_token(),
        "ozon_client_id": client_id or _get_ozon_credentials().get("client_id", ""),
        "ozon_api_key": api_key or _get_ozon_credentials().get("api_key", ""),
    }
    url = f"{_get_api_base().rstrip('/')}{REFRESH_PATH}"
    resp = _cloud_post(url, body, timeout_sec=60)
    if isinstance(resp, dict) and resp.get("ok"):
        return {"ok": True, "result": resp}
    return {"ok": False, "error": resp.get("error", {}).get("message", "refresh failed") if isinstance(resp, dict) else str(resp)}


# ═══════════════════════════════════════════════════════════════════════════
# Worker E: find_supply — 1688 search in one call
# ═══════════════════════════════════════════════════════════════════════════


def find_supply(
    *,
    query: str,
    page_size: int = 5,
) -> dict[str, Any]:
    """Worker E — Search 1688 for supply sources in one call."""
    from scripts.lib.ak_1688_client import search_products

    items = search_products(query, page_size=page_size)
    if not items:
        return {"ok": False, "items": [], "count": 0, "error": "1688 搜索无结果"}

    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "hint": f"找到 {len(items)} 个商品，选择一个进入 Worker A 上架流程",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Reusable flow steps (all call cloud pipeline — thin local wrapper)
# ═══════════════════════════════════════════════════════════════════════════

# These are the composable steps that both new-product and follow-sell flows use.
# Each step = POST to a cloud webhook → get result.
#
# Local-only steps (1688 matching via browser/CDP):
#   - search_1688(query) → product list
#   - get_1688_detail(url) → title/description/images/attributes
#   - match_supply(source_1688, target_ozon) → price calc
#
# Cloud steps (API calls, image gen, upload):
#   - follow_sell_cloud(sku, ...) → product_id | fallback
#   - submit_task(envelope) → task_id
#   - poll_task(task_id) → status
#   - analyze_ozon_product(url) → structured Ozon product data
#   - build_variant_envelope(...) → envelope with multi-SKU variants


# ═══════════════════════════════════════════════════════════════════════════
# 多SKU变体合并
# ═══════════════════════════════════════════════════════════════════════════


def build_variant_envelope(
    *,
    project_id: str,
    subproject_id: str,
    family_title: str,
    family_description: str = "",
    source_category_ids: list[str] | None = None,
    variants: list[dict[str, Any]] | None = None,
    common_attributes: dict[str, Any] | None = None,
    common_images: list[str] | None = None,
    store_id: str = "",
    cost_price: float | None = None,
    resolved_attributes: list[dict[str, Any]] | None = None,
    resolved_category: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an envelope for a multi-variant (merged) product card.

    On Ozon, variants share the same product card with a merge group identifier.
    Each variant has its own offer_id, price, images, and differentiating attributes
    (e.g., color, size). The cloud pipeline resolves the category, generates images,
    and uploads all variants together.

    Args:
        family_title: Master product name (e.g., "Ridberg Discover Suitcase")
        family_description: Shared product description
        source_category_ids: 1688 category IDs for mapping
        variants: List of variant specs:
            [{"sku_id": "red-m", "sku_title": "Red M", "price": "280",
            "attributes": {"color": "red", "size": "M"}, "images": [...]}, ...]
        common_attributes: Attributes shared across all variants (e.g., brand, material)
        common_images: Images shared across variants (fallback if variant has none)

    Returns: envelope dict ready for submit_envelope()
    """
    merged_attributes = dict(common_attributes or {})
    merged_attributes.setdefault("merge_group", family_title)

    variant_list = list(variants or [])
    # Ensure each variant has a merge key
    for i, v in enumerate(variant_list):
        if "variant_key" not in v:
            v["variant_key"] = v.get("sku_id", f"var-{i+1}")
        if "sku_title" not in v:
            v["sku_title"] = v.get("sku_id", f"Variant {i+1}")

    source: dict[str, Any] = {
        "source_item_id": f"variant-family-{subproject_id}",
        "source_url": "",
    }

    draft: dict[str, Any] = {
        "title": family_title,
        "description": family_description,
        "attributes": merged_attributes,
        "variants": variant_list,
    }
    if cost_price is not None:
        draft["cost_price"] = cost_price
        draft["cost_cny"] = cost_price  # Pipeline Pricing node looks for this
    elif draft.get("price"):
        draft["cost_cny"] = float(str(draft.get("price", 0)).replace("¥", "")) or 0
    if source_category_ids:
        draft["source_category_ids"] = list(source_category_ids)

    assets: dict[str, Any] = {}
    all_images = list(common_images or [])
    for v in variant_list:
        variant_images = v.get("images") or v.get("source_images") or []
        all_images.extend(variant_images)
    if all_images:
        assets["image_urls"] = list(dict.fromkeys(all_images))  # dedup, keep order

    envelope = build_envelope(
        project_id=project_id,
        subproject_id=subproject_id,
        source=source,
        assets=assets,
        draft=draft,
        store_id=store_id,
    )
    resolved = envelope.setdefault("resolved", {})
    if resolved_attributes:
        resolved["attributes"] = resolved_attributes
    if resolved_category:
        resolved["category"] = resolved_category
    return envelope


def publish_variant_product(
    *, family_title: str, variants: list[dict[str, Any]]
) -> dict[str, Any]:
    """Worker D — submit variant products via n8n pipeline."""
    envelope = build_variant_envelope(
        project_id='default',
        subproject_id=family_title[:20],
        family_title=family_title, variants=variants)
    tid = f'variant-{family_title[:20]}'
    envelope['task_id'] = tid
    submit_envelope(envelope, task_id=tid)  # category resolution
    result = submit_task(envelope, task_id=tid)
    return {"ok": result.get("status") not in ('rejected', 'failed'), "result": result}
# Re-exported below for backward compatibility


# ═══════════════════════════════════════════════════════════════════════════
# Attribute resolution — moved to cloud/attributes.py
# Re-exported below for backward compatibility
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# Ozon选品 → 1688找货源
# ═══════════════════════════════════════════════════════════════════════════


def analyze_ozon_product(
    url_or_id: str,
    *,
    ozon_client_id: str = "",
    ozon_api_key: str = "",
) -> dict[str, Any]:
    """Parse an Ozon product URL and fetch full product details.

    Returns structured data for the local skill to:
    1. Search 1688 for matching supply (by image/keyword)
    2. Compare prices
    3. Ask user: follow-sell (跟卖) or create new (新建)?

    Returns:
    - product_id, offer_id, sku (from URL parsing and product info)
    - name, price, currency
    - images (Ozon primary images for 1688 image search)
    - category_id, type_id
    - attributes (for supply matching)
    - suggested_action: "follow_sell" | "new_product" | "ask_user"
    - supply_search_hints: keywords for 1688 search
    """
    cid = ozon_client_id or _get_ozon_credentials().get("client_id", "")
    akey = ozon_api_key or _get_ozon_credentials().get("api_key", "")

    # Step 1: Parse URL
    parsed = parse_ozon_url(url_or_id)
    product_id = ""
    if parsed and parsed.get("product_id"):
        product_id = parsed["product_id"]
        product_type = parsed["type"]
    elif str(url_or_id).strip().isdigit():
        product_id = str(url_or_id).strip()
        product_type = "product_id"
    else:
        return {"ok": False, "error": f"无法从URL提取product_id: {url_or_id[:80]}"}

    # Step 2: Extract name from URL slug
    url_slug_name = ""
    # Pattern: /product/slug-name-123456789/ → extract "slug-name"
    slug_match = re.search(r"/([^/]+)-(\d{6,15})/?$", url_or_id)
    if slug_match:
        url_slug_name = slug_match.group(1).replace("-", " ").strip()

    # Step 3: Try to get richer product info from Ozon API
    images: list[str] = []
    attributes: list[dict[str, Any]] = []
    ozon_name = ""
    ozon_price = ""
    ozon_currency = ""
    category_id = ""
    type_id = ""
    offer_id = ""

    if cid and akey:
        info = get_ozon_product_info(cid, akey, product_id=product_id)
        if info:
            ozon_name = info.get("name", "")
            images = info.get("images", [])
            ozon_price = info.get("price", "")
            ozon_currency = info.get("currency", "CNY")
            category_id = info.get("category_id", "")
            offer_id = info.get("offer_id", "")
            attributes = info.get("attributes", [])

    # Step 4: Build supply search hints from name
    display_name = ozon_name or url_slug_name or f"Ozon Product {product_id}"
    search_hints = [display_name]
    # Add shorter search variations
    words = display_name.split()
    if len(words) > 3:
        search_hints.append(" ".join(words[:3]))  # First 3 words
        search_hints.append(" ".join(words[-3:]))  # Last 3 words

    return {
        "ok": True,
        "product_id": product_id,
        "product_type": product_type,
        "url_slug_name": url_slug_name,
        "name": display_name,
        "offer_id": offer_id,
        "price": ozon_price,
        "currency": ozon_currency or "CNY",
        "category_id": category_id,
        "type_id": type_id,
        "images": images,
        "image_count": len(images),
        "primary_image": images[0] if images else "",
        "attributes": attributes,
        "has_full_details": bool(ozon_name),
        "supply_search_hints": search_hints,
        "suggested_action": "ask_user",
        "needs_image_collection": not images,
        "next_steps": [
            f"1. 用浏览器打开 Ozon 商品页 → 收集主图和详情图（JS渲染，需CDP）" if not images else "1. 已有 {len(images)} 张参考图",
            f"2. 用关键词 '{search_hints[0][:60]}' 在1688搜同款 → 找最优货源",
            "3. 比较价格: Ozon售价 vs 1688成本+运费+利润",
            "4. 询问用户: 跟卖复制 or 新建商品?",
            "5. 跟卖 → follow_sell_cloud(sku=product_id)",
            "6. 制图以收集到的Ozon图+1688图为参考",
            "7. 新建 → submit_envelope(envelope) → 属性解析 → submit_task(envelope)",
        ],
    }
#   - generate_images(draft, mxou_token) → asset_manifest


# ═══════════════════════════════════════════════════════════════════════════
# Unified publish — one call for the entire Worker A flow
# ═══════════════════════════════════════════════════════════════════════════


def verify_product_quality(offer_id: str, client_id: str = "", api_key: str = "") -> dict[str, Any]:
    """Post-publish quality check. Returns {pass: bool, checks: {...}, issues: [...]}."""
    from scripts.lib.ozon_api import list_product_infos, get_product_attributes_v4

    cid = client_id or _get_ozon_credentials().get("client_id", "")
    akey = api_key or _get_ozon_credentials().get("api_key", "")

    if not cid or not akey:
        return {"pass": False, "error": "no_ozon_credentials"}

    try:
        items = list_product_infos(cid, akey, offer_ids=[offer_id])
        if not items:
            return {"pass": False, "error": "product_not_found"}
        p = items[0]
        pid = str(p.get("id", ""))
        price = float(p.get("price") or 0)
        dc = p.get("description_category_id", 0)

        attrs_resp = get_product_attributes_v4(cid, akey, product_ids=[pid])
        attr_list = []
        for item in attrs_resp.get("result", []):
            attr_list = item.get("attributes", [])

        attr_ids = {a.get("id") for a in attr_list}
        checks = {
            "price_ok": price not in (100, 1300, 0),
            "currency_cny": p.get("currency_code", "") == "CNY",
            "has_primary": bool(p.get("primary_image", "")),
            "has_images": len(p.get("images") or []) >= 2,
            "has_brand": 85 in attr_ids,
            "has_country": 4389 in attr_ids,
            "has_model": 9048 in attr_ids,
            "has_description": 4191 in attr_ids,
            "attrs_count": len(attr_list) >= 10,
            "category_ok": dc != 17027904,
        }
        issues = [k for k, v in checks.items() if not v]
        return {"pass": len(issues) == 0, "checks": checks, "issues": issues, "price": price, "attrs": len(attr_list)}
    except Exception as e:
        return {"pass": False, "error": str(e)[:100]}


def publish_product_new(
    *,
    item_id: str,
    detail_url: str,
    title: str = "",
    category_query: str = "",
    price: str = "",
    description: str = "",
    poll: bool = True,
    poll_interval_sec: float = 30.0,
    max_poll_sec: float = 900.0,
    store_id: str = "",
    auto_confirm: bool = False,  # Skip category confirmation (for batch/agent use)
    resolved_category: dict[str, Any] | None = None,  # Pre-resolved Ozon category {description_category_id, type_id}
    skip_self_learning: bool = False,  # Skip webhook self-learning lookup (when tables are empty/cleaned)
    skip_images: bool = False,  # Reuse existing COS image URLs (don't call mxou image gen)
    reuse_images: list[str] | None = None,  # Existing image URLs to populate ctx.image_urls
) -> dict[str, Any]:
    """1688 → Ozon. Client collects raw data; pipeline handles all Ozon work.

    If ``resolved_category`` is provided, the internal category resolution is
    skipped entirely and this category is injected directly into the envelope.
    This is useful when the caller has already validated the category via Ozon
    API (e.g. batch scripts, strict mode).
    """
    from scripts.lib.ak_1688_client import get_product_details, enrich_product_with_cdp

    result: dict[str, Any] = {
        'ok': False, 'task_id': f'task-{item_id}', 'stage': 'init',
        'category': None, 'enriched': {}, 'ozon_task_id': None, 'error': None, 'user_action': None,
    }

    # 1. 1688 API
    task_id = result['task_id']
    _log_task(task_id, 'ak1688', 'details', 'info', f'Fetching product details for {item_id}')
    try:
        details = get_product_details([str(item_id)])
        api_data = details.get(str(item_id), {})
    except Exception as e:
        logger.warning('1688 API failed (%s), degrading to CDP-only', e)
        api_data = {}
    if not api_data:
        api_data = {'title': '', 'price': '', 'images': []}

    # 2. CDP browser enrichment
    _log_task(task_id, 'cdp', 'probe', 'info', f'CDP probing {detail_url[:60]}')
    enriched = enrich_product_with_cdp(detail_url=detail_url, api_data=api_data)
    result['enriched'] = enriched.get('data', {})
    result['cdp_source'] = enriched.get('source', 'api_only')
    result['cdp_degraded'] = enriched.get('degraded', True)
    _log_task(task_id, 'cdp', 'probe',
            'warn' if enriched.get('degraded') else 'info',
            f'CDP source={enriched.get("source", "?")} degraded={enriched.get("degraded")}',
            {'images': len(enriched.get('data', {}).get('images', []))})
    if enriched.get('user_action'):
        result['user_action'] = enriched['user_action']

    # Check for 1688 CAPTCHA block — title becomes "验证码拦截" when anti-bot triggers
    title_check = (enriched.get('data', {}) or {}).get('title', '') or ''
    if title_check and '验证码拦截' in str(title_check):
        result['ok'] = False
        result['error'] = '1688 反爬拦截：API 被限制，请稍后重试或更换网络环境。'
        result['cdp_degraded'] = True
        _log_task(task_id, 'ak1688', 'details', 'error', 'CAPTCHA block: 验证码拦截')
        return result

    # 3. Resolve Ozon category — use pre-resolved if provided, otherwise search
    category_candidates = []
    ozon_creds = _get_ozon_credentials()

    if resolved_category:
        # Pre-resolved category injected by caller (e.g. batch script with Ozon API validation)
        _cat = resolved_category
        resolved_category = {
            "description_category_id": str(_cat["description_category_id"]),
            "type_id": str(_cat["type_id"]),
            "confidence": float(_cat.get("confidence", 0.9)),
        }
        category_candidates = [
            {"dc": str(_cat["description_category_id"]), "type": str(_cat["type_id"]),
            "score": 0, "name": _cat.get("type_name", ""), "category": _cat.get("category_name", "")}
        ]
        _log_task(task_id, "ozon", "category", "info",
                f"Using pre-resolved category: dc={_cat['description_category_id']} type={_cat['type_id']}")
    else:
        search_text = category_query or title or ""
        if search_text:
            # Step 3a: Check pipeline Supabase via webhook (self-learning)
            if not skip_self_learning:
                lookup = lookup_category_webhook(search_text)
                if lookup.get("found") and lookup.get("mappings"):
                    mappings = lookup["mappings"]
                    best = mappings[0]
                    resolved_category = {
                        "description_category_id": str(best["description_category_id"]),
                        "type_id": str(best["type_id"]),
                        "confidence": float(best.get("confidence", 0.9)),
                    }
                    category_candidates = [
                        {"dc": str(m["description_category_id"]), "type": str(m["type_id"]),
                        "score": 0, "name": m.get("title", ""), "category": ""}
                        for m in mappings
                    ]
                    logger.info("Category from webhook self-learning: dc=%s type=%s (keyword=%s)",
                            best["description_category_id"], best["type_id"], search_text)
                    _log_task(task_id, "ozon", "category", "info",
                            f"Self-learning hit: dc={best['description_category_id']} type={best['type_id']}",
                            {"source": "webhook", "mappings": len(mappings)})

        # Step 3b: If no self-learning hit, search Ozon API locally
        if not category_candidates:
            try:
                from scripts.lib.ozon_api import search_categories_validated as _search_validated
                # Ozon tree API supports ZH_HANS, EN, RU natively — pick the right language
                if any('一' <= c <= '鿿' for c in (search_text or '')):
                    _lang = 'ZH_HANS'
                elif any(c.isascii() and c.isalpha() for c in (search_text or '')) and not any(ord(c) > 127 for c in (search_text or '')):
                    _lang = 'EN'
                else:
                    _lang = 'RU'
                logger.info('Category search: "%s" (lang=%s)', search_text[:60], _lang)
                cats = _search_validated(ozon_creds['client_id'], ozon_creds['api_key'], search_text, language=_lang, max_results=5, validate_count=5, task_id=task_id)
                # ZH_HANS tree maps e.g. "手持风扇" to "纪念品和礼品/手持风扇"
                # (traditional folding hand fan).  USB-powered mini fans belong in
                # Климатическая техника.  If the best ZH_HANS hit is a souvenir/gift
                # category, discard it and fall through to RU translation.
                if cats and _lang == 'ZH_HANS':
                    _top_cat = (cats[0].get('category_name', '') + ' ' + cats[0].get('type_name', '')).lower()
                    _souvenir_keywords = ['纪念品', '礼品', 'сувенир', 'подар', 'souvenir', 'gift']
                    if any(kw in _top_cat for kw in _souvenir_keywords):
                        logger.info('ZH_HANS result is souvenir/gift category, falling back to RU: "%s"', search_text[:60])
                        cats = []  # force fallback
                if not cats and len(search_text.split()) > 2:
                    short = ' '.join(search_text.split()[:2])
                    if short and short != search_text:
                        cats = _search_validated(ozon_creds['client_id'], ozon_creds['api_key'], short, language=_lang, max_results=5, validate_count=5, task_id=task_id)
                # Fallback chain: ZH_HANS → EN → RU
                if not cats and _lang == 'ZH_HANS':
                    logger.info('Fallback EN search: "%s"', search_text[:60])
                    cats = _search_validated(ozon_creds['client_id'], ozon_creds['api_key'], search_text, language='EN', max_results=5, validate_count=5, task_id=task_id)
                    if not cats and len(search_text.split()) > 2:
                        short = ' '.join(search_text.split()[:2])
                        if short != search_text:
                            cats = _search_validated(ozon_creds['client_id'], ozon_creds['api_key'], short, language='EN', max_results=5, validate_count=5, task_id=task_id)
                if not cats and _lang != 'RU':
                    try:
                        import requests as _requests
                        _token = _get_token()
                        _resp = _requests.post(
                            'https://api.mxou.cn/v1/chat/completions',
                            json={'model': 'deepseek-v4-flash', 'messages': [{'role': 'user', 'content': f'Translate this product keyword to 2-3 Russian words only (no explanation): {search_text}'}]},
                            headers={'Authorization': f'Bearer {_token}', 'Content-Type': 'application/json'},
                            timeout=10,
                        )
                        _ru = (_resp.json().get('choices', [{}])[0].get('message', {}).get('content', '') or '').strip()
                        if _ru and len(_ru) < 100:
                            _ru_clean = _ru.replace('-', ' ')  # Ozon tree API doesn't match hyphenated words
                            logger.info('Fallback RU search: "%s" → "%s"', search_text[:60], _ru_clean)
                            cats = _search_validated(ozon_creds['client_id'], ozon_creds['api_key'], _ru_clean, language='RU', max_results=5, validate_count=5, task_id=task_id)
                    except Exception:
                        pass
                if cats:
                    best = cats[0]
                    score = best.get('score', 999)
                    resolved_category = {
                        'description_category_id': str(best['description_category_id']),
                        'type_id': str(best['type_id']),
                        'confidence': 0.9,  # validated — high confidence
                    }
                    category_candidates = [
                        {'dc': str(c['description_category_id']), 'type': str(c['type_id']),
                        'score': c['score'], 'name': c['type_name'], 'category': c['category_name']}
                        for c in cats
                    ]
                    logger.info('Resolved category (validated): %s / %s (score=%.1f, dc=%s, type=%s)',
                            best.get('category_name'), best.get('type_name'), score,
                            best['description_category_id'], best['type_id'])
                else:
                    logger.warning('No validated category found for query: %s', search_text)
            except Exception as e:
                logger.warning('Category resolution failed: %s', e)

    if not category_candidates:
        # No validated category from client-side Ozon API search.
        # Submit anyway — pipeline Category node has self-learning
        # (Steps 2-4b: Supabase lookup, fuzzy match, ozon_attribute_mappings)
        # that will resolve the category and record it for future requests.
        logger.info('No validated category from Ozon API — delegating to pipeline self-learning')
        _log_task(task_id, 'ozon', 'category', 'info',
                f'Category deferred to pipeline self-learning for "{category_query or title}"')

    result['category_candidates'] = category_candidates
    result['category'] = resolved_category  # may be empty — pipeline handles this

    _log_task(task_id, 'ozon', 'category', 'info',
            f'Category resolved: {resolved_category.get("description_category_id", "none") if resolved_category else "none"} conf={resolved_category.get("confidence", 0) if resolved_category else 0}',
            {'candidates': len(category_candidates)})

    # 4. Price estimate
    cost_cny = _parse_price(result['enriched'].get('price', ''))
    weight_g = result['enriched'].get('weight_grams') or 500
    est_shipping = 6.0 if weight_g <= 500 else (8.0 if weight_g <= 1000 else 15.0)
    est_retail = math.ceil((cost_cny + est_shipping + 2.0) * 1.44375)
    result['price_estimate'] = {
        'cost_cny': cost_cny,
        'est_shipping': est_shipping,
        'est_retail': est_retail,
    }

    # 5. Run local pipeline (DAG-based, replaces n8n cloud workflow)
    if poll:
        _log_task(task_id, 'pipeline', 'start', 'info', 'Starting local Python pipeline')
        try:
            from scripts.lib.pipeline import PipelineContext, run_pipeline

            src_data = result['enriched']
            pkg = (src_data.get('packaging_rows') or [{}])[0]

            # ── Read store config (shipping + pricing) ──
            shipping_provider = "RETS"
            shipping_service = "Standard"
            margin_rate, commission_rate = 0.25, 0.10
            fx_buffer, packaging_cost_cny = 0.05, 2.0
            if store_id:
                try:
                    from scripts.lib.config_store import get_store_profile
                    store_cfg = get_store_profile(str(store_id))
                    if store_cfg.get("shipping_provider"):
                        shipping_provider = store_cfg["shipping_provider"]
                    if store_cfg.get("shipping_service"):
                        shipping_service = store_cfg["shipping_service"]
                    margin_rate = float(store_cfg.get("margin_rate", 0.25))
                    commission_rate = float(store_cfg.get("commission_rate", 0.10))
                    fx_buffer = float(store_cfg.get("fx_buffer", 0.05))
                    packaging_cost_cny = float(store_cfg.get("packaging_cost_cny", 2.0))
                except Exception:
                    pass

            # ── Extract SKU variants from CDP runtimeSkuData ──
            variants: list[dict] = []
            rts = src_data.get("runtimeSkuData") or {}
            for s in (rts.get("sku") or []):
                if s.get("name"):
                    variants.append({
                        "sku_id": str(s.get("skuId", "")),
                        "name": s["name"],
                        "image": s.get("image") or "",
                        "price": float(s.get("price", 0)),
                    })
            sku_id = variants[0]["sku_id"] if variants else str(item_id)

            # ── Extract 1688 category_id for HS code lookup ──
            _1688_cat_id = str(src_data.get("category_id", "") or "")
            if not _1688_cat_id:
                rts = src_data.get("runtimeSkuData") or {}
                _1688_cat_id = str(rts.get("cateId", "") or "")

            # ── Populate image_urls from reuse_images (fix-wrong-products flow) ──
            _reuse_urls: dict[str, str] = {}
            if skip_images and reuse_images:
                IMG_SLOTS = [
                    "main_image", "multi_info", "detail", "social_proof",
                    "scene_1", "scene_2", "scene_3", "comparison",
                    "multi_angle", "white_bg",
                ]
                for i, url in enumerate(reuse_images):
                    if i < len(IMG_SLOTS):
                        _reuse_urls[IMG_SLOTS[i]] = url
                    else:
                        _reuse_urls[f"extra_{i}"] = url
                logger.info("Reusing %d existing images, mapped to %d slots",
                        len(reuse_images), len(_reuse_urls))

            ctx = PipelineContext(
                task_id=result['task_id'],
                item_id=str(item_id),
                title=title or src_data.get('title', ''),
                description=description or '',
                category_query=category_query or '',
                cost_cny=_parse_price(src_data.get('price', '')),
                weight_g=src_data.get('weight_grams') or 500,
                depth=pkg.get('depth', 0) or 60,
                width=pkg.get('width', 0) or 60,
                height=pkg.get('height', 0) or 80,
                images=get_best_product_images(src_data.get('images', []), limit=10),
                source_attrs=src_data.get('attributes', []),
                selling_points=src_data.get('selling_points', []),
                variants=variants,
                sku_id=sku_id,
                ozon_client_id=ozon_creds['client_id'],
                ozon_api_key=ozon_creds['api_key'],
                mxou_token=_read_pounding_config("api.key") or os.environ.get("MXOU_IMAGE_TOKEN", "") or _get_token(),
                store_id=store_id or '',
                shipping_provider=shipping_provider,
                shipping_service=shipping_service,
                margin_rate=margin_rate,
                commission_rate=commission_rate,
                fx_buffer=fx_buffer,
                packaging_cost_cny=packaging_cost_cny,
                category=resolved_category,  # Pre-resolved category (may be None)
                _1688_category_id=_1688_cat_id,
                skip_images=skip_images,
                image_urls=_reuse_urls,
            )

            # Run full pipeline
            ok = run_pipeline(ctx)
            result['ok'] = ok
            result['stage'] = ctx.stages.get('Status', ctx.stages.get('Import', 'unknown'))
            result['ozon_task_id'] = ctx.ozon_task_id or None
            result['product_id'] = ctx.product_id or None
            result['offer_id'] = ctx.offer_id or None
            result['pipeline'] = {
                'status': result['stage'],
                'stages': ctx.stages,
                'errors': ctx.errors,
                'warnings': ctx.warnings,
                'ozon_task_id': ctx.ozon_task_id,
                'product_id': ctx.product_id,
                'pricing': ctx.pricing,
                'image_count': len(ctx.image_urls),
            }

            if ok:
                logger.info("Pipeline succeeded: task_id=%s ozon_task_id=%s product_id=%s",
                        result['task_id'], ctx.ozon_task_id, ctx.product_id)
            else:
                logger.warning("Pipeline completed with issues: task_id=%s errors=%d warnings=%d",
                            result['task_id'], len(ctx.errors), len(ctx.warnings))

            _log_task(task_id, 'pipeline', 'done', 'info' if ok else 'warn',
                    f'Pipeline {result["stage"]} (errors={len(ctx.errors)} warnings={len(ctx.warnings)})',
                    {'stages': ctx.stages, 'image_count': len(ctx.image_urls)})
        except Exception as e:
            logger.exception('Pipeline failed: %s', e)
            result['ok'] = False
            result['stage'] = 'pipeline_error'
            result['error'] = str(e)[:200]
            _log_task(task_id, 'pipeline', 'error', 'error', f'Pipeline exception: {e}')
    else:
        result['note'] = 'poll=False — pipeline not run. Call with poll=True to run local pipeline.'
        result['ok'] = True
        result['stage'] = 'skipped'

    logger.info("Published: task_id=%s stage=%s", result['task_id'], result['stage'])
    return result

def _verify_product_health(
    offer_id: str,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Post-publish health check: query Ozon for product state + errors,
    auto-fix fixable issues, and return final status.

    Returns: {ok, status, fixed, unfixable, final_status}
    """
    import time as _time

    ozon_creds = _get_ozon_credentials()
    cid = ozon_creds["client_id"]
    akey = ozon_creds["api_key"]

    from scripts.lib.ozon_api import list_product_infos, get_product_attributes_v4, _post

    for attempt in range(max_retries + 1):
        _time.sleep(5)  # Wait for Ozon indexing

        # 1. Get product status + errors
        try:
            products = list_product_infos(cid, akey, offer_ids=[str(offer_id)])
        except Exception as e:
            logger.warning("Health check: Ozon query failed (attempt %d): %s", attempt + 1, e)
            if attempt < max_retries:
                continue
            return {"ok": False, "status": "error", "error": str(e), "fixed": [], "unfixable": []}

        if not products:
            if attempt < max_retries:
                continue
            return {"ok": False, "status": "not_found", "fixed": [], "unfixable": []}

        product = products[0]
        errors = product.get("errors", []) or []
        statuses = product.get("statuses", {}) or {}
        status_name = statuses.get("status_name", "?")
        pid = str(product.get("id", "?"))

        # 2b. If healthy, return success
        if not errors and status_name in ("Готов к продаже", "Продается"):
            return {"ok": True, "status": "healthy", "fixed": [], "unfixable": [], "final_status": status_name,
                    "price": int(float(product.get("price") or 0))}


        # 3. Classify errors and fix
        fixed_this_round = []
        unfixable = []

        for err in errors:
            code = err.get("code", "")
            attr_id = err.get("attribute_id", 0)

            try:
                if "INCORRECT_DIMENSION" in code or "density" in code.lower():
                    _fix_dimensions(offer_id, product)
                    fixed_this_round.append("dimensions")

                elif "CONDITIONAL_ATTRIBUTE" in code and attr_id:
                    _fix_attribute_value(offer_id, attr_id, product)
                    fixed_this_round.append(f"attr_{attr_id}")

                elif ("error_attribute_values" in code or "warning_attribute_values" in code) and attr_id:
                    _fix_attribute_value(offer_id, attr_id, product)
                    fixed_this_round.append(f"attr_{attr_id}")

                elif "DESCRIPTION_DECLINE" in code:
                    _fix_description_and_tags(offer_id, product)
                    fixed_this_round.append("description")

                elif "BR_hashtag" in code:
                    if attr_id and attr_id not in (4191, 11254):
                        # Attribute-level hashtag: strip # from the specific attribute value
                        _fix_attribute_value(offer_id, attr_id, product)
                        fixed_this_round.append(f"attr_{attr_id}_hashtag")
                    else:
                        _fix_description_and_tags(offer_id, product)
                        fixed_this_round.append("description")

                elif "EMPTY_REQUIRED" in code and attr_id:
                    _fix_attribute_value(offer_id, attr_id, product)
                    fixed_this_round.append(f"attr_{attr_id}")

                elif "VALUE_MIN_LIMIT" in code and attr_id:
                    _fix_attribute_value(offer_id, attr_id, product)
                    fixed_this_round.append(f"attr_{attr_id}")

                elif "error_card_with_deleted_photos" in code or "image_absent" in code:
                    unfixable.append(code)  # Needs re-publish with images

                elif "ML_INCORRECT_VOLUME_WEIGHT" in code:
                    _fix_dimensions(offer_id, product)
                    fixed_this_round.append("volume_weight")

                else:
                    # Unknown error — try generic attribute fix
                    if attr_id:
                        try:
                            _fix_attribute_value(offer_id, attr_id, product)
                            fixed_this_round.append(f"attr_{attr_id}")
                        except Exception:
                            unfixable.append(code)
                    else:
                        unfixable.append(code)

            except Exception as e:
                logger.warning("Health check: fix %s failed: %s", code, e)
                unfixable.append(code)

        # 4. If fixes applied, retry
        if fixed_this_round and attempt < max_retries:
            _time.sleep(10)  # Wait for Ozon to process
            continue

        # 5. Return final result
        return {
            "ok": len(unfixable) == 0,
            "status": "repaired" if fixed_this_round else status_name,
            "fixed": fixed_this_round,
            "unfixable": unfixable,
            "final_status": status_name,
            "initial_errors": [{"code": e["code"], "attr": e.get("attribute_id")} for e in errors],
            "price": int(float(product.get("price") or 0)),
        }

    return {"ok": False, "status": "retry_exhausted", "fixed": [], "unfixable": ["retry_exhausted"]}


def _fix_price_to_target(offer_id: str, product: dict[str, Any], target_price: int = 25) -> None:
    """Fix product price to a target value.

    Uses /v1/product/import/prices for fast price update (no re-import needed).
    Also updates old_price to show a "discount" (target × 130%).
    """
    ozon_creds = _get_ozon_credentials()
    from scripts.lib.ozon_api import _post

    old_price = math.ceil(target_price * 1.30)
    body = {
        "prices": [{
            "offer_id": str(offer_id),
            "price": str(target_price),
            "old_price": str(old_price),
            "currency_code": "CNY",
        }]
    }
    _post(ozon_creds["client_id"], ozon_creds["api_key"],
        "/v1/product/import/prices", body)
    logger.info("Price fixed: %s → ¥%d (old: ¥%d)", offer_id, target_price, old_price)


def _fix_dimensions(offer_id: str, product: dict[str, Any]) -> None:
    """Fix INCORRECT_DIMENSION: recalculate dimensions from volume_weight with density ~0.5."""
    vw = float(product.get("volume_weight") or product.get("weight") or 0.5)
    weight = max(50, int(vw * 1000) if vw < 10 else int(vw))
    side_mm = max(30, min(int((weight / 0.5) ** (1 / 3) * 10), 500))

    ozon_creds = _get_ozon_credentials()
    from scripts.lib.ozon_api import _post

    body = {
        "items": [{
            "offer_id": str(offer_id),
            "name": str(product.get("name", ""))[:200],
            "price": str(product.get("price", "100")),
            "currency_code": product.get("currency_code", "CNY"),
            "description_category_id": int(product.get("description_category_id", 0)),
            "type_id": int(product.get("type_id", 0)),
            "depth": side_mm, "width": side_mm, "height": side_mm,
            "dimension_unit": "mm",
            "weight": weight, "weight_unit": "g",
        }]
    }
    _post(ozon_creds["client_id"], ozon_creds["api_key"], "/v3/product/import", body)


def _fix_attribute_value(offer_id: str, attr_id: int, product: dict[str, Any]) -> None:
    """Fix an attribute: query Ozon dictionary for first valid value and PATCH."""
    dc = product.get("description_category_id", 0)
    tp = product.get("type_id", 0)
    if not dc or not tp or not attr_id:
        return

    ozon_creds = _get_ozon_credentials()
    from scripts.lib.ozon_api import _post

    # Get first valid dictionary value
    vals = _post(
        ozon_creds["client_id"], ozon_creds["api_key"],
        "/v1/description-category/attribute/values",
        {"attribute_id": int(attr_id), "description_category_id": int(dc),
        "type_id": int(tp), "limit": 3},
        timeout=15,
    )
    valid = (vals.get("result", []) or [])
    if valid:
        v = valid[0]
        attrs = [{"id": int(attr_id), "complex_id": 0,
                "values": [{"dictionary_value_id": v["id"], "value": v["value"]}]}]
    else:
        attrs = [{"id": int(attr_id), "complex_id": 0, "values": [{"value": "0"}]}]

    _post(ozon_creds["client_id"], ozon_creds["api_key"],
        "/v1/product/attributes/update",
        {"product_id": int(product.get("id", 0)), "attributes": attrs})


def _fix_description_and_tags(offer_id: str, product: dict[str, Any]) -> None:
    """Fix DESCRIPTION_DECLINE / BR_hashtag: clean description and resubmit."""
    ozon_creds = _get_ozon_credentials()
    from scripts.lib.ozon_api import _post, get_product_attributes_v4

    pid = str(product.get("id", ""))
    name = str(product.get("name", ""))
    desc = name + ". Прочный, надежный, подходит для ежедневного использования."

    attrs_resp = get_product_attributes_v4(ozon_creds["client_id"], ozon_creds["api_key"],
                                            product_ids=[pid])
    existing = []
    for item in attrs_resp.get("result", []):
        existing = item.get("attributes", [])

    # Clean hashtags and rebuild
    cleaned_attrs = [
        {"id": 85, "complex_id": 0, "values": [{"value": "Нет бренда"}]},
        {"id": 4389, "complex_id": 0, "values": [{"dictionary_value_id": 90296, "value": "Китай"}]},
        {"id": 9048, "complex_id": 0, "values": [{"value": name[:50]}]},
        {"id": 4191, "complex_id": 0, "values": [{"value": desc.replace("#", "").strip()[:1000]}]},
    ]
    seen = {85, 4389, 9048, 4191}
    for a in existing:
        aid = a.get("id", 0)
        if aid not in seen:
            vals = a.get("values", [])
            for v in vals:
                if v.get("value"):
                    v["value"] = str(v["value"]).replace("#", "").strip()
            seen.add(aid)
            cleaned_attrs.append(a)

    body = {
        "items": [{
            "offer_id": str(offer_id),
            "name": name[:200],
            "price": str(product.get("price", "100")),
            "currency_code": "CNY",
            "description_category_id": product.get("description_category_id", 0),
            "type_id": product.get("type_id", 0),
            "depth": 60, "width": 60, "height": 80,
            "dimension_unit": "mm", "weight": 200, "weight_unit": "g",
            "attributes": cleaned_attrs,
        }]
    }
    _post(ozon_creds["client_id"], ozon_creds["api_key"], "/v3/product/import", body)


def check_task_status(task_id: str) -> dict[str, Any]:
    """Query current task status — single call, no polling.

    Returns: {task_id, status, stages, ozon_task_id, result_json, ok}
    """
    import requests as _requests

    TASK_URL = f"{_get_api_base().rstrip('/')}{TASK_STATUS_PATH}"
    token = _get_token()

    try:
        resp = _requests.post(
            TASK_URL,
            json={"task_id": task_id, "token": token},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        data = resp.json() if resp.ok else {}
    except Exception as e:
        return {"task_id": task_id, "status": "query_error", "ok": False, "error": str(e)[:200]}

    task = data.get("task", data)
    if not task or not isinstance(task, dict):
        return {"task_id": task_id, "status": "not_found", "ok": False}

    status = task.get("status", "unknown")
    result_json = task.get("result_json", {}) or {}
    if isinstance(result_json, str):
        try:
            result_json = json.loads(result_json)
        except (json.JSONDecodeError, TypeError):
            result_json = {}

    # v0.8: read stages from both columns (pipeline writes to stages, legacy writes to result_json.stages)
    stages = (task.get("stages", {}) or {}) if isinstance(task.get("stages"), dict) else {}
    rj_stages = result_json.get("stages", {}) or {}
    # Merge: pipeline direct stages take priority over result_json.stages
    for k, v in rj_stages.items():
        if k not in stages:
            stages[k] = v
    ozon_task_id = result_json.get("ozon_task_id") or task.get("ozon_task_id")

    terminal = status in ("succeeded", "ozon_processing", "blocked", "failed", "rejected", "timeout")
    return {
        "task_id": task_id,
        "status": status,
        "ok": terminal and status in ("succeeded", "ozon_processing"),
        "terminal": terminal,
        "ozon_task_id": ozon_task_id,
        "stages": stages,
        "result_json": result_json,
    }


def check_all_tasks(task_ids: list[str]) -> list[dict[str, Any]]:
    """Batch status check for multiple tasks. Returns list of status dicts."""
    return [check_task_status(tid) for tid in task_ids]


def poll_pipeline_task(
    task_id: str,
    *,
    interval_sec: float = 30.0,
    max_wait_sec: float = 600.0,
) -> dict[str, Any]:
    """[DEPRECATED] Use check_task_status() for single query, or check_all_tasks() for batch.

    Poll cloud pipeline status via n8n webhook (not Supabase directly).
    Kept for backward compatibility — prefer fire-and-forget + on-demand status checks.
    """
    import time

    logger.warning("poll_pipeline_task is deprecated. Use check_task_status() for single query.")
    TASK_URL = f"{_get_api_base().rstrip('/')}{TASK_STATUS_PATH}"
    token = _get_token()

    deadline = time.monotonic() + max_wait_sec
    poll_num = 0
    last_status = ''
    logger.info("Polling %s (max %ss, interval %ss)...", task_id, max_wait_sec, interval_sec)
    while time.monotonic() < deadline:
        poll_num += 1
        remaining = max(0, deadline - time.monotonic())
        try:
            resp = requests.post(
                TASK_URL,
                json={"task_id": task_id, "token": token},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json() if resp.ok else {}
        except Exception:
            time.sleep(interval_sec)
            continue

        task = data.get('task', data)
        if not task or not isinstance(task, dict):
            time.sleep(interval_sec)
            continue

        status = task.get('status', '')
        result_json = task.get('result_json', {}) or {}
        if isinstance(result_json, str):
            try:
                result_json = json.loads(result_json)
            except (json.JSONDecodeError, TypeError):
                result_json = {}

        stages = result_json.get('stages', {}) or {}
        ozon_task_id = result_json.get('ozon_task_id') or task.get('ozon_task_id')

        # Log progress on status change or every 3rd poll
        if status != last_status or poll_num % 3 == 0:
            stage_keys = list(stages.keys()) if stages else []
            logger.info("Poll #%d: status=%s ozon_id=%s stages=%s (%.0fs left)",
                    poll_num, status, ozon_task_id or '-', stage_keys or '-', remaining)
            last_status = status

        if status in ('succeeded', 'blocked', 'failed', 'rejected', 'ozon_processing'):
            logger.info("Terminal: status=%s ozon_task_id=%s stages=%s",
                    status, ozon_task_id, stages)
            return {
                'status': status,
                'success': status in ('succeeded', 'ozon_processing'),
                'ozon_task_id': ozon_task_id,
                'stages': stages,
                'task_id': task_id,
            }
        # Status node may leave status as 'running' or 'in_progress' even after completion.
        # Check result_json for evidence of completion instead.
        elif status in ('running', 'in_progress') and ozon_task_id:
            learn_done = 'Learn' in stages
            upload_done = 'Upload' in stages and 'succeeded' in str(stages.get('Upload', ''))
            if learn_done or upload_done:
                logger.info("Early terminal: in_progress with ozon_id, stages=%s", stages)
                return {
                    'status': 'succeeded',
                    'success': True,
                    'ozon_task_id': ozon_task_id,
                    'stages': stages,
                    'task_id': task_id,
                }
        elif status == 'not_found':
            pass  # Task not yet created — keep waiting

        time.sleep(interval_sec)

    return {
        'status': 'timeout',
        'success': False,
        'ozon_task_id': None,
        'stages': {},
        'task_id': task_id,
    }

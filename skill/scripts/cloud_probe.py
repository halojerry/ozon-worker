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
import logging
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from scripts._const import CLOUD_API_BASE, SKILL_VERSION
from scripts._errors import (
    ERR_CLOUD_REJECTED,
    ERR_CLOUD_TIMEOUT,
    ERR_CLOUD_UNAVAILABLE,
)
from scripts.lib.ak_1688_client import AkAuthError
from scripts.lib.config_store import capture_exception, init_sentry
from scripts.lib.reference_images import get_best_product_images
from scripts.lib.task_paths import cleanup_old_files

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

from scripts.lib.config_store import get_mxou_token as _get_mxou_token

# Config is now in skill/data/config/ (stores.json + settings.json)
# No .env loading needed.

# Initialize Sentry for local skill error tracking (best-effort)
init_sentry()

# Periodically clean up old probe artifacts (>7 days) to prevent
# unlimited disk growth.  Runs at most once per session (import-time).
try:
    cleanup_old_files(max_age_days=7)
except Exception as e:
    logger.debug('cleanup_old_files: %s', e)

# update check removed (no scripts.lib.update module)


# ═══════════════════════════════════════════════════════════════════════════
# Structured Task Logging — see scripts/lib/logging_utils.py
# ═══════════════════════════════════════════════════════════════════════════

from scripts.lib.logging_utils import AuditLogger


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
                if data.get(key):
                    defaults[key] = data[key]
    except Exception as e:
        logger.debug('path_registry.json load failed: %s', e)

    # ⚠️ v0.14 E2: 模块加载不做网络 discovery（旧代码 import 时同步发 HTTP GET timeout=10，
    # 每次命令 graph/follow 额外 +10s 阻塞）。discovery 惰性化：submit_task(deprecated webhook) 首次调用前触发。
    # 本地 registry 文件仍是权威默认值，网络 discovery 仅补充更新。
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

# ⚠️ v0.14 E2: discovery 惰性化 — 进程级缓存，仅 deprecated webhook 路径（submit_task）首次调用前触发
_discovery_done = False


def _ensure_paths_discovered() -> None:
    """惰性触发云端 discovery（进程内只做一次）。仅 submit_task(deprecated) 需要。"""
    global _discovery_done
    if _discovery_done:
        return
    _discovery_done = True
    try:
        _refresh_from__discovery_api(_paths)
    except Exception:
        pass


def _get_api_base() -> str:
    return CLOUD_API_BASE


def _get_token() -> str:
    """Resolve MXOU token from config_store (settings.json + pounding fallback)."""
    from scripts.lib.config_store import get_mxou_token
    return get_mxou_token()


def _get_ozon_credentials(store_id: str | None = None) -> dict[str, str]:
    """Get Ozon credentials from config_store (stores.json).

    Returns {"client_id": str, "api_key": str}.
    Returns empty strings if not configured.
    """
    from scripts.lib.config_store import get_ozon_credentials
    # ✅ v0.25 FIX (F4): 显式环境变量优先 — batch_test 通过 --client-id/--api-key
    # 设置 OZON_CLIENT_ID/OZON_API_KEY，此前 follow_sell_cloud 忽略 env 只读
    # stores.json 默认店（5381204），导致指定 5371047 仍落错店铺
    _env_cid = str(os.environ.get("OZON_CLIENT_ID", "") or "").strip()
    _env_akey = str(os.environ.get("OZON_API_KEY", "") or "").strip()
    if _env_cid and _env_akey:
        return {"client_id": _env_cid, "api_key": _env_akey}
    creds = get_ozon_credentials(store_id or "")
    if creds:
        return creds
    return {"client_id": "", "api_key": ""}


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
            status = exc.response.status_code if exc.response else 0
            detail = (exc.response.text[:500] if exc.response else str(exc))
            capture_exception(exc, url=url, phase='cloud_post', status=status)
            return _error_envelope(body, ERR_CLOUD_REJECTED, f"云端拒绝请求 ({status}): {detail}", terminal=status < 500 if status else False, retryable=status >= 500 if status else True, details=detail)

    # Should not reach here, but be defensive
    return _error_envelope(body, ERR_CLOUD_UNAVAILABLE, f"无法连接云端 ({url})", details=str(last_error))


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
    ozon = _get_ozon_credentials(store_id)
    resolved_extensions.setdefault("ozon_client_id", ozon["client_id"])
    # ozon_api_key removed — Auth node looks up credentials from Supabase users table
    mxou_token = _get_mxou_token()
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
    """Query worker for previously learned category mappings.

    Primary: GET {WORKER_URL}/api/v1/mappings/lookup（W11，worker category_mapping 自学习表）。
    404/异常时自动降级老 n8n webhook ``/webhook/cat-lookup-v1``（老 hybrid 表）。
    Returns ``{found: true, mappings: [{description_category_id, type_id, confidence}]}``
    or ``{found: false}`` on miss/error.

    Only returns mappings with confidence >= min_confidence (A/B selection).
    """
    if not keyword or not keyword.strip():
        return {"found": False, "keyword": keyword}
    base = _get_api_base()
    token = _get_token()
    kw = keyword.strip()
    headers = {"Authorization": f"Bearer {token}"}
    # 主路径：worker /api/v1/mappings/lookup（W11）
    try:
        resp = requests.get(
            f"{base.rstrip('/')}/api/v1/mappings/lookup",
            params={"keyword": kw},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 404:
            logger.info("lookup_category_webhook: worker 无 /api/v1/mappings/lookup，降级老 webhook")
        else:
            resp.raise_for_status()
            result = resp.json() if resp.text else {}
            if isinstance(result, dict) and result.get("found") and result.get("mappings"):
                mappings = [
                    {"description_category_id": m.get("dc"), "type_id": m.get("tp"),
                     "confidence": float(m.get("confidence") or 0.0)}
                    for m in result["mappings"] if m.get("dc") and m.get("tp")
                ]
                mappings = [m for m in mappings if m["confidence"] >= min_confidence]
                if mappings:
                    return {"found": True, "mappings": mappings, "keyword": kw}
            return {"found": False, "keyword": kw}
    except Exception as e:
        logger.info("lookup_category_webhook: 新端点失败(%s)，降级老 webhook", e)
    # 降级：老 n8n webhook（cat-lookup-v1）
    try:
        result = _cloud_post(
            f"{base.rstrip('/')}{CAT_LOOKUP_PATH}",
            {"keyword": kw, "min_confidence": min_confidence},
            headers=headers,
            timeout_sec=15,
        )
        return result if isinstance(result, dict) else {"found": False}
    except Exception:
        return {"found": False, "keyword": kw}


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
    from scripts.lib.config_store import _require_auth
    _require_auth()
    url = f"{_get_api_base()}/submit_task"

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
        # v0.22: 非 2xx 也要把服务端原因（error_code/message/detail）解析出来，
        # 让 agent/用户知道怎么解决（token 无效/余额不足/配额不足/信封异常等）
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if resp.status_code >= 400:
            if isinstance(payload, dict):
                reason = (
                    payload.get("message")
                    or (payload.get("detail") if isinstance(payload.get("detail"), str) else "")
                    or f"HTTP {resp.status_code}"
                )
                extra = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
                return {
                    "ok": False,
                    "error": reason,
                    "error_code": payload.get("error_code") or "",
                    "detail": extra or payload.get("detail", ""),
                    "http_status": resp.status_code,
                    "task_id": task_id,
                }
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                "http_status": resp.status_code,
                "task_id": task_id,
            }
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": f"Worker unreachable: {url}", "task_id": task_id}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"Worker timeout: {url}", "task_id": task_id}
    except Exception as e:
        return {"ok": False, "error": str(e), "task_id": task_id}


def submit_draft(
    graph_input: dict[str, Any],
    worker_url: str | None = None,
) -> dict[str, Any]:
    """T9: 入采集箱 — POST GraphInput 到 Worker 的 /api/v1/drafts。

    Worker 端剥离凭证存 credential_id，只留 envelope 进 product_drafts
    （WebUI 认领 → 编辑 → 确认后上架）。请求体与 submit_task 相同
    （含顶层 token，Worker _authenticate 支持 body token 兜底）。

    ⚠️ fail-hard（v0.42 M0.5）: 采集箱不可用（404/连接失败/超时）一律如实
    失败返回，绝不静默降级 submit_envelope() 直接上架——用户以为已入箱、
    实际已上架，是对 WebUI 运营中心的背叛。

    Returns:
        成功: {"ok": True, "draft_id": str, "message": "已入采集箱..."}
        404（老 Worker 无 /drafts 端点）:
            {"ok": False, "error": "采集箱端点不可用(404)...", "http_status": 404}
        连接失败/超时: {"ok": False, "error": "Worker 不可达，无法入箱", "http_status": 0}
        其他 4xx/5xx: {"ok": False, "error": str, "http_status": int}（不掩盖真实错误）
    """
    from scripts.lib.config_store import _require_auth
    _require_auth()
    base = (worker_url or _get_api_base()).rstrip("/")
    url = f"{base}/api/v1/drafts"

    try:
        import requests
        resp = requests.post(url, json=graph_input, timeout=30)
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if resp.status_code == 404:
            # 老 Worker 没有 /api/v1/drafts 端点 → fail-hard，绝不降级直接上架
            return {
                "ok": False,
                "error": "采集箱端点不可用(404)，请升级 Worker 或显式去掉 --to-box 直连上架",
                "http_status": 404,
            }
        if resp.status_code >= 400:
            if isinstance(payload, dict):
                reason = (
                    payload.get("message")
                    or (payload.get("detail") if isinstance(payload.get("detail"), str) else "")
                    or f"HTTP {resp.status_code}"
                )
            else:
                reason = f"HTTP {resp.status_code}"
            return {"ok": False, "error": reason, "http_status": resp.status_code}
        draft_id = str(payload.get("id") or payload.get("draft_id") or "") if isinstance(payload, dict) else ""
        return {
            "ok": bool(draft_id),
            "draft_id": draft_id,
            "message": f"已入采集箱，请到 WebUI 认领（draft_id={draft_id}）",
        }
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        # 连接失败/超时 → fail-hard，绝不降级直接上架
        return {"ok": False, "error": "Worker 不可达，无法入箱", "http_status": 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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


def _sample_values(values: list[dict], limit: int) -> list[dict]:
    """v0.60 G1: 维度内优选采样（替代 [:limit] 截断）。

    排序键（降序）:
    1. 有图优先 — 无图 SKU 的白底图会 fallback 到共享图，优先保留有独立图的
    2. 价格距中位数近 — 保留主流规格，排除极端低价(引流已单过滤)/极端高价
    3. 兜底: 原顺序（保持 1688 排序稳定性）
    """
    if len(values) <= limit:
        return values
    prices: list[float] = []
    for v in values:
        try:
            p = float(v.get("price", 0) or 0)
        except (TypeError, ValueError):
            p = 0.0
        if p > 0:
            prices.append(p)
    median_p: float = 0.0
    if prices:
        sp = sorted(prices)
        median_p = sp[len(sp) // 2]

    def _sort_key(v: dict):
        has_img = 1 if (v.get("image") and str(v.get("image")).strip()) else 0
        try:
            p = float(v.get("price", 0) or 0)
        except (TypeError, ValueError):
            p = 0.0
        if p > 0 and median_p > 0:
            dist = abs(p - median_p)
        else:
            dist = 1e18
        return (has_img, -dist)

    return sorted(values, key=_sort_key, reverse=True)[:limit]


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
                        # Keyword check BEFORE adding to filtered
                        skip, kw = _is_skip_sku(str(v.get("name", v.get("color", ""))))
                        if skip:
                            removed.append(v)
                            reasons.append(f"SKU关键词: {kw}")
                        else:
                            filtered.append(v)
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


# ── 单产品折叠：多变体 → 单变体 ──

_SINGLE_UNIT_RE = re.compile(
    r"(?i)"
    r"1\s*(只|个|件|条|把|支|片|包|瓶|袋|盒|罐|卷|根|双|对|组|套|PIC|pcs|pack|piece|pc|шт)"
    r"|单只|单个|单件|单包|单瓶|单袋|单盒"
    r"|一只装|一个装|一件装|一片装|一包装|一瓶装|一袋装"
    r"|1只装|1个装|1件装|1片装|1包装|1瓶装|1袋装"
)


def _is_single_unit(variant: dict) -> bool:
    """判断变体名称是否为1只装/单件（数量变体中最小单位）"""
    name = str(variant.get("name", ""))
    return bool(_SINGLE_UNIT_RE.search(name))


def _collapse_variants_to_single(
    variants: list[dict],
    cost_cny: float,
    shipping: dict,
) -> tuple[list[dict], float]:
    """
    将多变体折叠为单产品。

    策略:
    - 纯数量变体 → 筛选"1只装"变体
    - 纯颜色/尺寸变体 → 取中位数价格
    - 混合变体（颜色×数量）→ 先筛"1只装"，再取中位数
    - 采购成本 = 代表变体价格 + 1688国内运费(freightCny)

    Returns:
        (折叠后的variants列表(1个元素), 修正后的采购成本)
    """
    if not variants:
        return variants, cost_cny

    freight = float((shipping or {}).get("freightCny", 0) or 0)

    # 只有 1 个变体，直接加运费
    if len(variants) == 1:
        v = variants[0]
        total = float(v.get("price", 0) or cost_cny) + freight
        v["price"] = total
        v["original_price"] = total
        return [v], total

    # 判断是否有数量相关变体（通过名称关键词，不依赖 variant_type）
    # 覆盖: 纯数量变体("1只装"/"5只装") + 混合变体("白色 1只装"/"黑色 5只装")
    _QTY_KW_RE = re.compile(r"\d+\s*(只|个|件|片|包|瓶|袋|盒|罐|卷|根|PIC|pcs|pack|piece|pc)")
    one_piece = [v for v in variants if _is_single_unit(v)]
    has_qty_keywords = any(_QTY_KW_RE.search(str(v.get("name", ""))) for v in variants)

    if one_piece:
        # 有1只装变体 → 在1只装中取中位数
        candidates = one_piece
    elif has_qty_keywords:
        # 有数量关键词但没有1只装（如"5片装"/"10片装"）→ 取价格最低（最小规格）
        candidates = [min(variants, key=lambda v: float(v.get("price", 0) or 0))]
    else:
        # 纯颜色/尺寸变体 → 全部作为候选，取中位数
        candidates = list(variants)

    # 中位数选价
    prices = sorted(
        [float(v.get("price", 0) or 0) for v in candidates if float(v.get("price", 0) or 0) > 0]
    )
    if not prices:
        median_price = cost_cny
    else:
        median_price = prices[len(prices) // 2]

    # 选最接近中位数的变体
    best = min(candidates, key=lambda v: abs(float(v.get("price", 0) or 0) - median_price))

    # 采购成本 = 变体价格 + 1688国内运费
    total_cost = float(best.get("price", 0) or cost_cny) + freight

    representative = dict(best)
    representative["price"] = total_cost
    representative["original_price"] = total_cost

    return [representative], total_cost


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


class ProductValidationError(Exception):
    """产品数据校验不通过——不应重试，直接跳过该品。"""


# ── v0.21 P2: 尺寸/重量解析与合理性守卫 ──────────────────────────────────
# 根因（2026-08-04 实证）：
# 1. 风扇页 module-od-product-attributes 有带单位「规格 8.5*6.5*11cm」，但
#    probe fallback 容器未覆盖该模块 + body 正则漏「规格/包装体积」，只抓到
#    无单位的「外观尺寸 85*65*11」并按 cm ×10 → 850×650×110mm。
# 2. 工具页「长(cm)」表商家实际填 mm 值（260）→ 按表头 cm ×10 → 2600mm，
#    体积放大 1000 倍。
# 3. density 兜底把「轻而大」的商品重量按体积×0.5 放大（风扇 300g→30.4kg、
#    工具 400g→364kg），无视商家已提供的真实重量。

MAX_DIM_MM = 5000          # 单边物理上限 5m，超过视为脏数据
MAX_EST_WEIGHT_G = 30000   # 无商家重量时估算上限 30kg
_DIM_LWH_RE = re.compile(
    r"(?:\(?(cm|CM|mm|MM)\)?\s*(?:[：:]\s*)?)?"
    r"(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)"
    r"(?:\s*(cm|CM|mm|MM))?"
)
_DIM_VALUE_RE = re.compile(r"\s*(\d+\.?\d*)\s*(cm|CM|mm|MM)?\s*$")


def _parse_dim_value(text: Any) -> tuple[float, str | None]:
    """解析单个维度文本（'85' / '85cm' / '85 mm'）→ (数值, 单位|None)。"""
    m = _DIM_VALUE_RE.match(str(text or "").strip())
    if not m:
        return 0.0, None
    unit = (m.group(2) or "").lower() or None
    return float(m.group(1)), unit


def extract_dimensions_from_texts(texts: list[str]) -> dict | None:
    """从候选文本行提取 L*W*H 尺寸（输出 mm）。

    优先级：带单位候选（cm/mm）> 无单位候选（unit='unknown'，保守按原值 mm，
    不乘 10）。单边 > MAX_DIM_MM 拒绝。
    """
    united: dict | None = None
    unitless: dict | None = None
    for text in texts or []:
        m = _DIM_LWH_RE.search(str(text or ""))
        if not m:
            continue
        l, w, h = float(m.group(2)), float(m.group(3)), float(m.group(4))
        unit = (m.group(5) or m.group(1) or "").lower() or None
        if l <= 0 or w <= 0 or h <= 0:
            continue
        if unit == "cm":
            dims = {"length": int(l * 10), "width": int(w * 10), "height": int(h * 10), "unit": "cm"}
        elif unit == "mm":
            dims = {"length": int(l), "width": int(w), "height": int(h), "unit": "mm"}
        else:
            dims = {"length": int(l), "width": int(w), "height": int(h), "unit": "unknown"}
        if max(dims["length"], dims["width"], dims["height"]) > MAX_DIM_MM:
            continue
        if dims["unit"] != "unknown" and united is None:
            united = dims
        elif dims["unit"] == "unknown" and unitless is None:
            unitless = dims
    return united or unitless


def resolve_packaging_dimensions(pkg_row: dict, weight_g: float) -> dict:
    """解析 packaging row → mm 尺寸（含 cm/mm 交叉判定）。

    判定规则：row 数值无显式单位时，分别按 cm（×10）与 mm（×1）计算密度
    （g/cm³），取落在 [0.05, 8] 合理区间的一方；两方都在区间内或都不在时
    保持 cm（不臆断）。weight<=0 无法判定时保持 cm。
    """
    l_val, l_unit = _parse_dim_value(pkg_row.get("lengthText"))
    w_val, w_unit = _parse_dim_value(pkg_row.get("widthText"))
    h_val, h_unit = _parse_dim_value(pkg_row.get("heightText"))
    units = [u for u in (l_unit, w_unit, h_unit) if u]
    if l_val <= 0 or w_val <= 0 or h_val <= 0:
        return {"length": 0, "width": 0, "height": 0, "unit_used": "unknown", "suspected": False}

    if units:
        unit = "cm" if "cm" in units else "mm"
        mult = 10 if unit == "cm" else 1
        return {
            "length": int(l_val * mult), "width": int(w_val * mult), "height": int(h_val * mult),
            "unit_used": unit,
            "suspected": False,
        }

    weight = float(weight_g or 0)
    if weight <= 0:
        return {
            "length": int(l_val * 10), "width": int(w_val * 10), "height": int(h_val * 10),
            "unit_used": "cm", "suspected": False,
        }

    def _density(mult: float) -> float:
        vol_mm3 = (l_val * mult) * (w_val * mult) * (h_val * mult)
        return weight / (vol_mm3 / 1000.0) if vol_mm3 > 0 else 0.0

    d_cm = _density(10.0)
    d_mm = _density(1.0)
    in_cm = 0.05 <= d_cm <= 8.0
    in_mm = 0.05 <= d_mm <= 8.0
    if in_mm and not in_cm:
        unit_used, mult, suspected = "mm", 1, True
    else:
        unit_used, mult, suspected = "cm", 10, False
    return {
        "length": int(l_val * mult), "width": int(w_val * mult), "height": int(h_val * mult),
        "unit_used": unit_used, "suspected": suspected,
    }


def _validate_and_fix_product_data(
    item_id: str,
    title: str,
    cost_cny: float,
    images: list,
    weight_g: int,
    dimensions: dict,
    variants: list,
    option_groups: list,
) -> tuple[int, dict, list[str], bool, bool]:
    """校验产品数据完整性，并应用软兜底默认值。

    返回 (weight_g, dimensions, errors, dimensions_estimated, weight_estimated)。
    errors 为空表示通过；非空表示硬阻断，应跳过该产品。
    dimensions_estimated=True 表示尺寸是估算值（1688 页面未提供尺寸），
    Ozon 可能报 INCORRECT_DIMENSION——由调用方标记到信封供 worker 决策。
    weight_estimated=True 表示重量被兜底/改写（缺失兜底或高密度保留），
    供 worker/审计识别数据来源可信度。
    """
    errors: list[str] = []
    estimated: bool = False
    weight_estimated: bool = False

    # ── 软兜底：重量 ──
    if weight_g <= 0:
        weight_g = 50
        weight_estimated = True
        logger.warning("物品 %s 重量缺失，使用默认值 50g", item_id)

    # ── 软兜底：尺寸 ──
    if dimensions.get("length", 0) <= 0 and dimensions.get("width", 0) <= 0 and dimensions.get("height", 0) <= 0:
        # 基于重量估算合理默认尺寸（密度 ~400 kg/m³，混合材质消费品偏抛）
        # v0.40: 密度 800→400——800 时 50g→60×45×30mm(0.62g/cm³) 偏"实"，
        # Ozon ML 报 ML_INCORRECT_VOLUME_WEIGHT（体积重量与类目期望不符，
        # 实测 96 商品 3 个 FAIL）。400 时体积翻倍（69×52×35mm, 0.40g/cm³）
        # 更接近轻抛货特征，ML 通过率更高。
        density = 400.0  # kg/m³
        volume_m3 = (weight_g / 1000.0) / density  # m³
        volume_mm3 = volume_m3 * 1e9  # mm³
        # 长方体比例 2:1.5:1
        ratio_product = 2.0 * 1.5 * 1.0  # = 3.0
        base_mm = (volume_mm3 / ratio_product) ** (1/3) if volume_mm3 > 0 else 50.0
        # 限制最小值（太小会被 Ozon ML 拒绝）
        base_mm = max(float(base_mm), 30.0)
        est_length = round(base_mm * 2.0)
        est_width = round(base_mm * 1.5)
        est_height = round(base_mm * 1.0)
        dimensions = {"length": est_length, "width": est_width, "height": est_height}
        estimated = True
        logger.warning(
            "物品 %s 尺寸缺失，根据重量 %dg 估算: "
            "%d×%d×%dmm（密度=%.0fkg/m³）",
            item_id, weight_g, est_length, est_width, est_height, density,
        )
    else:
        # v0.40.1: 部分维度缺失（如手套只解析出 长×宽，height=0）→ 用已有维度
        # 比例补齐缺失边，避免 draft_sanity 拦截含 0 的 dimensions。
        # 比例按 长:宽:高 = 2:1.5:1 回填（与全缺失估算一致）。
        _l0, _w0, _h0 = (
            int(dimensions.get("length", 0) or 0),
            int(dimensions.get("width", 0) or 0),
            int(dimensions.get("height", 0) or 0),
        )
        if _l0 <= 0 or _w0 <= 0 or _h0 <= 0:
            _missing = [k for k, v in (("length", _l0), ("width", _w0), ("height", _h0)) if v <= 0]
            _present = [v for v in (_l0, _w0, _h0) if v > 0]
            _avg_present = (sum(_present) / len(_present)) if _present else 30.0
            for _mk in _missing:
                dimensions[_mk] = max(round(_avg_present * 0.7), 30)
            estimated = True
            logger.warning(
                "物品 %s 部分尺寸缺失(%s)，按比例补齐: %d×%d×%dmm",
                item_id, "+".join(_missing),
                int(dimensions.get("length", 0) or 0),
                int(dimensions.get("width", 0) or 0),
                int(dimensions.get("height", 0) or 0),
            )

    # ✅ v0.10: 密度合理性检查 — 防止商家脏数据和异常估算
    l, w, h = dimensions.get("length", 0), dimensions.get("width", 0), dimensions.get("height", 0)
    if weight_g > 0 and l > 0 and w > 0 and h > 0:
        volume_cm3 = (l * w * h) / 1000.0  # mm³ → cm³
        density_g_cm3 = weight_g / volume_cm3 if volume_cm3 > 0 else 0
        if density_g_cm3 > 10:  # 比铅（11.3）还密？明显异常（塑料~1, 金属~7）
            if volume_cm3 < 10:
                # ⚠️ v0.26 FIX: 体积荒谬（<10cm³ 装不下几百克）→ 尺寸是脏数据。
                # 实测：一次性盘子 160g 被解析成 10×10×10mm（1cm³），密度 160 被砍成 50g，
                # 运费/售价/利润全错。与 v0.21 低密度分支同理：商家重量可信，
                # 重估尺寸（复用 800kg/m³ 估算），保留商家重量。
                _density = 800.0
                _vol_m3 = (weight_g / 1000.0) / _density
                _vol_mm3 = _vol_m3 * 1e9
                _ratio_p = 3.0
                _base_mm = max((_vol_mm3 / _ratio_p) ** (1 / 3) if _vol_mm3 > 0 else 50.0, 30.0)
                dimensions = {
                    "length": round(_base_mm * 2.0),
                    "width": round(_base_mm * 1.5),
                    "height": round(_base_mm * 1.0),
                }
                estimated = True
                logger.warning(
                    "物品 %s 密度过高 %.1f g/cm³（%dg / %.0f cm³）且体积荒谬，"
                    "判定尺寸脏数据，重估尺寸 %d×%d×%dmm，保留商家重量 %dg",
                    item_id, density_g_cm3, weight_g, volume_cm3,
                    dimensions["length"], dimensions["width"], dimensions["height"], weight_g,
                )
            else:
                # ⚠️ v0.37 A3 修复: 密度>10 且体积≥10cm³ 时不再改写真实重量。
                # 旧逻辑 weight = volume×1.0 会销毁商家真实值（300g 铅坠→24g）。
                # 商家重量可信（v0.21/v0.26 已确立原则）→ 保留，仅告警标记。
                weight_estimated = True
                logger.warning(
                    "物品 %s 密度过高 %.1f g/cm³（%dg / %.0f cm³），"
                    "保留商家重量 %dg（可能为高密度材质或尺寸单位偏差）",
                    item_id, density_g_cm3, weight_g, volume_cm3, weight_g,
                )
        elif density_g_cm3 < 0.25 and volume_cm3 > 1000:  # 大体积但极轻（比泡沫还轻？数据错误）
            # v0.21 P2: 商家已提供真实重量 → 信任重量，不再用体积反推覆盖
            # （根因：风扇 300g→30.4kg、工具 400g→364kg 都是这个分支干的）
            if weight_g > 0:
                logger.warning(
                    "物品 %s 密度过低 %.2f g/cm³（%dg / %.0f cm³），"
                    "但商家已提供重量，保留商家重量 %dg（尺寸可能单位错误）",
                    item_id, density_g_cm3, weight_g, volume_cm3, weight_g,
                )
            else:
                # 无商家重量才估算，且封顶 MAX_EST_WEIGHT_G，防止脏尺寸把重量推上天
                estimated_g = max(int(volume_cm3 * 0.5), 100)
                estimated_g = min(estimated_g, MAX_EST_WEIGHT_G)
                logger.warning(
                    "物品 %s 密度过低 %.2f g/cm³（%dg / %.0f cm³），估算为 %dg（封顶 %dg）",
                    item_id, density_g_cm3, weight_g, volume_cm3, estimated_g, MAX_EST_WEIGHT_G,
                )
                weight_g = estimated_g

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
        logger.warning("❌ 物品 %s 校验不通过: %s", item_id, '; '.join(errors))
    return weight_g, dimensions, errors, estimated, weight_estimated


def _last_seg(path) -> str:
    """取面包屑路径「 > 」分割的最后一段（最具体类目名），去空白；空 → ""。"""
    if not path:
        return ""
    parts = [p.strip() for p in str(path).split(">") if p.strip()]
    return parts[-1] if parts else ""


def _resolve_envelope_category(category_name: str, source_category_path: str, category_query: str) -> str:
    """信封 draft.category 解析（v0.32 修复，防恒空/俄语错位）：
    Ozon 类目名 → 1688 面包屑末级（中文最具体类目）→ 查询词。"""
    return category_name or _last_seg(source_category_path) or category_query


def _category_search_variants(source_category_path: str) -> list[str]:
    """1688 类目末级词 → 类目搜索候选词列表（v0.39 Issue3 净化）。

    末级词常为复合词（"化妆刷、刷包"/"粉扑、美妆蛋"）——Ozon 类目树按单段
    匹配，复合词必空（实证 "化妆刷、刷包" 空 / "化妆刷" 命中 78032222/93961）。
    按顿号分拆出各单段，供调用方逐个探测命中。
    """
    if not source_category_path:
        return []
    _parts = [p.strip() for p in source_category_path.split(">") if p.strip()]
    if not _parts:
        return []
    _last = _parts[-1]
    return [s.strip() for s in _last.split("、") if s.strip()] or [_last]


def _extract_source_category_id(source_categories) -> int | None:
    """从 1688 类目列表取最末级（叶子）类目数字 ID。兼容 id/leafId/thirdCategoryId/categoryId。"""
    if not isinstance(source_categories, list):
        return None
    last_id = None
    for c in source_categories:
        if not isinstance(c, dict):
            continue
        for key in ("id", "leafId", "thirdCategoryId", "categoryId"):
            val = c.get(key)
            try:
                if val is not None and str(val).isdigit():
                    last_id = int(val)
                    break
            except (TypeError, ValueError):
                continue
    return last_id


def _flatten_ozon_characteristics(chars) -> dict[str, str]:
    """Ozon widget characteristics（webCharacteristics 原始结构）→ {俄语属性名: 值}。

    webCharacteristics widget 分组结构：``[{title: {textRs: [{content}]}, values: [{text}]}, ...]``
    （title 可能是 str 或 dict，values 可能是 list[dict]/list[str]），支持嵌套 ``characteristics``
    子分组。结果与 follow 路径 ``_attrs_all`` 同构（draft.ozon_attributes 消费）。
    """
    attrs: dict[str, str] = {}

    def _walk(items):
        for g in items or []:
            if not isinstance(g, dict):
                continue
            t = g.get("title")
            if isinstance(t, dict):
                title = ""
                for _rs in (t.get("textRs") or []):
                    if isinstance(_rs, dict) and _rs.get("content"):
                        title = str(_rs["content"])
                        break
                if not title:
                    title = str(t.get("text") or "")
            else:
                title = str(t or "")
            vals = []
            for v in (g.get("values") or []):
                if isinstance(v, dict):
                    vals.append(str(v.get("text") or ""))
                elif isinstance(v, str):
                    vals.append(v)
            value = ", ".join(x for x in vals if x)
            if title and value:
                attrs.setdefault(title.strip(), value.strip())
            if g.get("characteristics"):
                _walk(g["characteristics"])

    _walk(chars)
    return attrs


# 可注入 extensions 的配置键（与 worker template_service.CONFIG_KEYS 一致）
_INJECTABLE_EXT_KEYS = ("margin_rate", "commission_rate", "fx_buffer",
                        "offer_id_prefix", "follow_type", "stock", "warehouse_id")


def _merge_config_tiers(ext: dict[str, Any], *, template_profile: dict[str, Any] | None,
                        store_profile: dict[str, Any] | None) -> dict[str, Any]:
    """D11: 三段降级合并——显式 extensions 恒优先 > worker 默认模板 > 本地 stores.json。

    仅补缺省：ext 已有非空值不被覆盖（R5 已定稿）。margin/commission/fx 沿用旧行为
    只注入非零值（Worker 默认兜底）。
    """
    template_profile = template_profile or {}
    store_profile = store_profile or {}
    for _key in _INJECTABLE_EXT_KEYS:
        if ext.get(_key) not in (None, ""):
            continue
        _val = template_profile.get(_key)
        if _val in (None, ""):
            _val = store_profile.get(_key)
        if _val in (None, ""):
            continue
        if _key in ("margin_rate", "commission_rate", "fx_buffer") and float(_val) == 0:
            continue
        ext[_key] = _val
    return ext


def build_graph_envelope(
    *,
    item_id: str,
    detail_url: str,
    category_query: str = "",
    store_id: str = "",
    title: str = "",
    poll_category: bool = True,
    max_skus: int | None = None,
    fallback_images: list[str] | None = None,
    cdp: Any = None,
    template_id: str = "",
) -> dict[str, Any]:
    """1688 API + CDP → GraphInput 格式 envelope。

    1. 1688 API get_product_details(item_id)
    2. CDP enrich_product_with_cdp(detail_url, api_data)
    3. Ozon category search (if poll_category=True)
    4. 组装为 {token, ozon_client_id, ozon_api_key, envelope}
    
    Args:
        max_skus: SKU 数量上限（None=使用默认值15，0=不限制）
        fallback_images: 1688 图片为空时使用的兜底图（如 follow 的 Ozon 竞品主图）。
            仅当 get_best_product_images 结果为空时生效，放行「产品图片为空」校验门。
        cdp: 可选外部 CdpConnection 复用（P5/T3）——传入时 enrich 跳过浏览器查找/
            登录等待，直接用调用方连接探测。连接归调用方所有，本函数不关闭。
    """
    from scripts.lib.config_store import _require_auth
    _require_auth()
    from scripts.lib.ak_1688_client import enrich_product_with_cdp, get_product_details
    from scripts.lib.reference_images import get_best_product_images

    # ── 1. 1688 API ──
    try:
        details = get_product_details([str(item_id)])
        api_data = details.get(str(item_id), {})
    except Exception:
        api_data = {}
    if not api_data:
        api_data = {"title": title or "", "price": "", "images": []}

    # v0.34: 提前提取 1688 类目面包屑（供 Ozon 类目搜索用末级词，见 L1285）
    _src_cats = api_data.get("categories", [])
    if isinstance(_src_cats, list) and _src_cats:
        _src_path = " > ".join(c["name"] for c in _src_cats if c.get("name"))
        _src_parts = [p.strip() for p in _src_path.split(">") if p.strip()]
        source_category_path = _src_path
        source_category_short = " > ".join(_src_parts[-2:]) if len(_src_parts) >= 2 else (_src_parts[0] if _src_parts else "")
    else:
        source_category_path = ""
        source_category_short = ""

    # ── 2. CDP 浏览器富化（必须成功，不允许降级）──
    enriched = enrich_product_with_cdp(detail_url=detail_url, api_data=api_data, cdp=cdp)
    data = enriched.get("data", {})
    cdp_source = enriched.get("source", "?")

    # Q2: CDP 完全失败 → 降级透传 API 数据（enrich data 仍含 API title/price/images），
    # 数据质量由下方 _validate_and_fix_product_data 校验门把关
    if cdp_source == "api_only":
        logger.warning(
            "build_graph_envelope: %s — CDP 完全失败(%s)，降级用 1688 API 数据组装信封",
            item_id, enriched.get('degraded_reason', 'unknown'),
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
        # ⚠️ v0.34: 优先用 1688 类目末级词（source_category_short 最后一级）搜索——
        # 长标题中文分词查 ZH_HANS 树 token 过多、泛化词(玩具/用品)稀释, 错配率高
        # (实证: 洗碗海绵→厨房秤, 竹知了益智玩具→甜品套装)。末级词整体辨识度最高。
        # ⚠️ v0.39 Issue3: 末级词常为复合词（"化妆刷、刷包"）→ 顿号分拆逐个尝试
        # （实证 "化妆刷、刷包" 空 / "化妆刷" 命中 78032222/93961）
        _src_short = ""
        _src_variants: list[str] = []
        if source_category_path:
            _src_parts = [p.strip() for p in source_category_path.split(">") if p.strip()]
            if _src_parts:
                _src_short = _src_parts[-1]
                _src_variants = _category_search_variants(source_category_path)
        search_text = category_query or _src_short or title or (data.get("title") or "")
        if search_text:
            try:
                from scripts.lib.ozon_api import search_categories
                # ✅ v0.27: 语言按搜索词自动选择 — 1688 中文类目/标题搜 ZH_HANS 树,
                # 俄语标题才搜 RU 树(旧代码恒 RU 搜中文 → 必空 → poll_category 形同虚设)
                _lang = "ZH_HANS" if any("\u4e00" <= ch <= "\u9fff" for ch in search_text) else "RU"
                # 复合末级词：先试各单段命中（"化妆刷、刷包" → "化妆刷"）
                _search_words = [search_text]
                if len(_src_variants) > 1:
                    _search_words = _src_variants + [search_text]
                cats = []
                for _word in _search_words:
                    cats = search_categories(
                        ozon_creds["client_id"], ozon_creds["api_key"],
                        _word, language=_lang, max_results=1,
                    )
                    if cats:
                        search_text = _word
                        break
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
    # v0.21 P2: cm/mm 交叉判定（工具页 cm 表实际填 mm 值 → 按密度合理性切到 mm）
    if pkg_first:
        resolved = resolve_packaging_dimensions(pkg_first, weight_g)
        dimensions = {
            "length": resolved["length"],
            "width": resolved["width"],
            "height": resolved["height"],
        }
    else:
        dimensions = {"length": 0, "width": 0, "height": 0}

    # 降级: 如果 packaging 表格没有尺寸，优先用 probe 收集的候选行
    # （module-od-product-attributes 属性表 / 描述区 / body 行，v0.21 P2）
    if (dimensions["length"] == 0 and dimensions["width"] == 0 and dimensions["height"] == 0):
        import re as _re
        candidates = data.get("dim_text_candidates") or []
        parsed = extract_dimensions_from_texts(candidates) if candidates else None
        if parsed:
            dimensions = {
                "length": parsed["length"],
                "width": parsed["width"],
                "height": parsed["height"],
            }
            logger.info(
                "物品 %s packaging 无尺寸，从候选文本提取 %s（unit=%s）",
                item_id, dimensions, parsed.get("unit"),
            )
        # 再兜底: description 文本正则（旧路径，保持兼容）
        desc = data.get("description") or ""
        if desc and (dimensions["length"] == 0 and dimensions["width"] == 0 and dimensions["height"] == 0):
            # 匹配 L*W*H 格式: "34*25*2CM", "尺寸：30*9.5*4.5cm", "MEAS:51*35*42CM"
            dim_pat = _re.search(
                r'(?:尺寸|单个|产品尺寸|MEAS|meas|箱规)?[：:\s]*'
                r'(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*(cm|CM|mm|MM)?',
                desc
            )
            if dim_pat:
                try:
                    l, w, h = float(dim_pat.group(1)), float(dim_pat.group(2)), float(dim_pat.group(3))
                    unit = (dim_pat.group(4) or "").lower()
                    if 0 < l < 300 and 0 < w < 300 and 0 < h < 300:  # sanity: < 3m
                        if unit == "mm":
                            dimensions = {"length": int(l), "width": int(w), "height": int(h)}
                        else:
                            # 默认 cm → mm
                            dimensions = {"length": int(l * 10), "width": int(w * 10), "height": int(h * 10)}
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
    # ⚠️ P4: 1688 图片为空但调用方提供兜底图（follow 的 Ozon 竞品主图）→ 用兜底，
    # 放行「产品图片为空」校验门（1688 api_only 降级时图片可能为空）
    if not images and fallback_images:
        images = list(fallback_images)

    # 属性（v0.40: AK CPV/SKU 属性优先 + contextPath featureAttributes 全量 + DOM 补充）
    # 上品帮采集方案借鉴——1688 页面 context(...) 内嵌 JSON 的 featureAttributes
    # 是结构化全量属性（含产地/材质/型号等），比 DOM 属性表更全更稳。
    attrs: dict[str, str] = {}
    # v0.40: AK 必填 CPV 属性（最大承重/功率/品牌）——API 直取，无需 CDP
    for _cpv_map in (api_data.get("cpv_attributes") or {}).items():
        _k, _v_list = _cpv_map
        _k = str(_k or "").strip()
        _v = str(_v_list[0] if isinstance(_v_list, list) and _v_list else _v_list or "").strip()
        if _k and _v and len(_k) < 30 and len(_v) < 80:
            attrs[_k] = _v
    # v0.40: AK SKU 属性（颜色/规格等）——多值取首个
    for _sk_map in (api_data.get("sku_attributes") or {}).items():
        _k, _v_list = _sk_map
        _k = str(_k or "").strip()
        _v = str(_v_list[0] if isinstance(_v_list, list) and _v_list else _v_list or "").strip()
        if _k and _v and _k not in attrs and len(_k) < 30 and len(_v) < 80:
            attrs[_k] = _v
    ctx_path = None
    for _sd in data.get("pageStructuredData") or []:
        if isinstance(_sd, dict) and _sd.get("name") == "contextPath":
            ctx_path = _sd
            break
    if ctx_path:
        try:
            import json as _json
            _sample = ctx_path.get("sample")
            _cp = _json.loads(_sample) if isinstance(_sample, str) else (_sample or {})
            for a in _cp.get("featureAttributes") or []:
                name = str(a.get("name", "")).strip()
                val = str(a.get("value", "") or "").strip()
                if name and val and len(name) < 30 and len(val) < 80:
                    attrs[name] = val
        except Exception:
            pass
    # DOM 属性表补充（contextPath 缺失的属性名，如"颜色分类"等页面特有字段）
    for a in data.get("attributes", []):
        name = str(a.get("name", "")).strip()
        val = str(a.get("value", "")).strip()
        if name and val and name not in attrs and len(name) < 30 and len(val) < 80:
            attrs[name] = val
        if len(attrs) >= 40:  # 上限 40（信封体积控制）
            break
    # 单件重量（contextPath unitWeight，克）——缺重量时的可靠来源
    if ctx_path and not (data.get("weight") or data.get("unit_weight")):
        try:
            import json as _json
            _sample = ctx_path.get("sample")
            _cp = _json.loads(_sample) if isinstance(_sample, str) else (_sample or {})
            _uw = _cp.get("unitWeight")
            if _uw:
                data["unit_weight"] = float(_uw) * 1000  # 0.1kg → 100g
        except Exception:
            pass

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
                # Apply sampling — v0.60 G1: 优选而非截断
                # 排序键: ①有图优先(无图 SKU 白底图会 fallback) ②价格距中位数近(保留主流规格,排极端价)
                for k in range(n_dims):
                    if len(value_lists[k]) > per_dim:
                        value_lists[k] = _sample_values(value_lists[k], per_dim)
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
                # Single dimension: v0.60 G1 优选而非截断
                dropped_skus = total_combos - effective_max_skus
                value_lists[0] = _sample_values(value_lists[0], effective_max_skus)
                drop_reason = (
                    f"single-dim sampling: total={total_combos} > max_skus={effective_max_skus}"
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
                "attributes": all_attrs,
                "variant_type": variant_type,
            })
            variant_idx += 1

    elif sku_details:
        # Fallback: no option_groups but have sku_details
        _max_skus_applied = min(len(sku_details), effective_max_skus)
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

    # ── 5.4.1 单产品折叠：多变体 → 单变体 ──
    # ⚠️ v0.14 P0-4: 无条件调用（_collapse_variants_to_single 内部已兼容 0/1/N 个变体）
    # 旧守卫 if len(variants) > 1 导致单SKU/跟卖/发现商品跳过折叠 → cost_cny 不含国内运费(freightCny)，
    # 采购成本偏低 → 定价利润失真（每单必现）。
    original_count = len(variants)
    variants, cost_cny = _collapse_variants_to_single(variants, cost_cny, shipping)
    logger.info(
        "单产品折叠: %d个变体 → 1个 (采购成本=%.2f CNY, 含运费)",
        original_count, cost_cny,
    )

    # ── 5.5 校验门：硬阻断 + 软兜底 ──
    weight_g, dimensions, validation_errors, dimensions_estimated, weight_estimated = _validate_and_fix_product_data(
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

    # ✅ v0.25 S2: 1688 类目数字 ID（最末级，供 Worker 类目学习/定向兜底）
    # (source_category_path/short 已在函数开头提前提取，供 L1285 Ozon 类目搜索用末级词)
    source_categories = api_data.get("categories", [])
    source_category_id = _extract_source_category_id(source_categories)

    draft: dict[str, Any] = {
        "item_id": str(item_id),
        "title": item_title,
        "description": (data.get("description") or "")[:5000],
        "currency": "CNY",
        "images": images,
        "attributes": attrs,
        "weight": weight_g,
        "dimensions": dimensions,
        "category": _resolve_envelope_category(category_name, source_category_path, category_query),
        "purchase_url": detail_url,
        "purchase_cost": cost_cny,
        "supplier": supplier,
    }
    if shipping:
        draft["shipping"] = shipping
    if dimensions_estimated:
        draft["dimensions_estimated"] = True  # ✅ v0.21: 尺寸为估算值，供 worker 决策
    if weight_estimated:
        draft["weight_estimated"] = True  # ✅ v0.37 A3: 重量被兜底/保留（非原始抓取值），供 worker/审计识别
    if ozon_category:
        draft["ozon_category"] = ozon_category
    # ✅ v0.21: 传完整 1688 类目路径（旧版只传末两级，丢失顶级信号如"成人用品"导致类目错配）
    if source_category_path:
        draft["source_category"] = source_category_path
    elif source_category_short:
        draft["source_category"] = source_category_short
    if source_category_id:
        draft["source_category_id"] = source_category_id

    if is_multi:
        draft["variants"] = variants
    else:
        # ⚠️ v0.29.x 防御: 无 option_groups 且无 sku_details 时 variants 可能为空
        # (如护发素单规格商品) → 兜底单变体, 避免 variants[0] IndexError
        if not variants:
            variants = [{
                "sku_id": str(item_id),
                "name": "default",
                "color": "default",
                "model": "",
                "size": "one size",
                "image": "",
                "price": cost_cny,
                "original_price": cost_cny,
                "attributes": {},
                "variant_type": "single",
            }]
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
            "category_id": source_category_id,  # ✅ v0.25 S2: 1688 叶子类目数字 ID
        },
        "extensions": {
            # ✅ v0.27: 删除 max_skus/dropped_skus/drop_reason/filtered_skus(无人消费);
            # margin_rate/commission_rate/fx_buffer 由下方 6.5 段注入
        },
    }

    # ── 6.5 注入定价参数（D11 三段降级：显式 extensions > worker 默认模板 > 本地 stores.json）──
    from scripts.lib.config_store import get_store_profile, get_template_profile
    store_profile = get_store_profile(store_id)
    template_profile = {}
    try:
        template_profile = get_template_profile(
            _get_mxou_token() or "", credential_id=ozon_creds.get("client_id"),
            template_id=template_id) or {}
    except Exception:
        template_profile = {}
    _merge_config_tiers(
        envelope["extensions"],
        template_profile=template_profile,
        store_profile=store_profile,
    )

    # ⚠️ v0.14 P1-7: 删除"用 1688 item_id 查 Ozon 佣金"死代码块
    # 原 fetch_product_commissions 用 product_id filter 查 /v5/product/info/prices，
    # 传入的是 1688 offer ID（非 Ozon product_id）→ 恒返回空，永远无效。
    # 佣金率由 store_config（get_store_profile）或 Worker 默认值提供。

    # Q2: api_only 降级透传 → 标记 degraded，供 worker/审计识别数据来源
    if cdp_source == "api_only":
        envelope["extensions"]["cdp_degraded"] = True

    # ── 6.7 完整性审计 ──
    try:
        _audit = AuditLogger(task_id=str(item_id))
        _audit.log("envelope", "integrity", "info", "Envelope assembled", {
            "item_id": str(item_id),
            "has_title": bool(item_title),
            "has_images": len(images) > 0,
            "has_weight": weight_g > 0,
            "has_dimensions": any(d > 0 for d in [dimensions.get("length"), dimensions.get("width"), dimensions.get("height")]),
            "has_ozon_category": bool(ozon_category),
            "has_description": bool(data.get("description")),
            "image_count": len(images),
            "cdp_source": cdp_source,
            "margin_rate": envelope["extensions"].get("margin_rate", 0),
            "commission_rate": envelope["extensions"].get("commission_rate", 0),
        })
    except Exception:
        pass

    try:
        _audit.log("envelope", "pricing", "info", "Pricing params", {
            "margin_rate": envelope["extensions"].get("margin_rate", 0),
            "commission_rate": envelope["extensions"].get("commission_rate", 0),
            "fx_buffer": envelope["extensions"].get("fx_buffer", 0),
            "source": "ozon_api" if envelope["extensions"].get("commission_rate", 0) > 0 else "store_config",
        })
    except Exception:
        pass

    # ── 7. 组装 GraphInput ──
    mxou_token = _get_mxou_token() or _get_token()
    return {
        "token": mxou_token,
        "ozon_client_id": ozon_creds["client_id"],
        "ozon_api_key": ozon_creds["api_key"],
        "envelope": envelope,
    }


def build_envelope_from_discovery(candidate, store_config: dict, store_id: str = "") -> dict:
    """Build Worker GraphInput envelope from a discovery candidate.

    ✅ P0 修复：调用完整 build_graph_envelope_with_retry() 走 AK+CDP 双通道，
    不再手动组装空属性/零尺寸/猜重量的信封。

    Args:
        candidate: ProductCandidate from ozon_discovery
        store_config: {"client_id": "...", "api_key": "...", "currency": "RUB"}
        store_id: 店铺名（用于获取定价参数）

    Returns:
        GraphInput dict: {token, ozon_client_id, ozon_api_key, envelope} or None
    """
    from scripts.lib.config_store import get_mxou_token

    token = get_mxou_token() or ""

    # 提取 1688 item_id
    best_id = candidate.match_1688_url.split("/offer/")[-1].rstrip(".html") if "/offer/" in candidate.match_1688_url else ""
    if not best_id:
        return None

    detail_url = f"https://detail.1688.com/offer/{best_id}.html"

    # ✅ 调用完整 AK+CDP 链路（包含 get_product_details + CDP 浏览器富集）
    try:
        result = build_graph_envelope_with_retry(
            item_id=best_id,
            detail_url=detail_url,
            store_id=store_id,
            max_skus=1,
        )
    except Exception as e:
        logger.warning("build_graph_envelope_with_retry 失败，降级使用原始候选品数据: %s", e)
        result = None

    if result and result.get("envelope"):
        draft = result["envelope"].get("draft", {})
        extensions = result["envelope"].get("extensions", {})

        # 跟卖标记：如果 Ozon 有竞品则标记为跟卖
        if candidate.competing_sellers > 0:
            draft["ozon_product_id"] = candidate.ozon_product_id
            extensions["follow_sell"] = True

        # 注入 Ozon 类目（候选品数据）
        ozon_cat = getattr(candidate, 'ozon_category', None)
        if ozon_cat:
            draft["ozon_category"] = ozon_cat

        # ✅ v0.35.x: 竞品重量/尺寸注入 extensions（worker 兜底链 C2）
        # what_to_sell 的竞品重量(4497)/尺寸(9454/9455/9456)经
        # apply_analytics_to_candidate 写入候选——1688 数据缺失时 worker
        # _resolve_weight_dimensions（prepare_ozon_upload_node.py:1373）用
        # extensions.competitor_weight_g / competitor_dimensions_mm 兜底，
        # 否则退到 100g/300×200×50mm 硬编码（上品尺寸不准）。
        _cand_w = getattr(candidate, "weight_g", 0) or 0
        _cand_dims = getattr(candidate, "dimensions_mm", None) or {}
        if _cand_w:
            extensions["competitor_weight_g"] = int(_cand_w)
        if _cand_dims.get("length") and _cand_dims.get("width") and _cand_dims.get("height"):
            extensions["competitor_dimensions_mm"] = {
                "length": int(_cand_dims["length"]),
                "width": int(_cand_dims["width"]),
                "height": int(_cand_dims["height"]),
            }
        if extensions.get("competitor_weight_g") or extensions.get("competitor_dimensions_mm"):
            logger.info(
                "✅ 竞品数据注入（discover）: weight=%s dims=%s",
                extensions.get("competitor_weight_g"),
                extensions.get("competitor_dimensions_mm"),
            )

        # ✅ v0.58: 佣金分段透传 extensions（worker 定价用 fbs/fbo 分段费率）
        # what_to_sell 三段佣金（_to_rate_segments: leq_1500/leq_5000/gt_5000）
        # 经 apply_analytics_to_candidate 写入候选——rfbs→fbs、fbp→fbo 映射，
        # 对齐 worker pricing 的 fbs/fbo 语义；无 segments 时不加该键。
        _rfbs_seg = getattr(candidate, "commission_rfbs_segments", None) or {}
        _fbp_seg = getattr(candidate, "commission_fbp_segments", None) or {}
        if _rfbs_seg or _fbp_seg:
            extensions["commission_segments"] = {
                "fbs": dict(_rfbs_seg),
                "fbo": dict(_fbp_seg),
            }

        # ✅ P0-5 修复：优先透传 build_graph_envelope_with_retry 已解析的凭证
        # （store_config 仅作兜底，避免提交空 Ozon 凭证）
        return {
            "token": token,
            "ozon_client_id": result.get("ozon_client_id")
                or store_config.get("client_id", ""),
            "ozon_api_key": result.get("ozon_api_key")
                or store_config.get("api_key", ""),
            "envelope": {
                "draft": draft,
                "source": result["envelope"].get("source", {}),
                "extensions": extensions,
            }
        }

    # 降级：保留原始简单组装（向后兼容）
    # ✅ 定价参数走 store profile（不再硬编码 0.25/10%）
    store_profile = {}
    try:
        from scripts.lib.config_store import get_store_profile
        store_profile = get_store_profile(store_id) or {}
    except Exception:
        pass

    draft = {
        "item_id": best_id,
        "title": candidate.match_1688_title or candidate.ozon_title,
        "description": "",
        "currency": "CNY",
        "images": candidate.match_1688_images or candidate.ozon_images[:1],
        "attributes": {},
        "weight": getattr(candidate, 'weight_g', 0) or 300,
        "dimensions": getattr(candidate, 'dimensions_mm', None) or {"length": 0, "width": 0, "height": 0},
        "purchase_cost": candidate.match_1688_price or 0,
        "purchase_url": candidate.match_1688_url or "",
        "supplier": getattr(candidate, 'match_1688_supplier', ''),
    }

    source = {
        "purchase_url": candidate.match_1688_url or "",
        "purchase_cost": candidate.match_1688_price or 0,
    }

    extensions = {
        "follow_sell": candidate.competing_sellers > 0,
        "margin_rate": float(store_profile.get("margin_rate", 0) or 0.25),
        "commission_rate": float(store_profile.get("commission_rate", 0) or 0.10),
    }

    return {
        "token": token,
        "ozon_client_id": store_config.get("client_id", ""),
        "ozon_api_key": store_config.get("api_key", ""),
        "envelope": {
            "draft": draft,
            "source": source,
            "extensions": extensions,
        }
    }


def build_graph_envelope_with_retry(
    *,
    item_id: str,
    detail_url: str,
    category_query: str = "",
    store_id: str = "",
    max_retries: int = 3,
    retry_delay: float = 15.0,
    max_skus: int | None = None,
    fallback_images: list[str] | None = None,
    cdp: Any = None,
    template_id: str = "",
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
                # ✅ v0.27: 打开 Ozon 官方类目解析(Seller 空间 search_categories),
                # 直采信封从此携带正确的 description_category_id/type_id(旧: False → 类目全靠 worker 猜)
                poll_category=True,
                max_skus=max_skus,
                fallback_images=fallback_images,
                cdp=cdp,
                template_id=template_id,
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


from scripts.lib.utils import parse_price as _parse_price


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
            from scripts.lib.ozon_api import (
                get_product_attributes_v4,
                list_product_infos,
            )
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
    cdp: Any = None,  # 可选外部 CdpConnection 复用（T3/P4），传入时 enrich 跳过浏览器查找/登录等待
) -> dict[str, Any]:
    """1688 → Ozon. Client collects raw data; pipeline handles all Ozon work.

    If ``resolved_category`` is provided, the internal category resolution is
    skipped entirely and this category is injected directly into the envelope.
    This is useful when the caller has already validated the category via Ozon
    API (e.g. batch scripts, strict mode).
    """
    from scripts.lib.ak_1688_client import enrich_product_with_cdp, get_product_details

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
    enriched = enrich_product_with_cdp(detail_url=detail_url, api_data=api_data, cdp=cdp)
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
    ozon_creds = _get_ozon_credentials(store_id)

    # ⚠️ v0.39 Issue3 修复（对齐 build_graph_envelope:1232-1315）:
    # 类目搜索关键词优先用 1688 类目末级词（source_category_path 最后一级）——
    # 长中文标题 token 过多/含歧义词（水枪→Пистолет、护手霜→Крем интимный、
    # 收纳盒→Органайзер рыболовный）导致错配或空类目（10/22 上架被阻断根因）。
    # 末级词（如"宠物项圈"）整体辨识度最高。
    source_category_path = ""
    _src_cats = api_data.get("categories", [])
    if isinstance(_src_cats, list) and _src_cats:
        _src_path = " > ".join(c["name"] for c in _src_cats if c.get("name"))
        if _src_path.strip():
            source_category_path = _src_path
    if not source_category_path:
        # aibuy 通道类目线索兜底（Issue3 协同：cate_level 字段，AK 详情失败时）
        _cl1 = api_data.get("cate_level1_id") or api_data.get("cateLevel1Id")
        _cl2 = api_data.get("cate_level2_id") or api_data.get("cateLevel2Id")
        if _cl1 or _cl2:
            logger.debug("1688 API 无 categories，用 aibuy 类目 ID 兜底: cl1=%s cl2=%s", _cl1, _cl2)

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
        # ⚠️ v0.39 Issue3: 优先 1688 类目末级词（替代歧义标题）→ category_query → title
        # 末级词常为复合词（"化妆刷、刷包"/"粉扑、美妆蛋"）——顿号分拆逐个尝试，
        # ZH_HANS 树按单段匹配（实证 "化妆刷、刷包" 空 / "化妆刷" 命中 78032222/93961）
        _src_short = ""
        _src_short_variants: list[str] = []
        if source_category_path:
            _src_parts = [p.strip() for p in source_category_path.split(">") if p.strip()]
            if _src_parts:
                _src_short = _src_parts[-1]
                _src_short_variants = _category_search_variants(source_category_path)
        search_text = category_query or _src_short or title or ""
        if search_text:
            # 末级词多段时优先试各单段（"化妆刷、刷包" → 先试 "化妆刷"）
            if len(_src_short_variants) > 1:
                for _variant in _src_short_variants:
                    try:
                        from scripts.lib.ozon_api import search_categories as _probe_cats
                        _v_lang = "ZH_HANS" if any('\u4e00' <= c <= '\u9fff' for c in _variant) else "RU"
                        if _probe_cats(ozon_creds['client_id'], ozon_creds['api_key'],
                                       _variant, language=_v_lang, max_results=1):
                            search_text = _variant
                            break
                    except Exception:
                        pass
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
                from scripts.lib.ozon_api import (
                    search_categories_validated as _search_validated,
                )
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
                    # ✅ v0.39 需求3: 类目歧义 LLM 消歧——候选>1 且首位可能错配
                    # （护手霜→Крем интимный 案例）时，用 1688 末级词中文语义判定
                    # 最相关候选（失败维持首位，宁缺毋滥）
                    if len(cats) > 1:
                        try:
                            from scripts.lib.ozon_discovery import _llm_disambiguate_category
                            _tok = _get_token()
                            _pick_idx = _llm_disambiguate_category(
                                _src_short or search_text, cats, token=_tok)
                            if _pick_idx != 0:
                                logger.info('LLM 类目消歧: 首位=%s/%s → 选中=%s/%s (idx=%d)',
                                            cats[0].get('type_name'), cats[0].get('category_name'),
                                            cats[_pick_idx].get('type_name'),
                                            cats[_pick_idx].get('category_name'), _pick_idx)
                                cats = [cats[_pick_idx]] + [c for i, c in enumerate(cats) if i != _pick_idx]
                        except Exception:
                            pass
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
        # ⚠️ v0.39 Issue3: 从静默放行升级为可见告警——本地匹配不到类目直接放行上送，
        # 云端 self-learning 兜底不可靠时阻断上架（10/22 被阻断根因之一）。
        # 保留放行（不硬阻断，避免误伤），但必须可见，供用户/agent 决策。
        logger.warning(
            '⚠️ 未匹配到有效 Ozon 类目（search_text="%s"）——提交后云端可能阻断上架。'
            '建议用 `python3 scripts/cli.py category "<1688类目末级词>"` 核对类目后重提。',
            (category_query or title or "")[:60],
        )
        _log_task(task_id, 'ozon', 'category', 'warn',
                f'Category NOT resolved locally (search_text="{category_query or title}") — cloud may block upload',
                {'source_category_path': source_category_path})

    result['category_candidates'] = category_candidates
    result['category'] = resolved_category  # may be empty — pipeline handles this
    # ⚠️ v0.39 Issue3: 信封带 1688 类目面包屑，云端核对优先读它而非猜标题
    result['source_category_path'] = source_category_path

    _log_task(task_id, 'ozon', 'category', 'info',
            f'Category resolved: {resolved_category.get("description_category_id", "none") if resolved_category else "none"} conf={resolved_category.get("confidence", 0) if resolved_category else 0}',
            {'candidates': len(category_candidates)})

    # 4. Price estimate
    cost_cny = _parse_price(result['enriched'].get('price', ''))
    # ⚠️ v0.58: 默认重量/运费与 discover 选品分析同源（ozon_discovery.estimate_shipping_cny
    # 分段 6/8/15）——此前默认 500g → ¥6，discover 无重量落 ¥15，差 ¥9/单误判利润不足。
    from scripts.lib.ozon_discovery import DEFAULT_WEIGHT_G, estimate_shipping_cny
    weight_g = result['enriched'].get('weight_grams') or DEFAULT_WEIGHT_G
    est_shipping = estimate_shipping_cny(weight_g)
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
        except ImportError:
            return {"success": False, "error": "pipeline 模块未安装（旧版功能，已废弃）", "item_id": ""}

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
            mxou_token=_get_mxou_token() or _get_token(),
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
    else:
        result['note'] = 'poll=False — pipeline not run. Call with poll=True to run local pipeline.'
        result['ok'] = True
        result['stage'] = 'skipped'

    logger.info("Published: task_id=%s stage=%s", result['task_id'], result['stage'])
    return result


def check_task_status(task_id: str) -> dict[str, Any]:
    """Query current task status from Worker — single call, no polling.

    Calls Worker's GET /task_status/{task_id} endpoint.
    Returns: {task_id, status, ok, terminal, result_json, error_message}
    """
    import requests as _requests

    url = f"{_get_api_base()}/task_status/{task_id}"

    try:
        resp = _requests.get(url, timeout=10)
        if resp.status_code == 404:
            return {"task_id": task_id, "status": "not_found", "ok": False, "terminal": True}
        data = resp.json() if resp.ok else {}
    except _requests.exceptions.ConnectionError:
        return {"task_id": task_id, "status": "worker_unreachable", "ok": False, "terminal": False}
    except Exception as e:
        return {"task_id": task_id, "status": "query_error", "ok": False, "terminal": False, "error": str(e)[:200]}

    # Worker returns: {id, status, result, error_message, tenant_id, ...}
    status = data.get("status", "unknown")
    result = data.get("result") or {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            result = {}

    # Map Worker statuses to skill terminal statuses
    terminal = status in ("completed", "failed", "cancelled")
    ok = status == "completed"

    return {
        "task_id": task_id,
        "status": status,
        "ok": ok,
        "terminal": terminal,
        "error_message": data.get("error_message"),
        "result_json": result,
        # P1-4 --watch: Worker task_status 顶层 progress {stage, percent, ...}
        "progress": data.get("progress"),
        "retry_count": data.get("retry_count", 0),
        "started_at": data.get("started_at"),
        "completed_at": data.get("completed_at"),
    }


def poll_task_status(
    task_id: str,
    timeout: int = 900,
    on_status=None,
) -> dict[str, Any]:
    """轮询 Worker task_status 直到终态（P1-4 主动状态通知）。

    复用 check_task_status（每 10s 一次，返回同样的结构化 dict）：
    completed/failed/cancelled → 立即返回终态 dict；超时返回
    {status: "timeout", timeout_seconds, ...}（非终态）。

    on_status(result): 每次非终态轮询后回调（供 --watch 打印进度中间态，
    如 "⏳ running (35%)..."）；终态不回调（终态由返回值呈现）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = check_task_status(task_id)
        if r.get("terminal"):
            return r
        if on_status is not None:
            on_status(r)
        time.sleep(10)
    return {
        "task_id": task_id,
        "status": "timeout",
        "ok": False,
        "terminal": False,
        "timeout_seconds": timeout,
    }


def _translate_slug_to_cn(slug: str, mxou_token: str) -> str:
    """LLM 翻译俄语 slug → 中文 1688 搜索关键词，带 fallback."""
    if not mxou_token or not slug:
        return slug
    # Q7: LLM 翻译成本高（deepseek-v4-flash），同 slug 30 天内复用翻译结果
    from scripts.lib.cache import cache_get, cache_set
    _cached_kw = cache_get("slug_cn", slug)
    if _cached_kw is not None:
        return _cached_kw
    
    import requests as req
    
    # 截断过长 slug（deepseek-v4-flash reasoning tokens 消耗大，长输入易导致输出为空）
    slug_short = " ".join(slug.split()[:6]) if len(slug.split()) > 6 else slug
    
    for attempt, max_tok in enumerate([500, 400, 300]):
        try:
            cn_resp = req.post("https://api.mxou.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {mxou_token}", "Content-Type": "application/json"},
                json={"model": "deepseek-v4-flash", "messages": [
                    {"role": "system", "content": "将俄语产品名精确翻译为中文1688搜索关键词。保留规格数字。只返回3-5个关键词。"},
                    {"role": "user", "content": f"翻译: {slug_short}"}
                ], "temperature": 0, "max_tokens": max_tok}, timeout=30)
            if cn_resp.status_code == 200:
                data = cn_resp.json()
                choices = data.get("choices", [])
                if not choices:
                    continue
                content = choices[0].get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    kw = content.strip()[:200]  # cap to prevent LLM hallucination overflow
                    cache_set("slug_cn", slug, kw, ttl=30 * 24 * 3600)
                    return kw
        except Exception:
            if attempt == 0:
                slug_short = " ".join(slug.split()[:3])  # 第二次尝试用更短的
    return ""


def _search_1688_with_fallback(search_kw: str) -> list[dict[str, Any]]:
    """1688 AK 搜索，带 fallback：翻译词 → 原始 slug 关键词 → 缩短关键词."""
    from scripts.lib.ak_1688_client import search_products
    
    # 尝试 1: 用翻译后的关键词
    if search_kw:
        try:
            products = search_products(search_kw, page_size=5)
            if products:
                return products
        except AkAuthError as e:
            logger.error("1688 AK 认证失败（403），不再降级重试: %s", e)
            raise
        except Exception:
            pass
    
    # 尝试 2: 用 slug 中提取的关键词（取前 3 个有意义的词）
    slug_words = [w for w in search_kw.split() if len(w) > 2 and not w.isdigit()]
    if slug_words and len(slug_words) >= 2:
        try:
            products = search_products(" ".join(slug_words[:3]), page_size=5)
            if products:
                return products
        except AkAuthError as e:
            logger.error("1688 AK 认证失败（403），不再降级重试: %s", e)
            raise
        except Exception:
            pass
    
    return []


def _cached_ozon_scrape(
    url: str,
    *,
    cdp_url: str = "http://127.0.0.1:9222",
    timeout: int = 30,
    conn=None,
) -> dict[str, Any]:
    """CDP Ozon 商品页抓取磁盘缓存包装（v0.36，昂贵操作 6h 复用）。

    不改编译的 ozon_scraper.py，只在明文调用方包缓存。key = 原始 URL
    （URL 即语言维度：lang=ru/country=RU 参数内嵌），只缓存 success 结果，
    失败/未命中照常抓取。
    """
    from scripts.lib.cache import cache_get, cache_set

    url = str(url or "").strip()
    if not url:
        return {"success": False, "error": "空 Ozon URL"}
    cached = cache_get("ozon_cdp", url)
    if cached is not None:
        return cached
    from scripts.lib.ozon_scraper import scrape_ozon_product_via_cdp

    result = scrape_ozon_product_via_cdp(url, cdp_url=cdp_url, timeout=timeout, conn=conn)
    if result and result.get("success"):
        cache_set("ozon_cdp", url, result, ttl=21600)
    return result


def follow_sell_cloud(ozon_url: str, auto_submit: bool = False, store_id: str = "",
                      review: bool = False, notify: bool = False,
                      to_box: bool = False) -> dict[str, Any]:
    """
    跟卖 Ozon 商品 (v9: Skill 不调 Ozon API, import-by-sku 移到 Worker):
      1. CDP 抓取 Ozon 商品页 → 拿到竞品图片 + 标题
      2. LLM 翻译标题 → 1688 搜索同款
      3. CDP 探针 1688 → 采购成本 + 规格
      4. (auto_submit) 组装 GraphInput(follow_sell=true) → Worker 跟卖管线

    review: D3 L3 人工评审暂停——展示全部 1688 候选，人工接受/改选/拒绝；
    拒绝 → no_relevant_match（不组装信封不提交），决策写 review_log。

    Returns: {success, product_id, slug, images, title, 1688_matches, task_id}
    """
    from scripts.lib.config_store import _require_auth
    _require_auth()
    import re


    # Step 1: 解析 Ozon URL
    parsed = parse_ozon_url(ozon_url)
    if not parsed or not parsed.get("product_id"):
        return {"success": False, "error": "无法解析 Ozon URL"}

    product_id = parsed["product_id"]
    m = re.search(r'/products?/(?:([^/]+)-)?(\d{6,15})', ozon_url)
    slug = m.group(1).replace("-", " ") if m and m.group(1) else ""

    ozon_creds = _get_ozon_credentials(store_id)
    client_id = ozon_creds.get("client_id", "")
    api_key = ozon_creds.get("api_key", "")
    from scripts.lib.config_store import get_mxou_token
    mxou_token = get_mxou_token()
    
    result: dict[str, Any] = {
        "success": False, "product_id": product_id, "slug": slug,
        "images": [], "title": "", "category": "",
        "1688_matches": [],
    }
    # Q7: envelope 级缓存（6h）——同 product_id+store_id 的完整跟卖结果直接复用，
    # 命中且有 images+1688_matches 才返回（空壳/no_relevant_match 不缓存）
    from scripts.lib.cache import cache_get, cache_set
    _follow_cache_key = f"{product_id}:{store_id}"
    _cached_follow = cache_get("follow", _follow_cache_key)
    if _cached_follow and _cached_follow.get("images") and _cached_follow.get("1688_matches"):
        logger.info("♻️ follow 信封缓存命中: %s", _follow_cache_key)
        _cached_follow["from_cache"] = True
        if auto_submit and _cached_follow.get("envelope"):
            _env = dict(_cached_follow["envelope"])
            if notify:
                _env["notify"] = True
            _sub = submit_draft(_env) if to_box else submit_envelope(_env)
            _cached_follow["submit_result"] = _sub
            if to_box:
                _cached_follow["draft_id"] = _sub.get("draft_id", "")
                _cached_follow["degraded"] = _sub.get("degraded", False)
            else:
                _cached_follow["task_id"] = _sub.get("task_id", "")
        return _cached_follow
    
    # Step 2: CDP 抓取 Ozon 商品页 → 竞品图片 + 标题
    # ⚠️ v0.29.x 修复: 前置确保 Chrome 就绪 —— 命令出口会关闭工具 Chrome(独立
    # profile 方案), 下次 follow 若不 ensure, Ozon 抓取连不上 9222 → 空数据 →
    # 图搜用错图/文字搜索兜底(错配货源根因之一)。
    try:
        from scripts.lib.chrome_launcher import ensure_chrome_cdp
        from scripts.cli import _chrome_profile_dir
        # PR-4: 显式传统一 profile（profiles/1688/default），杜绝双轨登录态错位
        ensure_chrome_cdp(port=9222, profile_dir=_chrome_profile_dir())
    except Exception as e:
        logger.warning("ensure_chrome_cdp 失败(继续尝试抓取): %s", e)

    # ⚠️ v0.14 E5: Step 2（抓 Ozon）+ Step 3a（1688 图搜）共享一个 CdpConnection，
    # 省 2-3 个冗余 WS 连接（旧代码各建各的连接）。Step 5 envelope 链路（probe_1688_page）
    # 有独立会话引导/登录检查逻辑，保持独立更安全。
    ozon_images: list[str] = []
    ozon_title: str = ""
    shared_cdp = None
    try:
        from scripts.lib.cdp_client import CdpConnection
        shared_cdp = CdpConnection("http://127.0.0.1:9222")
    except Exception:
        pass

    try:
        # ✅ v0.36: 昂贵 CDP 抓取走磁盘缓存包装（_cached_ozon_scrape，6h）
        cdp_data = _cached_ozon_scrape(
            ozon_url, cdp_url="http://127.0.0.1:9222", timeout=30, conn=shared_cdp)
        if cdp_data.get("success"):
            ozon_images = cdp_data.get("images", [])
            ozon_title = cdp_data.get("title", "")
            # ⚠️ v0.14 P0-6: 抓取 Ozon 竞品售价（scraper 已解析 price 字段），
            # 供 Worker 跟卖定价用（避免误用 1688 采购价当竞品价）
            ozon_price = str(cdp_data.get("price", "") or "").strip()
            if ozon_price:
                result["competitor_price"] = ozon_price
                logger.info("💰 Ozon 竞品售价: %s", ozon_price)
            result["scrape_source"] = "cdp"
            # ✅ 从 Ozon 页面提取类目 ID（面包屑链接中的数字 ID，优先）
            scraped_dc = cdp_data.get("description_category_id", "")
            scraped_type = cdp_data.get("type_id", "") or scraped_dc
            scraped_lang = cdp_data.get("breadcrumb_language", "")
            scraped_path = cdp_data.get("category_path", "")
            if scraped_dc:
                result["ozon_category"] = {
                    "description_category_id": str(scraped_dc),
                    "type_id": str(scraped_type),
                    "language": scraped_lang,
                    "category_path": scraped_path,
                }
                logger.info("✅ Ozon 类目从页面提取: dc=%s type=%s lang=%s", scraped_dc, scraped_type, scraped_lang)
            logger.info("✅ CDP 抓取 Ozon 成功: %d 张图, title=%s", len(ozon_images), ozon_title[:60])
    except Exception as e:
        logger.debug("CDP Ozon scraper unavailable: %s", e)
    
    result["ozon_images_count"] = len(ozon_images)
    result["images"] = ozon_images
    if ozon_title:
        result["title"] = ozon_title

    # ── Step 2.5: 竞品运营数据 + 重量/尺寸（what_to_sell，卖家后台借道）──
    # v0.22（参考 maozi）：竞品重量(4497)/尺寸(9454/9455/9456)/月销/GMV 从
    # seller.ozon.ru what_to_sell 获取，1688 数据缺失时 worker 用它兜底。
    # 未登录 seller 后台 → 降级跳过，不阻断跟卖。
    if shared_cdp is not None:
        try:
            from scripts.lib.ozon_seller_analytics import fetch_sales_analytics
            _metrics_map = fetch_sales_analytics(shared_cdp, [str(product_id)])
            _m = _metrics_map.get(str(product_id)) or {}
            if _m.get("weight_g"):
                result["competitor_weight_g"] = int(_m["weight_g"])
            if _m.get("length_mm") or _m.get("width_mm") or _m.get("height_mm"):
                result["competitor_dimensions_mm"] = {
                    "length": int(_m.get("length_mm") or 0),
                    "width": int(_m.get("width_mm") or 0),
                    "height": int(_m.get("height_mm") or 0),
                }
            if _m.get("sold_count"):
                result["ozon_monthly_sales"] = int(_m["sold_count"])
            if _m.get("gmv_sum"):
                result["ozon_gmv"] = float(_m["gmv_sum"])
            if _m.get("create_days"):
                result["ozon_listing_days"] = int(_m["create_days"])
            # ✅ v0.26 权威类目覆盖（wave2 眉笔类目错配根因修复）：
            # what_to_sell 返回 Seller 空间权威类目 category2Id(dc)/category3Id(type)，
            # 页面面包屑只是 Widget 空间 ID（worker pg_trgm 猜 sim=0.353 误匹配
            # → DESCRIPTION_DECLINE 类目不符）。有权威 ID 时覆盖面包屑类目。
            if _m.get("category2_id") and _m.get("category3_id"):
                result["ozon_category"] = {
                    "description_category_id": str(_m["category2_id"]),
                    "type_id": str(_m["category3_id"]),
                    "language": "RU",
                    "category_path": (result.get("ozon_category") or {}).get("category_path", ""),
                }
                logger.info(
                    "✅ 竞品权威类目（Seller 空间）: dc=%s type=%s（覆盖 Widget 面包屑 %s）",
                    _m["category2_id"], _m["category3_id"],
                    (result.get("ozon_category") or {}).get("description_category_id"),
                )
            if result.get("competitor_weight_g") or result.get("competitor_dimensions_mm"):
                logger.info(
                    "✅ 竞品数据（what_to_sell）: weight=%s dims=%s sales=%s gmv=%s",
                    result.get("competitor_weight_g"), result.get("competitor_dimensions_mm"),
                    result.get("ozon_monthly_sales"), result.get("ozon_gmv"),
                )
        except Exception as _se:
            logger.debug("竞品 what_to_sell 获取失败（降级）: %s", _se)

    # Step 3: 1688 搜索（图片搜索优先，文字搜索为辅）
    search_text = ozon_title if ozon_title else slug
    matches_raw = []
    search_method = ""

    # 3a. 图片搜索（优先）— 用 Ozon 竞品主图搜1688同款，避免翻译歧义
    # ⚠️ 始终用第一张图（产品主图），不要按分辨率选图（后面的可能是场景图/细节图）
    if ozon_images:
        main_img = ozon_images[0]  # 第一张 = 产品主图
        logger.info("🔍 以图搜款: %s", main_img[:80])

        # 3a-0. aibuy mtop API 直调（v0.39 优先）— 免浏览器秒级返回结构化结果，
        # 官方排序精准（实测 guest 视图=精准图搜排序）。fail-fast：无 token/失败
        # 快速返回 [] 由下方 CDP/AK 降级承接，不阻塞。
        try:
            from scripts.lib.ozon_image_search import search_by_image_aibuy
            aibuy_results = search_by_image_aibuy(image_url=main_img, page_size=20)
            if aibuy_results:
                matches_raw = aibuy_results
                search_method = "aibuy"
                logger.info("✅ aibuy图搜命中 %d 个结果", len(matches_raw))
        except Exception as e:
            # ✅ W5.4 (I-8): 降级出声——debug 静默 → warning 带原因（为什么走 CDP）
            logger.warning("aibuy 图搜失败，降级 CDP 图搜: %s", e)

        # 3a-1. CDP 网页版以图搜款（aibuy 不可用时，用1688网页搜索引擎）
        if not matches_raw:
            # ⚠️ v0.14 E5+: 匹配质量差（badge 全 0/无有效评分）时自动重新图搜最多 3 次取最佳
            # （1688 图搜算法偶发匹配差，重搜可显著提高命中质量）
            # ✅ v0.19: page_size 20 + 仅在页面确实渲染了徽标且质量差时才重搜；
            # 无徽标（未登录/未渲染）不重搜，交给 _pick_best_match 标题相关性降级
            try:
                from scripts.lib.ozon_image_search import (
                    _get_badge_score,
                    search_by_image_cdp,
                )
                cdp_results = search_by_image_cdp(image_url=main_img, page_size=20, wait_seconds=10, conn=shared_cdp)
                # ✅ v0.19: CDP 空结果先原地重试 1 次（页面渲染偶发失败，甩脂机案例），
                # 仍空才降级 AK API
                if not cdp_results:
                    logger.info("🔄 CDP图搜空结果，等待后原地重试 1 次...")
                    time.sleep(3)
                    cdp_results = search_by_image_cdp(
                        image_url=main_img, page_size=20, wait_seconds=15,
                        conn=shared_cdp, force_refresh=True)
                if cdp_results:
                    badge_scores = [_get_badge_score(p.get("badge", "") or "") for p in cdp_results]
                    has_badge = any(s > 0 for s in badge_scores)
                    top_score = max(badge_scores, default=0)
                    _re_attempt = 0
                    while has_badge and top_score <= 1 and _re_attempt < 2:
                        _re_attempt += 1
                        logger.info(f"🔄 图搜匹配质量低(badge={top_score})，重新图搜 {_re_attempt}/2...")
                        retry_results = search_by_image_cdp(image_url=main_img, page_size=20, wait_seconds=15, conn=shared_cdp, force_refresh=True)
                        if not retry_results:
                            break
                        retry_score = max((_get_badge_score(p.get("badge", "")) for p in retry_results), default=0)
                        if retry_score > top_score:
                            cdp_results = retry_results
                            top_score = retry_score
                    if top_score > 1:
                        logger.info(f"✅ 重搜后图搜质量提升: badge={top_score}")
                if cdp_results:
                    matches_raw = cdp_results
                    search_method = "cdp"
                    logger.info("✅ CDP图搜命中 %d 个结果", len(matches_raw))
            except Exception as e:
                logger.debug("CDP image search failed: %s", e)

        # 3a-2. API 以图搜款（后备）
        if not matches_raw:
            try:
                from scripts.lib.ak_1688_client import search_by_image
                img_results = search_by_image(image_url=main_img, page_size=5, score_level="high")
                if img_results:
                    matches_raw = img_results
                    search_method = "image"
                    logger.info("✅ API图搜命中 %d 个结果", len(matches_raw))
            except AkAuthError as e:
                logger.error("1688 AK 认证失败（403），不再降级重试: %s", e)
                raise
            except Exception as e:
                logger.debug("图片搜索失败: %s", e)

    # 3b. 文字搜索（fallback）— LLM 翻译俄语标题 → 中文关键词
    if not matches_raw:
        search_kw = _translate_slug_to_cn(search_text, mxou_token)
        if not search_kw:
            search_kw = " ".join(search_text.split()[:4])
        result["search_keyword"] = search_kw
        matches_raw = _search_1688_with_fallback(search_kw)
        search_method = "text"
        logger.info("📝 文字搜索: %s", search_kw)

    result["search_method"] = search_method

    # Step 4: 整理搜索结果
    matches = []
    if matches_raw:
        # ✅ 保留 badge 评分（1688 图搜匹配质量）
        from scripts.lib.ozon_image_search import _get_badge_score

        matches = []
        for p in matches_raw:
            pid = p.get("product_id") or p.get("itemId") or str(p.get("id", ""))
            if not pid:
                continue
            badge_text = p.get("badge", "")
            badge_score = _get_badge_score(badge_text) if badge_text else 0
            _m = {
                "id": pid,
                "title": p.get("title", "")[:80],
                "price": p.get("price", ""),
                "image": p.get("image", ""),
                "badge": badge_text,
                "badge_score": badge_score,
            }
            # v0.39 aibuy 通道: 透传 normalization_score（trusted_source 放行信号辅助）
            if "normalization_score" in p:
                _m["normalization_score"] = p.get("normalization_score")
            matches.append(_m)

        # 按 badge_score 降序排列（最高分在前）
        matches.sort(key=lambda m: m["badge_score"], reverse=True)

        result["1688_matches"] = matches
        if matches:
            # ⚠️ v0.14 E5: 标题相关性护栏（复用 discover 的 _pick_best_match）
            # 旧逻辑只按 badge 排序取第一个 → 图搜匹配到不同产品也组装信封。
            # 现在用竞品标题（俄语）做相关性校验：badge "符合0/N" 跳过、RU→ZH 标题
            # 重叠打分、相关性过弱拒绝（返回 None → 不组装 envelope，宁缺毋滥）。
            from scripts.lib.ozon_discovery import _pick_best_match
            # ⚠️ v0.26: 传 mxou_token — 护栏边界时 LLM 语义判定（词对词典覆盖窄，
            # 「палочки от комаров 驱蚊棒」等无词对 → conf=0 误拒，修"匹配了却不选"）
            # ✅ v0.39: aibuy 来源 trusted_source=True（信任官方排序前 2 位放行），
            # CDP/AK 来源保持 False 维持原护栏
            _trusted = search_method == "aibuy"
            best = _pick_best_match(matches, ozon_title, token=mxou_token, trusted_source=_trusted) if ozon_title else matches[0]
            if best:
                result["best_match"] = best
                # ── D3 L3: 人工评审暂停（--review）──
                # 展示全部候选 + 自动最佳元数据；接受最佳 / 选序号改选 / n 拒绝全部。
                # 拒绝 → no_relevant_match=True（跳过信封组装+提交），决策写 review_log。
                if review:
                    from scripts.lib.review_log import write_review_record
                    print(f"\n🔍 人工评审（--review）：{len(matches)} 个 1688 候选", flush=True)
                    for _i, _m in enumerate(matches, 1):
                        print(f"  [{_i}] ¥{_m.get('price', '?')} badge={_m.get('badge', '')!r} "
                              f"{_m.get('title', '')[:48]}", flush=True)
                    print(f"  → 自动最佳: confidence={float(best.get('confidence', 0) or 0):.2f} "
                          f"badge_eff={float(best.get('badge_eff', 0) or 0):.2f} "
                          f"{best.get('title', '')[:48]}", flush=True)
                    _ans = input("  接受最佳 (回车/y) / 选序号 / n=拒绝全部: ").strip().lower()
                    if _ans in ("n", "no"):
                        write_review_record({
                            "task_id": "",
                            "product_id": product_id,
                            "ozon_title": ozon_title,
                            "match_title": best.get("title", ""),
                            "match_url": (f"https://detail.1688.com/offer/"
                                          f"{best.get('id', '')}.html"),
                            "confidence": float(best.get("confidence", 0) or 0),
                            "badge_eff": float(best.get("badge_eff", 0) or 0),
                            "score": best.get("score", 0),
                            "reject_reason": "agent_review_reject",
                            "decision": "agent_reject",
                            "image_urls": [best.get("image", "")] if best.get("image") else [],
                        })
                        result.pop("best_match", None)
                        result["no_relevant_match"] = True
                        logger.warning(
                            "⚠️ 人工评审拒绝全部候选（no_relevant_match），不组装信封不提交")
                    elif _ans.isdigit() and 1 <= int(_ans) <= len(matches):
                        result["best_match"] = matches[int(_ans) - 1]
                        logger.info("✅ 人工改选第 %s 个候选", _ans)
                logger.info("📊 图搜匹配质量: %d 个结果, 最佳 badge=%s (score=%d)",
                           len(matches), best.get("badge", "?"), best.get("badge_score", 0))
                # ⚠️ badge 仅 CDP 通道的 DOM 信号（可选参考，非硬指标）。aibuy/AK 通道
                # 无 badge（靠官方排序+norm），打「badge 评分仅 0」是误导性告警——
                # 只对 cdp 通道且 badge 确实弱时提示。
                if search_method == "cdp" and best.get("badge_score", 0) <= 1:
                    logger.warning("⚠️ 最佳匹配 badge 评分仅 %d，图搜可能不准确，建议人工核实", best.get("badge_score"))
            else:
                result["no_relevant_match"] = True
                logger.warning("⚠️ 图搜结果与竞品标题相关性过低，拒绝匹配（不组装信封）")

    # Step 5: 组装 envelope（跟卖标记）— 有相关匹配才组装，auto_submit 时才提交
    # ⚠️ v0.14 E5: 用相关性筛选后的 best_match（旧逻辑无脑取 matches[0]，不同产品也组装）
    if result.get("best_match"):
        best = result["best_match"]
        best_id = best.get("id", "")
        if best_id:
            try:
                detail_url = f"https://detail.1688.com/offer/{best_id}.html"
                # ⚠️ P4: 1688 api_only 图片可能为空 → 用 Ozon 竞品主图兜底放行图片
                # 校验门（draft.images 下方仍会被 Ozon 主图覆盖）；cdp 复用 shared 连接
                envelope = build_graph_envelope_with_retry(
                    item_id=best_id,
                    detail_url=detail_url,
                    store_id=store_id,
                    max_skus=1,
                    fallback_images=ozon_images[:1] if ozon_images else None,
                    cdp=shared_cdp,
                )
                if envelope and envelope.get("envelope"):
                    draft = envelope["envelope"].get("draft", {})
                    extensions = envelope["envelope"].get("extensions", {})
                    # 跟卖标记: Worker 走跟卖管线
                    draft["ozon_product_id"] = product_id
                    extensions["follow_sell"] = True
                    # ✅ v0.22（参考 maozi follow_type）: hand=防侵权跟卖（默认，
                    # 跳过 import-by-sku 1:1 复制，走 CREATE 重建——我们管线重做
                    # 类目/属性/生图，天然防同款/侵权检测）；api=import-by-sku 强制
                    extensions["follow_type"] = extensions.get("follow_type") or "hand"
                    # 注入定价参数
                    from scripts.lib.config_store import get_store_profile as _gsp
                    _sp = _gsp(store_id)
                    for _pk in ("margin_rate", "commission_rate", "fx_buffer"):
                        _pv = float(_sp.get(_pk, 0) or 0)
                        if _pv > 0:
                            extensions[_pk] = _pv
                    envelope["envelope"]["extensions"] = extensions
                    # 竞品图片 — 跟卖始终用 Ozon 竞品原图，绝不漏 1688 alicdn
                    # ✅ v0.33.1: 只拿第一张主图（对齐 1688 get_best_product_images 主图优先逻辑）
                    # ——竞品 104 张全塞会混入带品牌 logo/促销文字的细节图，Phase1 当参考图
                    # 被 AI 复刻（GardLuna 水印实测）。第一张 = 产品主图，相对干净。
                    draft["images"] = ozon_images[:1] if ozon_images else []
                    # ✅ 竞品俄语标题（覆盖 1688 中文标题，保留 SEO 优化后的竞品原标题）
                    if ozon_title:
                        draft["title"] = ozon_title
                    # ✅ v0.25: 竞品 Ozon 属性表透传（Пол/Размер/Цвет/Тип 等俄语键值，
                    # 供 worker 必填字典属性优先填充，避免从 1688 推断/缺值）
                    _ozon_attrs = cdp_data.get("attributes") or {}
                    if _ozon_attrs:
                        draft["ozon_attributes"] = _ozon_attrs
                    _full = cdp_data.get("characteristics") or []
                    _attrs_all = dict(_ozon_attrs)
                    for _fc in _full:
                        if isinstance(_fc, dict) and _fc.get("title") and _fc.get("value"):
                            _attrs_all.setdefault(str(_fc["title"]), str(_fc["value"]))
                    if _attrs_all:
                        draft["ozon_attributes"] = _attrs_all
                    # ✅ PR-5: follow 也透传竞品类目 dc（与 graph --ozon-ref-url 一致）。
                    # worker ozon_attrs_allowed 对显式 category 做一致性校验，
                    # 防 what_to_sell 类目与页面面包屑类目漂移时跨类目属性错配。
                    _oz_cat_dc = (result.get("ozon_category") or {}).get("description_category_id") or ""
                    if str(_oz_cat_dc).isdigit():
                        draft["ozon_attributes_category"] = int(_oz_cat_dc)
                    if not any("цвет" in k.lower() or "颜色" in k for k in _attrs_all):
                        _aspects = cdp_data.get("aspects") or []
                        _color_ru = next(
                            (str(a) for a in _aspects if any(
                                c in str(a).lower() for c in
                                ("черн", "бел", "сер", "син", "красн", "зелен", "розов", "беж", "коричн", "золот")
                            )), ""
                        )
                        if _color_ru:
                            draft["ozon_attributes"]["Цвет"] = _color_ru
                    if not result.get("competitor_weight_g") or not result.get("competitor_dimensions_mm"):
                        try:
                            from scripts.lib.ozon_scraper import extract_weight_dims_from_attrs
                            _wd_w, _wd_d = extract_weight_dims_from_attrs(_attrs_all)
                            if _wd_w:
                                result.setdefault("competitor_weight_g", _wd_w)
                            if _wd_d:
                                result.setdefault("competitor_dimensions_mm", _wd_d)
                        except Exception:
                            pass
                    # ✅ Ozon 类目 ID（从竞品页面提取，Worker 跳过 1688 类目匹配）
                    ozon_cat = result.get("ozon_category")
                    if ozon_cat:
                        draft["ozon_category"] = ozon_cat
                    # ⚠️ v0.14 P0-6: 注入 Ozon 竞品售价（独立字段，避免与 1688 采购价 draft.price 混淆）
                    comp_price = result.get("competitor_price", "")
                    if comp_price:
                        draft["competitor_price"] = comp_price
                    # ✅ v0.19.1 P1: 竞品信息透传（可选字段，GraphInput envelope 为 Dict 无 extra 限制，
                    # Worker 忽略未知字段，契约兼容）。供后续蓝海评分/展示使用。
                    # ✅ v0.27: 竞品信息只透传 worker 兜底用的物理字段(重量/尺寸);
                    # 运营字段(月销/GMV/评分/跟卖数等)属 agent 判定层, 移出信封(ENVELOPE-STANDARD)
                    for _info_key in (
                        # worker assemble 兜底消费: 1688 物理数据缺失时用竞品值
                        "competitor_weight_g", "competitor_dimensions_mm",
                    ):
                        _info_val = result.get(_info_key)
                        if _info_val not in (None, "", [], {}):
                            extensions[_info_key] = _info_val
                    # 凭证（顶层）
                    envelope["ozon_client_id"] = client_id
                    envelope["ozon_api_key"] = api_key
                    envelope["token"] = mxou_token
                    result["envelope"] = envelope
                    result["envelope_built"] = True
                    # ⚠️ P4: success 必须在提交之后才置位——图搜命中 ≠ 上架成功
                    if auto_submit:
                        if notify:
                            envelope["notify"] = True
                        submit_res = submit_draft(envelope) if to_box else submit_envelope(envelope)
                        result["submit_result"] = submit_res
                        if to_box:
                            result["draft_id"] = submit_res.get("draft_id", "")
                            result["degraded"] = submit_res.get("degraded", False)
                            result["success"] = bool(submit_res.get("ok")) and bool(
                                submit_res.get("draft_id") or submit_res.get("task_id")
                            )
                        else:
                            result["task_id"] = submit_res.get("task_id", "")
                            result["success"] = bool(submit_res.get("ok")) and bool(submit_res.get("task_id"))
                    else:
                        # dry-run：仅组装信封，构建成功即算成功
                        result["success"] = True
                else:
                    result["envelope_error"] = "build_graph_envelope 返回空"
                    result["success"] = False
            except Exception as e:
                result["envelope_error"] = str(e)
                result["success"] = False
    elif result.get("no_relevant_match"):
        # ⚠️ v0.26 FIX: 图搜无 1688 货源匹配 → 不再 api 强制跟卖（import-by-sku 复制竞品卡）。
        # 原逻辑（v0.22）组装「无采购价/无 1688 属性」空壳信封提交 → worker 定价/属性全缺
        # （Ozon 实证：无货源 api 跟卖提交空壳，价格 0/属性缺失被拒）。
        # 决策（用户确认）：无货源必须拦截，不丢单也不上空壳。
        result["blocked_reason"] = "no_relevant_match"
        result["envelope_built"] = False
        result["api_fallback"] = False
        logger.warning(
            "⛔ 图搜无 1688 货源匹配，拦截提交（no_relevant_match）。"
            "候选列表中有相关商品但相关性护栏拒绝——可调低护栏或改用 LLM 语义判定"
        )

    # ⚠️ v0.14 E5: 收尾关闭共享连接（Step 2/3a 用完后释放）
    if shared_cdp is not None:
        try:
            shared_cdp.close()
        except Exception:
            pass

    # Q7: 成功且有图+匹配才写 envelope 缓存（失败/拦截结果不缓存，避免毒化后续复用）
    if result.get("success") and result.get("images") and result.get("1688_matches"):
        cache_set("follow", _follow_cache_key, result, ttl=21600)
        logger.info("💾 follow 信封缓存写入: %s (%d 图, %d 匹配)",
                    _follow_cache_key, len(result["images"]), len(result["1688_matches"]))

    return result

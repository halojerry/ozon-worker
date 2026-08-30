"""账号级租户解析(PRD M2):key 仅鉴权,租户 = Supabase tokens.user_id。

- 已配置 Supabase:key → user_id(30-60s LRU);查询失败 fail-closed 503。
- 未配置(本地/测试):回退 key 哈希派生,保持旧行为,测试夹具无需全量改。
- get_supabase 为可替换获取器(默认 storage 单例),测试可 patch。
- resolve_analytics_scope:读端点 scope {tenant_id, is_admin};角色查询失败按非 admin。
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time

from fastapi import HTTPException

from storage.database.supabase_client import get_supabase_client as _storage_get_supabase

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60

_tenant_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()

# 可被测试替换的 Supabase 获取器
get_supabase = _storage_get_supabase


def key_derived_tenant(clean_token: str) -> str:
    """回退租户:key 哈希派生(M2 前行为;本地/未配置 Supabase 时使用)。"""
    return f"user_{hashlib.sha256(clean_token.encode()).hexdigest()[:16]}"


def _clean_token(token: str) -> str:
    return token[3:] if token.startswith("sk-") else token


def resolve_tenant(token: str) -> str:
    """token → user_id;未配置 Supabase → key 派生;已配置查询失败 fail-closed 503。"""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean = _clean_token(token)
    supabase = get_supabase()
    if supabase is None:
        return key_derived_tenant(clean)
    now = time.time()
    with _cache_lock:
        hit = _tenant_cache.get(clean)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    try:
        rows = (
            supabase.table("tokens")
            .select("user_id")
            .eq("key", clean)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        if not rows.data or not rows.data[0].get("user_id"):
            raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
        user_id = str(rows.data[0]["user_id"])
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("租户解析失败(fail-closed): %s", str(exc)[:200])
        raise HTTPException(status_code=503, detail="service_unavailable")
    with _cache_lock:
        _tenant_cache[clean] = (now, user_id)
    return user_id


def resolve_analytics_scope(token: str) -> dict:
    """analytics 读端点 scope:{tenant_id, is_admin};未配置 Supabase → 本地非 admin。"""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean = _clean_token(token)
    supabase = get_supabase()
    if supabase is None:
        return {"tenant_id": key_derived_tenant(clean), "is_admin": False}
    try:
        rows = (
            supabase.table("tokens")
            .select("user_id,status")
            .eq("key", clean)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        if not rows.data or int(rows.data[0].get("status", 0)) != 1 or not rows.data[0].get("user_id"):
            raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
        user_id = str(rows.data[0]["user_id"])
        is_admin = False
        try:
            urows = supabase.table("users").select("role").eq("id", user_id).limit(1).execute()
            if urows.data:
                from services.admin_service import is_admin_role
                is_admin = is_admin_role(urows.data[0].get("role"))
        except Exception:
            is_admin = False  # 角色查询失败按非 admin,不阻断读
        return {"tenant_id": user_id, "is_admin": is_admin}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("analytics scope 解析失败(fail-closed): %s", str(exc)[:200])
        raise HTTPException(status_code=503, detail="service_unavailable")


def clear_cache() -> None:
    """测试用:清空租户缓存。"""
    with _cache_lock:
        _tenant_cache.clear()

"""通用磁盘缓存模块。

基于 config_store.py 的 auth_cache 模式。
每个命名空间一个子目录，每个 key 一个 JSON 文件。

用法:
    from scripts.lib.cache import cache_get, cache_set

    data = cache_get("1688", "767909843908")
    if data is None:
        data = fetch_from_api()
        cache_set("1688", "767909843908", data, ttl=86400)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

from scripts._const import CACHE_DIR

logger = logging.getLogger(__name__)


def _cache_dir(namespace: str):
    d = CACHE_DIR / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(namespace: str, key: str):
    key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
    return _cache_dir(namespace) / f"{key_hash}.json"


def cache_get(namespace: str, key: str) -> dict | None:
    """查询缓存。未命中或过期返回 None。"""
    path = _cache_path(namespace, key)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if time.time() > record.get("expires_at", 0):
            path.unlink(missing_ok=True)
            return None
        logger.debug("Cache hit: %s/%s (age=%.0fs)", namespace, key[:20],
                      time.time() - record.get("created_at", 0))
        return record.get("value")
    except Exception:
        return None


def cache_set(namespace: str, key: str, data: Any, ttl: int = 86400) -> None:
    """写入缓存，TTL 单位秒。"""
    path = _cache_path(namespace, key)
    record = {
        "key": key,
        "value": data,
        "created_at": time.time(),
        "expires_at": time.time() + ttl,
    }
    try:
        # ⚠️ v0.14 E3: 原子写 — 临时文件 + os.replace（旧代码直接 write_text 覆写，
        # 并发 CLI 进程可能写坏 JSON 半截文件 → 缓存失效）
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
        try:
            os.replace(tmp_path, path)
        except OSError:
            # Windows 文件锁重试
            time.sleep(0.05)
            os.replace(tmp_path, path)
        logger.debug("Cache set: %s/%s (ttl=%ds)", namespace, key[:20], ttl)
    except Exception as e:
        logger.debug("Cache write failed: %s/%s: %s", namespace, key[:20], e)


def cache_clear(namespace: str | None = None) -> int:
    """清理缓存。namespace=None 清理全部。返回清理文件数。"""
    target = CACHE_DIR / namespace if namespace else CACHE_DIR
    count = 0
    if target.exists():
        for f in target.rglob("*.json"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
    return count


def cache_stats() -> dict[str, Any]:
    """返回缓存统计。"""
    stats: dict[str, Any] = {}
    if CACHE_DIR.exists():
        for ns_dir in CACHE_DIR.iterdir():
            if ns_dir.is_dir():
                files = list(ns_dir.glob("*.json"))
                valid = expired = 0
                for f in files:
                    try:
                        rec = json.loads(f.read_text(encoding="utf-8"))
                        if time.time() > rec.get("expires_at", 0):
                            expired += 1
                        else:
                            valid += 1
                    except Exception:
                        expired += 1
                stats[ns_dir.name] = {"total": len(files), "valid": valid, "expired": expired}
    return stats

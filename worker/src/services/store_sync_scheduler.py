"""店铺数据自动同步调度器(PRD M1 任务化)。

STORE_SYNC_JOBS_ENABLED=1(默认): 任务化调度 —
  每 STORE_SYNC_DISPATCH_SECONDS(默认 5s)扫描 due 店 → store_sync_jobs 入队 →
  worker 池(STORE_SYNC_MAX_CONCURRENT,默认 3)认领执行
  (唯一部分索引 + FOR UPDATE SKIP LOCKED + advisory lock 防并发)→
  job 状态/进度/错误落 PG,前端可轮询;zombie(running 超 30min)自动重置。
STORE_SYNC_JOBS_ENABLED=0: 回退旧版 15min 全局轮询(临时兜底,下一版移除)。
"""
from __future__ import annotations

import asyncio
import logging
import os

from services import store_sync_jobs, store_sync_service

logger = logging.getLogger(__name__)

DISPATCH_INTERVAL_SECONDS = float(os.getenv("STORE_SYNC_DISPATCH_SECONDS", "5"))
MAX_CONCURRENT = int(os.getenv("STORE_SYNC_MAX_CONCURRENT", "3"))


def jobs_enabled() -> bool:
    """任务化调度特性开关(PRD §2 回滚:0 → 旧循环)。"""
    return os.getenv("STORE_SYNC_JOBS_ENABLED", "1") == "1"


def _classify_error(exc: Exception) -> str:
    """异常 → error_code(invalid_key/rate_limited/ozon_api_error/network/timeout/internal)。"""
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", 0)
        if code in (401, 403):
            return "invalid_key"
        if code == 429:
            return "rate_limited"
        if code >= 500:
            return "ozon_api_error"
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "connection" in msg or "max retries" in msg:
        return "network"
    return "internal"


def _run_job(job: dict) -> None:
    """执行一个同步 job:sync_store(订单+商品,逐域容错)→ 水位/失败统计 → finish。"""
    tenant, cid, jid = job["tenant_id"], job["credential_id"], job["id"]
    try:
        # initial/manual 强制全域;定时增量各域按自身水位节流
        result = store_sync_service.sync_store(
            tenant, cid, force_domains=job["kind"] in ("initial", "manual"))
        orders = result.get("orders") or {}
        products = result.get("products") or {}
        store_sync_jobs.update_progress(
            jid,
            orders_synced=int(orders.get("synced") or 0),
            products_synced=int(products.get("synced") or 0),
            progress=100,
        )
        errs = [str(e) for e in (orders.get("error"), products.get("error")) if e]
        if errs:
            store_sync_jobs.mark_sync_failure(tenant, cid)
            store_sync_jobs.finish(jid, status="failed",
                                   error="; ".join(errs)[:500], error_code="ozon_api_error")
        else:
            store_sync_jobs.mark_sync_success(tenant, cid, jid)
            store_sync_jobs.finish(jid, status="ok")
    except Exception as exc:
        logger.warning("同步 job 异常 tenant=%s store=%s job=%s: %s",
                       tenant, cid, jid, str(exc)[:200])
        store_sync_jobs.mark_sync_failure(tenant, cid)
        store_sync_jobs.finish(jid, status="failed",
                               error=str(exc)[:500], error_code=_classify_error(exc))


async def _dispatch_once() -> None:
    """一轮:zombie 恢复 → due 入队 → worker 池认领执行(阻塞调用走 to_thread)。"""
    try:
        store_sync_jobs.zombie_reset()
    except Exception as exc:
        logger.warning("zombie 恢复异常(不阻断): %s", str(exc)[:200])
    # PRD M4b: 到点定时上架
    try:
        from services.draft_service import process_scheduled_listings
        await process_scheduled_listings(limit=20)
    except Exception as exc:
        logger.warning("定时上架处理异常(不阻断): %s", str(exc)[:200])
    for due in store_sync_jobs.due_credentials():
        kind = "continuation" if due["incomplete"] else "incremental"
        try:
            job = store_sync_jobs.enqueue(due["tenant_id"], due["credential_id"],
                                          kind=kind, trigger="scheduler")
            logger.info("调度入队 tenant=%s store=%s kind=%s job=%s",
                        due["tenant_id"], due["credential_id"], kind, job["id"])
        except Exception as exc:
            logger.warning("调度入队失败 tenant=%s store=%s: %s",
                           due["tenant_id"], due["credential_id"], str(exc)[:200])

    async def _worker() -> None:
        while True:
            job = await asyncio.to_thread(store_sync_jobs.claim_next)
            if job is None:
                break
            await asyncio.to_thread(_run_job, job)

    workers = [asyncio.create_task(_worker()) for _ in range(MAX_CONCURRENT)]
    await asyncio.gather(*workers)


async def store_sync_jobs_loop(stop_event: asyncio.Event | None = None) -> None:
    """任务化调度循环:每 DISPATCH_INTERVAL_SECONDS 一轮;stop_event 置位即退出。"""
    while True:
        try:
            await _dispatch_once()
        except Exception as exc:
            logger.warning("同步任务调度异常(不退出): %s", str(exc)[:200])
        try:
            await asyncio.wait_for(
                (stop_event.wait() if stop_event else asyncio.sleep(DISPATCH_INTERVAL_SECONDS)),
                timeout=DISPATCH_INTERVAL_SECONDS,
            )
            if stop_event and stop_event.is_set():
                return
        except asyncio.TimeoutError:
            continue


# ── 旧版(特性开关=0 时回退,下一版移除) ─────────────────────────────

SYNC_INTERVAL_SECONDS = int(os.getenv("STORE_SYNC_INTERVAL", "900"))
SYNC_STORE_GAP_SECONDS = float(os.getenv("STORE_SYNC_GAP", "2"))


def _all_active_credentials() -> list[tuple[str, str]]:
    from sqlalchemy import text
    from storage.database.db import get_engine
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT tenant_id, id::text FROM credentials "
            "WHERE status='active' ORDER BY tenant_id"
        )).fetchall()
    return [(str(r.tenant_id), str(r.id)) for r in rows]


async def sync_all_now() -> dict:
    """旧版:遍历全部 active 凭证逐店同步(特性开关=0 或运维手动触发)。"""
    creds = _all_active_credentials()
    results: dict[str, int] = {"stores": len(creds), "ok": 0, "failed": 0}
    for tenant_id, credential_id in creds:
        try:
            await asyncio.to_thread(
                store_sync_service.sync_store, tenant_id, credential_id)
            results["ok"] += 1
        except Exception as exc:
            results["failed"] += 1
            logger.warning("自动同步失败 tenant=%s store=%s: %s",
                           tenant_id, credential_id, str(exc)[:200])
        await asyncio.sleep(SYNC_STORE_GAP_SECONDS)
    if creds:
        logger.info("店铺自动同步完成: %d/%d 店成功", results["ok"], len(creds))
    return results


async def store_sync_loop(stop_event: asyncio.Event | None = None) -> None:
    """旧版后台循环(每 SYNC_INTERVAL_SECONDS 一轮)。"""
    while True:
        try:
            await sync_all_now()
        except Exception as exc:
            logger.warning("自动同步循环异常(不退出): %s", str(exc)[:200])
        try:
            await asyncio.wait_for(
                (stop_event.wait() if stop_event else asyncio.sleep(SYNC_INTERVAL_SECONDS)),
                timeout=SYNC_INTERVAL_SECONDS,
            )
            if stop_event and stop_event.is_set():
                return
        except asyncio.TimeoutError:
            continue

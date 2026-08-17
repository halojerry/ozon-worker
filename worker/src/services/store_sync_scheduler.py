"""v0.56: 店铺数据自动同步调度器 — 每 15 分钟遍历全部租户 active 凭证逐店同步。

设计：
- 循环间隔 SYNC_INTERVAL_SECONDS（默认 900s，env STORE_SYNC_INTERVAL 可覆盖）
- 遍历 credentials 表全部 active 行（跨租户）→ 逐店 sync_store（订单+商品）
- 店间 SYNC_STORE_GAP_SECONDS（默认 2s）错峰，防 Ozon 限流（info/list 高频静默限流）
- 单店失败不中断循环（sync_store 内部已逐项容错 + 记录错误状态）
- 手动触发入口 sync_all_now()：调度器在跑时也可调（互不干扰，各自遍历）
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from services import store_sync_service
from storage.database.db import get_engine

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = int(os.getenv("STORE_SYNC_INTERVAL", "900"))
SYNC_STORE_GAP_SECONDS = float(os.getenv("STORE_SYNC_GAP", "2"))


def _all_active_credentials() -> list[tuple[str, str]]:
    """全部租户的 active 凭证 → [(tenant_id, credential_id)]（跨租户遍历）。"""
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT tenant_id, id::text FROM credentials "
            "WHERE status='active' ORDER BY tenant_id"
        )).fetchall()
    return [(str(r.tenant_id), str(r.id)) for r in rows]


async def sync_all_now() -> dict:
    """遍历全部租户 active 凭证逐店同步（可手动触发，返回汇总）。"""
    creds = _all_active_credentials()
    results: dict[str, int] = {"stores": len(creds), "ok": 0, "failed": 0}
    for tenant_id, credential_id in creds:
        try:
            # 同步是阻塞 IO（requests）——丢线程池，避免卡事件循环
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
    """后台循环：每 SYNC_INTERVAL_SECONDS 同步一轮；stop_event 置位即退出。"""
    while True:
        try:
            await sync_all_now()
        except Exception as exc:
            logger.warning("自动同步循环异常（不退出）: %s", str(exc)[:200])
        try:
            await asyncio.wait_for(
                (stop_event.wait() if stop_event else asyncio.sleep(SYNC_INTERVAL_SECONDS)),
                timeout=SYNC_INTERVAL_SECONDS,
            )
            if stop_event and stop_event.is_set():
                return
        except asyncio.TimeoutError:
            continue

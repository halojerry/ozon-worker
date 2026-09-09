"""后台循环单实例锁（E-4，2026-09-09 审计）。

多副本部署时 asyncio 后台循环（定期清理 / 旧版店铺同步轮询 / 指标日聚合）会在
每个副本各跑一份——计数/聚合型循环会重复清理、重复聚合（任务队列循环不受影响：
task_processor 与 store_sync_jobs 已用 FOR UPDATE SKIP LOCKED 多副本安全）。

机制：PG **会话级** advisory lock——启动时 pg_try_advisory_lock 抢锁，抢到者持有
**独立连接**（不归还连接池，还池即释放）至进程退出；连接断开 PG 自动释放锁，
未抢到者跳过该循环。

单副本部署（现状 docker compose）恒抢成功，零行为变化。

fail-open 口径：抢锁过程任何异常（PG 暂不可用等）→ 放行启动。宁可极端情况
重复跑维护循环，不可因锁面故障停掉清理/聚合。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 已持有的连接（进程生命周期）：{name: connection}
_held: Dict[str, Any] = {}

# 锁 key 分配：base 高位为 'OZ'（0x4F5A），低位自定义，避免与其他 advisory lock 撞键
# 0x01 periodic_task_cleanup / 0x02 store_sync 旧版全局轮询 / 0x03 metrics 日聚合
_LOCK_BASE = 0x4F5A0000

PERIODIC_CLEANUP = _LOCK_BASE | 0x01
STORE_SYNC_LEGACY = _LOCK_BASE | 0x02
METRICS_AGGREGATION = _LOCK_BASE | 0x03


def try_acquire_instance_lock(name: str, key: int) -> bool:
    """尝试抢进程级实例锁。

    Returns:
        True：锁已归本进程（新抢到，或此前已持有）→ 可启动循环；
        False：其他副本持有 → 本实例跳过该循环。
        异常（PG 不可用等）→ True（fail-open，见模块 docstring）。
    """
    if name in _held:
        return True
    try:
        from storage.database.db import get_engine
        conn = get_engine().connect()  # 独立连接持锁至进程退出，不还池
        try:
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": key},
            ).scalar()
        except Exception:
            conn.close()
            raise
        if got:
            _held[name] = conn
            logger.info("🔒 实例锁 [%s] 已持有（key=%s）", name, hex(key))
            return True
        conn.close()
        logger.info("⏭️ 实例锁 [%s] 被其他副本持有，本实例跳过该后台循环", name)
        return False
    except Exception as e:
        logger.warning("⚠️ 实例锁 [%s] 抢锁异常（fail-open 放行启动）: %s", name, e)
        return True

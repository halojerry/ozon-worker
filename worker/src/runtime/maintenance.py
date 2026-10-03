"""周期维护族：定期清理循环 + 节流清扫钩子 + checkpoint 三表清理（W3c 自 main.py 迁出）。

此前本族代码住在 main 模块顶层，`_periodic_task_cleanup` 是 lifespan 的
``asyncio.create_task`` 目标（进程生命周期任务）。本模块是唯一权威；
main.py 不再保留同名 re-export（消费方仅 lifespan 与本模块——存量测试
patch 靶点已随迁，见 tests/test_cache_expiry_sweep.py 等）。

迁移纪律（零行为变化）：SQL/节流/失败语义逐字随迁。⚠️ 本文件 SQL 为
main.py 历史豁免形态（``text(':bind')`` 命名绑定）的原样搬家——若 Mimosa
安全扫描对本新文件报 SQL 规则，按 AGENTS.md 唯一放行形态（静态 SQL +
``%s`` 占位 + 参数元组经 ``exec_driver_sql``）改造，勿改语义（runtime/progress.py
是改造范本）。

- 节流时间戳是模块级可变全局（epoch 秒；0.0 = 启动后首轮即执行）；
  测试直接 monkeypatch 本模块 ``_LAST_CACHE_SWEEP`` / ``_LAST_FX_REFRESH``。
- ``_periodic_task_cleanup`` 内的 ``from storage.database.db import get_engine``、
  ``from utils.task_image_cache import cleanup_old`` 保持函数内延迟导入
  （测试 patch ``storage.database.db.get_engine`` 的既有靶点依赖每次调用重读）。
"""

import asyncio
import os
import time

from runtime.progress import _purge_stale_progress
from utils.logger import get_logger

logger = get_logger(__name__)

# B2a-5/6: 两个每日级维护钩子的节流时间戳（epoch 秒；0.0 = 启动后首轮即执行）
_LAST_CACHE_SWEEP: float = 0.0
_LAST_FX_REFRESH: float = 0.0

# 过期缓存清扫覆盖面：只扫两缓存表——category_cache 是类目树快照，另有治理，勿加入
_SWEEP_CACHE_TABLES = ("dictionary_value_cache", "attribute_cache")
_SWEEP_BATCH_LIMIT = 5000  # 每批 ctid 删除上限（防长事务锁表）
_SWEEP_MAX_BATCHES = 20    # 单表迭代批数封顶（防病态大量过期行拖死清理循环）

# ✅ v0.77.2（运维修复）: store_metrics_history 保留策略默认天数——_append_metrics_snapshot
# 每次同步 append 一条快照，是唯一高速增长表（生产 15,557 行 / ~650 行/店/天，无保留策略）。
# env STORE_METRICS_RETENTION_DAYS 可覆盖；非法值回落 90。
STORE_METRICS_RETENTION_DAYS = 90
_STORE_METRICS_RETENTION_ENV = "STORE_METRICS_RETENTION_DAYS"


def _store_metrics_retention_days() -> int:
    """保留天数（运行时读 env，故 ops 改环境变量即生效；非法值回落 90）。"""
    try:
        days = int(os.getenv(_STORE_METRICS_RETENTION_ENV, str(STORE_METRICS_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return STORE_METRICS_RETENTION_DAYS
    return days if days >= 1 else STORE_METRICS_RETENTION_DAYS


def _sweep_store_metrics_history(conn, retention_days: int | None = None) -> int:
    """删 snapshot_at 早于 NOW()-N 天的店铺指标快照，返回删除行数（v0.77.2）。

    实现细节：
    - 天数经 int() 强转后传入 **绑定参数**，SQL 用 ``make_interval(days => :days)``
      组装——不用 f-string 拼字面量（避免 sqlalchemy text() 裸 cast 家族坑，见
      AGENTS 记忆 sqlalchemy-jsonb-cast-trap；也免注入面）。
    - 幂等：删过的行不再命中 → 每轮清理循环调用安全。
    - 失败由调用方（:func:`_maybe_sweep_store_metrics`）处理——本函数把异常如实抛出。
    """
    from sqlalchemy import text

    days = retention_days if retention_days is not None else _store_metrics_retention_days()
    days = max(1, int(days))
    res = conn.execute(
        text(
            "DELETE FROM store_metrics_history "
            "WHERE snapshot_at < NOW() - make_interval(days => :days)"
        ),
        {"days": days},
    )
    return int(res.rowcount or 0)


def _maybe_sweep_store_metrics(conn) -> int:
    """每轮清理 store_metrics_history 超期快照（默认 90 天，v0.77.2）。

    - 每轮（非节流）执行——单语句 DELETE 成本低，且增长最快表需及时回收；
    - **SAVEPOINT（begin_nested）隔离**：sweep 失败只回滚本语句，绝不污染同轮
      r1/r1f/r2/r3 等其它清理写入，也不中止主循环；
    - 失败仅 warning（非致命），返回 0。
    """
    try:
        with conn.begin_nested():
            n = _sweep_store_metrics_history(conn)
        if n:
            logger.info(
                f"🧹 定期清理: {n} 行过期店铺指标快照已删除"
                f"（store_metrics_history > {_store_metrics_retention_days()} 天）"
            )
        return n
    except Exception:
        logger.warning("store_metrics_history 保留清理失败（非致命，下轮重试）", exc_info=True)
        return 0


def _sweep_expired_caches(conn) -> int:
    """物理删除两缓存表的过期行（expires_at 为 int 秒），返回删除总数。

    ctid 批删（LIMIT 5000/批，删空一批再看下一批）；表名来自固定元组非用户
    输入，f-string 拼接安全；绑定参数纯 int 无需 CAST。
    """
    from sqlalchemy import text
    total = 0
    now = int(time.time())
    for table in _SWEEP_CACHE_TABLES:
        for _ in range(_SWEEP_MAX_BATCHES):
            res = conn.execute(text(
                f"DELETE FROM {table} WHERE ctid IN "
                f"(SELECT ctid FROM {table} WHERE expires_at < :now LIMIT {_SWEEP_BATCH_LIMIT})"
            ), {"now": now})
            deleted = int(res.rowcount or 0)
            total += deleted
            if deleted < _SWEEP_BATCH_LIMIT:
                break
    return total


def _maybe_sweep_caches(conn) -> None:
    """每 24h 一轮的过期缓存清扫（B2a-5）。整体非致命：失败吞掉且不推进节流，
    下一轮清理循环自然重试。"""
    global _LAST_CACHE_SWEEP
    if time.time() - _LAST_CACHE_SWEEP <= 86400:
        return
    try:
        deleted = _sweep_expired_caches(conn)
        conn.commit()
        _LAST_CACHE_SWEEP = time.time()
        if deleted:
            logger.info(f"🧹 定期清理: 过期缓存清扫删除 {deleted} 行"
                        f"（dictionary_value_cache/attribute_cache，expires_at 已过期）")
    except Exception:
        logger.warning("过期缓存清扫失败（非致命，下轮重试）", exc_info=True)


def _maybe_refresh_fx() -> None:
    """fx 汇率每日刷新钩子（B2a-6）。lazy import 与并行开发的
    utils.fx_rate_service 解耦（缺失/失败都不影响清理主循环）；
    失败也推进节流——防清理循环每分钟 warning 刷屏。"""
    global _LAST_FX_REFRESH
    if time.time() - _LAST_FX_REFRESH <= 86400:
        return
    try:
        from utils.fx_rate_service import refresh_cny_rub_if_due
        refresh_cny_rub_if_due()
    except Exception:
        logger.warning("fx refresh failed", exc_info=True)
    finally:
        _LAST_FX_REFRESH = time.time()


# ✅ v0.75 C2（BL-25 Phase 1-2 漏项）: langgraph checkpoint 三表清理序——
# thread_id == ozon_product_tasks.id（task_processor.py:970 configurable
# {"thread_id": task_id}），任务行 30 天归档删除后三表行永久孤儿。
# 本地 PG information_schema 探针实证三表无外键；顺序 checkpoints →
# checkpoint_blobs → checkpoint_writes（语义父表先删）。
# ⚠️ memory.checkpoint_migrations 是 langgraph 自有版本表，绝不清理。
_CHECKPOINT_PURGE_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _purge_checkpoints(conn, task_ids) -> int:
    """v0.75 C2: 按 thread_id 清 memory.checkpoints 三表（删任务行**前**调用）。

    Args:
        conn: SQLAlchemy Connection（调用方事务内，随外层 commit 提交）
        task_ids: 将删任务 uuid 文本列表（与任务 DELETE 同 WHERE 收集）

    Returns:
        三表删除总行数；task_ids 空 → 0（零 execute）。

    ANY(:ids) bind 传 Python list[str]——psycopg2 自动数组化（勿手拼 IN 字面量）。
    一次性存量孤儿清理见 worker/scripts/cleanup_checkpoints.py。
    """
    if not task_ids:
        return 0
    from sqlalchemy import text
    total = 0
    for _table in _CHECKPOINT_PURGE_TABLES:
        res = conn.execute(
            text(f"DELETE FROM memory.{_table} WHERE thread_id = ANY(:ids)"),
            {"ids": list(task_ids)},
        )
        total += int(res.rowcount or 0)
    logger.debug(
        f"checkpoint purge: {total} rows across {len(task_ids)} tasks "
        f"({_CHECKPOINT_PURGE_TABLES})"
    )
    return total


async def _periodic_task_cleanup(interval_seconds: int = 60):
    """定期清理僵尸任务：重置卡死的 running 任务，清理过期 completed 任务"""
    await asyncio.sleep(30)  # 启动后等 30 秒再开始
    while True:
        try:
            from sqlalchemy import text
            from storage.database.db import get_engine
            engine = get_engine()
            with engine.connect() as conn:
                # ⚠️ v0.26 FIX: stale running 无条件重置 → 无限重跑（Sentry 超时×100/failed×120 实证）。
                # 改为有界：retry_count < max_retries → pending + retry_count+1 + 记错误（可重试但封顶）；
                # retry_count >= max_retries → failed（终止循环，不再无条件回炉重跑全管线烧生图额度）。
                r1 = conn.execute(text(
                    "UPDATE ozon_product_tasks SET status='pending', retry_count=retry_count+1, "
                    "error_message='[STALE_RUNNING] 任务运行超30分钟未更新，自动重试(有界)', "
                    "started_at=NULL, updated_at=NOW() "
                    "WHERE status='running' AND updated_at < NOW() - INTERVAL '30 minutes' "
                    "AND retry_count < max_retries"
                )).rowcount
                r1f = conn.execute(text(
                    "UPDATE ozon_product_tasks SET status='failed', "
                    "error_message='[STALE_RUNNING] 重试次数耗尽，终止（不再重跑）', "
                    "completed_at=NOW(), updated_at=NOW() "
                    "WHERE status='running' AND updated_at < NOW() - INTERVAL '30 minutes' "
                    "AND retry_count >= max_retries"
                )).rowcount
                # 归档 30 天前的 completed 任务（结果已留存 listing_result_log，见
                # utils/listing_result_log.py——v0.67 起每任务一行事实留存，物理删不再丢数据）
                # ✅ v0.75 C2: 删任务行**前**先收集将删 id → 清 checkpoint 三表
                # （thread_id==task_id，删除谓词与任务 DELETE 同 WHERE；空列表跳过）。
                _due_task_ids = [
                    str(row[0]) for row in conn.execute(text(
                        "SELECT id::text FROM ozon_product_tasks "
                        "WHERE status='completed' AND updated_at < NOW() - INTERVAL '30 days'"
                    )).fetchall()
                ]
                _purge_checkpoints(conn, _due_task_ids)
                r2 = conn.execute(text(
                    "DELETE FROM ozon_product_tasks "
                    "WHERE status='completed' AND updated_at < NOW() - INTERVAL '30 days'"
                )).rowcount
                # F-D04（2026-09-09 审计）对账归位：终态 commit 与 draft_submissions
                # 写回不同事务，崩溃窗口会让 submission 卡 pending/uploading，
                # has_active_submission 恒真 → 该草稿重提永久 409。每轮把
                # 「任务已终态而 submission 仍活跃」的行按任务终态归位（幂等）。
                r3 = conn.execute(text(
                    "UPDATE draft_submissions ds SET status = CASE t.status "
                    "WHEN 'completed' THEN 'published' "
                    "WHEN 'rejected' THEN 'rejected' "
                    "ELSE 'failed' END, updated_at = NOW() "
                    "FROM ozon_product_tasks t "
                    "WHERE ds.submitted_task_id = t.id::text "
                    "AND ds.status IN ('pending','uploading') "
                    "AND t.status IN ('completed','failed','rejected','cancelled')"
                )).rowcount
                # ✅ v0.77.2（运维修复）: store_metrics_history 保留策略——每店每次同步
                # append 快照，唯一高速增长表（生产 15,557 行 / ~650 行/店/天）。每轮清理
                # 超期行（默认 90 天，env 可覆盖），savepoint 隔离 + 失败仅 warning。
                _maybe_sweep_store_metrics(conn)
                conn.commit()
                if r1 or r1f or r2 or r3:
                    logger.info(f"🧹 定期清理: {r1} stale running → pending(重试+1), {r1f} stale running → failed(耗尽), {r2} old completed deleted (结果已留存 listing_result_log), {r3} submission 对账归位")
                    # v0.29.2 监控: 超时任务重跑/终止上报 Sentry
                    try:
                        from utils.sentry_setup import capture_task_event
                        capture_task_event(
                            "stale_running_reset",
                            f"定期清理: stale running→pending(重试+1) {r1}, "
                            f"stale running→failed(耗尽) {r1f}, 清理completed {r2}",
                            level="warning",
                            stale_pending=r1,
                            stale_failed=r1f,
                            old_completed_deleted=r2,
                        )
                    except Exception:
                        pass
                # ✅ v0.26: 清理任务生图缓存（7 天前，防表无限膨胀）
                try:
                    from utils.task_image_cache import cleanup_old
                    _del = cleanup_old(older_than_days=7)
                    if _del:
                        logger.info(f"🧹 定期清理: {_del} 条任务生图缓存（>7天）已删除")
                except Exception:
                    pass
                # ✅ v0.10: 清理 _task_progress 中已完成超过 1 小时的任务条目（防内存泄漏）
                if r2:
                    _purge_stale_progress()
                # ✅ B2a-5/6: 每日级维护钩子（各自 24h 节流，失败非致命）
                _maybe_sweep_caches(conn)
                _maybe_refresh_fx()
        except Exception as _e:
            logger.debug(f"定期清理跳过: {_e}")
        await asyncio.sleep(interval_seconds)

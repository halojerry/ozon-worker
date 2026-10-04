"""应用生命周期（W3c 自 main.py 迁出）：启动图构建/holder 注入/周期任务装配 + 优雅关闭。

此前 `lifespan` 与 `_root_lifespan` 住在 main 模块顶部，main.py 是唯一
写入 main.task_processor / main.async_graph 全局的地方。本模块是唯一权威：

- 本模块只导出 `lifespan`（worker 主 lifespan）。`_root_lifespan`（合并
  FastMCP session manager lifespan）随 MCP 装配住在 app_factory（见该文件）。
- 全局 holder 化：``set_task_processor``（orchestrator）与 ``set_async_graph``
  （runtime.graph_service）是唯一注入通道；main.task_processor / main.async_graph
  降级为恒 None 兼容占位（已无消费者，见 main.py 注释）。
- ``store_sync_task`` 是历史 write-only 全局（原 main.lifespan 内 ``global``
  赋值，全仓无读取方）——原样保留在本模块（不再写 main 命名空间）。
- 依赖与懒加载时序逐字随迁：``concurrent.futures``/``services.store_sync_scheduler``/
  ``services.metrics_aggregation`` 等仍按原位置函数内导入（测试 patch 靶点与
  冷启动时序依赖不变）。
- 僵尸恢复调用点与原 main.lifespan 逐字对应（graph 构建后、周期任务启动前），
  实现见 runtime.startup_checks._recover_zombie_tasks。
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from sqlalchemy import event

from orchestrator.task_processor import SupabaseTaskProcessor, set_task_processor
from runtime.graph_service import service, set_async_graph
from runtime.helpers import graph_helper
from runtime.maintenance import _periodic_task_cleanup
from runtime.progress import request_shutdown
from runtime.startup_checks import (
    _assert_critical_configs,
    _recover_zombie_tasks,
    _warn_if_multi_worker,
)
from storage.database.db import get_engine, get_session, init_db
from storage.memory.memory_saver import get_memory_saver
from utils.instance_lock import (  # E-4: 后台循环单实例锁（多副本防重复跑）
    METRICS_AGGREGATION,
    PERIODIC_CLEANUP,
    STORE_SYNC_LEGACY,
    try_acquire_instance_lock,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# 历史 write-only 全局（原 main.store_sync_task；无任何读取方，仅保留形态）
store_sync_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ✅ B4 BL-31: 启动即探测多 worker 误部署（内存态组件非多副本安全，见函数注释）
    _warn_if_multi_worker()
    # ✅ v0.77.3（管线延迟同批运维守卫）：启动即校验关键 config 文件在位。
    # 实测事故（2026-09-19 gate）：栈从已删除的 worktree 启动 → config bind 挂到
    # 死路径 → Docker 静默挂空目录遮住镜像内配置 → scene 节点 FileNotFound、
    # 受限词表静默回退内置、任务 4 轮重试白烧。缺文件大声报（ERROR + Sentry），
    # 不 crash（可热修 bind 后重启）；restricted_keywords 属可选降级仅 WARNING。
    _assert_critical_configs()
    engine = get_engine()
    # 自动建表（幂等，CREATE TABLE IF NOT EXISTS）
    init_db()
    @event.listens_for(engine, "connect")
    def _set_utc(dbapi_conn, _):
        with dbapi_conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
    checkpointer = get_memory_saver()
    if graph_helper.is_agent_proj():
        base = graph_helper.get_agent_instance("agents.agent", None)
        sync_graph = base.builder.compile(checkpointer=checkpointer)
    else:
        base = graph_helper.get_graph_instance("graphs.graph")
        sync_graph = base.builder.compile(checkpointer=checkpointer)
    # R3a: async 图单例归 runtime/graph_service holder（唯一权威；唯一消费者
    # routes/ops_routes.py GET /progress/{run_id} 经 get_async_graph() 取）。
    set_async_graph(base.builder.compile(checkpointer=checkpointer))
    service.set_graph(sync_graph)

    # 启动Supabase任务处理器（最多30个并发任务 — 4核4G 服务器 I/O 密集安全值，外部 API 由全局限流器兜底）
    # ✅ W3b: 单例归 orchestrator holder（services 层经 get_task_processor 取，
    # 不再 from main）；main.task_processor 是恒 None 兼容占位（路由裸名与
    # 测试 monkeypatch main.task_processor 兼容面不变）。
    global store_sync_task
    max_concurrent = int(os.getenv("MAX_CONCURRENT", "30"))
    task_processor = SupabaseTaskProcessor(max_concurrent=max_concurrent)
    set_task_processor(task_processor)

    # v0.63.1 架构优化 R1: 扩容 asyncio 默认线程池——LangGraph 全部节点是 sync 函数,
    # ainvoke 下经 run_in_executor(None, ...) 跑在 asyncio 默认 ThreadPoolExecutor
    # (min(32, cpu_count+4), 4 核 = 8 线程) → 「MAX_CONCURRENT 并发」实际并行度被卡死,
    # Phase2 的 8 个并行生图节点退化为排队。按 并发×节点扇出 扩容, 上限 128 防 4GB OOM;
    # 上游 MXOU/Ozon 由全局限流器兜底, 不会被打爆。
    try:
        import concurrent.futures
        _graph_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(128, max(64, max_concurrent * 8)),
            thread_name_prefix="graph-node",
        )
        _loop = asyncio.get_running_loop()
        _loop.set_default_executor(_graph_executor)
        logger.info("🔄 asyncio 默认线程池扩容: %d workers（sync 图节点并行度）",
                    _graph_executor._max_workers)
    except Exception as _exec_e:
        logger.warning("默认线程池扩容失败（不影响启动）: %s", _exec_e)

    # ✅ 启动时僵尸任务恢复（语义见 runtime.startup_checks._recover_zombie_tasks）
    _recover_zombie_tasks()

    # 启动定时清理任务（E-4: 多副本时仅持锁副本跑，单副本恒 True 零变化）
    cleanup_task = None
    if try_acquire_instance_lock("periodic_cleanup", PERIODIC_CLEANUP):
        cleanup_task = asyncio.create_task(_periodic_task_cleanup(interval_seconds=60))

    # 店铺数据自动同步（PRD M1 任务化:5s 扫描+worker 池+job 可见;SKIP_STORE_SYNC=1 关闭;
    # STORE_SYNC_JOBS_ENABLED=0 回退旧版 15min 全局轮询）
    if os.getenv("SKIP_STORE_SYNC", "0") == "1":
        logger.info("⏸️ 店铺自动同步已关闭（SKIP_STORE_SYNC=1）")
        store_sync_task = None
    else:
        from services.store_sync_scheduler import (
            jobs_enabled, store_sync_jobs_loop, store_sync_loop,
        )
        if jobs_enabled():
            from services import store_sync_jobs as _sync_jobs_mod
            try:
                _sync_jobs_mod.zombie_reset()
                logger.info("🧹 同步任务僵尸恢复完成")
            except Exception as _zexc:
                logger.warning("同步任务僵尸恢复异常(不阻断): %s", str(_zexc)[:200])
        if jobs_enabled():
            # jobs 版：PG 队列 SKIP LOCKED 认领，天然多副本安全，不加实例锁
            store_sync_task = asyncio.create_task(store_sync_jobs_loop())
        elif try_acquire_instance_lock("store_sync_legacy", STORE_SYNC_LEGACY):
            store_sync_task = asyncio.create_task(store_sync_loop())
        else:
            store_sync_task = None

    # PRD M3: 指标日聚合 + 保留清理(10min)
    from services.metrics_aggregation import aggregation_loop
    metrics_agg_task = None
    if try_acquire_instance_lock("metrics_aggregation", METRICS_AGGREGATION):
        metrics_agg_task = asyncio.create_task(aggregation_loop())

    # 启动Worker后台任务（不阻塞主服务启动）
    # ⚠️ v0.14 E9: num_workers 联动 MAX_CONCURRENT（旧代码硬编码 10，调大 env 实际并发仍封顶 10）
    worker_task = asyncio.create_task(task_processor.start_workers(num_workers=max_concurrent))

    yield

    # ✅ v0.29(PRD-cicd-stability): 优雅关闭 — 不再强杀运行中任务
    # 1. 停止接收新任务(worker_loop 检查 SHUTDOWN_FLAG 后 break)
    request_shutdown()
    logger.info("🛑 收到关闭信号, 停止接收新任务, 等待运行中任务排空(最多 5 分钟)...")
    # 2. drain: 轮询 PG 中 running 任务数, 直到为 0 或超时 300s
    try:
        from sqlalchemy import text as _sa_text
        for _drain_i in range(300):
            sess = get_session()
            try:
                _running = sess.execute(
                    _sa_text("SELECT COUNT(*) FROM ozon_product_tasks WHERE status='running'")
                ).scalar() or 0
            finally:
                sess.close()
            if _running == 0:
                logger.info("✅ 运行中任务已全部完成, 优雅关闭")
                break
            if _drain_i % 30 == 0:
                logger.info(f"⏳ 排空中: 仍有 {_running} 个任务运行中 ({_drain_i}s)")
            await asyncio.sleep(1)
        else:
            logger.warning("⚠️ 排空超时(5 分钟), 取消剩余任务(zombie cleanup 兜底)")
            # F-C05（2026-09-09 审计）：被部署中断的 running 任务若只靠 zombie
            # 复活，重启后会被静默重跑（同 SKU 双倍成本）。原地置 failed 并
            # 推满 retry_count（防复活谓词命中），让用户显式重提。
            _sess = get_session()
            try:
                _n = _sess.execute(
                    _sa_text("""
                        UPDATE ozon_product_tasks
                        SET status='failed', retry_count=max_retries, completed_at=NOW(),
                            error_message='[部署重启] 排空超时任务中断，请重新提交'
                        WHERE status='running'
                    """)
                ).rowcount
                _sess.commit()
                if _n:
                    logger.warning("🛑 已将 %d 个运行中任务标记为 failed（部署中断），不自动重跑", _n)
            finally:
                _sess.close()
    except Exception as _drain_e:
        logger.warning(f"⚠️ 排空检查异常(继续关闭): {_drain_e}")

    # 关闭时停止Worker
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            logger.info("Worker任务已取消")
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            logger.info("定时清理任务已取消")

    # v0.64 P2: 关闭 asyncio 默认线程池（扩容的 ThreadPoolExecutor）
    # 防止进程退出时线程池中的 sync 节点泄漏（内存/句柄）
    try:
        if _graph_executor is not None:
            _graph_executor.shutdown(wait=True, timeout=30)
            logger.info("🔄 asyncio 线程池已关闭")
    except Exception as _exec_shutdown_e:
        logger.warning("线程池关闭异常(不影响退出): %s", _exec_shutdown_e)

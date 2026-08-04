import os
import json
import asyncio
import logging
import traceback as _traceback
from typing import Dict, Any, Optional
from utils.logger import get_logger, set_trace_context, log_task_event, clear_trace_context
from datetime import datetime
from supabase import Client
from sqlalchemy import text

from storage.database.supabase_client import get_supabase_client
from storage.database.db import get_engine
from utils.task_statistics import statistics_payload
from graphs.graph import main_graph  # 导入LangGraph主图

logger = get_logger(__name__)


# ── 进度回调 ──
# 节点名 → 阶段名映射
_NODE_STAGE_MAP = {
    "auth": "auth", "ingest": "ingest", "category_match": "category_match",
    "pricing": "pricing", "attributes": "attributes", "description": "description",
    "main_image_gen": "image_generation", "white_bg_gen": "image_generation",
    "detail_gen": "image_generation", "scene_1_gen": "image_generation",
    "scene_2_gen": "image_generation", "scene_3_gen": "image_generation",
    "comparison_gen": "image_generation", "social_proof_gen": "image_generation",
    "multi_angle_gen": "image_generation",
    "prepare_ozon_upload": "prepare_ozon_upload",
    "ozon_validate": "ozon_validate", "ozon_upload": "ozon_upload",
    "ozon_status": "ozon_status", "learning_record": "learning_record",
}


class ProgressCallback:
    """LangGraph 回调：节点执行时更新进度"""
    # LangChain >=0.3 required callback attributes
    run_inline = True
    ignore_chain = False
    ignore_agent = False
    ignore_llm = False
    ignore_retry = False
    ignore_chat_model = False
    raise_error = False

    def __init__(self, task_id: str, update_fn):
        self.task_id = task_id
        self.update_fn = update_fn

    def on_chain_start(self, serialized, inputs, **kwargs):
        node_name = serialized.get("name", "") if isinstance(serialized, dict) else ""
        stage = _NODE_STAGE_MAP.get(node_name, node_name)
        if stage and self.update_fn:
            self.update_fn(self.task_id, stage, f"执行 {node_name}...")

    def on_chain_end(self, outputs, **kwargs):
        pass

    def on_chain_error(self, error, **kwargs):
        if self.update_fn:
            self.update_fn(self.task_id, "error", str(error)[:100])


class SupabaseTaskProcessor:
    """
    Supabase云端任务处理器
    
    功能：
    - 任务提交：将任务提交到Supabase云端队列
    - 任务处理：从Supabase获取pending任务并执行
    - 并发控制：asyncio.Semaphore限制最多10个并发任务
    - 任务重试：失败任务自动重试最多3次
    - 任务超时：每个任务最多30分钟超时
    - 多租户支持：支持租户隔离和租户级别并发控制
    """
    
    def __init__(self, max_concurrent: int = 10):
        """
        初始化任务处理器
        
        Args:
            max_concurrent: 最大并发任务数（默认10个）
        """
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # ✅ 处理supabase_client为None的情况（环境变量未配置）
        self.supabase: Optional[Client] = get_supabase_client()
        if self.supabase is None:
            logger.warning("Supabase客户端未初始化（环境变量未配置），将只使用PostgreSQL")
        
        self.engine = get_engine()  # 使用SQLAlchemy engine直接操作PostgreSQL
        self.running_tasks: Dict[str, asyncio.Task] = {}
        
        logger.info(f"SupabaseTaskProcessor初始化完成（最大并发: {max_concurrent}, 使用SQL直接操作）")
    
    async def submit_task(
        self, 
        tenant_id: str, 
        payload: Dict[str, Any], 
        priority: int = 0,
        timeout_seconds: int = 1800,
        max_retries: int = 3
    ) -> str:
        """
        提交任务到Supabase队列
        
        Args:
            tenant_id: 用户ID（从token中提取，用于用户隔离和进度查询）
            payload: 任务数据（LangGraph输入参数，包含user_id、token、ozon_client_id等）
            priority: 任务优先级（0-100，VIP用户使用更高优先级）
            timeout_seconds: 任务超时时间（默认30分钟）
            max_retries: 最大重试次数（默认3次）
        
        Returns:
            task_id: 任务UUID
        
        Example:
            >>> processor = SupabaseTaskProcessor()
            >>> task_id = await processor.submit_task(
            >>>     tenant_id="user_123",  # ✅ 用户ID（从token中提取）
            >>>     payload={"user_id": "user_123", "token": "...", "ozon_client_id": "..."},
            >>>     priority=50,
            >>>     timeout_seconds=1800
            >>> )
        """
        task_data = {
            "tenant_id": tenant_id,
            "status": "pending",
            "priority": priority,
            "payload": payload,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries
        }
        
        try:
            # 使用SQL INSERT直接操作PostgreSQL（绕过PostgREST schema cache）
            insert_sql = text("""
                INSERT INTO ozon_product_tasks (
                    tenant_id, status, priority, payload, timeout_seconds, max_retries, retry_count
                ) VALUES (
                    :tenant_id, 'pending', :priority, :payload_json, :timeout_seconds, :max_retries, 0
                ) RETURNING id
            """)
            
            with self.engine.connect() as conn:
                result = conn.execute(insert_sql, {
                    "tenant_id": tenant_id,
                    "priority": priority,
                    "payload_json": json.dumps(payload),
                    "timeout_seconds": timeout_seconds,
                    "max_retries": max_retries
                })
                task_id = str(result.fetchone()[0])
                conn.commit()
            
            logger.info(f"任务{task_id}已提交到Supabase队列（租户: {tenant_id}, 优先级: {priority})")
            return task_id
            
        except Exception as e:
            logger.error(f"任务提交失败: {e}")
            raise e
    
    async def process_next_task(self) -> Optional[Dict[str, Any]]:
        """
        处理下一个优先级最高的任务
        
        流程：
        1. 从Supabase获取优先级最高的pending任务
        2. 使用asyncio.Semaphore控制并发
        3. 更新任务状态为running
        4. 执行LangGraph流程（带超时控制）
        5. 更新任务状态为completed或failed
        
        Returns:
            任务结果或None（无待处理任务）
        """
        async with self.semaphore:
            # ✅ 使用SQL SELECT获取优先级最高的pending任务（添加FOR UPDATE SKIP LOCKED避免并发竞争）
            try:
                # ✅ Step1: 使用事务锁定任务（FOR UPDATE SKIP LOCKED）
                with self.engine.connect() as conn:
                    # ✅ 关键：FOR UPDATE SKIP LOCKED避免并发竞争
                    # - 锁定选中的行（其他worker无法选择）
                    # - 跳过已被锁定的行（自动选择下一个任务）
                    # - 避免同一任务被重复认领
                    select_sql = text("""
                        SELECT id, tenant_id, priority, payload, timeout_seconds
                        FROM ozon_product_tasks
                        WHERE status = 'pending'
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    """)
                    
                    result = conn.execute(select_sql)
                    task_row = result.fetchone()
                    
                    if not task_row:
                        logger.debug("没有待处理的任务")
                        return None
                    
                    task_id = str(task_row[0])
                    tenant_id = task_row[1]
                    priority = task_row[2]
                    payload = task_row[3] if isinstance(task_row[3], dict) else json.loads(task_row[3])
                    timeout_seconds = task_row[4]

                    # ✅ P1 修复：注入 PG UUID 到 payload，使 progress callback 能按 task_id 追踪
                    payload["task_id"] = task_id
                    payload["tenant_id"] = tenant_id

                    set_trace_context(task_id=task_id, user_id=tenant_id)
                    log_task_event("started", task_id=task_id, user_id=tenant_id, priority=priority)
                    
                    # ✅ Step2: 在同一事务中更新任务状态为running（确保原子性）
                    update_running_sql = text("""
                        UPDATE ozon_product_tasks
                        SET status = 'running', started_at = NOW()
                        WHERE id = :task_id
                    """)
                    
                    conn.execute(update_running_sql, {"task_id": task_id})
                    conn.commit()  # ✅ 提交事务，释放锁
                
                # ✅ Step3: 执行任务（LangGraph流程）
                try:
                    graph_result = await self.execute_graph_with_timeout(
                        payload=payload,
                        timeout=timeout_seconds
                    )

                    # v0.22: 合并产品明细（1688链接/利润率/售价/采购价/运费预估），
                    # 让 skill/agent 查询 task_status 就能拿到可读经营数据
                    try:
                        from utils.product_summary import build_product_summary
                        draft = (payload or {}).get("envelope", {}).get("draft", {}) or {}
                        graph_result["product_summary"] = build_product_summary(graph_result, draft)
                        if not graph_result.get("purchase_url"):
                            graph_result["purchase_url"] = draft.get("purchase_url", "")
                        if not graph_result.get("purchase_cost"):
                            graph_result["purchase_cost"] = str(draft.get("purchase_cost", ""))
                    except Exception as _ps_err:
                        logger.warning("product_summary 组装失败（不影响任务结果）: %s", _ps_err)
                    
                    # 使用SQL UPDATE更新任务状态为completed
                    update_completed_sql = text("""
                        UPDATE ozon_product_tasks
                        SET status = 'completed', result = :result_json, completed_at = NOW()
                        WHERE id = :task_id
                    """)
                    
                    with self.engine.connect() as conn:
                        conn.execute(update_completed_sql, {
                            "task_id": task_id,
                            "result_json": json.dumps(graph_result)
                        })
                        conn.commit()
                    
                    log_task_event("completed", task_id=task_id, user_id=tenant_id)
                    clear_trace_context()
                    return graph_result

                except TimeoutError:
                    log_task_event("failed", task_id=task_id, user_id=tenant_id,
                                   error_message=f"timeout ({timeout_seconds}s)")
                    await self.handle_task_failure(task_id, f"任务超时（{timeout_seconds}秒）")
                    return None
                    
                except Exception as e:
                    logger.error("任务执行异常: %s\n%s", str(e), _traceback.format_exc())
                    log_task_event("failed", task_id=task_id, user_id=tenant_id,
                                   error_message=str(e), error_type=type(e).__name__)
                    await self.handle_task_failure(task_id, str(e))
                    clear_trace_context()
                    return None
                    
            except Exception as e:
                logger.error(f"任务处理失败: {e}")
                return None
    
    async def handle_task_failure(self, task_id: str, error_message: str):
        """
        处理任务失败（自动重试机制）
        
        Args:
            task_id: 任务UUID
            error_message: 错误信息
        
        流程：
        1. 检查重试次数
        2. 如果未达到最大重试次数，更新状态为pending并增加retry_count
        3. 如果达到最大重试次数，更新状态为failed
        """
        try:
            # 使用SQL SELECT获取任务详情
            select_sql = text("""
                SELECT retry_count, max_retries
                FROM ozon_product_tasks
                WHERE id = :task_id
            """)
            
            with self.engine.connect() as conn:
                result = conn.execute(select_sql, {"task_id": task_id})
                task_row = result.fetchone()
            
            if not task_row:
                logger.error(f"任务{task_id}不存在")
                return
            
            retry_count = task_row[0]
            max_retries = task_row[1]
            
            if retry_count < max_retries:
                # 临时错误自动重试，使用SQL UPDATE
                update_retry_sql = text("""
                    UPDATE ozon_product_tasks
                    SET status = 'pending',
                        retry_count = :retry_count,
                        error_message = :error_message,
                        updated_at = NOW()
                    WHERE id = :task_id
                """)
                
                with self.engine.connect() as conn:
                    conn.execute(update_retry_sql, {
                        "task_id": task_id,
                        "retry_count": retry_count + 1,
                        "error_message": error_message
                    })
                    conn.commit()
                
                log_task_event("retried", task_id=task_id, retry_count=retry_count + 1,
                               max_retries=max_retries, error_message=error_message)
            else:
                update_failed_sql = text("""
                    UPDATE ozon_product_tasks
                    SET status = 'failed',
                        error_message = :error_message,
                        completed_at = NOW()
                    WHERE id = :task_id
                """)

                with self.engine.connect() as conn:
                    conn.execute(update_failed_sql, {
                        "task_id": task_id,
                        "error_message": error_message
                    })
                    conn.commit()

                log_task_event("failed", task_id=task_id, error_message=error_message,
                               retry_count=retry_count, max_retries=max_retries, permanent=True)
                
        except Exception as e:
            logger.error(f"任务失败处理异常: {e}")

    async def execute_graph_with_timeout(
        self,
        payload: Dict[str, Any],
        timeout: int
    ) -> Dict[str, Any]:
        """
        执行LangGraph流程（带超时控制和进度追踪）

        Args:
            payload: LangGraph输入参数
            timeout: 超时时间（秒）

        Returns:
            LangGraph输出结果
        """
        try:
            from langchain_core.runnables import RunnableConfig
            from main import update_progress, set_current_task_id

            task_id = payload.get("task_id", "unknown")
            # ✅ v0.10: 设置全局当前 task_id，使 ProgressLogger 能自动获取
            set_current_task_id(task_id)
            config = RunnableConfig(
                configurable={"thread_id": task_id},
                run_name=f"task_{task_id}",
                metadata={"task_id": task_id},
                callbacks=[ProgressCallback(task_id, update_progress)],
            )
            result = await asyncio.wait_for(
                main_graph.ainvoke(payload, config=config),
                timeout=timeout
            )
            return result

        except TimeoutError:
            raise TimeoutError(f"LangGraph流程执行超时（{timeout}秒）")
        finally:
            # ✅ v0.10: 清除全局 task_id 上下文
            try:
                from main import set_current_task_id
                set_current_task_id(None)
            except Exception:
                pass
    
    async def worker_loop(self):
        """
        Worker持续处理任务
        
        流程：
        1. 持续循环处理任务
        2. 每次处理完成后等待1秒
        3. 异常情况下等待5秒
        """
        logger.info("Worker开始运行")
        
        while True:
            try:
                result = await self.process_next_task()
                
                if result is None:
                    # 无待处理任务，等待5秒
                    await asyncio.sleep(5)
                else:
                    # 任务处理完成，等待1秒
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Worker异常: {e}")
                # 异常后等待5秒
                await asyncio.sleep(5)
    
    async def start_workers(self, num_workers: int = 10):
        """
        启动多个Worker处理任务
        
        Args:
            num_workers: Worker数量（默认10个）
        """
        workers = []
        for i in range(num_workers):
            worker = asyncio.create_task(self.worker_loop())
            workers.append(worker)
        
        logger.info(f"启动{num_workers}个Worker处理任务")
        
        # 持续运行所有Worker
        await asyncio.gather(*workers)
    
    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        查询任务状态
        
        Args:
            task_id: 任务UUID
        
        Returns:
            任务详情或None
        """
        try:
            # 使用SQL SELECT查询任务详情
            select_sql = text("""
            SELECT id, tenant_id, status, priority, payload, result,
                   error_message, retry_count, max_retries,
                   created_at, updated_at, started_at, completed_at, timeout_seconds,
                   progress
            FROM ozon_product_tasks
            WHERE id = :task_id
        """)
            
            with self.engine.connect() as conn:
                result = conn.execute(select_sql, {"task_id": task_id})
                task_row = result.fetchone()
            
            if not task_row:
                return None
            
            # 将结果转换为字典
            task_dict = {
                "id": str(task_row[0]),
                "tenant_id": task_row[1],
                "status": task_row[2],
                "priority": task_row[3],
                "payload": task_row[4] if isinstance(task_row[4], dict) else json.loads(task_row[4]),
                "result": task_row[5] if isinstance(task_row[5], dict) else json.loads(task_row[5]) if task_row[5] else None,
                "error_message": task_row[6],
                "retry_count": task_row[7],
                "max_retries": task_row[8],
                "created_at": task_row[9],
                "updated_at": task_row[10],
                "started_at": task_row[11],
                "completed_at": task_row[12],
                "timeout_seconds": task_row[13],
                "progress": task_row[14] if isinstance(task_row[14], dict) else json.loads(task_row[14]) if task_row[14] else None,
            }
            
            return task_dict
            
        except Exception as e:
            logger.error(f"查询任务状态失败: {e}")
            return None
    
    async def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        Args:
            task_id: 任务UUID
        
        Returns:
            是否成功取消
        """
        try:
            # 使用SQL UPDATE取消任务（仅pending状态）
            update_cancel_sql = text("""
                UPDATE ozon_product_tasks
                SET status = 'cancelled', completed_at = NOW()
                WHERE id = :task_id AND status = 'pending'
            """)
            
            with self.engine.connect() as conn:
                result = conn.execute(update_cancel_sql, {"task_id": task_id})
                conn.commit()
                
                # 检查是否有实际更新
                if result.rowcount > 0:
                    logger.info(f"任务{task_id}已取消")
                    return True
                else:
                    logger.info(f"任务{task_id}无法取消（可能不是pending状态）")
                    return False
            
        except Exception as e:
            logger.error(f"取消任务失败: {e}")
            return False
    
    async def get_task_statistics(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        获取任务统计信息
        
        Args:
            tenant_id: 租户ID（可选，不传则查询所有租户）
        
        Returns:
            任务统计信息（总数、成功率、平均耗时等）
        """
        try:
            # 使用SQL查询获取任务统计信息
            if tenant_id:
                # 查询特定租户的统计信息
                stats_sql = text("""
                    SELECT 
                        COUNT(*) as total_tasks,
                        COUNT(*) FILTER (WHERE status = 'completed') as completed_tasks,
                        COUNT(*) FILTER (WHERE status = 'failed') as failed_tasks,
                        COUNT(*) FILTER (WHERE status = 'running') as running_tasks,
                        COUNT(*) FILTER (WHERE status = 'pending') as pending_tasks,
                        AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) 
                            FILTER (WHERE status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL) as avg_duration_seconds
                    FROM ozon_product_tasks
                    WHERE tenant_id = :tenant_id
                """)
                
                with self.engine.connect() as conn:
                    result = conn.execute(stats_sql, {"tenant_id": tenant_id})
                    stats_row = result.fetchone()
            else:
                # 查询所有租户的统计信息
                stats_sql = text("""
                    SELECT 
                        COUNT(*) as total_tasks,
                        COUNT(*) FILTER (WHERE status = 'completed') as completed_tasks,
                        COUNT(*) FILTER (WHERE status = 'failed') as failed_tasks,
                        COUNT(*) FILTER (WHERE status = 'running') as running_tasks,
                        COUNT(*) FILTER (WHERE status = 'pending') as pending_tasks,
                        AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) 
                            FILTER (WHERE status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL) as avg_duration_seconds
                    FROM ozon_product_tasks
                """)
                
                with self.engine.connect() as conn:
                    result = conn.execute(stats_sql)
                    stats_row = result.fetchone()
            
            if not stats_row:
                return {}
            
            # ✅ v0.19: 字段名对齐 TaskStatisticsResponse（此前 total_tasks 等
            # 与模型 total/completed 对不上 → 统计接口恒返回全 0）
            return statistics_payload(
                total=stats_row[0],
                completed=stats_row[1],
                failed=stats_row[2],
                running=stats_row[3],
                pending=stats_row[4],
                avg_duration_seconds=stats_row[5],
            )
            
        except Exception as e:
            logger.error(f"获取任务统计失败: {e}")
            return {}


__all__ = ["SupabaseTaskProcessor"]

import os
import json
import asyncio
import logging
import requests  # 任务终态 webhook 通知（P1-4）
import traceback as _traceback
from typing import Dict, Any, Optional
from utils.logger import get_logger, set_trace_context, log_task_event, clear_trace_context
from datetime import datetime
from supabase import Client
from sqlalchemy import text
from utils.sentry_setup import capture_task_error  # v0.23 Sentry 任务异常上报
from utils.mxou_api import MxouContentViolationError, MxouOutOfQuotaError  # v0.63.1 R1/R4 闭环

from storage.database.supabase_client import get_supabase_client
from storage.database.db import get_engine
from utils.task_statistics import statistics_payload
from graphs.graph import main_graph  # 导入LangGraph主图
from utils.draft_status_writeback import writeback_submission_status, map_worker_status  # M0.3

logger = get_logger(__name__)


def _should_report_task_rerun(retry_count: int, error_message: str) -> bool:
    """v0.62 R6: task_rerun 是否上报 Sentry。

    仅当任务疑似「僵尸/超时恢复」重跑才上报 warning（error_message 含
    STALE_RUNNING/ZOMBIE/超时）；正常业务失败重试不上报，避免噪音刷屏。
    """
    if retry_count <= 0:
        return False
    msg = str(error_message or "")
    return any(kw in msg for kw in ("STALE_RUNNING", "ZOMBIE", "zombie", "超时"))


def _is_permanent_task_error(exc: Exception) -> bool:
    """永久性错误判定：余额/鉴权（OUT_OF_QUOTA）与内容违规不重试（R1/R4 闭环）。

    MXOU 401/403 已由 mxou_api 归类为 MxouOutOfQuotaError（余额/鉴权耗尽），
    内容违规为 MxouContentViolationError —— 二者重试无意义（token 无效不会因重试变有效，
    违规 prompt 不会因重试变合规），应直接终态 failed，不消耗 retry_count。
    Sentry 仍由 capture_task_error 上报一次（带 token 指纹定位账号），并在
    before_send 聚合为单一 fingerprint（mxou-permanent-error）防刷屏。
    除 isinstance 外按消息信号兜底（防未来异常被包装后丢失类型）。
    """
    if isinstance(exc, (MxouOutOfQuotaError, MxouContentViolationError)):
        return True
    msg = str(exc or "")
    return msg.startswith("OUT_OF_QUOTA:") or "内容违规" in msg


def _writeback_status(task_id: str, status: str, error_message: str | None = None) -> None:
    """draft_submissions 终态写回（M0.3）。必须在任务终态 conn.commit() 之后调用——
    写回独立于终态事务（该事务已含 shop_usage upsert），写回失败绝不能回滚任务状态。

    writeback_submission_status 本身非致命（内部吞异常）；这里再兜一层，
    防未来 writeback 实现回归成会 raise 时破坏任务终态落库（测试锁定该行为）。
    """
    try:
        writeback_submission_status(task_id, map_worker_status(status), error_message)
    except Exception:
        logger.warning("draft_submissions 状态写回调用异常（不影响任务终态）task=%s status=%s",
                       task_id, status, exc_info=True)


# v0.34 C6: 店铺使用埋点 upsert SQL，按 (ozon_client_id, stat_date) 按天聚合。
#   task_count/approved_count/validation_failed_count 用 EXCLUDED 增量累加；
#   common_errors 拼接当日最近 5 条失败 error_message（成功路径传 NULL → 保持不增）；
#   last_error 仅在失败路径更新（成功路径传 NULL → COALESCE 保留旧值）。
_SHOP_USAGE_UPSERT_SQL = text("""
    INSERT INTO shop_usage_stats
        (ozon_client_id, stat_date, task_count, approved_count, validation_failed_count,
         common_errors, last_error, updated_at)
    VALUES
        (:ozon_client_id, CURRENT_DATE, :task_delta, :approved_delta, :validation_failed_delta,
         :common_errors, :last_error, NOW())
    ON CONFLICT (ozon_client_id, stat_date) DO UPDATE SET
        task_count = shop_usage_stats.task_count + EXCLUDED.task_count,
        approved_count = shop_usage_stats.approved_count + EXCLUDED.approved_count,
        validation_failed_count = shop_usage_stats.validation_failed_count + EXCLUDED.validation_failed_count,
        common_errors = CASE
            WHEN EXCLUDED.common_errors IS NULL THEN shop_usage_stats.common_errors
            ELSE (
                SELECT jsonb_agg(q.elem ORDER BY q.ord)
                FROM (
                    SELECT elem, ord
                    FROM jsonb_array_elements(
                        COALESCE(shop_usage_stats.common_errors, '[]'::jsonb) || EXCLUDED.common_errors
                    ) WITH ORDINALITY AS e(elem, ord)
                    ORDER BY ord DESC
                    LIMIT 5
                ) q
            )
        END,
        last_error = COALESCE(EXCLUDED.last_error, shop_usage_stats.last_error),
        updated_at = NOW()
""")


def _moderation_status_deltas(graph_result):
    """按 graph_result.moderation_status 判定 approved/validation_failed 计数增量。

    approved → (1, 0)；validation_failed → (0, 1)；其余/缺失 → (0, 0)。
    """
    moderation_status = str((graph_result or {}).get("moderation_status") or "").lower()
    if moderation_status == "approved":
        return 1, 0
    if moderation_status == "validation_failed":
        return 0, 1
    return 0, 0


def _upsert_shop_usage(conn, ozon_client_id, *, task_delta=1, approved_delta=0,
                       validation_failed_delta=0, error_message=None):
    """店铺使用埋点增量写入（v0.34 C6）。尽力而为：任何失败只 log warning，不影响主流程。

    task_count = 任务执行次数（每次终态 +1，重试重新计数是预期行为）。
    common_errors 降级实现：JSONB 数组保留当日最近 5 条失败 error_message（非 top-5 聚合），
    只在失败终态路径累积；成功路径 error_message=None → common_errors/last_error 不增。
    """
    try:
        if not ozon_client_id:
            return
        msg = str(error_message)[:500] if error_message else None
        conn.execute(_SHOP_USAGE_UPSERT_SQL, {
            "ozon_client_id": str(ozon_client_id),
            "task_delta": int(task_delta),
            "approved_delta": int(approved_delta),
            "validation_failed_delta": int(validation_failed_delta),
            "common_errors": json.dumps([msg]) if msg is not None else None,
            "last_error": msg,
        })
    except Exception as e:
        logger.warning("shop_usage_stats 埋点写入失败（不影响主流程）: %s", e)


def _send_task_notify(task_id, status, graph_result, payload) -> None:
    """任务终态 webhook 通知（P1-4，fire-and-forget，绝不抛出）。

    触发条件：TASK_NOTIFY_URL 环境变量（Server酱等任意 webhook）已配置，
    或 payload 顶层 notify=True（skill --notify 传入，经 raw dict 存储直通）。
    命中后向 URL POST 终态摘要 {task_id, status, product_summary,
    error_message, product_id, ozon_client_id}（timeout=5s）。
    任何异常只 log warning，不影响任务主流程。
    """
    try:
        env_url = os.environ.get("TASK_NOTIFY_URL") or ""
        if not (env_url or (payload or {}).get("notify")):
            return
        if not env_url:
            logger.warning("notify 已请求但未配置 TASK_NOTIFY_URL，跳过 webhook 通知")
            return
        graph_result = graph_result or {}
        draft = ((payload or {}).get("envelope") or {}).get("draft") or {}
        body = {
            "task_id": task_id,
            "status": status,
            "product_summary": graph_result.get("product_summary"),
            "error_message": graph_result.get("_harness_error")
            or graph_result.get("error_message") or "",
            "product_id": graph_result.get("product_id")
            or draft.get("product_id") or draft.get("item_id") or "",
            "ozon_client_id": str((payload or {}).get("ozon_client_id") or ""),
        }
        # allow_redirects=False：重定向可能把通知 payload 转发到意外主机（v0.38.1）
        requests.post(env_url, json=body, timeout=5, allow_redirects=False)
        logger.info("任务终态通知已发送 task_id=%s status=%s", task_id, status)
    except Exception as e:
        logger.warning("任务终态 webhook 通知失败（不影响主流程）: %s", e)


async def _send_task_notify_async(task_id, status, graph_result, payload) -> None:
    """任务终态 webhook 通知（async 版，v0.38.1）。

    _send_task_notify 内是阻塞 requests.post（最多 5s）——在 async
    process_next_task 中直接调用会阻塞整个事件循环（30 并发 worker 全部卡住）。
    用 asyncio.to_thread 丢线程池，不阻塞事件循环。
    """
    await asyncio.to_thread(_send_task_notify, task_id, status, graph_result, payload)


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
    """LangGraph 回调：节点执行时更新进度 + Sentry 节点 span（v0.26）"""
    # LangChain >=0.3 required callback attributes
    run_inline = True
    ignore_chain = False
    ignore_agent = False
    ignore_llm = False
    ignore_retry = False
    ignore_chat_model = False
    raise_error = False

    def __init__(self, task_id: str, update_fn, transaction=None):
        self.task_id = task_id
        self.update_fn = update_fn
        self.transaction = transaction
        self._spans: dict = {}  # run_id → span

    def on_chain_start(self, serialized, inputs, **kwargs):
        node_name = serialized.get("name", "") if isinstance(serialized, dict) else ""
        stage = _NODE_STAGE_MAP.get(node_name, node_name)
        if stage and self.update_fn:
            self.update_fn(self.task_id, stage, f"执行 {node_name}...")
        # ✅ v0.26 Sentry: 节点 span（trace 视图看节点耗时/重跑次数）
        if self.transaction is not None and node_name:
            try:
                from utils.sentry_setup import start_node_span
                run_id = kwargs.get("run_id") or str(len(self._spans))
                self._spans[run_id] = start_node_span(self.transaction, node_name)
            except Exception:
                pass

    def on_chain_end(self, outputs, **kwargs):
        run_id = kwargs.get("run_id")
        span = self._spans.pop(run_id, None) if run_id else None
        if span is not None:
            try:
                from utils.sentry_setup import finish_span
                finish_span(span, status="ok")
            except Exception:
                pass

    def on_chain_error(self, error, **kwargs):
        if self.update_fn:
            self.update_fn(self.task_id, "error", str(error)[:100])
        run_id = kwargs.get("run_id")
        span = self._spans.pop(run_id, None) if run_id else None
        if span is not None:
            try:
                from utils.sentry_setup import finish_span
                finish_span(span, status="internal_error")
            except Exception:
                pass
        # v0.63.1 架构优化: 不再在此上报——LangGraph 对同一异常触发 node 级 +
        # graph 级两级 on_chain_error，且 capture_task_error(exc+message) 每次
        # 双发，一次节点异常会被放大为 4~6 个事件。异常统一由 process_next_task
        # 顶层捕获上报（带 task_id/tenant_id/token 全上下文），回调只记录 span。


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
        max_retries: int = 3,
        sku_key: str = "",
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
            "max_retries": max_retries,
            "sku_key": sku_key or None,
        }
        
        try:
            # 使用SQL INSERT直接操作PostgreSQL（绕过PostgREST schema cache）
            insert_sql = text("""
                INSERT INTO ozon_product_tasks (
                    tenant_id, status, priority, payload, timeout_seconds, max_retries, retry_count, sku_key
                ) VALUES (
                    :tenant_id, 'pending', :priority, :payload_json, :timeout_seconds, :max_retries, 0, :sku_key
                ) RETURNING id
            """)
            
            with self.engine.connect() as conn:
                result = conn.execute(insert_sql, {
                    "tenant_id": tenant_id,
                    "priority": priority,
                    "payload_json": json.dumps(payload),
                    "timeout_seconds": timeout_seconds,
                    "max_retries": max_retries,
                    "sku_key": sku_key or None,
                })
                task_id = str(result.fetchone()[0])
                conn.commit()
            
            logger.info(f"任务{task_id}已提交到Supabase队列（租户: {tenant_id}, 优先级: {priority})")
            return task_id
            
        except Exception as e:
            logger.error(f"任务提交失败: {e}")
            raise e

    def _claim_next_task(self) -> Optional[Dict[str, Any]]:
        """同步认领一个 pending 任务（纯 DB，供 asyncio.to_thread 调用）。

        v0.63.1 架构优化 R6: 阻塞 SQLAlchemy 调用不在事件循环上执行——池耗尽时
        pool_timeout=30s 的等待会冻结整个服务。单事务内完成 SELECT FOR UPDATE
        SKIP LOCKED + UPDATE running + commit，返回任务字段 dict；无任务返回 None。
        注意：ContextVar（trace_id）与日志不在此设置——线程不跨 ContextVar，
        由调用方（协程侧）设置。
        """
        with self.engine.connect() as conn:
            select_sql = text("""
                SELECT id, tenant_id, priority, payload, timeout_seconds, retry_count, error_message
                FROM ozon_product_tasks
                WHERE status = 'pending'
                -- P1d 定时上架：scheduled_at 未到时间不认领（payload extensions 里）
                AND (
                    payload->'envelope'->'extensions'->>'scheduled_at' IS NULL
                    OR payload->'envelope'->'extensions'->>'scheduled_at' = ''
                    OR (payload->'envelope'->'extensions'->>'scheduled_at')::timestamptz <= now()
                )
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)
            result = conn.execute(select_sql)
            task_row = result.fetchone()
            if not task_row:
                return None
            task_id = str(task_row[0])
            tenant_id = task_row[1]
            priority = task_row[2]
            payload = task_row[3] if isinstance(task_row[3], dict) else json.loads(task_row[3])
            timeout_seconds = task_row[4]
            retry_count = int(task_row[5] or 0)
            error_message = str(task_row[6] or "") if len(task_row) > 6 else ""
            # 同一事务更新为 running（原子性：认领即占用）
            conn.execute(text("""
                UPDATE ozon_product_tasks
                SET status = 'running', started_at = NOW()
                WHERE id = :task_id
            """), {"task_id": task_id})
            conn.commit()
        return {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "priority": priority,
            "payload": payload,
            "timeout_seconds": timeout_seconds,
            "retry_count": retry_count,
            "error_message": error_message,
        }

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
                # ✅ Step1+2: 认领任务（纯 DB，to_thread 不阻塞事件循环）
                # v0.63.1 架构优化 R6: 阻塞 SQLAlchemy 调用不在事件循环上执行——
                # 池耗尽时 pool_timeout=30s 的等待会冻结整个服务（含 30 worker 心跳）。
                # 同步函数只做纯 DB，ContextVar（trace_id）与日志留在协程侧（不跨线程）。
                claimed = await asyncio.to_thread(self._claim_next_task)
                if claimed is None:
                    logger.debug("没有待处理的任务")
                    return None

                task_id = claimed["task_id"]
                tenant_id = claimed["tenant_id"]
                priority = claimed["priority"]
                payload = claimed["payload"]
                timeout_seconds = claimed["timeout_seconds"]
                retry_count = claimed["retry_count"]
                error_message = claimed["error_message"]

                # v0.29.2 监控: 重跑任务(非首次)上报 Sentry — 判断是否有僵尸/超时
                # 恢复导致的重跑(用户可见的"偷偷跑任务")
                # v0.62 R6: 仅当 error_message 含 STALE_RUNNING/ZOMBIE/超时（疑似僵尸/
                # 超时恢复）才上报 warning；正常业务失败重试不上报（避免噪音刷屏）。
                if _should_report_task_rerun(retry_count, error_message):
                    try:
                        from utils.sentry_setup import capture_task_event
                        capture_task_event(
                            "task_rerun",
                            f"任务重跑(第 {retry_count+1} 次): 僵尸/超时恢复 ({error_message[:80]})",
                            task_id=task_id,
                            tenant_id=tenant_id,
                            level="warning",
                            retry_count=retry_count,
                        )
                    except Exception:
                        pass

                # ✅ P1 修复：注入 PG UUID 到 payload，使 progress callback 能按 task_id 追踪
                payload["task_id"] = task_id
                payload["tenant_id"] = tenant_id

                # v0.63.1 架构优化: worker 执行路径补 trace_id（此前未传 → 恒空，
                # LOGGING.md 宣称的链路追踪不可用）。task_id 本身唯一，取前 12
                # 位即天然关联提交请求 → 执行全链路。
                set_trace_context(
                    trace_id=task_id[:12] if task_id else "",
                    task_id=task_id,
                    user_id=tenant_id,
                )
                log_task_event("started", task_id=task_id, user_id=tenant_id, priority=priority)
                
                # ✅ Step3: 执行任务（LangGraph流程）
                try:
                    # v0.63.1 架构优化: configure_scope 在 sentry-sdk 2.x 操作共享
                    # isolation scope 且不还原 → task_id/tenant/token tag 跨任务串号。
                    # 在任务边界 fork 干净 current scope（new_scope 进入时设为 current、
                    # 退出还原），图执行期间节点内 logger.error 自动上报与 mxou_api
                    # token 指纹只归因本任务。
                    from contextlib import nullcontext
                    try:
                        import sentry_sdk
                        _task_scope_cm = sentry_sdk.new_scope()
                    except Exception:
                        _task_scope_cm = nullcontext()
                    with _task_scope_cm:
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
                    
                    # ✅ v0.26 假成功修复：图执行完成 ≠ 上架成功。wave2 实证：
                    # created=False 的卡（ML_INCORRECT_VOLUME_WEIGHT 等）此前
                    # 无条件 completed，final_error 全空——用户无法感知失败。
                    # 现在按 graph_result 失败标记如实落库：
                    #   upload_status=failed / error_message 非空 → status=failed
                    _up = str(graph_result.get("upload_status") or "")
                    # v0.28.5 C2: notice(中文可读)优先作为失败信息, 否则原始错误
                    _notice = str(graph_result.get("notice") or "")
                    _err = str(_notice or graph_result.get("error_message") or "")
                    _stg = str(graph_result.get("failed_stage") or "")
                    _is_failed = (
                        _up in ("failed",)
                        or _err.startswith("[")
                        or bool(_err and _stg)
                    )
                    # ✅ P0-2 审核被拒自动修复链：Ozon 审核 rejected/declined → 不可修复
                    # 时如实标记 rejected（此前 rejected_unfixable 落 completed，拒绝原因
                    # 被埋没在 result 里 → 任务"消失"，用户无法触发重新提交）
                    _mod_rejected = (
                        _up == "rejected_unfixable"
                        or str(graph_result.get("moderation_status") or "") in ("rejected", "declined")
                    )
                    if _is_failed:
                        graph_result["_harness_status"] = "failed"
                        graph_result["_harness_error"] = _err or f"上架失败（stage={_stg}, upload_status={_up}）"
                        log_task_event("failed", task_id=task_id, user_id=tenant_id,
                                       error_message=graph_result["_harness_error"])
                        # 使用SQL UPDATE更新任务状态为failed（如实反映，不再假成功）
                        with self.engine.connect() as conn:
                            conn.execute(text("""
                                UPDATE ozon_product_tasks
                                SET status = 'failed', result = :result_json,
                                    error_message = :err, completed_at = NOW()
                                WHERE id = :task_id
                            """), {
                                "task_id": task_id,
                                "result_json": json.dumps(graph_result),
                                "err": graph_result["_harness_error"],
                            })
                            # v0.34 C6: 店铺使用埋点（与终态 SQL 同事务，尽力而为）
                            _app_delta, _vf_delta = _moderation_status_deltas(graph_result)
                            _upsert_shop_usage(
                                conn,
                                (payload or {}).get("ozon_client_id", ""),
                                task_delta=1,
                                approved_delta=_app_delta,
                                validation_failed_delta=_vf_delta,
                                error_message=graph_result.get("_harness_error"),
                            )
                            conn.commit()
                        # M0.3: draft_submissions 状态写回（在 commit 之后，不扩事务）
                        _writeback_status(task_id, "failed", graph_result.get("_harness_error"))
                        await _send_task_notify_async(task_id, "failed", graph_result, payload)
                        clear_trace_context()
                        return graph_result

                    elif _mod_rejected:
                        graph_result["_harness_status"] = "rejected"
                        graph_result["moderation_rejected"] = True
                        graph_result["_harness_error"] = _err or f"审核被拒（stage={_stg}, upload_status={_up}）"
                        log_task_event("rejected", task_id=task_id, user_id=tenant_id,
                                       error_message="审核被拒，已标记 rejected")
                        with self.engine.connect() as conn:
                            conn.execute(text("""
                                UPDATE ozon_product_tasks
                                SET status = 'rejected', result = :result_json,
                                    error_message = :err, completed_at = NOW()
                                WHERE id = :task_id
                            """), {
                                "task_id": task_id,
                                "result_json": json.dumps(graph_result),
                                "err": graph_result["_harness_error"],
                            })
                            # v0.34 C6: 店铺使用埋点（rejected 计入 task_count，不算 approved/validation_failed）
                            _app_delta, _vf_delta = _moderation_status_deltas(graph_result)
                            _upsert_shop_usage(
                                conn,
                                (payload or {}).get("ozon_client_id", ""),
                                task_delta=1,
                                approved_delta=_app_delta,
                                validation_failed_delta=_vf_delta,
                                error_message=graph_result.get("_harness_error"),
                            )
                            conn.commit()
                        # M0.3: draft_submissions 状态写回（在 commit 之后，不扩事务）
                        _writeback_status(task_id, "rejected", graph_result.get("_harness_error"))
                        await _send_task_notify_async(task_id, "rejected", graph_result, payload)
                        clear_trace_context()
                        return graph_result

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
                        # v0.34 C6: 店铺使用埋点（成功路径 common_errors/last_error 不增）
                        _app_delta, _vf_delta = _moderation_status_deltas(graph_result)
                        _upsert_shop_usage(
                            conn,
                            (payload or {}).get("ozon_client_id", ""),
                            task_delta=1,
                            approved_delta=_app_delta,
                            validation_failed_delta=_vf_delta,
                            error_message=None,
                        )
                        conn.commit()

                    # M0.3: draft_submissions 状态写回（在 commit 之后，不扩事务）
                    _writeback_status(task_id, "completed", None)
                    await _send_task_notify_async(task_id, "completed", graph_result, payload)
                    log_task_event("completed", task_id=task_id, user_id=tenant_id)
                    clear_trace_context()
                    return graph_result

                except TimeoutError:
                    capture_task_error(
                        message=f"任务超时（{timeout_seconds}秒）——旧线程可能仍在执行, 不再自动重试",
                        task_id=task_id,
                        tenant_id=tenant_id,
                        token=(payload or {}).get("token", ""),
                    )
                    log_task_event("failed", task_id=task_id, user_id=tenant_id,
                                   error_message=f"timeout ({timeout_seconds}s)")
                    # v0.63.1 架构优化 R3: 超时不再自动重试——sync 节点线程不可取消,
                    # 重试 = 新旧两份竞争 + 重复烧 MXOU 额度。按永久错误终态处理。
                    await self.handle_task_failure(
                        task_id, f"任务超时（{timeout_seconds}秒）", permanent=True)
                    clear_trace_context()
                    return None
                    
                except Exception as e:
                    permanent = _is_permanent_task_error(e)
                    # 已由 handle_task_failure 管理的「已处理」失败 → 不 ERROR 级自动上报
                    # （LoggingIntegration 会把它发成无上下文的 message 事件，与
                    # capture_task_error 的 exception 事件双重上报）；捕获统一走
                    # capture_task_error（带 task/tenant/token 指纹）。
                    logger.warning("任务执行异常(permanent=%s): %s\n%s", permanent, str(e), _traceback.format_exc())
                    if not permanent:
                        capture_task_error(e, task_id=task_id, tenant_id=tenant_id,
                                           token=(payload or {}).get("token", ""))
                    log_task_event("failed", task_id=task_id, user_id=tenant_id,
                                   error_message=str(e), error_type=type(e).__name__)
                    # v0.63.1: 余额/鉴权/内容违规为永久性错误 → 不重试直接终态
                    await self.handle_task_failure(task_id, str(e), permanent=permanent)
                    clear_trace_context()
                    return None
                    
            except Exception as e:
                logger.error(f"任务处理失败: {e}")
                return None
    
    async def handle_task_failure(self, task_id: str, error_message: str, permanent: bool = False):
        """
        处理任务失败（自动重试机制）
        
        Args:
            task_id: 任务UUID
            error_message: 错误信息
            permanent: 永久性错误（余额/鉴权/内容违规）→ 跳过重试直接终态 failed
        
        流程：
        1. 检查重试次数
        2. 如果未达到最大重试次数，更新状态为pending并增加retry_count
        3. 如果达到最大重试次数，更新状态为failed

        v0.63.1 架构优化 R6: DB 部分走 _handle_failure_sync（to_thread），
        日志留在协程侧（ContextVar trace_id 不跨线程）。
        """
        outcome = await asyncio.to_thread(
            self._handle_failure_sync, task_id, error_message, permanent)
        if outcome is None:
            logger.error(f"任务{task_id}不存在")
            return
        if outcome["retried"]:
            log_task_event("retried", task_id=task_id,
                           retry_count=outcome["retry_count"],
                           max_retries=outcome["max_retries"],
                           error_message=error_message)
        else:
            log_task_event("failed", task_id=task_id, error_message=error_message,
                           retry_count=outcome["retry_count"],
                           max_retries=outcome["max_retries"], permanent=True)

    def _handle_failure_sync(self, task_id: str, error_message: str,
                             permanent: bool = False) -> Optional[Dict[str, Any]]:
        """同步失败处理（纯 DB，供 asyncio.to_thread 调用）。

        SELECT 任务详情 → 未到重试上限且非永久 → UPDATE pending + retry_count+1；
        否则 UPDATE failed + 店铺埋点。返回 {retried, retry_count, max_retries}
        或 None（任务不存在）。日志由调用方（协程侧）输出。
        """
        select_sql = text("""
            SELECT retry_count, max_retries, payload
            FROM ozon_product_tasks
            WHERE id = :task_id
        """)
        with self.engine.connect() as conn:
            result = conn.execute(select_sql, {"task_id": task_id})
            task_row = result.fetchone()
        if not task_row:
            return None
        retry_count = task_row[0]
        max_retries = task_row[1]
        payload_raw = task_row[2] if len(task_row) > 2 else None
        payload = payload_raw if isinstance(payload_raw, dict) else (
            json.loads(payload_raw) if payload_raw else {}
        )
        ozon_client_id = str(payload.get("ozon_client_id", "")) if payload else ""

        if not permanent and retry_count < max_retries:
            with self.engine.connect() as conn:
                conn.execute(text("""
                    UPDATE ozon_product_tasks
                    SET status = 'pending',
                        retry_count = :retry_count,
                        error_message = :error_message,
                        updated_at = NOW()
                    WHERE id = :task_id
                """), {
                    "task_id": task_id,
                    "retry_count": retry_count + 1,
                    "error_message": error_message,
                })
                conn.commit()
            return {"retried": True, "retry_count": retry_count + 1, "max_retries": max_retries}

        with self.engine.connect() as conn:
            conn.execute(text("""
                UPDATE ozon_product_tasks
                SET status = 'failed',
                    error_message = :error_message,
                    completed_at = NOW()
                WHERE id = :task_id
            """), {
                "task_id": task_id,
                "error_message": error_message,
            })
            _upsert_shop_usage(
                conn,
                ozon_client_id,
                task_delta=1,
                error_message=error_message,
            )
            conn.commit()
        # M0.3: draft_submissions 状态写回（在 commit 之后，不扩事务）
        _writeback_status(task_id, "failed", error_message)
        return {"retried": False, "retry_count": retry_count, "max_retries": max_retries}

    async def _heartbeat(self, task_id: str) -> None:
        """每 60s 刷新任务 updated_at —— 健康但慢的任务（生图轮询 180s/LLM 长耗时）不再被
        main._periodic_task_cleanup 的「30 分钟未更新 → stale」误判卡死重置（v0.26 保活）。"""
        if not task_id or task_id == "unknown":
            return
        try:
            while True:
                await asyncio.sleep(60)
                try:
                    # v0.63.1 架构优化 R6: 心跳 DB 更新走线程池，不在事件循环上执行
                    await asyncio.to_thread(self._heartbeat_tick, task_id)
                except Exception:
                    pass  # 心跳失败不阻断主流程，下次周期再试
        except asyncio.CancelledError:
            pass

    def _heartbeat_tick(self, task_id: str) -> None:
        """同步心跳单次更新（纯 DB，供 asyncio.to_thread 调用）。"""
        with self.engine.connect() as conn:
            conn.execute(text(
                "UPDATE ozon_product_tasks SET updated_at = NOW() "
                "WHERE id = :tid AND status = 'running'"
            ), {"tid": task_id})
            conn.commit()

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
        task_id = payload.get("task_id", "unknown")
        # ⚠️ v0.26: 心跳保活 — 图执行期间每 60s 刷新 updated_at，防慢任务被 stale 清理重置
        heartbeat = asyncio.create_task(self._heartbeat(task_id))
        # ✅ v0.26 Sentry: 任务级 transaction（trace 视图看全链路节点耗时）
        transaction = None
        try:
            from utils.sentry_setup import start_task_transaction
            transaction = start_task_transaction(task_id=task_id, tenant_id=payload.get("tenant_id", ""))
        except Exception:
            transaction = None
        try:
            from langchain_core.runnables import RunnableConfig
            from main import update_progress, set_current_task_id

            # ✅ v0.10: 设置全局当前 task_id，使 ProgressLogger 能自动获取
            set_current_task_id(task_id)
            config = RunnableConfig(
                configurable={"thread_id": task_id},
                run_name=f"task_{task_id}",
                metadata={"task_id": task_id},
                callbacks=[ProgressCallback(task_id, update_progress, transaction=transaction)],
            )
            # v0.63.1 架构优化 R5: 队列主路径刻意不用 LangGraph checkpointer——
            # 30~50 并发任务写同一 PG checkpoint 会引入锁竞争；超时/重启后从零重跑
            # 的图片额度由 task_image_cache（task_id+slot+version 缓存）兜底。
            # 这是设计约束而非缺陷（/run 等低频端点才启用 PostgresSaver）。
            result = await asyncio.wait_for(
                main_graph.ainvoke(payload, config=config),
                timeout=timeout
            )
            return result

        except TimeoutError:
            raise TimeoutError(f"LangGraph流程执行超时（{timeout}秒）")
        finally:
            # 停止心跳
            heartbeat.cancel()
            # ✅ v0.26 Sentry: 结束任务 transaction
            try:
                from utils.sentry_setup import finish_transaction
                finish_transaction(transaction, status="ok")
            except Exception:
                pass
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
            # ✅ v0.29(PRD-cicd-stability): 优雅关闭 — 不再接收新任务
            try:
                from main import is_shutting_down
                if is_shutting_down():
                    logger.info("🛑 Worker 收到关闭信号, 停止拉取新任务")
                    break
            except Exception:
                pass
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

"""R3a: 任务队列路由族（自 main.py 迁出）——提交 / 状态 / 取消 / 重提交 / 统计。

端点（legacy 绝对路径，main app.include_router 注册）：
    POST /submit_task                skill 直连提交
    GET  /task_status/{task_id}      任务状态（v0.73 Bearer + 租户校验）
    POST /cancel_task/{task_id}      （v0.76 T6 Bearer + 租户校验）
    POST /resubmit_task/{task_id}    终态重提交（P0-2 / race-L1 余额预检）
    GET  /task_statistics            （v0.76 T7 Bearer + 租户强制）
v1 别名（v1_router，prefix=/api/v1，main app.include_router 注册）：
    POST /api/v1/submit_task、GET /api/v1/task_status/{task_id}、
    POST /api/v1/cancel_task/{task_id}、POST /api/v1/resubmit_task/{task_id}、
    GET /api/v1/task_statistics（转发型，装饰器与原 main 内定义逐字等价）。

依赖纪律（R3a）：任务处理器单例经 orchestrator.task_processor holder 取
（lifespan 注入；测试打 ``orchestrator.task_processor._task_processor``）；
鉴权/限流/进度一律走权威模块（api.security / runtime.rate_limit /
runtime.progress），本模块禁止 ``from main import``。
"""
from __future__ import annotations

import copy
import traceback
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from api.errors import WorkerErrorCode, error_response
from api.schemas import (
    CancelTaskResponse,
    ErrorBody,
    SubmitTaskRequest,
    SubmitTaskResponse,
    TaskStatisticsResponse,
    TaskStatusResponse,
)
from api.security import (
    _authenticate_token,
    _balance_source_label,
    _check_mxou_balance,
    _require_bearer,
    _task_status_guard,
)
from orchestrator.task_processor import get_task_processor
from runtime.progress import STAGE_ORDER, get_progress
from runtime.rate_limit import RATE_LIMIT_PER_MINUTE, rate_limiter
from services.tenant_service import resolve_analytics_scope
from storage.database.db import get_engine
from utils.draft_sanity import validate_draft_sanity
from utils.envelope_contract import envelope_strict_enabled, validate_envelope
from utils.logger import get_logger, log_task_event, set_trace_context
from utils.ozon_client import ozon_check_quota

logger = get_logger(__name__)

router = APIRouter()
v1_router = APIRouter(prefix="/api/v1", tags=["v1"])


def _validate_draft_required_fields(draft: dict, extensions: dict) -> str | None:
    """返回缺失字段错误信息；通过返回 None。
    api 强制跟卖（follow_sell + follow_type=api）走 import-by-sku 复制竞品，
    不需要 1688 货源字段（weight/dimensions/purchase_cost/purchase_url），
    但 ⚠️ v0.26 FIX: 必须带 ozon_product_id（竞品身份）——否则是空壳信封，
    worker 拿不到竞品卡去复制（原漏洞：空壳能通过校验，定价/属性全缺被 Ozon 拒）。"""
    _is_api_follow = bool(
        (extensions or {}).get("follow_sell")
        and str((extensions or {}).get("follow_type") or "hand").lower() == "api"
    )
    base_fields = [("item_id", str), ("title", str), ("currency", str), ("images", list)]
    source_fields = [
        ("weight", (int, float)), ("dimensions", dict),
        ("purchase_cost", (int, float)), ("purchase_url", str),
    ]
    required = base_fields if _is_api_follow else base_fields + source_fields
    missing = []
    for field, expected_type in required:
        val = draft.get(field)
        if val is None or (isinstance(val, (str, list, dict)) and not val):
            missing.append(f"draft.{field}")
        elif not isinstance(val, expected_type):
            if expected_type == (int, float) and isinstance(val, (int, float)):
                continue
            missing.append(f"draft.{field}(类型错误: 期望{expected_type}, 实际{type(val).__name__})")
    # ⚠️ v0.26: api 跟卖必须带竞品身份（ozon_product_id 或 competitor_price 二选一），
    # 否则拦截（防空壳信封——图搜无货源仍提交）
    if _is_api_follow:
        if not str(draft.get("ozon_product_id") or "").strip() and not str(draft.get("competitor_price") or "").strip():
            missing.append("draft.ozon_product_id/competitor_price（api 跟卖必须有竞品身份）")
    return f"envelope.draft 缺少必填字段: {', '.join(missing)}" if missing else None


# ==================== Supabase任务队列API ====================


def _find_existing_task(tenant_id: str, sku_key: str) -> Optional[tuple[str, str]]:
    """查询同租户同 SKU 的活跃任务（pending/running），返回 (task_id, status) 或 None。

    P0-1: 提交层去重防线 — 防同一商品重跑产生重复 Ozon listing。
    去重查询失败时放行（fail-open，不因去重引入新的 500）。
    """
    from sqlalchemy import text
    try:
        _engine = get_engine()
        with _engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status FROM ozon_product_tasks "
                    "WHERE tenant_id = :t AND sku_key = :k "
                    "AND status IN ('pending', 'running') "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": tenant_id, "k": sku_key},
            ).fetchone()
        if row:
            return str(row[0]), str(row[1])
        return None
    except Exception as e:
        logger.warning("SKU 去重查询失败，跳过去重检查: %s", str(e)[:200])
        return None

def _write_direct_submission_row(task_id: str, tenant_id: str, ozon_client_id: str) -> None:
    """直连提交（skill submit_task 端点）写 draft_submissions 行 — 非致命。

    A4 决策：所有任务（skill 直连 + 采集箱提交）都有 draft_submissions 行，
    提交历史/在售货架/生命周期视图对两条路径完全统一。直连任务：
    - draft_id=NULL（无草稿）
    - credential_id=credentials 表按 (tenant_id, ozon_client_id) 反查（标量子查询注入，
      未登记则 NULL）——修复 T9 索引回填因 credential_id=NULL 跳过（W6）
    - store_client_id=payload 的 ozon_client_id、status='pending'（终态由 M0.3 写回）
    - submitted_task_id=task_id、extensions=NULL

    采集路径由 draft_service.submit_draft 自行写行（draft_id 有值）——本辅助只被
    http_submit_task 端点层调用，绝不进 task_processor.submit_task（否则双写）。

    写行失败仅 warning，绝不阻断任务入队（与 M0.2 同纪律）。
    """
    try:
        from sqlalchemy import text
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO draft_submissions "
                    "(draft_id, credential_id, store_client_id, extensions, status, submitted_task_id, tenant_id) "
                    "VALUES (NULL, "
                    "(SELECT id FROM credentials "
                    " WHERE tenant_id = :tenant_id AND ozon_client_id = :store_client_id "
                    "   AND status = 'active' ORDER BY created_at DESC LIMIT 1), "
                    ":store_client_id, NULL, 'pending', :task_id, :tenant_id)"
                ),
                {
                    "tenant_id": tenant_id,
                    "store_client_id": ozon_client_id,
                    "task_id": task_id,
                },
            )
    except Exception as exc:
        logger.warning(
            "直连提交写 draft_submissions 行失败（不阻断）task=%s tenant=%s client=%s: %s",
            task_id, tenant_id, ozon_client_id, str(exc)[:200],
        )


@router.post("/submit_task", responses={
    200: {"content": {"application/json": {"example": {
        "ok": True,
        "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "message": "Task submitted to queue (user: 28, balance: 12.5)",
    }}}}})
async def http_submit_task(request: Request):
    """
    提交任务到Supabase云端队列（方案2：验证token + 提交到队列，不立即执行拓扑）

    Args:
        payload: 任务数据（必须包含token、ozon_client_id、ozon_api_key、envelope）
        priority: 任务优先级（0-100，VIP用户使用更高优先级）
        timeout_seconds: 任务超时时间（默认30分钟）
        max_retries: 最大重试次数（默认3次）

    Returns:
        task_id: 任务UUID
        user_id: 用户ID（从token中提取）
        balance: 用户余额（可选）
    """
    task_processor = get_task_processor()
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        body = await request.json()

        # ✅ 链路追踪：生成 trace_id
        trace_id = uuid.uuid4().hex[:12]
        set_trace_context(trace_id=trace_id)

        # ✅ Step1: 从payload中提取认证参数（兼容直连格式和包装格式）
        payload = body.get("payload") or body
        token = payload.get("token", "")
        ozon_client_id = payload.get("ozon_client_id", "")
        ozon_api_key = payload.get("ozon_api_key", "")
        envelope = payload.get("envelope", {})

        # ✅ v4: 提交层 envelope 结构校验 — 避免无效信封穿透到管线 node 层才报错
        if not isinstance(envelope, dict) or not envelope:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                "envelope 不能为空，必须包含 draft 字段",
                detail={"missing": ["envelope.draft"]},
            )
        draft = envelope.get("draft")
        if not isinstance(draft, dict) or not draft:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                "envelope.draft 不能为空",
                detail={"missing": ["draft"]},
            )
        # 必填字段校验（v0.22 P1: 抽成可测函数；api 强制跟卖跳过 1688 货源字段）
        _missing = _validate_draft_required_fields(draft, envelope.get("extensions") or {})
        if _missing:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                _missing,
                detail={"missing": _missing},
            )
        # weight 和 dimensions 的合理性校验
        weight_g = draft.get("weight", 0)
        dims = draft.get("dimensions", {})
        if isinstance(weight_g, (int, float)) and weight_g < 0:
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                f"draft.weight 不能为负数: {weight_g}",
            )
        for dim_key in ("length", "width", "height"):
            dv = dims.get(dim_key, 0) if isinstance(dims, dict) else 0
            if isinstance(dv, (int, float)) and dv < 0:
                return error_response(
                    WorkerErrorCode.INVALID_REQUEST,
                    f"draft.dimensions.{dim_key} 不能为负数: {dv}",
                )

        # v0.21 P2: 物理合理性防线 — 脏重量/脏尺寸直接拒绝，防止打爆定价
        # C2: 传 envelope.extensions（竞品 competitor_weight_g/competitor_dimensions_mm 可放行）
        sanity_err = validate_draft_sanity(draft, envelope.get("extensions") or {})
        if sanity_err:
            logger.warning("❌ 信封数据异常被拒: %s", sanity_err)
            return error_response(
                WorkerErrorCode.INVALID_REQUEST,
                f"信封数据异常: {sanity_err}",
                detail={"sanity": sanity_err},
            )

        # ✅ W2 信封契约硬化：未知顶层/未知 extensions 键 fail-closed（权威 =
        # utils/envelope_contract.py；ENVELOPE_STRICT=0 降级 warn，存量旧包逃生门）。
        # 只在边界校验——worker 在 ingest 后注入 box_reviewed/update_* 等键不受影响。
        envelope_errors = validate_envelope(envelope)
        if envelope_errors:
            if envelope_strict_enabled():
                return error_response(
                    WorkerErrorCode.INVALID_REQUEST,
                    f"信封契约校验失败（{len(envelope_errors)} 项）",
                    detail={"envelope_errors": envelope_errors},
                )
            logger.warning("⚠️ 信封契约校验失败（ENVELOPE_STRICT=0 降级放行）: %s", envelope_errors)

        # ✅ Step2: 验证token（查询Supabase tokens表）
        if not token:
            raise HTTPException(status_code=401, detail="Token is required")

        raw_token = token  # T21(race-M5): 限流键保持原始 token（含 sk- 前缀）形态不变

        # Step2: 处理sk-前缀
        if token.startswith("sk-"):
            token = token.replace("sk-", "", 1)  # ✅ 剥离第一个sk-前缀

        # Step3: MXOU key 即用户 —— 租户以真实 Supabase 为准（resolve_tenant 已做 Supabase→哈希回退）。
        # v0.62.4 修复：改用 resolve_tenant 而非 _key_user_id（哈希租户），
        # 否则余额降级查 users 表用哈希 id 查不到真实用户 → 误判余额不足，
        # 且任务 tenant_id 与全系统(WebUI/采集箱)不一致造成租户漂移。
        from services.tenant_service import resolve_tenant
        user_id = resolve_tenant(token)

        # ✅ 限流检查（T21(race-M5) 后置：移到 resolve_tenant 凭证校验之后——
        # 未认证洪水不再写限流键（与 logistics/analytics 端点 T6-T10 后的
        # 「鉴权先行」次序对齐）；键仍按原始 token（含 sk- 前缀）。保持在余额
        # 检查之前，超限 token 不会打到 MXOU 外部接口）
        allowed, remaining = rate_limiter.check(raw_token)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute"
            )

        balance, has_quota = _check_mxou_balance({"key": token, "user_id": user_id})
        if not has_quota:
            # v0.64.1 B3: 402 文案带来源标识便于定位误报（订阅 0 哨兵 vs 真欠费 vs
            # Supabase 兜底）。真欠费（MXOU 实查负数）保持原文案与拒绝行为完全不变。
            if isinstance(balance, (int, float)) and balance < 0:
                _msg = f"MXOU 余额不足 (current: {balance}). 请充值"
            else:
                _src = _balance_source_label({"key": token, "user_id": user_id}, balance)
                _msg = (
                    f"MXOU 余额不足 (current: {balance}). 请充值 "
                    f"(source: {_src}, raw_balance: {balance})"
                )
            return error_response(
                WorkerErrorCode.INSUFFICIENT_BALANCE,
                _msg,
            )

        # ✅ Step3: 提交任务到队列（使用user_id作为tenant_id）
        # ✅ v0.75 C7: priority 开放（BL-25 Phase 2-5）——读请求体可选 priority
        # （顶层 body，与 timeout_seconds/max_retries 同位同读法）。缺省/非数字 → 0
        # （容错与该端点其余数值字段一致，不 422 不 500）；越界 clamp [0,100]
        # （对齐本端点 docstring 口径）。认领 SQL 零改动（task_processor
        # ORDER BY priority DESC, created_at ASC 已就绪）。
        try:
            priority = min(100, max(0, int(body.get("priority", 0) or 0)))
        except (TypeError, ValueError):
            priority = 0
        timeout_seconds = body.get("timeout_seconds", 1800)
        max_retries = body.get("max_retries", 3)

        # ✅ Step3.5: 检查 Ozon 店铺配额（提前拒绝，避免浪费 MXOU 生图/LLM 额度）
        if ozon_client_id and ozon_api_key:
            try:
                quota = ozon_check_quota(
                    client_id=ozon_client_id,
                    api_key=ozon_api_key,
                    timeout=5,  # submit 阶段只做快速检查
                )
                if not quota["ok"]:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"Ozon 店铺配额不足: "
                            f"日创建 {quota['daily_used']}/{quota['daily_limit']}"
                            f", 总产品 {quota['total_used']}/{quota['total_limit']}"
                            f"。请等待配额重置或归档旧产品。"
                        )
                    )
                if quota["remaining_daily"] <= 3:
                    logger.warning(
                        "店铺 %s 创建配额紧张: 日剩余 %d, 总剩余 %d",
                        ozon_client_id,
                        quota["remaining_daily"],
                        quota["remaining_total"],
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("submit_task 阶段配额检查异常（允许继续）: %s", str(e)[:200])

        # ✅ Step4: payload中添加user_id（供下游节点使用）
        payload_with_user_id = {
            **payload,
            "user_id": user_id,  # ✅ 添加user_id字段
            "envelope": envelope
        }

        # ✅ P0-1: SKU 级重复提交防护 — 同一用户同一店铺同一商品已有活跃任务则拒绝二次入队
        #   跟卖走 draft.ozon_product_id，1688 走 draft.item_id，兜底 draft.sku_id；
        #   无任何商品 ID 时跳过去重（sku_key 为空）。
        #   ⚠️ v0.38.1: sku_key 加入店铺维度 {user}:{ozon_client_id}:{product}——
        #   修复同用户两店铺提交同款被误拦（N8 多店铺与 N1 去重冲突）。
        _store_dim = str(ozon_client_id or "").strip()
        sku_key = (
            f"{user_id}:{_store_dim}:{str(draft.get('ozon_product_id') or draft.get('item_id') or draft.get('sku_id') or '').strip()}"
            if _store_dim else
            f"{user_id}:{str(draft.get('ozon_product_id') or draft.get('item_id') or draft.get('sku_id') or '').strip()}"
        )
        if sku_key.endswith(":"):
            sku_key = ""
        if sku_key:
            existing = _find_existing_task(user_id, sku_key)
            if existing:
                existing_id, existing_status = existing
                log_task_event(
                    "duplicate_submit_blocked", task_id=existing_id, user_id=user_id,
                    trace_id=trace_id, sku_key=sku_key, status=existing_status,
                )
                return error_response(
                    WorkerErrorCode.DUPLICATE_SUBMIT,
                    f"该商品已在提交队列 (task_id={existing_id})，请勿重复提交",
                    detail={"task_id": existing_id, "status": existing_status},
                )

        # F-C06（2026-09-09 审计）：去重 SELECT 与 INSERT 之间的并发窗口由
        # 部分唯一索引 uq_ozon_product_tasks_tenant_sku 兜底——撞索引映射为
        # 干净的 409（此前 IntegrityError 冒泡成 500，客户端重试放大窗口）。
        try:
            task_id = await task_processor.submit_task(
                tenant_id=user_id,
                payload=payload_with_user_id,
                priority=priority,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                sku_key=sku_key,
            )
        except IntegrityError:
            log_task_event("duplicate_submit_blocked", user_id=user_id,
                           trace_id=trace_id, sku_key=sku_key, status="unique_index")
            return error_response(
                WorkerErrorCode.DUPLICATE_SUBMIT,
                "该商品已在提交队列（并发提交命中唯一约束），请勿重复提交",
            )

        # ✅ 更新 trace context + 生命周期日志
        set_trace_context(task_id=task_id, user_id=user_id)
        log_task_event("submitted", task_id=task_id, user_id=user_id,
                       trace_id=trace_id, priority=priority, timeout_seconds=timeout_seconds)

        # ✅ M0.7: 直连提交写 draft_submissions 行（A4：所有任务都有 submission 行）。
        #   非致命——写行失败不阻断任务入队；采集路径由 draft_service 自行写行不在此处。
        _write_direct_submission_row(task_id, user_id, ozon_client_id)

        return {
            "ok": True,
            "task_id": task_id,
            "message": f"Task submitted to queue (user: {user_id}, balance: {balance})"
        }

    except HTTPException:
        raise  # 直接抛出HTTP异常
    except Exception as e:
        # T2(api-M1): 500 detail 固定文案（str(e) 可能携带内部信息），异常细节只进日志
        logger.exception("提交任务失败")
        raise HTTPException(status_code=500, detail="Failed to submit task")


# T3(crypto-H1): payload 内可能出现凭证的键名集合（大小写不敏感匹配）。
_PAYLOAD_SECRET_KEYS = frozenset({"token", "ozon_api_key", "api_key", "secret", "password", "client_secret"})


def _redact_payload(payload):
    """T3(crypto-H1): task_status 出口对 payload 做键名级凭证脱敏（深拷贝，不改原 dict）。
    payload JSONB 存提交时 GraphInput 原文（顶层 token / ozon_api_key 明文），原样回显
    即泄漏。覆盖顶层与任意嵌套 dict/list；非字符串值不动（键名命中但值是 dict/list
    → 不替换、继续下钻）；顶层非 dict（None/list/str）原样透传。REST/MCP 同源生效
    （MCP get_task_status 走本进程 REST 回调）。"""
    def walk(obj):
        if isinstance(obj, dict):
            return {k: ("[REDACTED]" if str(k).lower() in _PAYLOAD_SECRET_KEYS and isinstance(v, str) else walk(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj
    return walk(copy.deepcopy(payload))


@router.get("/task_status/{task_id}", responses={
    200: {"content": {"application/json": {"example": {
        # ozon_product_tasks 行 + 终态归位后的 progress（v0.19）
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "tenant_id": "28",
        "status": "running",
        "priority": 0,
        "result": None,
        "error_message": None,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": "2026-09-11T08:00:00+00:00",
        "updated_at": "2026-09-11T08:02:30+00:00",
        "started_at": "2026-09-11T08:01:00+00:00",
        "completed_at": None,
        "timeout_seconds": 1800,
        "progress": {"stage": "image_generation", "percent": 61,
                     "stages_completed": ["auth", "ingest", "category_match",
                                          "pricing", "attributes", "description", "image_generation"],
                     "stages_remaining": ["prepare_ozon_upload", "ozon_validate",
                                          "check_quota", "ozon_upload", "ozon_status", "learning_record"]},
    }}}}, 401: {"model": ErrorBody}, 404: {"model": ErrorBody}})
async def http_task_status(task_id: str, request: Request):
    """
    查询任务状态（含进度信息）

    v0.73: Bearer 鉴权 + 租户校验（``_task_status_guard``）。

    Returns:
        任务详情（包含status、result、error_message、progress等）
    """
    task_processor = get_task_processor()
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        task_status = await task_processor.get_task_status(task_id)

        if task_status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # ✅ v0.73: 鉴权 + 租户校验（取到 row 后；HTTPException 由下面的
        # except HTTPException 直通，不再被兜底 except 吞成 500）
        _task_status_guard(request, task_status)

        # ✅ v0.19: 终态优先——completed/failed 时 progress 直接归位，
        # 不再显示内存里残留的中间阶段（如 0%/social_proof_gen）
        if task_status.get("status") == "completed":
            task_status["progress"] = {
                "stage": "completed", "percent": 100,
                "stages_completed": STAGE_ORDER, "stages_remaining": []
            }
        elif task_status.get("status") == "failed":
            task_status["progress"] = {
                "stage": "failed", "percent": 100,
                "message": task_status.get("error_message", "")
            }
        elif task_status.get("status") == "rejected":
            # ✅ P0-2: rejected 是终态 — progress 直接归位，指引用户走重新提交
            task_status["progress"] = {
                "stage": "rejected", "percent": 100,
                "message": "审核被拒，可调用 /resubmit_task/{task_id} 重新提交",
            }
        else:
            progress = get_progress(task_id)
            if progress:
                task_status["progress"] = progress

        # ✅ T3(crypto-H1): payload 存提交时 GraphInput 原文（token/ozon_api_key 明文），
        # 出口必须脱敏。旧路径（无 response_model）与 /api/v1 别名共用此组装点，
        # 单点应用即双路径生效；非 dict payload 原样透传。
        if isinstance(task_status.get("payload"), dict):
            task_status["payload"] = _redact_payload(task_status["payload"])

        return task_status

    except HTTPException:
        raise  # v0.73: 404/401/租户 404 直通（此前被吞成 500，与 v1 docs 的 404 约定不符）
    except Exception as e:
        # T2(api-M1 补): 500 detail 固定文案，异常细节只进日志
        logger.exception("查询任务状态失败")
        raise HTTPException(status_code=500, detail="Failed to get task status")


@router.post("/cancel_task/{task_id}", responses={
    200: {"content": {"application/json": {"example": {
        "status": "success",
        "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "message": "Task cancelled successfully",
    }}}}, 401: {"model": ErrorBody}, 404: {"model": ErrorBody},
    409: {"content": {"application/json": {"example": {
        # 不可取消（非 pending/处理异常）→ TASK_NOT_CANCELLABLE 统一错误信封
        "ok": False,
        "error_code": "TASK_NOT_CANCELLABLE",
        "message": "Task 3fa85f64-5717-4562-b3fc-2c963f66afa6 cannot be cancelled (may not in pending status)",
    }}}}})
async def http_cancel_task(task_id: str, request: Request):
    """
    取消任务（仅pending状态的任务可取消）

    T6(api-H2): 补 Bearer 鉴权 + 租户校验（语义与 task_status v0.73 同源，
    复用 ``_task_status_guard``：TASK_STATUS_AUTH=0 应急关 / 无 Bearer 401 /
    跨租户 404 "task not found" 不泄漏存在性 / 老数据无租户宽容放行）。
    修复前匿名持有 task uuid 即可跨租户取消任意 pending 任务（安全探针实证）。

    Returns:
        取消结果
    """
    task_processor = get_task_processor()
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        # ✅ T6(api-H2): 先查归属（不存在 → 404，与 task_status 同序），再
        # 鉴权 + 租户校验，放行后才执行取消。HTTPException 由下面的
        # except HTTPException 直通，不被兜底 except 吞成 500。
        task_row = await task_processor.fetch_task_owner(task_id)
        if not task_row:
            raise HTTPException(status_code=404, detail="task not found")
        _task_status_guard(request, task_row)

        success = await task_processor.cancel_task(task_id)

        if success:
            return {
                "status": "success",
                "task_id": task_id,
                "message": "Task cancelled successfully"
            }
        else:
            # B4 (2026-09-11 仓库治理, A5 §2 D-01)：不可取消（非 pending /
            # 处理异常）由 200+{status:failed} 改返 409 + TASK_NOT_CANCELLABLE
            # 统一错误信封——原 200 形态客户端无法编程区分成败（该错误码此前零接线）。
            return error_response(
                WorkerErrorCode.TASK_NOT_CANCELLABLE,
                f"Task {task_id} cannot be cancelled (may not in pending status)",
            )

    except HTTPException:
        raise  # T6: 401/404/租户 404 直通（同 http_task_status v0.73 处理）
    except Exception as e:
        # T2(api-M1 补): 500 detail 固定文案，异常细节只进日志
        logger.exception("取消任务失败")
        raise HTTPException(status_code=500, detail="Failed to cancel task")


@router.post("/resubmit_task/{task_id}", responses={
    200: {"content": {"application/json": {"example": {
        "ok": True,
        "task_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "message": "任务 3fa85f64-5717-4562-b3fc-2c963f66afa6 已重新提交（rejected → pending，parent_task_id=3fa85f64-5717-4562-b3fc-2c963f66afa6）",
    }}}}, 402: {"content": {"application/json": {"example": {
        "ok": False,
        "error_code": "INSUFFICIENT_BALANCE",
        "message": "MXOU 余额不足 (current: -5.0). 请充值",
    }}}}, 409: {"content": {"application/json": {"example": {
        "ok": False,
        "error_code": "TASK_NOT_RESUBMITTABLE",
        "message": "任务状态 completed 不可重新提交，仅 rejected/failed 终态任务可重试",
        "detail": {"task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "status": "completed"},
    }}}}})
async def http_resubmit_task(task_id: str, request: Request):
    """重新提交终态任务（审核被拒/失败自动修复链入口，P0-2）。

    仅 rejected/failed 终态任务可重试：复制原载荷 → 注入 parent_task_id +
    extensions.image_regen=True → 重新入队（pending）。返回新任务 task_id。

    ⚠️ v0.38.1 安全修复：请求体必须携带调用者 token（与 submit_task 一致），
    校验 token 归属租户 == 任务 tenant_id，防跨租户凭证重放（CRITICAL）。

    ⚠️ race-L1（v0.76 Task 26）：与 submit_task 同款两段——入队前
    _check_mxou_balance 余额预检（欠费 → 402，重提交重跑生图/LLM 同样烧额度）；
    入队 IntegrityError（并发撞部分唯一索引）→ 干净 409 DUPLICATE_SUBMIT
    （此前冒泡成 500）。
    """
    task_processor = get_task_processor()
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    try:
        body = await request.json()
        token = (body.get("payload") or body).get("token", "")
        caller_user_id = _authenticate_token(token)

        task_status = await task_processor.get_task_status(task_id)
        if task_status is None:
            return error_response(
                WorkerErrorCode.TASK_NOT_FOUND,
                f"Task {task_id} not found",
            )

        task_tenant = str(task_status.get("tenant_id") or "")
        if task_tenant and caller_user_id != "local_dev" and task_tenant != caller_user_id:
            return error_response(
                WorkerErrorCode.TASK_NOT_FOUND,
                f"Task {task_id} not found",
            )

        status = str(task_status.get("status") or "")
        if status not in ("rejected", "failed"):
            return error_response(
                WorkerErrorCode.TASK_NOT_RESUBMITTABLE,
                f"任务状态 {status} 不可重新提交，仅 rejected/failed 终态任务可重试",
                detail={"task_id": task_id, "status": status},
            )

        # race-L1（v0.76 Task 26）: 余额预检对齐 submit_task——重提交重跑生图/LLM
        # 同样烧 MXOU 额度，欠费 token 不得借重提交绕过 402。在鉴权+归属+状态
        # 校验之后、入队之前执行；key 剥 sk- 前缀 + user_id=caller 租户，与
        # submit_task 同参形态。402 文案逐字同款（含 v0.64.1 B3 来源标识）。
        _balance_key = token[3:] if token.startswith("sk-") else token
        balance, has_quota = _check_mxou_balance({"key": _balance_key, "user_id": caller_user_id})
        if not has_quota:
            if isinstance(balance, (int, float)) and balance < 0:
                _msg = f"MXOU 余额不足 (current: {balance}). 请充值"
            else:
                _src = _balance_source_label({"key": _balance_key, "user_id": caller_user_id}, balance)
                _msg = (
                    f"MXOU 余额不足 (current: {balance}). 请充值 "
                    f"(source: {_src}, raw_balance: {balance})"
                )
            return error_response(
                WorkerErrorCode.INSUFFICIENT_BALANCE,
                _msg,
            )

        # 深拷贝原载荷（避免改到 DB 返回的原始 dict），注入重提交标记
        payload = copy.deepcopy(task_status.get("payload") or {})
        payload["parent_task_id"] = task_id
        envelope = payload.get("envelope")
        if not isinstance(envelope, dict):
            envelope = {}
        extensions = envelope.get("extensions")
        if not isinstance(extensions, dict):
            extensions = {}
        extensions["image_regen"] = True
        envelope["extensions"] = extensions
        payload["envelope"] = envelope

        # sku_key 从原载荷 draft 派生（与 submit_task 去重口径一致，含店铺维度）
        draft = envelope.get("draft") or {}
        product_id = str(
            draft.get("ozon_product_id") or draft.get("item_id") or draft.get("sku_id") or ""
        ).strip()
        tenant_id = str(task_status.get("tenant_id") or "local_dev")
        _store_dim = str((payload or {}).get("ozon_client_id") or "").strip()
        sku_key = (
            f"{tenant_id}:{_store_dim}:{product_id}"
            if _store_dim else
            f"{tenant_id}:{product_id}"
        ) if product_id else ""

        # F-C06 同款（race-L1，v0.76 Task 26）: 去重 SELECT 与 INSERT 之间的并发
        # 窗口由部分唯一索引 uq_ozon_product_tasks_tenant_sku 兜底——重提交撞索引
        # 映射为与 submit_task 同款的干净 409（此前 IntegrityError 冒泡进通用
        # except → 500，客户端重试放大窗口）。
        try:
            new_task_id = await task_processor.submit_task(
                tenant_id=tenant_id,
                payload=payload,
                priority=0,
                timeout_seconds=int(task_status.get("timeout_seconds") or 1800),
                max_retries=int(task_status.get("max_retries") or 3),
                sku_key=sku_key,
            )
        except IntegrityError:
            log_task_event("duplicate_submit_blocked", task_id=task_id, user_id=tenant_id,
                           sku_key=sku_key, status="unique_index")
            return error_response(
                WorkerErrorCode.DUPLICATE_SUBMIT,
                "该商品已在提交队列（并发提交命中唯一约束），请勿重复提交",
            )

        log_task_event("resubmitted", task_id=new_task_id, user_id=tenant_id,
                       parent_task_id=task_id, from_status=status)
        return {
            "ok": True,
            "task_id": new_task_id,
            "message": f"任务 {task_id} 已重新提交（{status} → pending，parent_task_id={task_id}）",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resubmit task error: {e}, traceback: {traceback.format_exc()}")
        # 不向客户端回传 str(e)（防内部路径/DB 细节泄露，v0.38.1）
        return error_response(
            WorkerErrorCode.INTERNAL_ERROR,
            "重提交任务失败，请稍后重试",
        )


@router.get("/task_statistics", responses={
    200: {"content": {"application/json": {"example": {
        "status": "success",
        "statistics": {
            "total": 130, "pending": 2, "running": 5, "completed": 120,
            "failed": 3, "cancelled": 0, "avg_duration_seconds": 210.55,
        },
    }}}}, 401: {"model": ErrorBody}, 403: {"model": ErrorBody}})
async def http_task_statistics(request: Request):
    """
    获取任务统计信息

    T7(api-H3): 补 Bearer 鉴权 + 租户强制。修复前端点完全无鉴权——匿名可枚举
    任意租户任务量，且不传 tenant_id 时 task_processor 层跨全租户聚合。
    规则（保持 MCP get_task_statistics 兼容，其恒传自己租户）：
    - 无 Authorization Bearer → 401 "Token is required"。
    - Bearer 无效 → ``_verify_analytics_token`` 的 401/503 原样透传。
    - query ``tenant_id`` 缺省/为空/等于自己 → 恒查自己租户。
    - ``tenant_id`` 指向他人租户 → 仅 admin（``resolve_analytics_scope`` 放行），
      否则 403 "admin only"。

    Args:
        tenant_id: 租户ID（可选，缺省=自己租户；指定他人租户需 admin）

    Returns:
        任务统计信息（总数、成功率、平均耗时等）
    """
    task_processor = get_task_processor()
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")

    # ✅ T8(api-M2): 鉴权收敛到 ``_require_bearer``（T7 内联三行 → 共享 helper；
    # resolve_tenant 内部自剥 sk-，helper 返回的 clean token 直传等价）。仍在
    # try 外——401/403 是 HTTPException，不会被下面的兜底 except 吞成 500
    # （同 http_task_status 口径）。
    own_token = _require_bearer(request)
    from services.tenant_service import resolve_tenant
    own_tenant = resolve_tenant(own_token)
    q_tenant = request.query_params.get("tenant_id") or ""
    tenant = own_tenant
    if q_tenant and q_tenant != own_tenant:
        scope = resolve_analytics_scope(own_token)
        if not scope.get("is_admin"):
            raise HTTPException(status_code=403, detail="admin only")
        tenant = q_tenant

    try:
        statistics = await task_processor.get_task_statistics(tenant)

        return {
            "status": "success",
            "statistics": statistics
        }

    except HTTPException:
        raise  # T7: 401/403/租户语义 HTTPException 直通（同 http_task_status v0.73 处理）
    except Exception as e:
        # T2(api-M1 补): 500 detail 固定文案，异常细节只进日志
        logger.exception("查询任务统计失败")
        raise HTTPException(status_code=500, detail="Failed to get task statistics")


# ==================== API v1 路由 ====================
# 新端点统一走 /api/v1/ 前缀，旧路径通过重定向兼容

@v1_router.post("/submit_task", response_model=SubmitTaskResponse, tags=["task"],
         responses={401: {"model": ErrorBody}, 402: {"model": ErrorBody},
                    403: {"model": ErrorBody}, 429: {"model": ErrorBody}},
         # 处理函数手读 raw Request（兼容 body.payload 包装），FastAPI 推不出请求体；
         # 这里只补 OpenAPI 元数据，让 /docs 与 API-REFERENCE 能展示 SubmitTaskRequest。
         openapi_extra={"requestBody": {"required": True, "content": {
             "application/json": {"schema": SubmitTaskRequest.model_json_schema()}}}})
async def v1_submit_task(request: Request):
    """提交任务到队列。鉴权通过 Supabase tokens 表校验。"""
    # 委托给现有实现
    return await http_submit_task(request)


@v1_router.get("/task_status/{task_id}", response_model=TaskStatusResponse, tags=["task"],
        responses={401: {"model": ErrorBody}, 404: {"model": ErrorBody}})
async def v1_task_status(task_id: str, request: Request):
    """查询任务状态（v0.73: Bearer 鉴权 + 租户校验，TASK_STATUS_AUTH=0 应急关）。"""
    return await http_task_status(task_id, request)


@v1_router.post("/cancel_task/{task_id}", response_model=CancelTaskResponse, tags=["task"],
         responses={401: {"model": ErrorBody}, 404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def v1_cancel_task(task_id: str, request: Request):
    """取消待处理的任务（v0.76 T6: Bearer 鉴权 + 租户校验，TASK_STATUS_AUTH=0 应急关）。"""
    return await http_cancel_task(task_id, request)


@v1_router.post("/resubmit_task/{task_id}", response_model=SubmitTaskResponse, tags=["task"],
         responses={402: {"model": ErrorBody}, 404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def v1_resubmit_task(task_id: str, request: Request):
    """重新提交被拒(rejected)/失败(failed)的任务（P0-2 自动修复链入口；race-L1: 补余额预检 402 + 并发 IntegrityError 409）。"""
    return await http_resubmit_task(task_id, request)


@v1_router.get("/task_statistics", response_model=TaskStatisticsResponse, tags=["task"],
        responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}})
async def v1_task_statistics(request: Request):
    """获取任务统计信息（v0.76 T7: Bearer 必填 + 租户强制，tenant_id 缺省=查自己，
    跨租户仅 admin——语义与旧路径同源）。

    ⚠️ v0.19.2: 旧路径返回 {"status","statistics"} 包裹结构（无 response_model），
    v1 声明了 TaskStatisticsResponse 响应模型，必须解包 statistics 再返回，
    否则字段对不上被 Pydantic 填默认值 → 统计恒 0。
    """
    resp = await http_task_statistics(request)
    return resp.get("statistics", {})

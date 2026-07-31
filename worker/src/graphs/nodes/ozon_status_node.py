import os
import json
import time
import logging
import requests
from utils.http_session import session
from typing import Dict, Any, List, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import OzonStatusInput, OzonStatusOutput
from utils.progress_logger import ProgressLogger

logger = logging.getLogger(__name__)

# 轮询配置
MAX_POLL_ATTEMPTS = 10
POLL_INTERVAL_SECONDS = 3
# Phase 2: 审核状态轮询（Ozon审核需要较长时间，300s足够覆盖绝大多数产品）
MAX_MODERATE_POLL_ATTEMPTS = 120  # ✅ v0.11: 60→120 (10 分钟，覆盖多数审核)
MODERATE_POLL_INTERVAL_SECONDS = 5


def ozon_status_node(
    state: OzonStatusInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> OzonStatusOutput:
    """
    title: Ozon状态轮询节点
    desc: 上传后轮询Ozon商品状态（最多10次，每3秒间隔），解析错误并触发修复流程
    integrations: Ozon API
    """
    ctx = runtime.context

    progress = ProgressLogger()
    progress.log_node_start("ozon_status_node", "Ozon状态轮询节点")
    progress.log_node_action("正在轮询Ozon商品状态...")

    # ✅ P3 修复：优先使用 ozon_task_id（upload 节点写入的临时任务 ID）
    # product_id 保留向后兼容（旧版 upload 节点直接写 product_id）
    task_id_to_poll = getattr(state, 'ozon_task_id', '') or state.product_id or ""
    product_id = state.product_id or task_id_to_poll
    purchase_url = state.purchase_url
    purchase_cost = state.purchase_cost
    sku_id = state.sku_id
    profit_estimation = state.profit_estimation

    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key

    logger.info(f"开始轮询Ozon商品状态: product_id(task_id)={product_id}")

    errors: List[Dict[str, Any]] = []
    status: str = ""

    try:
        if not product_id:
            logger.error("product_id为空，无法轮询状态")
            return OzonStatusOutput(
                product_id=None,
                product_ids=[],
                status="failed",
                moderation_status="error",
                errors=[{"error": "product_id缺失"}],
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                error_message="product_id缺失",
                stages={"ozon_status": "failed"}
            )

        headers: Dict[str, str] = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json"
        }

        # 防御性类型转换：product_id可能是Ozon的数字task_id，也可能是系统UUID
        task_id_int = None
        try:
            task_id_int = int(float(product_id))
            logger.info(f"task_id类型转换成功: {product_id} -> {task_id_int}")
        except (ValueError, TypeError):
            # product_id不是数字（可能是UUID），说明Ozon API没有返回有效的task_id
            logger.warning(f"product_id不是数字格式: {product_id}，可能是Ozon API未返回task_id")
            # 不直接失败，而是标记为pending，让validation_retry_loop处理
            return OzonStatusOutput(
                product_id=None,
                product_ids=[],
                status="pending",
                moderation_status="pending",
                errors=[],
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                error_message="",
                stages={"ozon_status": "pending"}
            )

        # ===== 阶段1: 轮询 /v1/product/import/info =====
        ozon_api_url: str = "https://api-seller.ozon.ru/v1/product/import/info"
        payload: Dict[str, Any] = {"task_id": task_id_int}

        # 初始化（避免 else 分支引用未定义变量导致 NameError）
        real_product_ids: List[str] = []
        all_item_errors: List[Dict[str, Any]] = []
        total_item_count: int = 0
        has_pending: bool = False

        for attempt in range(MAX_POLL_ATTEMPTS):
            logger.info(f"轮询第{attempt + 1}/{MAX_POLL_ATTEMPTS}次...")
            progress.log_node_action(f"轮询Ozon状态第{attempt + 1}/{MAX_POLL_ATTEMPTS}次...")

            response = session.post(ozon_api_url, headers=headers, json=payload, timeout=60)

            if response.status_code != 200:
                logger.error(f"Ozon API调用失败: {response.status_code}, {response.text[:200]}")
                return OzonStatusOutput(
                    product_id=product_id,
                    product_ids=[],
                    status="failed",
                    moderation_status="error",
                    errors=[{"error": f"Ozon API错误: {response.status_code}"}],
                    purchase_url=purchase_url,
                    purchase_cost=purchase_cost,
                    sku_id=sku_id,
                    profit_estimation=profit_estimation,
                    error_message=f"Ozon API错误: {response.status_code}",
                    stages={"ozon_status": "api_error"}
                )

            result: Dict[str, Any] = response.json()
            result_items: list = result.get("result", {}).get("items", [])

            if not result_items or len(result_items) == 0:
                logger.warning(f"第{attempt + 1}次轮询无结果，等待重试...")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # ✅ P0修复：收集所有变体的product_id和状态（多变体场景）
            all_product_ids: List[str] = []
            total_item_count: int = len(result_items)  # ✅ 总变体数量（用于pending计数）
            all_statuses: List[str] = []
            all_item_errors: List[Dict[str, Any]] = []
            has_failed: bool = False
            has_pending: bool = False
            failed_details: List[str] = []
            first_product_id: Optional[str] = None
            first_item_status: str = "pending"
            first_item_errors: List[Dict[str, Any]] = []

            for item in result_items:
                pid = item.get("product_id")
                if pid is not None:
                    all_product_ids.append(str(pid))
                    if first_product_id is None:
                        first_product_id = str(pid)
                item_status_val: str = item.get("status", "pending")
                all_statuses.append(item_status_val)
                if first_item_status == "pending" and item_status_val != "pending":
                    first_item_status = item_status_val
                item_errs = item.get("errors", [])
                if item_errs:
                    all_item_errors.extend(item_errs)
                    if not first_item_errors:
                        first_item_errors = item_errs
                if item_status_val == "failed":
                    has_failed = True
                    err_details = item.get("errors", [])
                    if err_details:
                        failed_details.append(f"product_id={pid}: {json.dumps(err_details, ensure_ascii=False)[:200]}")
                    else:
                        failed_details.append(f"product_id={pid}: 未知错误")
                elif item_status_val in ("pending", "importing", "processing", "skipped"):
                    has_pending = True

            # 兼容旧代码的变量名
            item_status: str = first_item_status
            item_errors: List[Dict[str, Any]] = first_item_errors
            real_product_id: Optional[str] = first_product_id
            real_product_ids: List[str] = all_product_ids

            logger.info(f"第{attempt + 1}次轮询: {len(all_product_ids)}个变体, statuses={set(all_statuses)}, product_ids={all_product_ids}")

            # 如果有任何变体失败
            if has_failed:
                error_msg = f"部分变体上传失败: {'; '.join(failed_details[:3])}"
                logger.error(f"❌ {error_msg}")
                return OzonStatusOutput(
                    upload_status="failed",
                    product_id=real_product_ids[0] if real_product_ids else None,
                    product_ids=real_product_ids,
                    status="failed",
                    moderation_status="error",
                    errors=all_item_errors,
                    error_message=error_msg,
                    error_code="VARIANT_UPLOAD_FAILED",
                    failed_stage="ozon_status",
                    purchase_url=purchase_url,
                    purchase_cost=purchase_cost,
                    sku_id=sku_id,
                    profit_estimation=profit_estimation,
                    stages={"ozon_status": "failed"},
                )

            # 如果有变体还在处理中
            if has_pending:
                logger.info(f"⏳ 等待所有变体处理完成... statuses={set(all_statuses)}")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # ✅ 所有变体都已imported
            if real_product_ids:
                logger.info(f"✅ 所有{len(real_product_ids)}个变体导入成功: product_ids={real_product_ids}")
                break
        else:
            # 轮询超时
            logger.warning(f"⚠️ 轮询超时（{MAX_POLL_ATTEMPTS}次），已imported={len(real_product_ids)}个变体，仍pending={total_item_count - len(real_product_ids) if has_pending else 0}")
            if real_product_ids:
                # 部分导入成功，继续走moderate_status查询
                pass
            else:
                return OzonStatusOutput(
                    product_id=str(real_product_ids[0]) if real_product_ids else product_id,
                    product_ids=real_product_ids,
                    status="timeout",
                    moderation_status="pending",
                    upload_status="timeout",
                    errors=all_item_errors,
                    purchase_url=purchase_url,
                    purchase_cost=purchase_cost,
                    sku_id=sku_id,
                    profit_estimation=profit_estimation,
                    error_message="",
                    stages={"ozon_status": "timeout"}
                )

        # ===== 阶段2: 查询 /v3/product/info/list 获取所有变体的moderate_status =====
        if real_product_ids and len(real_product_ids) > 0:
            # 构建product_id整数列表
            all_pids_int: List[int] = []
            for pid_str in real_product_ids:
                try:
                    all_pids_int.append(int(pid_str))
                except (ValueError, TypeError):
                    pass

            if not all_pids_int:
                logger.warning("没有有效的product_id，跳过moderate_status查询")
                return OzonStatusOutput(
                    product_id=real_product_ids[0] if real_product_ids else product_id,
                    product_ids=real_product_ids,
                    status="imported",
                    moderation_status="pending",
                    upload_status="success",
                    errors=all_item_errors if all_item_errors else item_errors,
                    purchase_url=purchase_url,
                    purchase_cost=purchase_cost,
                    sku_id=sku_id,
                    profit_estimation=profit_estimation,
                    error_message="",
                    stages={"ozon_status": "success"}
                )

            info_url: str = "https://api-seller.ozon.ru/v3/product/info/list"
            # 一次查询所有变体的product_id
            info_payload: Dict[str, Any] = {
                "product_id": all_pids_int,
                "seller_tag": []
            }

            logger.info(f"查询{len(all_pids_int)}个变体的moderate_status: product_ids={all_pids_int}")

            for attempt2 in range(MAX_MODERATE_POLL_ATTEMPTS):
                logger.info(f"查询moderate_status第{attempt2 + 1}/{MAX_MODERATE_POLL_ATTEMPTS}次...")
                progress.log_node_action(f"查询审核状态第{attempt2 + 1}/{MAX_MODERATE_POLL_ATTEMPTS}次（{len(all_pids_int)}个变体）...")

                info_response = session.post(info_url, headers=headers, json=info_payload, timeout=60)

                if info_response.status_code == 200:
                    info_result: Dict[str, Any] = info_response.json()
                    info_items: list = info_result.get("items", [])

                    if info_items and len(info_items) > 0:
                        # ✅ P0修复：检查所有变体的moderate_status
                        all_approved: bool = True
                        any_rejected: bool = False
                        any_pending: bool = False
                        all_real_errors: List[Dict[str, Any]] = []
                        all_moderate_statuses: Dict[str, str] = {}
                        rejected_details: List[str] = []

                        for info_item in info_items:
                            item_pid: int = info_item.get("id", 0)
                            statuses: Dict[str, Any] = info_item.get("statuses", {})
                            if isinstance(statuses, dict):
                                ms: str = statuses.get("moderate_status", "")
                            else:
                                ms = ""
                            all_moderate_statuses[str(item_pid)] = ms

                            if ms == "approved":
                                continue  # 这个变体通过了
                            elif ms in ("rejected", "declined"):
                                any_rejected = True
                                all_approved = False
                                # 收集错误信息
                                item_errs = info_item.get("errors", [])
                                # Ozon 已知可忽略的错误码（平台自动修正，不算真错误）
                                IGNORABLE_CODES = {9782}  # Ozon可能在审核后擦除危险品等级值，不算真错误
                                real_errs = []
                                for err in item_errs:
                                    if not isinstance(err, dict):
                                        continue
                                    err_code = err.get("code", "")
                                    err_level = err.get("level", "")
                                    # 按 code 白名单跳过（即使 level 是 ERROR）
                                    if isinstance(err_code, int) and err_code in IGNORABLE_CODES:
                                        continue
                                    if isinstance(err_code, str) and err_code.isdigit() and int(err_code) in IGNORABLE_CODES:
                                        continue
                                    if err_level != "ERROR_LEVEL_WARNING":
                                        real_errs.append(err)
                                if real_errs:
                                    all_real_errors.extend(real_errs)
                                    rejected_details.append(f"product_id={item_pid}: {json.dumps(real_errs, ensure_ascii=False)[:200]}")
                                else:
                                    rejected_details.append(f"product_id={item_pid}: 被拒（无ERROR级别错误详情）")
                            else:
                                # pending / processing 等其他状态
                                any_pending = True
                                all_approved = False

                        logger.info(f"moderate_status汇总: {all_moderate_statuses}")

                        if all_approved:
                            logger.info(f"✅ 所有{len(info_items)}个变体审核通过: product_ids={real_product_ids}")

                            # ✅ P0-1: 验证变体是否已合并（model_info检查）
                            # API文档: /v3/product/info/list 返回 model_info: {count, model_id}
                            # count > 1 表示变体已合并到同一商品卡
                            if len(real_product_ids) > 1:
                                model_ids: set = set()
                                model_counts: Dict[int, int] = {}
                                for info_item in info_items:
                                    mi: Dict[str, Any] = info_item.get("model_info", {})
                                    if isinstance(mi, dict):
                                        m_id: Any = mi.get("model_id", 0)
                                        m_count: Any = mi.get("count", 0)
                                        if m_id and isinstance(m_id, int):
                                            model_ids.add(m_id)
                                        if isinstance(m_count, int) and m_count > 0:
                                            model_counts[info_item.get("id", 0)] = m_count

                                logger.info(f"model_info检查: model_ids={model_ids}, model_counts={model_counts}")

                                if len(model_ids) > 1:
                                    # 变体分配到不同model → 未合并
                                    error_msg = f"变体未合并：{len(model_ids)}个不同的model_id={model_ids}，变体各自独立成卡"
                                    logger.error(f"❌ {error_msg}")
                                    return OzonStatusOutput(
                                        product_id=real_product_ids[0],
                                        product_ids=real_product_ids,
                                        status="failed",
                                        moderation_status="error",
                                        upload_status="failed",
                                        error_code="VARIANT_NOT_MERGED",
                                        errors=[{"code": "VARIANT_NOT_MERGED", "message": error_msg, "model_ids": list(model_ids)}],
                                        purchase_url=purchase_url,
                                        purchase_cost=purchase_cost,
                                        sku_id=sku_id,
                                        profit_estimation=profit_estimation,
                                        error_message=error_msg,
                                        stages={"ozon_status": "failed"}
                                    )
                                elif model_counts and all(c <= 1 for c in model_counts.values()):
                                    # 所有变体count=1 → 未合并（正常合并后count应等于变体数）
                                    error_msg = f"变体未合并：model_info.count={model_counts}（全部为1，预期≥{len(real_product_ids)}）"
                                    logger.error(f"❌ {error_msg}")
                                    return OzonStatusOutput(
                                        product_id=real_product_ids[0],
                                        product_ids=real_product_ids,
                                        status="failed",
                                        moderation_status="error",
                                        upload_status="failed",
                                        error_code="VARIANT_NOT_MERGED",
                                        errors=[{"code": "VARIANT_NOT_MERGED", "message": error_msg, "model_counts": model_counts}],
                                        purchase_url=purchase_url,
                                        purchase_cost=purchase_cost,
                                        sku_id=sku_id,
                                        profit_estimation=profit_estimation,
                                        error_message=error_msg,
                                        stages={"ozon_status": "failed"}
                                    )
                                else:
                                    logger.info(f"✅ model_info验证通过: model_id={list(model_ids)}, counts={model_counts}（变体已合并）")

                            return OzonStatusOutput(
                                product_id=real_product_ids[0],
                                product_ids=real_product_ids,
                                status="imported",
                                moderation_status="approved",
                                upload_status="success",
                                errors=[],
                                purchase_url=purchase_url,
                                purchase_cost=purchase_cost,
                                sku_id=sku_id,
                                profit_estimation=profit_estimation,
                                error_message="",
                                stages={"ozon_status": "success"}
                            )

                        if any_rejected:
                            error_msg = f"部分变体审核被拒: {'; '.join(rejected_details[:3])}"
                            logger.error(f"❌ {error_msg}")
                            return OzonStatusOutput(
                                product_id=real_product_ids[0],
                                product_ids=real_product_ids,
                                status="error",
                                moderation_status="error",
                                upload_status="error",
                                errors=all_real_errors,
                                purchase_url=purchase_url,
                                purchase_cost=purchase_cost,
                                sku_id=sku_id,
                                profit_estimation=profit_estimation,
                                error_message=error_msg,
                                error_code="VARIANT_MODERATE_REJECTED",
                                failed_stage="ozon_status",
                                stages={"ozon_status": "failed"}
                            )

                        if any_pending:
                            logger.info(f"⏳ 审核进行中(statuses={set(all_moderate_statuses.values())})，等待{MODERATE_POLL_INTERVAL_SECONDS}秒...")
                            time.sleep(MODERATE_POLL_INTERVAL_SECONDS)
                    else:
                        logger.warning(f"第{attempt2 + 1}次查询moderate_status无结果")
                        time.sleep(MODERATE_POLL_INTERVAL_SECONDS)
                else:
                    logger.warning(f"查询moderate_status API返回{info_response.status_code}")
                    time.sleep(MODERATE_POLL_INTERVAL_SECONDS)

            # moderate_status 轮询超时 — 审核仍在进行中
            mod_retries = getattr(state, 'moderation_retry_count', 0) + 1
            logger.warning(f"⚠️ 审核仍在进行中（已轮询{MAX_MODERATE_POLL_ATTEMPTS * MODERATE_POLL_INTERVAL_SECONDS}s），"
                          f"返回 pending 状态等待重试 ({mod_retries}/3)")
            return OzonStatusOutput(
                product_id=real_product_ids[0] if real_product_ids else product_id,
                product_ids=real_product_ids,
                status="pending",
                moderation_status="pending",
                upload_status="pending",
                errors=[],
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                error_message="",
                stages={"ozon_status": "pending"},
                moderation_retry_count=mod_retries,
            )

        # 如果imported但没有real_product_ids
        logger.info(f"✅ 商品导入成功（无product_id信息）")
        return OzonStatusOutput(
            product_id=real_product_ids[0] if real_product_ids else product_id,
            product_ids=real_product_ids,
            status="imported",
            moderation_status="pending",
            upload_status="success",
            errors=all_item_errors if all_item_errors else item_errors,
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message="",
            stages={"ozon_status": "success"}
        )

    except Exception as e:
        logger.error(f"Ozon状态轮询异常: {str(e)}")
        return OzonStatusOutput(
            product_id=product_id,
            product_ids=[],
            status="failed",
            moderation_status="error",
            errors=[{"error": str(e)}],
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=str(e),
            stages={"ozon_status": "failed"}
        )

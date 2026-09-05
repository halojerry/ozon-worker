# 验证循环修复包装器节点（调用子图）
import logging
from typing import Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

logger = logging.getLogger(__name__)

from graphs.state import (
    ValidationRetryWrapperInput,
    ValidationRetryWrapperOutput,
)

from graphs.validation_retry_loop import validation_retry_loop, ValidationRetryLoopInput


def validation_retry_wrapper_node(
    state: ValidationRetryWrapperInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ValidationRetryWrapperOutput:
    """
    title: 验证循环修复包装器
    desc: 调用validation_retry_loop子图，实现修复循环（最多3次）。修复范围：属性、特征、类目、价格（不包含图片）。修复后子图内部自动检查Ozon状态，如有错误继续修复。
    integrations: Ozon API, api.mxou.cn LLM (deepseek-v4-flash)
    """
    ctx = runtime.context

    logger.info("🔄 开始调用validation_retry_loop子图（修复循环）")

    # 构造ValidationRetryLoopInput（直接访问Pydantic属性）
    validation_retry_input = ValidationRetryLoopInput(
        ozon_payload=state.ozon_payload,
        validation_errors=state.validation_errors,
        errors=state.errors,
        error_message=state.error_message,
        draft=state.draft,
        token=state.token,
        ozon_client_id=state.ozon_client_id,
        ozon_api_key=state.ozon_api_key,
        description_category_id=state.description_category_id,
        type_id=state.type_id,
        task_id=state.task_id,
        product_id=state.product_id or "",  # ← 关键！传入已有 product_id 以启用靶向修复
        purchase_url=state.purchase_url,
        purchase_cost=state.purchase_cost,
        sku_id=state.sku_id,
        profit_estimation=state.profit_estimation,
        final_attributes=state.final_attributes,
        attributes_schema=state.attributes_schema,
        dictionary_values=state.dictionary_values,
        learned_attributes=state.learned_attributes,
        pricing_info=state.pricing_info,
        # ⚠️ PR-1 (D3): 跨入口累积 — 从 GlobalState 传入已累计次数，子图在此基础上继续
        retry_count=state.retry_count,
    )

    # 调用子图
    logger.info("🔄 调用validation_retry_loop.invoke()...")

    result: Dict[str, Any] = validation_retry_loop.invoke(validation_retry_input.model_dump())

    # 解析子图返回结果
    is_valid: bool = result.get("is_valid", False)
    retry_count: int = result.get("retry_count", 0)
    repaired_ozon_payload: Dict[str, Any] = result.get("ozon_payload", {})
    final_validation_errors: list = result.get("validation_errors", [])
    final_error_message: str = result.get("error_message", "")
    product_id: Any = result.get("product_id", None)
    upload_status: str = result.get("upload_status", "")
    moderation_status: str = result.get("moderation_status", "")
    # v0.65 C1(N4): 子图可能重配 type/dc + 改属性 → 透传回主图 state
    #（learning_record 需用修复后类目落库，否则按主图旧 dc/tp 学习固化错映射）
    # 空值回退主图原值（子图未重配类目时保持主图 state 不变，避免空串清空主图 dc/tp）
    _repaired_dc: str = str(result.get("description_category_id", "") or "") or str(state.description_category_id or "")
    _repaired_tp: str = str(result.get("type_id", "") or "") or str(state.type_id or "")
    _repaired_attrs: list = result.get("final_attributes") or []
    _repaired_schema: list = result.get("attributes_schema") or []

    logger.info(f"✅ 子图执行完成：is_valid={is_valid}, retry_count={retry_count}, upload_status={upload_status}")

    # 返回ValidationRetryWrapperOutput
    return ValidationRetryWrapperOutput(
        ozon_payload=repaired_ozon_payload,
        validation_errors=final_validation_errors,
        is_valid=is_valid,
        retry_count=retry_count,
        error_message=final_error_message,
        product_id=product_id if product_id else None,
        upload_status=upload_status,
        moderation_status=moderation_status,
        progress_counter=21,
        # v0.65 C1(N4): 透传修复后的类目/属性（空则不覆盖主图——子图未重配时保持原值）
        description_category_id=_repaired_dc,
        type_id=_repaired_tp,
        final_attributes=_repaired_attrs,
        attributes_schema=_repaired_schema,
    )

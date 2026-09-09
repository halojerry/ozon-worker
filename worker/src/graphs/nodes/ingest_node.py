"""数据摄入节点 - 任务队列写入Supabase"""
import os
import json
import logging
import uuid
import time
import requests
from typing import Any, Dict
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import IngestInput, IngestOutput


logger = logging.getLogger(__name__)


def ingest_node(state: IngestInput, config: RunnableConfig, runtime: Runtime[Context]) -> IngestOutput:
    """
    title: 数据摄入节点
    desc: 接收产品envelope，写入Supabase任务队列，返回任务ID
    integrations: Supabase
    """
    ctx = runtime.context
    
    envelope = state.envelope
    user_id = state.user_id
    supabase_url = state.supabase_url
    supabase_key = state.supabase_key
    currency_code = state.currency_code  # 关键：从auth_node传递
    
    # 参数验证
    if not envelope:
        return IngestOutput(
            task_id="",
            status="error",
            draft=None,  # 返回None而不是空字典
            source=None,
            extensions=None,
            currency_code=currency_code,  # 关键：传递currency_code
        )
    
    if not user_id:
        # ✅ 降级处理：认证失败时使用anonymous，不丢弃draft数据
        # auth_node在AUTH_INVALID时仍会传递draft（降级设计），这里不能丢掉
        user_id = "anonymous"
        logger.warning("ingest_node: user_id为空，使用anonymous降级处理，继续传递draft数据")
    
    try:
        # ✅ 解析envelope（兼容扁平payload结构）
        # 检查envelope是否包含draft字段（标准三层结构）
        if "draft" in envelope and isinstance(envelope.get("draft"), dict):
            # 标准三层结构（envelope包含draft/source/extensions）
            draft: Dict[str, Any] = envelope.get("draft", {})
            source: Dict[str, Any] = envelope.get("source", {})
            extensions: Dict[str, Any] = envelope.get("extensions", {})
            logger.info("✅ Payload结构：标准三层结构（envelope包含draft字段）")
        else:
            # 扁平结构（envelope直接包含产品数据）
            draft: Dict[str, Any] = envelope  # 直接使用envelope作为draft
            source: Dict[str, Any] = {
                "purchase_url": envelope.get("purchase_url", ""),
                "purchase_cost": envelope.get("purchase_cost", "")
            }
            extensions: Dict[str, Any] = {}
            logger.info("✅ Payload结构：扁平结构（envelope直接包含产品数据，已转换为draft）")
        
        # ✅ 提取variants列表（多SKU变体商品）
        variants: list = draft.get("variants", []) if isinstance(draft, dict) else []
        
        # ✅ 提取item_id（1688商品ID，用于变体绑定）
        item_id: str = draft.get("item_id", "") if isinstance(draft, dict) else ""

        # ✅ v0.73 空标题闸（生产实证：9048 属性退化裸 item_id → draft.title 空 →
        # 标题靠 LLM 盲生成上架）。选位理由：ingest 是创建流进图后的第一道校验位，
        # 且 follow_sell 在 route_by_sell_type 已分流 follow_sell_import（跟卖 title
        # 来源是竞品卡，语义不同）——本闸天然只对创建流 title 缺失生效，无需额外交互
        # 条件。title 空/空白 → fail-fast 出清（failed_stage="ingest" +
        # error_message，route_after_ingest 判 END，task_processor 判 failed 终态），
        # 拒绝盲生成。此类缺 title 信封不入采集箱（create_draft 缺 title 必 400，
        # 见 assemble._maybe_create_blocked_draft 范围红线注释）。
        _title = str((draft or {}).get("title", "") or "").strip() if isinstance(draft, dict) else ""
        if not _title:
            logger.error("❌ 信封缺 draft.title，拒绝盲生成（fail-fast 出清）")
            return IngestOutput(
                task_id="",
                status="error",
                draft=draft if isinstance(draft, dict) else {},
                source=source,
                extensions=extensions,
                currency_code=currency_code,
                variants=variants if isinstance(variants, list) else [],
                item_id=item_id,
                error_message="信封缺 draft.title，拒绝盲生成",
                failed_stage="ingest",
            )

        # ✅ 提取原始产品图片列表（用于图片生成节点）
        original_images: list = []
        if isinstance(draft, dict):
            images_field = draft.get("images", [])
            if isinstance(images_field, list):
                original_images = [str(img) for img in images_field if isinstance(img, str)]
        
        logger.info(f"提取的draft字段：title={draft.get('title', '')[:50]}..., weight={draft.get('weight', 0)}, variants={len(variants)}个, images={len(original_images)}张")
        logger.info(f"商品ID（item_id）：{item_id}")
        
        # ✅ 关键：检查draft是否为空字典（调试）
        if not draft or draft == {}:
            logger.error("❌ draft数据为空！这可能导致后续节点无法获取产品数据")
            logger.error(f"envelope结构：{json.dumps(envelope, ensure_ascii=False)[:200]}...")
        else:
            logger.info("✅ draft数据提取成功")
            logger.info(f"draft.title={draft.get('title', '')}")
            logger.info(f"draft.category={draft.get('category', '')}")
            logger.info(f"draft.weight={draft.get('weight', 0)}")
        
        # 生成任务ID
        task_id: str = str(uuid.uuid4())
        current_time: int = int(time.time() * 1000)
        
        # ✅ 直接返回draft数据（跳过Supabase写入，因为submit_task endpoint已经写入任务队列）
        logger.info("✅ 数据摄入完成，跳过Supabase写入（任务已由submit_task endpoint写入队列）")
        
        return IngestOutput(
            task_id=task_id,
            status="accepted",
            draft=draft,
            source=source,
            extensions=extensions,
            currency_code=currency_code,
            variants=variants,
            item_id=item_id,
            original_images=original_images
        )
        
    except requests.RequestException as e:
        logger.error(f"HTTP请求失败: {str(e)}")
        return IngestOutput(
            task_id="",
            status="error",
            draft={},
            source={},
            extensions={},
            currency_code=currency_code,  # 关键：传递currency_code
        )
    except Exception as e:
        logger.error(f"数据摄入失败: {str(e)}")
        return IngestOutput(
            task_id="",
            status="error",
            draft={},
            source={},
            extensions={},
            currency_code=currency_code,  # 关键：传递currency_code
        )
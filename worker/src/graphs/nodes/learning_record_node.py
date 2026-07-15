# 学习记录节点（上传成功后记录学习数据）
import os
import time
import logging
import requests
from typing import Dict, Any, List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

logger = logging.getLogger(__name__)

from graphs.state import (
    LearningRecordInput,
    LearningRecordOutput
)

from utils.local_db_manager import LocalDBManager


def learning_record_node(
    state: LearningRecordInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> LearningRecordOutput:
    """
    title: 学习记录节点
    desc: 上传成功后记录成功的属性映射到数据库（学习闭环，让数据库越用越智能）
    integrations: Supabase
    """
    ctx = runtime.context
    
    logger.info("📚 开始记录学习数据（属性映射闭环）")
    
    # 从LearningRecordInput提取数据
    description_category_id_str: str = state.description_category_id
    final_attributes: List[Dict[str, Any]] = state.final_attributes or []
    attributes_schema: List[Dict[str, Any]] = state.attributes_schema or []
    draft: Dict[str, Any] = state.draft or {}
    
    # ✅ 构建attribute_id → attribute_name映射
    attr_name_map: Dict[int, str] = {}
    for schema_attr in attributes_schema:
        if isinstance(schema_attr, dict):
            attr_id: Any = schema_attr.get("id")
            if attr_id is not None:
                attr_name_map[int(attr_id)] = str(schema_attr.get("name", ""))
    
    # ✅ 从draft中提取常见字段值，用于匹配原始中文源值
    draft_text_values: List[str] = []
    for v in draft.values():
        if isinstance(v, str) and len(v) > 0:
            draft_text_values.append(v)
        elif isinstance(v, (int, float)):
            draft_text_values.append(str(v))
    
    # 合并draft所有文本为一个搜索池
    draft_text_pool: str = " ".join(draft_text_values)
    
    # ✅ 类型转换：str → int（LocalDBManager需要int类型）
    try:
        description_category_id: int = int(description_category_id_str)
    except (ValueError, TypeError) as e:
        logger.warning(f"⚠️ 类目ID转换失败：{description_category_id_str} → {e}")
        return LearningRecordOutput(
            recorded_count=0,
            progress_counter=24
        )
    
    # ✅ 从status字段判断是否上传成功（imported/active/approved=成功）
    ozon_status: str = state.status or ""
    ozon_upload_success: bool = ozon_status in ("imported", "active", "approved", "processed")

    # ✅ 也检查upload_status字段（从validation_retry_wrapper传入，优先级更高）
    upload_status: str = state.upload_status if hasattr(state, 'upload_status') else ""
    if upload_status == "success":
        ozon_upload_success = True
        logger.info(f"✅ upload_status=success，视为上传成功")

    # 也检查ozon_upload_success字段（从ValidationRetryWrapperOutput传入）
    if not ozon_upload_success and state.ozon_upload_success:
        ozon_upload_success = True
    
    logger.info(f"📊 Ozon状态：{ozon_status} → 是否上传成功：{ozon_upload_success}")
    
    # 判断是否上传成功
    if not ozon_upload_success:
        logger.info("❌ 上传失败，跳过学习记录")
        return LearningRecordOutput(
            recorded_count=0,
            progress_counter=24  # ← 固定进度计数器（24号节点）
        )
    
    # ✅ 记录成功的属性映射到数据库
    local_db = LocalDBManager()
    recorded_count: int = 0
    
    # ✅ 关键修复：获取Supabase配置（用于双写）
    supabase_url: str = os.getenv("SUPABASE_URL", "https://kekmppsuiiokdckdeolv.supabase.co")
    supabase_key: str = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtla21wcHN1aWlva2Rja2Rlb2x2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDYyMDA0NCwiZXhwIjoyMDkwMTk2MDQ0fQ.ZkJMnjrlUQKaUpMU3eug9EQLUsoN0mOWI8wzC3jRkAU")
    supabase_headers: Dict[str, str] = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"  # ✅ upsert模式：存在则更新，不存在则插入
    }
    supabase_upsert_url: str = f"{supabase_url}/rest/v1/ozon_attribute_mappings"
    
    logger.info(f"📝 开始记录{len(final_attributes)}个属性映射（本地SQLite + Supabase双写）...")
    
    for attr in final_attributes:
        # 验证attr是否为dict类型
        if not isinstance(attr, dict):
            logger.warning(f"⚠️ 属性格式错误（非dict类型），跳过：{type(attr)}")
            continue
        
        # 提取属性字段
        attribute_id: Any = attr.get("attribute_id")
        value: Any = attr.get("value")
        dictionary_value_id: Any = attr.get("dictionary_value_id", 0)
        
        # 验证attribute_id是否为int类型
        if attribute_id is None:
            logger.warning(f"⚠️ 属性ID缺失，跳过")
            continue
        
        # 类型转换和验证
        try:
            attribute_id_int: int = int(attribute_id)
            dictionary_value_id_int: int = int(dictionary_value_id) if dictionary_value_id else 0
            value_str: str = str(value) if value else ""
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ 属性类型转换失败，跳过：{e}")
            continue
        
        # 获取属性名称（从attr_name_map）
        attribute_name: str = attr_name_map.get(attribute_id_int, "")
        
        # ✅ 提取原始中文源值：从draft中搜索与当前属性值相关的原始文本
        # 策略：如果属性值能在draft文本中找到子串，说明它来源于draft
        source_value: str = ""
        for draft_val in draft_text_values:
            # 如果draft中的值是当前属性值的子串（或反过来），则认为是源值
            if value_str and (value_str in draft_val or draft_val in value_str):
                source_value = draft_val
                break
        
        # 如果未找到匹配，使用属性值本身但标注来源
        if not source_value:
            source_value = f"[{attribute_name or 'unknown'}]" if attribute_name else ""
        
        # 跳过硬编码属性（如品牌"无品牌"），无学习价值
        attr_source: Any = attr.get("source", "")
        if attr_source == "hardcoded":
            logger.debug(f"⏭️ 跳过硬编码属性: attr_id={attribute_id_int}")
            continue
        
        # ✅ 调用add_attribute_mapping方法（记录到本地SQLite）
        try:
            local_db.add_attribute_mapping(
                category_id=int(description_category_id),
                attribute_id=attribute_id_int,
                attribute_name=attribute_name,
                source_value=source_value,  # ← 原始中文源值（从draft提取）
                target_value=value_str,  # ← Ozon实际接受的值
                dictionary_value_id=dictionary_value_id_int
            )
            
            # ✅ 关键修复：同步写入Supabase（之前只写本地SQLite，导致attributes_fetch查Supabase时永远为空）
            supabase_payload: Dict[str, Any] = {
                "category_id": int(description_category_id),
                "attribute_id": attribute_id_int,
                "attribute_name": attribute_name,
                "source_value": source_value,
                "attribute_value": value_str,  # ✅ Supabase字段名是attribute_value（不是target_value）
                "dictionary_value_id": dictionary_value_id_int if dictionary_value_id_int > 0 else None,
                "success_count": 1,
                "last_used_at": int(time.time())
            }
            try:
                sb_response = requests.post(supabase_upsert_url, headers=supabase_headers, json=supabase_payload, timeout=10)
                if sb_response.status_code in (200, 201):
                    logger.debug(f"✅ Supabase写入成功: attr_id={attribute_id_int}")
                else:
                    logger.warning(f"⚠️ Supabase写入失败: {sb_response.status_code} - {sb_response.text[:100]}")
            except Exception as sb_err:
                logger.warning(f"⚠️ Supabase写入异常: {str(sb_err)}")
            
            recorded_count += 1
            
            logger.info(f"✅ 属性映射记录成功（双写）：attr_id={attribute_id_int}, value={value_str}, dictionary_value_id={dictionary_value_id_int}")
            
        except Exception as e:
            logger.warning(f"⚠️ 属性映射记录失败：{e}")
            continue
    
    logger.info(f"✅ 学习记录完成：{recorded_count}个属性映射已写入数据库（本地SQLite + Supabase）")
    
    # 返回LearningRecordOutput
    return LearningRecordOutput(
        recorded_count=recorded_count,
        progress_counter=24  # ← 固定进度计数器（24号节点）
    )
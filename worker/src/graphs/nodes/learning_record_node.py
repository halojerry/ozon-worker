# 学习记录节点（上传成功后记录学习数据）
import os
import time
import logging
import requests
from typing import Dict, Any, List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

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
    # ✅ v0.25 T1: 1688 类目数字 ID（Skill 侧提取，供类目学习回写）
    _src_cat_id: Any = draft.get("source_category_id")
    if not _src_cat_id:
        _src_cat_id = (getattr(state, "source", None) or {}).get("category_id")
    
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
    
    # ✅ v0.21 (P0-1): 只有审核 approved 才算成功，才允许写学习记录。
    # imported/active/processed 只是"导入成功"，不代表审核通过；
    # upload_status=="success" / state.ozon_upload_success 是假成功来源，一律不再放行。
    ozon_status: str = getattr(state, 'moderation_status', '') or state.status or ""
    ozon_upload_success: bool = ozon_status == "approved"
    
    logger.info(f"📊 Ozon状态：{ozon_status} → 是否上传成功：{ozon_upload_success}")
    
    # 判断是否上传成功
    if not ozon_upload_success:
        logger.info("❌ 上传失败，跳过学习记录")
        return LearningRecordOutput(
            recorded_count=0,
            progress_counter=24  # ← 固定进度计数器（24号节点）
        )
    
    # ✅ 记录成功的属性映射到 PG 数据库
    local_db = LocalDBManager()
    recorded_count: int = 0
    
    logger.info(f"📝 开始记录{len(final_attributes)}个属性映射（PostgreSQL）...")
    
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

        # ✅ 写入 PG（替代旧 SQLite + Supabase 双写）
        local_db.add_attribute_mapping(
            category_id=int(description_category_id),
            attribute_id=attribute_id_int,
            attribute_name=attribute_name,
            source_value=source_value,
            target_value=value_str,
            dictionary_value_id=dictionary_value_id_int
        )
        
        recorded_count += 1
        logger.info(f"✅ 属性映射记录成功：attr_id={attribute_id_int}, value={value_str}, dictionary_value_id={dictionary_value_id_int}")
    
    logger.info(f"✅ 学习记录完成：{recorded_count}个属性映射已写入 PostgreSQL")
    
    # ═══════════════════════════════════════════════════════
    # v4: 写入 category_mapping（类目学习缓存）
    # ⚠️ 跟卖跳过 — 类目来自Ozon面包屑，source_category是1688图搜噪音
    # ═══════════════════════════════════════════════════════
    is_follow = False
    try:
        is_follow = bool(getattr(state, 'envelope', {}).get("extensions", {}).get("follow_sell", False))
    except Exception:
        pass
    source_category = draft.get("source_category", "") if draft else ""
    if source_category and not is_follow:
        try:
            import re as _re
            cleaned = _re.sub(r'[>、/→]', ' ', source_category)
            cat_terms = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]
            leaf = cat_terms[-1] if cat_terms else ""
            if leaf and description_category_id:
                tp_val = int(state.type_id or 0)
                # jieba 关键词
                try:
                    import jieba as _jieba
                    jieba_kws = list({w for w in _jieba.cut(leaf) if len(w) >= 2})
                except Exception:
                    jieba_kws = [leaf]
                # 查 ZH + RU 路径
                cat_zh = ""; cat_ru = ""
                try:
                    from sqlalchemy import text as _sql_t
                    from storage.database.db import get_session as _gs
                    with _gs() as _s:
                        _r = _s.execute(_sql_t(
                            "SELECT full_path FROM category_tree_nodes WHERE description_category_id=:dc AND type_id=:tp AND language='ZH_HANS' LIMIT 1"
                        ), {"dc": int(description_category_id), "tp": tp_val}).fetchone()
                        if _r: cat_zh = _r[0]
                        _r2 = _s.execute(_sql_t(
                            "SELECT full_path FROM category_tree_nodes WHERE description_category_id=:dc AND type_id=:tp AND language='RU' LIMIT 1"
                        ), {"dc": int(description_category_id), "tp": tp_val}).fetchone()
                        if _r2: cat_ru = _r2[0]
                except Exception:
                    pass
                local_db.add_category_mapping(
                    source_category_leaf=leaf,
                    source_category_id=int(_src_cat_id) if _src_cat_id else None,
                    description_category_id=int(description_category_id),
                    type_id=tp_val,
                    source_category_path=source_category,
                    source_keywords=jieba_kws,
                    category_path_zh=cat_zh, category_path_ru=cat_ru,
                    confidence=0.85, source="learned_approved",
                )
                logger.info(f"📚 category_mapping: '{leaf}' → [{description_category_id}/{tp_val}]")
        except Exception as e:
            logger.warning(f"category_mapping写入失败（非致命）: {e}")
    
    return LearningRecordOutput(
        recorded_count=recorded_count,
        progress_counter=24  # ← 固定进度计数器（24号节点）
    )

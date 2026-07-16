"""属性学习节点 - 字典查询 + 学习记录"""
import os
import json
import logging
import asyncio
import requests
from utils.http_session import session
from typing import Any, Dict, List, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import AttributesLearningInput, AttributesLearningOutput
from utils.local_db_manager import LocalDBManager  # ✅ 新增：本地数据库管理器


logger = logging.getLogger(__name__)


def attributes_learning_node(state: AttributesLearningInput, config: RunnableConfig, runtime: Runtime[Context]) -> AttributesLearningOutput:
    """
    title: 属性学习节点
    desc: 字典查询验证 + 写入学习记录到Supabase
    integrations: Ozon API, Supabase
    """
    ctx = runtime.context
    
    llm_attributes = state.llm_attributes
    attributes_schema = state.attributes_schema
    description_category_id = state.description_category_id
    type_id = state.type_id
    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key
    supabase_url = state.supabase_url
    supabase_key = state.supabase_key
    task_id = state.task_id
    draft = state.draft
    
    # 参数验证
    if not llm_attributes:
        return AttributesLearningOutput(
            final_attributes=[],
            enrich_count=0,
            llm_count=0
        )
    
    try:
        # Step 1: 并行字典查询（enrich_attributes_with_dictionary）
        final_attributes: List[Dict[str, Any]] = []
        enrich_count: int = 0
        
        # 需要并行查询字典的属性列表
        attributes_need_dictionary: List[Dict[str, Any]] = []
        
        # 根据schema判断哪些属性需要字典查询
        schema_dict: Dict[int, Dict[str, Any]] = {}
        for schema_attr in attributes_schema:
            attr_id: int = int(schema_attr.get("id", 0))
            schema_dict[attr_id] = schema_attr
            
            # 判断是否有字典约束
            if (schema_attr.get("dictionary_id", 0) or 0) > 0:
                attributes_need_dictionary.append(schema_attr)
        
        # 并行查询字典（使用asyncio模拟并行，实际是顺序执行）
        # 注意：Python节点函数必须是同步函数，所以使用requests顺序查询
        # 如果需要真正并行，可以在主图中编排多个并行节点
        
        for llm_attr in llm_attributes:
            attr_id: int = int(llm_attr.get("attribute_id", 0))
            value: str = str(llm_attr.get("value", ""))
            # 优先使用attributes_llm_node已匹配的dictionary_value_id
            existing_dict_id: Optional[int] = llm_attr.get("dictionary_value_id")
            if existing_dict_id is not None and int(existing_dict_id) > 0:
                final_attributes.append({
                    "attribute_id": attr_id,
                    "value": value,
                    "dictionary_value_id": int(existing_dict_id),
                    "source": llm_attr.get("source", "llm")
                })
                continue
            
            # 查找对应的schema
            schema_attr: Optional[Dict[str, Any]] = schema_dict.get(attr_id)
            
            if schema_attr and (schema_attr.get("dictionary_id", 0) or 0) > 0:
                # Step 2: 调用Ozon API查询字典值
                dictionary_value_id: Optional[int] = _search_dictionary_value(
                    ozon_client_id, ozon_api_key,
                    attr_id, value, description_category_id, type_id
                )
                
                if dictionary_value_id:
                    # 字典查询成功，使用字典值
                    final_attributes.append({
                        "attribute_id": attr_id,
                        "value": value,
                        "dictionary_value_id": dictionary_value_id,
                        "source": "dictionary_enrich"
                    })
                    enrich_count += 1
                else:
                    # 字典查询失败，使用LLM值
                    final_attributes.append({
                        "attribute_id": attr_id,
                        "value": value,
                        "dictionary_value_id": 0,
                        "source": "llm"
                    })
            else:
                # 不需要字典查询，直接使用LLM值
                final_attributes.append({
                    "attribute_id": attr_id,
                    "value": value,
                    "dictionary_value_id": 0,
                    "source": "llm"
                })
        
        # ❌ 删除学习表写入逻辑（根据用户要求，学习机制应该在最后错误处理节点后）
        # 学习表写入逻辑已移动到learning_node（在最后错误处理节点后执行）
        
        llm_count: int = len(llm_attributes)
        
        logger.info(f"属性字典查询完成: llm_count={llm_count}, enrich_count={enrich_count}")
        
        return AttributesLearningOutput(
            final_attributes=final_attributes,
            enrich_count=enrich_count,
            llm_count=llm_count
        )
        
    except Exception as e:
        logger.error(f"属性学习失败: {str(e)}")
        return AttributesLearningOutput(
            final_attributes=llm_attributes,  # 失败时返回原始LLM属性
            enrich_count=0,
            llm_count=len(llm_attributes)
        )


def _search_dictionary_value(ozon_client_id: str, ozon_api_key: str, attribute_id: int, search_text: str, category_id: str, type_id: int = 0) -> Optional[int]:
    """查询Ozon字典值"""
    try:
        ozon_headers: Dict[str, str] = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json"
        }
        
        ozon_url: str = "https://api-seller.ozon.ru/v1/description-category/attribute/values/search"
        
        ozon_payload: Dict[str, Any] = {
            "attribute_id": attribute_id,
            "description_category_id": int(category_id),
            "type_id": int(type_id) if type_id else 0,
            "value": search_text,
            "limit": 5
        }
        
        response: Any = session.post(ozon_url, headers=ozon_headers, json=ozon_payload, timeout=30)
        
        if response.status_code == 200:
            ozon_data: Any = response.json()
            
            if isinstance(ozon_data, dict) and ozon_data.get("result"):
                results: List[Dict[str, Any]] = ozon_data.get("result", [])
                
                if len(results) > 0:
                    # 返回第一个匹配的字典值ID
                    first_result: Dict[str, Any] = results[0]
                    dictionary_value_id: int = int(first_result.get("id", 0))
                    return dictionary_value_id
        
        return None
        
    except Exception as e:
        logger.error(f"字典查询失败: {str(e)}")
        return None
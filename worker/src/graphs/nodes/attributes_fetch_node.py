"""属性获取节点 - 获取类目属性schema + 字典值查询"""
import os
import json
import time
import logging
import requests
from utils.http_session import session
from typing import Any, Dict, List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import AttributesFetchInput, AttributesFetchOutput
from utils.progress_logger import ProgressLogger
from utils.local_db_manager import LocalDBManager  # ✅ 新增：本地数据库管理器


logger = logging.getLogger(__name__)


def attributes_fetch_node(state: AttributesFetchInput, config: RunnableConfig, runtime: Runtime[Context]) -> AttributesFetchOutput:
    """
    title: 属性获取节点
    desc: 获取类目属性schema + 篩选dictionary_id > 0的属性 + 查询字典值列表 + 已学习的属性映射
    integrations: Ozon API, Supabase
    """
    ctx = runtime.context
    
    # 添加进度日志
    progress = ProgressLogger()
    progress.log_node_start("attributes_fetch_node", "属性获取节点")
    progress.log_node_action("正在获取类目属性schema...")
    
    description_category_id = state.description_category_id
    type_id = state.type_id
    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key
    supabase_url = state.supabase_url
    supabase_key = state.supabase_key
    task_id = state.task_id
    draft = state.draft or {}
    
    # 参数验证
    if not description_category_id:
        return AttributesFetchOutput(
            attributes_schema=[],
            learned_attributes={},
            dictionary_values={},  # 关键：新增字段
            ozon_source=""
        )
    
    try:
        # ✅ 初始化本地数据库管理器
        local_db = LocalDBManager()
        
        # ✅ 优先级1：本地SQLite缓存查询（attribute_cache表）
        current_time = int(time.time())
        local_cache = local_db.get_attribute_cache(description_category_id, type_id, "ZH_HANS")
        
        attributes_schema: List[Dict[str, Any]] = []
        use_cache = False
        
        if local_cache and local_cache.get("attributes_schema"):
            attributes_schema = local_cache["attributes_schema"]
            use_cache = True
            logger.info(f"✅ 使用本地缓存的属性schema（节省Supabase+Ozon API调用）")
            logger.info(f"本地缓存数据：count={len(attributes_schema)}")
        
        # ✅ 优先级2：Supabase缓存查询（本地未命中时）
        if not attributes_schema:
            headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json"
            }
            
            cache_url = f"{supabase_url}/rest/v1/attribute_cache?description_category_id=eq.{description_category_id}&type_id=eq.{type_id}&language=eq.ZH_HANS&expires_at=gt.{current_time}&select=attributes_schema"
            
            logger.info(f"本地未命中，查询Supabase缓存（attribute_cache表）...")
            cache_response = session.get(cache_url, headers=headers, timeout=10)
            
            if cache_response.status_code == 200:
                cache_data = cache_response.json()
                if cache_data and len(cache_data) > 0:
                    attributes_schema = cache_data[0].get("attributes_schema", [])
                    use_cache = True
                    logger.info(f"✅ 使用Supabase缓存的属性schema（expires_at>{current_time}）")
                    logger.info(f"Supabase缓存数据：count={len(attributes_schema)}")
                    
                    # ✅ 双写：缓存到本地SQLite
                    local_db.set_attribute_cache(description_category_id, type_id, attributes_schema, "ZH_HANS", expires_in=86400)
        
        # ✅ 优先级3：调用Ozon API获取属性schema（本地+Supabase都未命中）
        if not attributes_schema:
            logger.info("缓存不存在或过期，调用Ozon API获取属性schema（language=ZH_HANS）...")
            
            # Step 1: 调用Ozon API获取属性schema
            ozon_headers = {
                "Client-Id": ozon_client_id,
                "Api-Key": ozon_api_key,
                "Content-Type": "application/json"
            }
            
            ozon_url = "https://api-seller.ozon.ru/v1/description-category/attribute"  # ✅ 关键：使用单数形式（不是attributes）
            
            ozon_payload: Dict[str, Any] = {
                "description_category_id": int(description_category_id),
                "type_id": int(type_id) if type_id else 0,
                "language": "ZH_HANS"  # ✅ 关键：使用中文查询（支持ZH_HANS中文、EN英文）
            }
            
            ozon_response = session.post(ozon_url, headers=ozon_headers, json=ozon_payload, timeout=60)
            
            if ozon_response.status_code != 200:
                logger.error(f"Ozon API获取属性失败: {ozon_response.status_code} - {ozon_response.text}")
                return AttributesFetchOutput(
                    attributes_schema=[],
                    learned_attributes={},
                    dictionary_values={},
                    ozon_source=""
                )
            
            ozon_data: Any = ozon_response.json()
            
            if not isinstance(ozon_data, dict) or not ozon_data.get("result"):
                logger.error(f"Ozon API返回数据格式错误")
                return AttributesFetchOutput(
                    attributes_schema=[],
                    learned_attributes={},
                    dictionary_values={},
                    ozon_source=""
                )
            
            attributes_schema = ozon_data.get("result", [])
            
            logger.info(f"获取属性schema成功: count={len(attributes_schema)}")
            
            # ✅ 双写：缓存到本地SQLite + Supabase（24小时有效）
            local_db.set_attribute_cache(description_category_id, type_id, attributes_schema, "ZH_HANS", expires_in=86400)
            logger.info("✅ 属性schema已缓存到本地SQLite（有效期24小时）")
            
            insert_url = f"{supabase_url}/rest/v1/attribute_cache"
            insert_payload = {
                "description_category_id": int(description_category_id),
                "type_id": int(type_id) if type_id else 0,
                "language": "ZH_HANS",
                "attributes_schema": attributes_schema,
                "expires_at": current_time + 86400  # 24小时后过期
            }
            
            try:
                insert_response = session.post(insert_url, headers=headers, json=insert_payload, timeout=10)
                if insert_response.status_code == 201:
                    logger.info("✅ 属性schema已缓存到Supabase（有效期24小时）")
                else:
                    logger.warning(f"缓存写入失败: {insert_response.status_code} - {insert_response.text}")
            except Exception as e:
                logger.warning(f"缓存写入异常: {str(e)}")
        
        # Step 2: 关键改进 - 篮选dictionary_id > 0的属性，查询字典值列表
        dictionary_attributes: List[Dict[str, Any]] = [
            attr for attr in attributes_schema 
            if attr.get("dictionary_id", 0) > 0
        ]
        
        logger.info(f"筛选字典属性: count={len(dictionary_attributes)}")

        # ✅ 确保 headers 和 ozon_headers 已定义（防止本地缓存命中时未定义）
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json"
        }
        ozon_headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json"
        }
        dict_url = "https://api-seller.ozon.ru/v1/description-category/attribute/values"

        # 关键：查询字典值列表（用于后续LLM节点正确选择dictionary_value_id）
        # ✅ 三层缓存优先级：本地SQLite → Supabase → Ozon API（分页查询）
        dictionary_values: Dict[str, List[Dict[str, Any]]] = {}

        for dict_attr in dictionary_attributes:
            attr_id = dict_attr.get("id")
            dictionary_id = dict_attr.get("dictionary_id")

            if not attr_id or not dictionary_id:
                continue

            attr_id_int: int = int(attr_id)
            cat_id_int: int = int(description_category_id)
            type_id_int: int = int(type_id) if type_id else 0

            # ✅ 优先级1：本地SQLite缓存查询（dictionary_value_cache表）
            local_dict_cache = local_db.get_dictionary_value_cache(attr_id_int, cat_id_int, type_id_int, "ZH_HANS")
            if local_dict_cache and local_dict_cache.get("values_data"):
                values_list: List[Dict[str, Any]] = local_dict_cache["values_data"]
                dictionary_values[str(attr_id)] = values_list
                logger.info(f"✅ 使用本地SQLite缓存的字典值（attribute_id={attr_id}, count={len(values_list)}）")
                continue

            # ✅ 优先级2：Supabase缓存查询（本地未命中）
            dict_cache_url = f"{supabase_url}/rest/v1/dictionary_value_cache?attribute_id=eq.{attr_id}&description_category_id=eq.{description_category_id}&type_id=eq.{type_id}&language=eq.ZH_HANS&expires_at=gt.{current_time}&select=values_data"

            try:
                dict_cache_response = session.get(dict_cache_url, headers=headers, timeout=10)

                if dict_cache_response.status_code == 200:
                    dict_cache_data: Any = dict_cache_response.json()

                    if isinstance(dict_cache_data, list) and len(dict_cache_data) > 0:
                        values_list = dict_cache_data[0].get("values_data", [])
                        if values_list:
                            dictionary_values[str(attr_id)] = values_list
                            # ✅ 双写：回填到本地SQLite
                            local_db.set_dictionary_value_cache(attr_id_int, cat_id_int, type_id_int, values_list, "ZH_HANS", expires_in=86400)
                            logger.info(f"✅ 使用Supabase缓存的字典值（attribute_id={attr_id}, count={len(values_list)}），已回填本地SQLite")
                            continue

            except requests.RequestException as e:
                logger.warning(f"字典值Supabase缓存查询异常: attribute_id={attr_id}, error={str(e)}")

            # ✅ 优先级3：调用Ozon API查询字典值列表（本地+Supabase都未命中）
            # 关键修复：使用分页查询（last_value_id循环），不再用limit:50
            logger.info(f"缓存不存在或过期，调用Ozon API查询字典值（分页查询，language=ZH_HANS）...")

            all_values: List[Dict[str, Any]] = []
            last_value_id: int = 0
            has_next: bool = True
            page_count: int = 0

            while has_next:
                dict_payload: Dict[str, Any] = {
                    "attribute_id": attr_id_int,
                    "description_category_id": cat_id_int,
                    "type_id": type_id_int,
                    "language": "ZH_HANS",
                    "limit": 5000,  # ✅ 关键修复：从50改为5000（Ozon API最大值）
                    "last_value_id": last_value_id  # ✅ 关键修复：分页游标
                }

                try:
                    dict_response = session.post(dict_url, headers=ozon_headers, json=dict_payload, timeout=60)

                    if dict_response.status_code == 200:
                        dict_data: Any = dict_response.json()

                        if isinstance(dict_data, dict) and dict_data.get("result"):
                            page_values: List[Dict[str, Any]] = dict_data.get("result", [])
                            all_values.extend(page_values)
                            page_count += 1

                            # ✅ 关键修复：检查是否有更多数据（has_next字段）
                            has_next = bool(dict_data.get("has_next", False))
                            if has_next and page_values:
                                last_value_id = int(page_values[-1].get("id", 0))
                            else:
                                has_next = False
                        else:
                            has_next = False
                    else:
                        logger.error(f"字典值查询失败: attribute_id={attr_id}, status={dict_response.status_code}")
                        has_next = False

                except requests.RequestException as e:
                    logger.error(f"字典值查询异常: attribute_id={attr_id}, error={str(e)}")
                    has_next = False

            dictionary_values[str(attr_id)] = all_values
            logger.info(f"查询字典值成功: attribute_id={attr_id}, count={len(all_values)}, pages={page_count}")

            # ✅ 双写缓存：本地SQLite + Supabase
            if all_values:
                # 本地SQLite
                local_db.set_dictionary_value_cache(attr_id_int, cat_id_int, type_id_int, all_values, "ZH_HANS", expires_in=86400)

                # Supabase
                dict_insert_url = f"{supabase_url}/rest/v1/dictionary_value_cache"
                dict_insert_payload = {
                    "attribute_id": attr_id_int,
                    "description_category_id": cat_id_int,
                    "type_id": type_id_int,
                    "language": "ZH_HANS",
                    "values_data": all_values,
                    "expires_at": current_time + 86400
                }

                try:
                    dict_insert_response = session.post(dict_insert_url, headers=headers, json=dict_insert_payload, timeout=10)
                    if dict_insert_response.status_code == 201:
                        logger.info(f"✅ 字典值已双写到Supabase（有效期24小时）：attribute_id={attr_id}")
                    else:
                        logger.warning(f"字典值缓存写入Supabase失败: {dict_insert_response.status_code}")
                except Exception as e:
                    logger.warning(f"字典值缓存写入Supabase异常: {str(e)}")

        # Step 3: 查询已学习的属性映射（本地SQLite优先 → Supabase回退）
        learned_attributes: Dict[str, Any] = {}

        # ✅ 优先级1：本地SQLite查询
        local_mappings: List[Dict[str, Any]] = local_db.get_attribute_mappings(int(description_category_id))
        if local_mappings:
            for record in local_mappings:
                rec_attr_id: str = str(record.get("attribute_id", ""))
                rec_target_value: str = str(record.get("target_value", ""))
                if rec_attr_id:
                    learned_attributes[rec_attr_id] = rec_target_value
            logger.info(f"✅ 使用本地SQLite学习记录: count={len(learned_attributes)}")

        # ✅ 优先级2：Supabase查询（本地未命中时）
        if not learned_attributes:
            learning_query_url = f"{supabase_url}/rest/v1/ozon_attribute_mappings?category_id=eq.{description_category_id}&select=*"

            learning_response = session.get(learning_query_url, headers=headers, timeout=30)

            if learning_response.status_code == 200:
                learning_data: Any = learning_response.json()

                if isinstance(learning_data, list) and len(learning_data) > 0:
                    for record in learning_data:
                        rec_attr_id = str(record.get("attribute_id", ""))
                        rec_attr_value = str(record.get("attribute_value", ""))
                        if rec_attr_id:
                            learned_attributes[rec_attr_id] = rec_attr_value
                    logger.info(f"✅ 使用Supabase学习记录: count={len(learned_attributes)}")

        logger.info(f"获取已学习属性成功: count={len(learned_attributes)}, 字典值查询成功: count={len(dictionary_values)}")
        
        return AttributesFetchOutput(
            attributes_schema=attributes_schema,
            learned_attributes=learned_attributes,
            dictionary_values=dictionary_values,  # 关键：新增字段
            ozon_source="ozon_api"
        )
        
    except requests.RequestException as e:
        logger.error(f"HTTP请求失败: {str(e)}")
        return AttributesFetchOutput(
            attributes_schema=[],
            learned_attributes={},
            dictionary_values={},
            ozon_source=""
        )
    except Exception as e:
        logger.error(f"属性获取失败: {str(e)}")
        return AttributesFetchOutput(
            attributes_schema=[],
            learned_attributes={},
            dictionary_values={},
            ozon_source=""
        )
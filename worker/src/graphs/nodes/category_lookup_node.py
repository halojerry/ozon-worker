import os
import json
import time
import logging
import requests
from utils.http_session import session
import jieba
from typing import Dict, Any, Optional, List, Tuple
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from jinja2 import Template

from graphs.state import CategoryLookupInput, CategoryLookupOutput
from utils.progress_logger import ProgressLogger
from utils.mxou_llm import call_mxou_chat_api
from utils.local_db_manager import LocalDBManager

logger = logging.getLogger(__name__)


def _flatten_category_tree(
    nodes: list,
    parent_path: str = "",
    parent_cat_id: str = "",
    top_level_name: str = ""
) -> List[Dict[str, Any]]:
    """
    递归遍历类目树，提取所有有type_id的叶子节点。
    
    Returns:
        列表，每个元素包含:
        - description_category_id: 类目ID
        - type_id: 类型ID
        - category_path: 完整路径（如 "住宅和花园 > 园艺工具 > 手动修枝剪"）
        - type_name: 类型名称
        - top_level: 顶级类目名称
    """
    result: List[Dict[str, Any]] = []
    
    for node in nodes:
        if not isinstance(node, dict):
            continue
        
        node_name = node.get("type_name", "") or node.get("category_name", "")
        type_id = node.get("type_id", "")
        node_desc_cat_id = str(node.get("description_category_id", "") or parent_cat_id)
        
        current_path = f"{parent_path} > {node_name}" if parent_path else node_name
        # 记录顶级类目名称（最外层的category_name）
        current_top = top_level_name if top_level_name else node_name
        
        # 如果有type_id，这是一个叶子类型节点
        if type_id and str(type_id).strip():
            result.append({
                "description_category_id": int(node_desc_cat_id) if str(node_desc_cat_id).isdigit() else node_desc_cat_id,
                "type_id": int(type_id) if str(type_id).isdigit() else type_id,
                "category_path": current_path,
                "type_name": node_name,
                "top_level": current_top
            })
        
        # 递归搜索子节点
        children = node.get("children", [])
        if isinstance(children, list) and len(children) > 0:
            result.extend(_flatten_category_tree(children, current_path, node_desc_cat_id, current_top))
    
    return result


def _call_llm_for_category(
    title: str,
    attr_text: str,
    candidates_text: str,
    token: str,
    model_id: str,
    sp: str
) -> Optional[Dict[str, Any]]:
    """调用LLM进行类目匹配，返回解析后的dict或None"""
    try:
        user_prompt = (
            f"产品标题：{title}\n"
            f"产品属性关键词：{attr_text}\n\n"
            f"Ozon类目候选列表：\n{candidates_text}\n\n"
            f"请从以上候选列表中选择最匹配产品的一行，返回JSON。"
        )
        
        resp_text: str = call_mxou_chat_api(
            token=token,
            system_prompt=sp,
            user_prompt=user_prompt,
            model=model_id,
            temperature=0.0,
            max_tokens=1024
        ) or ""
        
        if not resp_text.strip():
            logger.error("LLM类目匹配返回空响应")
            return None
        
        resp_text = resp_text.replace("```json", "").replace("```", "").strip()
        match_result = json.loads(resp_text)
        
        desc_cat_id = match_result.get("description_category_id")
        type_id = match_result.get("type_id")
        reason = match_result.get("reason", "")
        cat_path = match_result.get("category_path", "")
        
        # 返回完整的解析结果（包含category_path），由调用方决定是否可用
        return {
            "description_category_id": desc_cat_id,
            "type_id": type_id,
            "reason": reason,
            "category_path": cat_path,
            "raw": resp_text[:300]
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"LLM类目匹配JSON解析失败: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"LLM类目匹配异常: {str(e)}")
        return None


def _llm_match_category(
    title: str,
    attributes: Dict[str, Any],
    all_candidates: List[Dict[str, Any]],
    token: str
) -> Optional[Dict[str, Any]]:
    """
    两步LLM类目匹配：
    Step 1: 从顶级类目中选择最匹配的
    Step 2: 在该顶级类目下的候选中选择最匹配的type
    
    Returns:
        匹配的类目信息 dict，或 None
    """
    if not all_candidates:
        return None
    
    try:
        cfg_file: str = os.path.join(
            os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"),
            "config/category_match_llm_cfg.json"
        )
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            llm_cfg: Dict[str, Any] = json.load(fd)
        
        llm_config: Dict[str, Any] = llm_cfg.get("config", {})
        model_id: str = llm_config.get("model", "doubao-seed-1-8-251228")
        sp: str = llm_cfg.get("sp", "")
        
        attr_text = ", ".join(f"{k}: {v}" for k, v in attributes.items()) if attributes else "无"
        
        # Step 1: 按顶级类目分组，让LLM选择最佳顶级类目
        top_level_groups: Dict[str, List[Dict[str, Any]]] = {}
        for c in all_candidates:
            top_name = c.get("top_level", "未知")
            if top_name not in top_level_groups:
                top_level_groups[top_name] = []
            top_level_groups[top_name].append(c)
        
        top_level_names = list(top_level_groups.keys())
        logger.info(f"Step 1: 共{len(top_level_names)}个顶级类目，让LLM选择最佳匹配")
        
        # 构建顶级类目候选列表（包含每个顶级类目下的子类目样例）
        top_candidates_lines: List[str] = []
        for i, top_name in enumerate(top_level_names):
            group = top_level_groups[top_name]
            # 取前3个子类目路径作为样例
            sample_paths = [g["category_path"] for g in group[:3]]
            top_candidates_lines.append(
                f"{i+1}. 顶级类目: {top_name} (共{len(group)}个子类型，样例: {'; '.join(sample_paths)})"
            )
        
        top_candidates_text = "\n".join(top_candidates_lines)
        
        step1_result = _call_llm_for_category(
            title=title,
            attr_text=attr_text,
            candidates_text=top_candidates_text,
            token=token,
            model_id=model_id,
            sp=sp
        )
        
        if not step1_result:
            logger.warning("Step 1 LLM匹配失败")
            return None
        
        # Step 1的候选列表是顶级类目名，LLM可能返回无效的desc_cat_id/type_id
        # 用reason和category_path来匹配选中的顶级类目名
        selected_top: Optional[str] = None
        reason_text = step1_result.get("reason", "")
        full_response = str(step1_result)
        
        # 尝试从reason或整个响应中匹配顶级类目名
        for top_name in top_level_names:
            if top_name in reason_text or top_name in full_response:
                selected_top = top_name
                break
        
        # 如果reason没匹配到，尝试用category_path字段
        if not selected_top:
            cat_path = step1_result.get("category_path", "")
            for top_name in top_level_names:
                if top_name in cat_path:
                    selected_top = top_name
                    break
        
        # 如果还没匹配到，尝试用description_category_id匹配
        if not selected_top:
            step1_desc_id = step1_result.get("description_category_id")
            if step1_desc_id:
                for top_name, group in top_level_groups.items():
                    for c in group:
                        if str(c["description_category_id"]) == str(step1_desc_id):
                            selected_top = top_name
                            break
                    if selected_top:
                        break
        
        if not selected_top:
            logger.warning(f"Step 1无法确定顶级类目，LLM返回: {step1_result.get('raw', '')[:200]}")
            return None
        
        logger.info(f"✅ Step 1 完成: 选择顶级类目 '{selected_top}'")
        
        # Step 2: 在选中的顶级类目下的候选中选择最佳type
        filtered_candidates = top_level_groups[selected_top]
        logger.info(f"Step 2: 在 '{selected_top}' 下共{len(filtered_candidates)}个候选类型")
        
        # Step 2a: 用关键词预过滤，缩小候选范围
        title_lower = title.lower() if title else ""
        title_keywords: List[str] = []
        if title_lower:
            jieba_words = [w for w in jieba.cut(title_lower) if len(w) >= 2 and w.strip()]
            title_keywords.extend(jieba_words)
            search_text = title_lower[:20]
            for n in (4, 3, 2):
                for i in range(len(search_text) - n + 1):
                    kw = search_text[i:i + n]
                    if kw not in title_keywords:
                        title_keywords.append(kw)
        
        scored_candidates: List[Tuple[int, Dict[str, Any]]] = []
        for c in filtered_candidates:
            cat_path_lower = c.get("category_path", "").lower()
            type_name_lower = c.get("type_name", "").lower()
            score: int = 0
            for kw in title_keywords:
                if kw in cat_path_lower or kw in type_name_lower:
                    score += len(kw) * (2 if len(kw) >= 3 else 1)
            scored_candidates.append((score, c))
        
        # 取有分数的候选，按分数降序排列，最多50个
        positive_candidates = [c for s, c in scored_candidates if s > 0]
        positive_candidates.sort(key=lambda x: next(s for s, c in scored_candidates if c is x), reverse=True)
        
        if len(positive_candidates) > 0:
            truncated_step2 = positive_candidates[:50]
            logger.info(f"Step 2: 关键词预过滤后{len(positive_candidates)}个有匹配，取前{len(truncated_step2)}个让LLM选择")
        else:
            truncated_step2 = filtered_candidates[:100]
            logger.info(f"Step 2: 关键词无匹配，取前{len(truncated_step2)}个让LLM选择")
        
        step2_lines: List[str] = []
        for i, c in enumerate(truncated_step2):
            step2_lines.append(
                f"{i+1}. desc_cat_id={c['description_category_id']}, type_id={c['type_id']}, 路径: {c['category_path']}"
            )
        
        step2_text = "\n".join(step2_lines)
        
        step2_result = _call_llm_for_category(
            title=title,
            attr_text=attr_text,
            candidates_text=step2_text,
            token=token,
            model_id=model_id,
            sp=sp
        )
        
        if not step2_result:
            logger.warning("Step 2 LLM匹配失败")
            return None
        
        desc_cat_id = step2_result.get("description_category_id")
        type_id = step2_result.get("type_id")
        reason = step2_result.get("reason", "")
        
        if not desc_cat_id or not type_id:
            logger.warning(f"Step 2 LLM返回的ID无效: desc_cat_id={desc_cat_id}, type_id={type_id}")
            return None
        
        # 在候选列表中验证返回的ID是否存在
        for c in truncated_step2:
            if str(c["description_category_id"]) == str(desc_cat_id) and str(c["type_id"]) == str(type_id):
                logger.info(f"✅ Step 2 LLM类目匹配成功: {c['category_path']}")
                logger.info(f"   匹配原因: {reason}")
                return c
        
        # 如果精确匹配失败，尝试只匹配description_category_id
        for c in truncated_step2:
            if str(c["description_category_id"]) == str(desc_cat_id):
                logger.info(f"✅ Step 2 LLM类目匹配(desc_cat_id匹配): {c['category_path']}")
                return c
        
        # 如果还没匹配到，尝试只匹配type_id
        for c in truncated_step2:
            if str(c["type_id"]) == str(type_id):
                logger.info(f"✅ Step 2 LLM类目匹配(type_id匹配): {c['category_path']}")
                return c
        
        logger.warning(f"Step 2 LLM返回的ID不在候选列表中: desc_cat_id={desc_cat_id}, type_id={type_id}")
        return None
        
    except Exception as e:
        logger.error(f"LLM类目匹配异常: {str(e)}")
        return None


def _find_best_type_fallback(
    nodes: list,
    title_keywords: list,
    title_lower: str,
    depth: int = 0,
    parent_category_id: str = ""
) -> Tuple[Optional[dict], int, str]:
    """
    关键词子串匹配的兜底逻辑（当LLM匹配失败时使用）。
    
    Returns:
        (best_node, best_score, description_category_id)
    """
    best_node: Optional[dict] = None
    best_score: int = 0
    best_desc_cat_id: str = ""
    
    for node in nodes:
        if not isinstance(node, dict):
            continue
        
        node_name = node.get("type_name", "") or node.get("category_name", "")
        node_name_lower = node_name.lower()
        type_id = node.get("type_id", "")
        node_desc_cat_id = str(node.get("description_category_id", "") or parent_category_id)
        
        node_score: int = 0
        
        for kw in title_keywords:
            if kw in node_name_lower:
                kw_len = len(kw)
                node_score += kw_len * (2 if kw_len >= 3 else 1)
        
        if type_id and node_score > 0:
            if node_score > best_score:
                best_score = node_score
                best_node = node
                best_desc_cat_id = node_desc_cat_id
        
        children = node.get("children", [])
        if isinstance(children, list) and len(children) > 0:
            child_node, child_score, child_desc_id = _find_best_type_fallback(
                children, title_keywords, title_lower, depth + 1, node_desc_cat_id
            )
            if child_node and child_score > best_score:
                best_score = child_score
                best_node = child_node
                best_desc_cat_id = child_desc_id
    
    return (best_node, best_score, best_desc_cat_id)


def category_lookup_node(state: CategoryLookupInput, config: RunnableConfig, runtime: Runtime[Context]) -> CategoryLookupOutput:
    """
    title: 类目查找节点
    desc: 使用LLM两步匹配（先选顶级类目，再选子类型）从Ozon类目树中智能匹配最合适的类目
    integrations: api.mxou.cn LLM (deepseek-v4-flash), Ozon API, Supabase
    """
    ctx = runtime.context
    
    progress = ProgressLogger()
    progress.log_node_start("category_lookup_node", "类目查找节点")
    progress.log_node_action("正在查询类目映射表...")
    
    currency_code = state.currency_code or ""
    token = state.token or ""
    logger.info(f"类目查找节点接收到currency_code: {currency_code}")
    
    draft = state.draft if state.draft else {}
    source = state.source if state.source else {}
    extensions = state.extensions if state.extensions else {}
    
    # ✅ 优先级1：检查draft中是否已包含ozon_category字段
    ozon_category = draft.get("ozon_category", None)
    
    if ozon_category and isinstance(ozon_category, dict):
        type_id_str = str(ozon_category.get("type_id", ""))
        description_category_id_str = str(ozon_category.get("description_category_id", ""))
        
        if type_id_str and description_category_id_str:
            logger.info(f"✅ 从draft.ozon_category提取类目信息（跳过Ozon API查询）")
            logger.info(f"类目信息：type_id={type_id_str}, description_category_id={description_category_id_str}")
            
            return CategoryLookupOutput(
                category=ozon_category,
                description_category_id=description_category_id_str,
                type_id=type_id_str,
                draft=draft,
                source=source,
                extensions=extensions,
                currency_code=currency_code,
                error_message="",
                failed_stage="",
                blocked=False
            )
        else:
            logger.warning(f"draft.ozon_category字段不完整：type_id={type_id_str}, description_category_id={description_category_id_str}")
    
    # ✅ 优先级2：从draft中提取关键信息
    title = draft.get("title", "")
    category_name = draft.get("category", "")
    attributes = draft.get("attributes", {}) if isinstance(draft.get("attributes", {}), dict) else {}
    
    logger.info(f"提取title和category进行LLM智能匹配")
    logger.info(f"title={title}, category={category_name}")
    
    try:
        supabase_url = state.supabase_url
        supabase_key = state.supabase_key
        
        current_time = int(time.time())
        
        # ✅ 从 PG 缓存读取类目树（替代旧 Supabase REST API）
        local_db = LocalDBManager()
        cached_cat = local_db.get_category_cache(state.ozon_client_id, language="ZH_HANS")
        category_tree: Optional[list] = cached_cat.get("tree_data") if cached_cat else None
        
        if category_tree:
            logger.info(f"✅ 使用 PG 缓存的类目树（{len(category_tree)}个顶级类目）")
        
        # ✅ 缓存不存在时调用Ozon API获取类目树
        if not category_tree:
            logger.info("PG 缓存不存在或过期，调用Ozon API获取类目树（language=ZH_HANS）...")
            
            ozon_client_id = state.ozon_client_id
            ozon_api_key = state.ozon_api_key
            
            ozon_url = "https://api-seller.ozon.ru/v1/description-category/tree"
            ozon_headers = {
                "Client-Id": ozon_client_id,
                "Api-Key": ozon_api_key,
                "Content-Type": "application/json"
            }
            ozon_payload = {"language": "ZH_HANS"}
            
            ozon_response = session.post(ozon_url, headers=ozon_headers, json=ozon_payload, timeout=120)
            
            if ozon_response.status_code != 200:
                logger.error(f"Ozon API获取类目树失败: {ozon_response.status_code} - {ozon_response.text}")
                return CategoryLookupOutput(
                    category=None,
                    description_category_id="",
                    type_id="",
                    draft=draft,
                    source=source,
                    extensions=extensions,
                    currency_code=currency_code,
                    error_message=f"Ozon API获取类目树失败: {ozon_response.status_code}",
                    failed_stage="category_lookup",
                    blocked=True
                )
            
            ozon_data = ozon_response.json()
            category_tree = ozon_data.get("result", [])
            
            logger.info(f"✅ 获取类目树成功: {len(category_tree)}个顶级类目")
            
            # ✅ 缓存到 PG（24小时有效，替代旧 Supabase REST API）
            local_db.set_category_cache(
                ozon_client_id=ozon_client_id,
                tree_data=category_tree,
                language="ZH_HANS",
                expires_in=86400
            )
        
        # ✅ 核心：LLM两步智能匹配类目
        # Step 1: 扁平化类目树，提取所有有type_id的叶子节点
        all_candidates = _flatten_category_tree(category_tree)
        logger.info(f"扁平化类目树: 共{len(all_candidates)}个叶子类型节点")
        
        matched_category: Optional[dict] = None
        
        # Step 2: 使用LLM两步匹配
        if all_candidates and title:
            logger.info(f"启动LLM两步匹配（{len(all_candidates)}个候选）...")
            llm_match = _llm_match_category(title, attributes, all_candidates, token)
            
            if llm_match:
                matched_category = {
                    "description_category_id": llm_match["description_category_id"],
                    "type_id": llm_match["type_id"],
                    "type_name": llm_match.get("type_name", ""),
                    "category_name": llm_match.get("type_name", ""),
                }
                logger.info(f"✅ LLM两步匹配成功: {llm_match['category_path']}")
            else:
                logger.warning("LLM两步匹配失败，回退到关键词子串匹配...")
        
        # Step 3: LLM匹配失败时的兜底 — 关键词子串匹配
        if not matched_category:
            title_lower = title.lower() if title else ""
            title_keywords: List[str] = []
            if title_lower:
                jieba_words = [w for w in jieba.cut(title_lower) if len(w) >= 2 and w.strip()]
                title_keywords.extend(jieba_words)
                search_text = title_lower[:20]
                for n in (4, 3, 2):
                    for i in range(len(search_text) - n + 1):
                        kw = search_text[i:i + n]
                        if kw not in title_keywords:
                            title_keywords.append(kw)
            
            logger.info(f"关键词兜底匹配，候选关键词: {title_keywords[:15]}")
            
            global_best_node = None
            global_best_score = 0
            global_best_desc_id = ""
            
            for parent_category in category_tree:
                parent_cat_id_str = str(parent_category.get("description_category_id", ""))
                children = parent_category.get("children", [])
                if not isinstance(children, list) or len(children) == 0:
                    continue
                
                child_node, child_score, child_desc_id = _find_best_type_fallback(
                    children, title_keywords, title_lower, 0, parent_cat_id_str
                )
                if child_node and child_score > global_best_score:
                    global_best_score = child_score
                    global_best_node = child_node
                    global_best_desc_id = child_desc_id
            
            if global_best_node and global_best_score > 0:
                matched_category = global_best_node
                if not global_best_node.get("description_category_id"):
                    global_best_node["description_category_id"] = global_best_desc_id
                logger.info(f"✅ 关键词兜底匹配成功: score={global_best_score}")
        
        # ✅ 提取类目信息
        if matched_category:
            description_category_id_str = str(matched_category.get("description_category_id", ""))
            type_id_str = str(matched_category.get("type_id", ""))
            
            # 如果匹配到父类目但没有type_id，取第一个有type_id的叶子节点
            if not type_id_str:
                children = matched_category.get("children", [])
                if children and isinstance(children, list) and len(children) > 0:
                    def _find_first_type_id(nodes):
                        for nd in nodes:
                            if not isinstance(nd, dict):
                                continue
                            tid = nd.get("type_id", "")
                            if tid:
                                return nd
                            ch = nd.get("children", [])
                            if isinstance(ch, list) and ch:
                                found = _find_first_type_id(ch)
                                if found:
                                    return found
                        return None
                    
                    first_type = _find_first_type_id(children)
                    if first_type:
                        type_id_str = str(first_type.get("type_id", ""))
                        logger.info(f"父类目无type_id，取第一个类型节点: type_id={type_id_str}")
            
            logger.info(f"✅ 类目匹配成功: description_category_id={description_category_id_str}, type_id={type_id_str}")
            
            return CategoryLookupOutput(
                category=matched_category,
                description_category_id=description_category_id_str,
                type_id=type_id_str,
                draft=draft,
                source=source,
                extensions=extensions,
                currency_code=currency_code,
                error_message="",
                failed_stage="",
                blocked=False
            )
        else:
            logger.error("未找到匹配的Ozon类目")
            return CategoryLookupOutput(
                category=None,
                description_category_id="",
                type_id="",
                draft=draft,
                source=source,
                extensions=extensions,
                currency_code=currency_code,
                error_message="未找到匹配的Ozon类目",
                failed_stage="category_lookup",
                blocked=True
            )
    
    except requests.RequestException as e:
        logger.error(f"Ozon API请求异常: {str(e)}")
        return CategoryLookupOutput(
            category=None,
            description_category_id="",
            type_id="",
            draft=draft,
            source=source,
            extensions=extensions,
            currency_code=currency_code,
            error_message=f"Ozon API请求异常: {str(e)}",
            failed_stage="category_lookup",
            blocked=True
        )
    
    except Exception as e:
        logger.error(f"类目查找异常: {str(e)}")
        return CategoryLookupOutput(
            category=None,
            description_category_id="",
            type_id="",
            draft=draft,
            source=source,
            extensions=extensions,
            currency_code=currency_code,
            error_message=f"类目查找异常: {str(e)}",
            failed_stage="category_lookup",
            blocked=True
        )

"""属性LLM映射节点 - LLM智能属性映射"""
import os
import json
import time
import logging
import re
import requests
from utils.http_session import session
from typing import Any, Dict, List, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import AttributesLLMInput, AttributesLLMOutput
from utils.mxou_llm import call_mxou_chat_api
from utils.progress_logger import ProgressLogger
from utils.size_mapper import filter_brand_from_hashtags


logger = logging.getLogger(__name__)


# 预定义英文→俄语翻译词典（常见产品属性用词）
_EN_RU_DICT = {
    # 常见材质
    "plastic": "пластик", "metal": "металл", "steel": "сталь", "aluminum": "алюминий",
    "wood": "дерево", "bamboo": "бамбук", "rubber": "резина", "silicone": "силикон",
    "glass": "стекло", "ceramic": "керамика", "fabric": "ткань", "cotton": "хлопок",
    "polyester": "полиэстер", "nylon": "нейлон", "leather": "кожа", "iron": "железо",
    "carbon steel": "углеродистая сталь", "stainless steel": "нержавеющая сталь",
    "pp": "полипропилен", "pe": "полиэтилен", "abs": "АБС-пластик",
    # 常见产品类型
    "rake": "грабли", "shovel": "лопата", "scissors": "ножницы", "knife": "нож",
    "saw": "пила", "trimmer": "триммер", "cutter": "резак", "pruner": "секатор",
    "shears": "ножницы", "hedge trimmer": "ножницы для живой изгороди",
    "garden tool": "садовый инструмент", "hand tool": "ручной инструмент",
    "leaf grabber": "захват для листьев", "leaf collector": "собиратель листьев",
    "leaf rake": "грабли для листьев", "garden rake": "садовые грабли",
    # 常见描述词
    "outdoor": "для улицы", "indoor": "для дома", "portable": "портативный",
    "foldable": "складной", "adjustable": "регулируемый", "lightweight": "легкий",
    "durable": "прочный", "waterproof": "водонепроницаемый", "rechargeable": "аккумуляторный",
    "cordless": "беспроводной", "electric": "электрический", "manual": "ручной",
    "battery powered": "на батарейках", "lithium battery": "литиевая батарея",
    "for garden": "для сада", "for yard": "для двора", "for home": "для дома",
    "for outdoor": "для улицы", "for agriculture": "для сельского хозяйства",
    # 包装/数量
    "piece": "шт.", "pieces": "шт.", "set": "набор", "pack": "упаковка",
    "1 piece": "1 шт.", "2 pieces": "2 шт.", "1 set": "1 набор",
    # 颜色
    "green": "зеленый", "yellow": "желтый", "red": "красный", "blue": "синий",
    "black": "черный", "white": "белый", "orange": "оранжевый", "purple": "фиолетовый",
    "pink": "розовый", "gray": "серый", "grey": "серый", "brown": "коричневый",
    # 国家/产地
    "china": "Китай", "russia": "Россия", "germany": "Германия",
    # 其他
    "no brand": "Нет бренда", "unbranded": "Без бренда",
    "plastic bag": "пластиковый пакет", "box": "коробка", "carton": "картонная коробка",
    "for collecting leaves and debris": "для сбора листьев и мусора",
    "outdoor tool for yard maintenance": "инструмент для ухода за двором",
    "lightweight and easy to use": "легкий и удобный в использовании",
    "garden plastic rake": "пластиковые садовые грабли",
}


def _translate_to_russian(text: str) -> str:
    """将英文文本翻译为俄语，使用预定义词典 + 简单规则"""
    if not text or not isinstance(text, str):
        return text
    result = text.strip()
    # 检查是否已经包含西里尔字母
    has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', result))
    if has_cyrillic:
        return result  # 已有俄语，不翻译
    # 按短语长度降序排序，先匹配长短语
    sorted_items = sorted(_EN_RU_DICT.items(), key=lambda x: len(x[0]), reverse=True)
    lower_result = result.lower()
    for en_phrase, ru_phrase in sorted_items:
        pattern = re.compile(re.escape(en_phrase), re.IGNORECASE)
        result = pattern.sub(ru_phrase, result)
        lower_result = result.lower()
    # 如果翻译后仍有大量拉丁字母，添加俄语前缀说明
    remaining_latin = len(re.findall(r'[a-zA-Z]{2,}', result))
    if remaining_latin > 3:
        # 使用LLM翻译作为兜底（通过简单标记）
        logger.warning(f"翻译后仍有{remaining_latin}个英文词未翻译: {result[:100]}")
    return result


def attributes_llm_node(state: AttributesLLMInput, config: RunnableConfig, runtime: Runtime[Context]) -> AttributesLLMOutput:
    """
    title: 属性LLM映射节点
    desc: 使用LLM智能映射产品属性（deepseek-v4-flash）
    integrations: api.mxou.cn LLM (deepseek-v4-flash)
    """
    ctx = runtime.context
    
    # 添加进度日志
    progress = ProgressLogger()
    progress.log_node_start("attributes_llm_node", "属性LLM映射节点")
    progress.log_node_action("正在进行LLM智能属性映射...")
    
    draft = state.draft
    attributes_schema = state.attributes_schema
    learned_attributes = state.learned_attributes
    dictionary_values = state.dictionary_values  # 关键：接收dictionary_values数据
    token = state.token
    description_category_id = state.description_category_id
    
    # 参数验证
    if not draft:
        return AttributesLLMOutput(
            llm_attributes=[],
            llm_count=0
        )
    
    if not attributes_schema:
        return AttributesLLMOutput(
            llm_attributes=[],
            llm_count=0
        )
    
    try:
        # Step 1: 加载LLM配置
        cfg_file: str = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "config/attributes_llm_cfg.json")
        
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            llm_cfg: Dict[str, Any] = json.load(fd)
        
        # Step 2: 提取配置参数
        llm_config: Dict[str, Any] = llm_cfg.get("config", {})
        sp_template: str = llm_cfg.get("sp", "")
        up_template: str = llm_cfg.get("up", "")
        
        model_id: str = llm_config.get("model", "doubao-seed-1-8-251228")
        temperature: float = llm_config.get("temperature", 0.3)
        max_completion_tokens: int = llm_config.get("max_completion_tokens", 4096)
        
        # Step 3: 精简字典值（避免LLM输入超限）
        # 全量字典值用于缓存，但传给LLM时只传精简摘要（每属性最多20个值）
        summarized_dict_values: Dict[str, Any] = {}
        if isinstance(dictionary_values, dict):
            for attr_id_key, values_list in dictionary_values.items():
                if not isinstance(values_list, list):
                    summarized_dict_values[attr_id_key] = []
                    continue
                total_count: int = len(values_list)
                if total_count <= 20:
                    # 少于20个，全部传递
                    summarized_dict_values[attr_id_key] = [
                        {"id": v.get("id"), "value": v.get("value")}
                        for v in values_list if isinstance(v, dict)
                    ]
                else:
                    # 超过20个，只传前20个 + 总数提示
                    summarized_dict_values[attr_id_key] = {
                        "total_count": total_count,
                        "sample_values": [
                            {"id": v.get("id"), "value": v.get("value")}
                            for v in values_list[:20] if isinstance(v, dict)
                        ],
                        "note": f"共{total_count}个可选值，仅显示前20个。请生成最匹配的候选值，系统将通过API精确匹配dictionary_value_id。"
                    }
        
        logger.info(f"字典值精简完成: 原始{len(dictionary_values) if isinstance(dictionary_values, dict) else 0}个属性, 精简后{len(summarized_dict_values)}个属性")
        
        # Step 3.1: 渲染提示词（使用Jinja2模板）
        sp_tpl: Template = Template(sp_template)
        up_tpl: Template = Template(up_template)
        
        system_prompt: str = sp_tpl.render({})
        
        user_prompt: str = up_tpl.render({
            "draft": draft,
            "attributes_schema": attributes_schema,
            "learned_attributes": learned_attributes,
            "dictionary_values": summarized_dict_values  # ✅ 传递精简后的字典值
        })
        
        # Step 4: 调用 mxou LLM Chat API
        try:
            response_text: str = call_mxou_chat_api(
                token=token,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model_id,
                temperature=temperature,
                max_tokens=max_completion_tokens
            ) or ""
        except Exception as _llm_err:
            logger.error(f"LLM API调用异常: {str(_llm_err)[:200]}")
            return AttributesLLMOutput(llm_attributes=[], llm_count=0)
        
        if not response_text.strip():
            logger.error("LLM返回空响应")
            return AttributesLLMOutput(
                llm_attributes=[],
                llm_count=0
            )
        
        # Step 5: 解析JSON响应（LLM应该返回JSON数组）
        try:
            # 3层防御：清洗LLM输出中的常见问题
            import re as _re
            # 第1层：剥离 Markdown 代码块
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            # 第2层：去除所有控制字符。Python json 不允许 U+2028/U+2029
            import unicodedata
            response_text = ''.join(c for c in response_text if unicodedata.category(c)[0] != 'C' or c in '\n\r\t')
            # 第3层：提取JSON数组片段（LLM可能在前后附加解释文本）
            _match = _re.search(r'\[.*\]', response_text, _re.DOTALL)
            if _match:
                response_text = _match.group(0)

            llm_attributes: Any = json.loads(response_text)
            
            # 验证返回格式是否正确
            if isinstance(llm_attributes, dict) and llm_attributes.get("error"):
                logger.error(f"LLM返回错误: {llm_attributes.get('error')}")
                return AttributesLLMOutput(
                    llm_attributes=[],
                    llm_count=0
                )
            
            if not isinstance(llm_attributes, list):
                logger.error(f"LLM返回格式错误: 不是数组")
                return AttributesLLMOutput(
                    llm_attributes=[],
                    llm_count=0
                )
            
            # 验证每个元素格式
            valid_attributes: List[Dict[str, Any]] = []
            for attr in llm_attributes:
                if isinstance(attr, dict):
                    attribute_id: Any = attr.get("attribute_id")
                    value: Any = attr.get("value")
                    
                    if attribute_id is not None and value is not None:
                        valid_attributes.append({
                            "attribute_id": attribute_id,
                            "value": value,
                            "dictionary_value_id": attr.get("dictionary_value_id"),
                            "source": "llm"
                        })
            
            # ✅ P1修复：按 attribute_id 去重（保留最后出现的，LLM 通常会修正前面的错误值）
            seen_ids: Dict[str, Dict[str, Any]] = {}
            for attr in valid_attributes:
                aid = str(attr.get("attribute_id", ""))
                seen_ids[aid] = attr  # 后出现的覆盖前面的
            if len(seen_ids) < len(valid_attributes):
                logger.warning(f"⚠️ LLM返回了重复attribute_id，已去重：{len(valid_attributes)}→{len(seen_ids)}")
            valid_attributes = list(seen_ids.values())
            
            # ✅ 关键修复：动态查找品牌属性（不再硬编码attribute_id=85）
            # 从schema中查找品牌属性（name包含Brand/品牌/Бренд）
            brand_attr_id: Any = None
            for schema_attr in attributes_schema:
                attr_name: str = str(schema_attr.get("name", "")).lower()
                if any(kw in attr_name for kw in ["brand", "品牌", "бренд"]):
                    brand_attr_id = schema_attr.get("id")
                    logger.info(f"✅ 在schema中找到品牌属性: id={brand_attr_id}, name={schema_attr.get('name')}")
                    break

            if brand_attr_id is not None:
                # ✅ 从字典值中查找"Нет бренда"对应的dictionary_value_id
                brand_dict_id: Any = None
                brand_dict_values: List[Dict[str, Any]] = dictionary_values.get(str(brand_attr_id), [])
                for dv in brand_dict_values:
                    dv_value: str = str(dv.get("value", "")).strip().lower()
                    if dv_value == "нет бренда":
                        brand_dict_id = dv.get("id")
                        logger.info(f"✅ 在字典值中找到'Нет бренда': dictionary_value_id={brand_dict_id}")
                        break

                # ✅ 兜底：字典值中没找到，使用规范文档中的固定值
                if brand_dict_id is None:
                    brand_dict_id = 126745801
                    logger.warning(f"⚠️ 字典值中未找到'Нет бренда'，使用规范文档固定值: dict_id={brand_dict_id}")

                brand_attribute = {
                    "attribute_id": brand_attr_id,
                    "value": "Нет бренда",  # ✅ 修复：使用Ozon接受的俄语值（不是中文"无品牌"）
                    "dictionary_value_id": brand_dict_id,  # ✅ 修复：不再是None
                    "source": "hardcoded"
                }

                # 检查是否已经包含品牌属性（类型安全比较）
                brand_attr_id_str: str = str(brand_attr_id)
                has_brand: bool = any(str(attr.get("attribute_id")) == brand_attr_id_str for attr in valid_attributes)

                if not has_brand:
                    valid_attributes.insert(0, brand_attribute)  # 插入到第一个位置
                    logger.info(f"✅ 添加品牌属性：id={brand_attr_id}, value=Нет бренда, dict_id={brand_dict_id}")
                else:
                    # LLM已生成品牌属性但dict_id可能为-1，覆盖为正确值
                    for attr in valid_attributes:
                        if str(attr.get("attribute_id")) == brand_attr_id_str:
                            attr["value"] = "Нет бренда"
                            attr["dictionary_value_id"] = brand_dict_id
                            attr["source"] = "hardcoded"
                            logger.info(f"✅ 覆盖LLM品牌属性：id={brand_attr_id}, dict_id={brand_dict_id}")
                            break
            else:
                logger.warning("⚠️ 未在schema中找到品牌属性，跳过品牌属性添加")
            
            llm_count: int = len(valid_attributes)
            
            logger.info(f"LLM映射成功: count={llm_count}（包含硬编码品牌属性）")

            # ✅ Step 8.5: 过滤hashtags中的品牌名（属性23171）
            for attr in valid_attributes:
                attr_id_val: Any = attr.get("attribute_id")
                if str(attr_id_val) == "23171":
                    original_tags: str = str(attr.get("value", ""))
                    filtered_tags: str = filter_brand_from_hashtags(original_tags)
                    if filtered_tags != original_tags:
                        attr["value"] = filtered_tags
                        logger.info(f"✅ 属性23171 hashtags品牌过滤: '{original_tags[:60]}' -> '{filtered_tags[:60]}'")
                    break

            # ✅ Step 9: 对 dictionary_value_id == -1 的属性，调用 Ozon API 精确匹配
            ozon_client_id: str = state.ozon_client_id
            ozon_api_key: str = state.ozon_api_key
            type_id_str: str = state.type_id
            cat_id_str: str = description_category_id
            
            if ozon_client_id and ozon_api_key and cat_id_str and type_id_str:
                # 构建 attribute_id -> dictionary_id 映射
                attr_dict_map: Dict[int, int] = {}
                for schema_attr in attributes_schema:
                    s_id: Any = schema_attr.get("id")
                    dict_id: Any = schema_attr.get("dictionary_id", 0)
                    if s_id is not None and dict_id and int(dict_id) > 0:
                        attr_dict_map[int(s_id)] = int(dict_id)
                
                # 对每个 dictionary_value_id == -1 的属性调用 /values/search API
                api_matched: int = 0
                api_failed: int = 0
                for attr in valid_attributes:
                    dv_id: Any = attr.get("dictionary_value_id")
                    if dv_id == -1:
                        attr_id_val: Any = attr.get("attribute_id")
                        attr_val: str = str(attr.get("value", "")).strip()
                        dict_id_for_attr: int = attr_dict_map.get(int(attr_id_val), 0) if attr_id_val is not None else 0
                        
                        if dict_id_for_attr > 0 and attr_val:
                            # ✅ 第一阶段：在完整缓存的字典值中做模糊匹配
                            # 对英文值做常见翻译映射
                            en_ru_map: Dict[str, str] = {
                                "black": "Черный", "white": "Белый", "red": "Красный",
                                "blue": "Синий", "green": "Зеленый", "yellow": "Желтый",
                                "pink": "Розовый", "gray": "Серый", "grey": "Серый",
                                "brown": "Коричневый", "purple": "Фиолетовый", "orange": "Оранжевый",
                                "china": "Китай", "russia": "Россия", "usa": "США",
                                "nylon": "Нейлон", "plastic": "Пластик", "metal": "Металл",
                                "cotton": "Хлопок", "leather": "Кожа", "silicone": "Силикон",
                                "yes": "Да", "no": "Нет", "male": "Мужской", "female": "Женский",
                                "unisex": "Унисекс", "small": "S", "medium": "M", "large": "L",
                                "1 year": "1 год", "2 years": "2 года", "3 years": "3 года"
                            }
                            attr_val_lower: str = attr_val.lower().strip()
                            translated_val: str = en_ru_map.get(attr_val_lower, attr_val)
                            
                            # 在缓存的完整字典值列表中查找
                            cached_match_found: bool = False
                            cached_values_for_attr: List[Dict[str, Any]] = dictionary_values.get(str(attr_id_val), []) if isinstance(dictionary_values, dict) else []
                            
                            if cached_values_for_attr:
                                # 先精确匹配（忽略大小写）
                                for cv in cached_values_for_attr:
                                    cv_value: str = str(cv.get("value", "")).strip()
                                    cv_id: Any = cv.get("id")
                                    if cv_value.lower() == translated_val.lower() or cv_value.lower() == attr_val_lower:
                                        attr["dictionary_value_id"] = cv_id
                                        attr["value"] = cv_value
                                        api_matched += 1
                                        cached_match_found = True
                                        logger.info(f"✅ 缓存匹配成功: attr_id={attr_id_val}, value='{attr_val}' -> dict_id={cv_id}, value='{cv_value}'")
                                        break
                                
                                # 如果精确匹配失败，尝试包含匹配
                                if not cached_match_found:
                                    for cv in cached_values_for_attr:
                                        cv_value = str(cv.get("value", "")).strip()
                                        cv_id = cv.get("id")
                                        if attr_val_lower in cv_value.lower() or translated_val.lower() in cv_value.lower():
                                            attr["dictionary_value_id"] = cv_id
                                            attr["value"] = cv_value
                                            api_matched += 1
                                            cached_match_found = True
                                            logger.info(f"✅ 缓存模糊匹配: attr_id={attr_id_val}, value='{attr_val}' -> dict_id={cv_id}, value='{cv_value}'")
                                            break
                                
                                # ✅ 第三阶段：词级模糊匹配（提取关键词逐词匹配）
                                if not cached_match_found and len(attr_val) > 2:
                                    # 提取输入值的关键词（去除括号、数字范围等）
                                    input_words: List[str] = re.findall(r'[а-яА-ЯёЁa-zA-Z]{3,}', attr_val_lower)
                                    if not input_words and translated_val:
                                        input_words = re.findall(r'[а-яА-ЯёЁa-zA-Z]{3,}', translated_val.lower())
                                    
                                    if input_words:
                                        best_match_cv: Optional[Dict[str, Any]] = None
                                        best_match_score: int = 0
                                        for cv in cached_values_for_attr:
                                            cv_value = str(cv.get("value", "")).strip()
                                            cv_id = cv.get("id")
                                            cv_words: set = set(re.findall(r'[а-яА-ЯёЁa-zA-Z]{3,}', cv_value.lower()))
                                            # 计算匹配分数：输入词在字典值中出现的数量
                                            match_score: int = sum(1 for w in input_words if w in cv_words)
                                            if match_score > best_match_score:
                                                best_match_score = match_score
                                                best_match_cv = cv
                                        
                                        if best_match_cv and best_match_score > 0:
                                            attr["dictionary_value_id"] = best_match_cv.get("id")
                                            attr["value"] = str(best_match_cv.get("value", "")).strip()
                                            api_matched += 1
                                            cached_match_found = True
                                            logger.info(f"✅ 词级模糊匹配: attr_id={attr_id_val}, value='{attr_val}' -> dict_id={best_match_cv.get('id')}, value='{best_match_cv.get('value')}', score={best_match_score}")
                            
                            # ✅ 第二阶段：缓存未命中 → 调用 Ozon API /values/search
                            if not cached_match_found:
                                try:
                                    search_url: str = "https://api-seller.ozon.ru/v1/description-category/attribute/values/search"
                                    search_headers: Dict[str, str] = {
                                        "Client-Id": ozon_client_id,
                                        "Api-Key": ozon_api_key,
                                        "Content-Type": "application/json"
                                    }
                                    # 先用原始值搜索，再用翻译值搜索
                                    search_queries: List[str] = [attr_val]
                                    if translated_val != attr_val:
                                        search_queries.append(translated_val)
                                    
                                    for sq in search_queries:
                                        search_payload: Dict[str, Any] = {
                                            "attribute_id": int(attr_id_val),
                                            "description_category_id": int(cat_id_str),
                                            "type_id": int(type_id_str),
                                            "value": sq,
                                            "limit": 5
                                        }
                                        search_resp: requests.Response = session.post(
                                            search_url, headers=search_headers, json=search_payload, timeout=15
                                        )
                                        
                                        if search_resp.status_code == 200:
                                            search_data: Dict[str, Any] = search_resp.json()
                                            search_results: List[Dict[str, Any]] = search_data.get("result", [])
                                            if search_results:
                                                matched_val: Dict[str, Any] = search_results[0]
                                                matched_id: Any = matched_val.get("id")
                                                matched_text: str = matched_val.get("value", "")
                                                attr["dictionary_value_id"] = matched_id
                                                attr["value"] = matched_text
                                                api_matched += 1
                                                cached_match_found = True
                                                logger.info(f"✅ API匹配成功: attr_id={attr_id_val}, query='{sq}' -> dict_id={matched_id}, value='{matched_text}'")
                                                break
                                        
                                        time.sleep(0.3)
                                    
                                    if not cached_match_found:
                                        api_failed += 1
                                        logger.warning(f"⚠️ API匹配无结果: attr_id={attr_id_val}, value='{attr_val}'")
                                    
                                except Exception as search_err:
                                    api_failed += 1
                                    logger.warning(f"⚠️ API搜索异常: attr_id={attr_id_val}, error={str(search_err)}")
                
                logger.info(f"API精确匹配完成: 成功={api_matched}, 失败={api_failed}")
            
            # ✅ Step 10: 强制将属性4191（Описание）翻译为俄语
            # Ozon要求描述必须包含西里尔字母，纯拉丁字母会被拒绝
            for attr in valid_attributes:
                attr_id_val_str: str = str(attr.get("attribute_id", ""))
                if attr_id_val_str == "4191":
                    attr_val_str: str = str(attr.get("value", "")).strip()
                    # 检测是否纯拉丁字母（无西里尔字符）
                    has_cyrillic: bool = bool(re.search(r'[а-яА-ЯёЁ]', attr_val_str))
                    if not has_cyrillic and attr_val_str:
                        logger.warning(f"⚠️ 属性4191描述为拉丁字母，调用LLM翻译为俄语: {attr_val_str[:80]}...")
                        try:
                            translated_text: str = call_mxou_chat_api(
                                token=token,
                                system_prompt="你是一个专业翻译。将给定的英文产品描述翻译成俄语。只返回翻译后的俄语文本，不要添加任何其他内容。",
                                user_prompt=f"翻译为俄语: {attr_val_str}",
                                model=model_id,
                                temperature=0.0,
                                max_tokens=500
                            ) or ""
                            
                            translated_text = translated_text.strip()
                            
                            if translated_text and re.search(r'[а-яА-ЯёЁ]', translated_text):
                                attr["value"] = translated_text
                                logger.info(f"✅ 属性4191翻译为俄语成功: {translated_text[:80]}...")
                            else:
                                logger.error(f"❌ 翻译结果不含西里尔字母，保留原值")
                        except Exception as trans_err:
                            logger.error(f"❌ 翻译异常: {str(trans_err)}")
                    else:
                        logger.info(f"✅ 属性4191已包含西里尔字母，无需翻译")
                    break
            
            return AttributesLLMOutput(
                llm_attributes=valid_attributes,
                llm_count=llm_count
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {str(e)}, response_text={response_text[:200]}")
            return AttributesLLMOutput(
                llm_attributes=[],
                llm_count=0
            )
        
    except Exception as e:
        logger.error(f"LLM映射失败: {str(e)}")
        return AttributesLLMOutput(
            llm_attributes=[],
            llm_count=0
        )
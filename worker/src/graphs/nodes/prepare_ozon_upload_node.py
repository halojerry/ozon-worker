import os
import json
import time
import re
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import PrepareOzonUploadInput, PrepareOzonUploadOutput
from utils.progress_logger import ProgressLogger
from utils.size_mapper import build_attribute_matching_table
from utils.mxou_llm import call_mxou_chat_api

logger = logging.getLogger(__name__)


# ✅ 中俄颜色映射（用于多SKU变体的颜色属性，属性ID动态检测）
COLOR_CN_TO_RU: Dict[str, str] = {
    "绿色": "зеленый",
    "黄色": "желтый",
    "军绿色": "зеленый",
    "黑色": "черный",
    "白色": "белый",
    "红色": "красный",
    "蓝色": "синий",
    "灰色": "серый",
    "橙色": "оранжевый",
    "紫色": "фиолетовый",
    "粉色": "розовый",
    "棕色": "коричневый",
    "透明": "прозрачный",
    "银色": "серебристый",
    "金色": "золотой",
    "深色": "темный",
    "浅色": "светлый",
    "深绿色": "темно-зеленый",
    "浅绿色": "светло-зеленый",
    "深蓝色": "темно-синий",
    "浅蓝色": "светло-синий",
    "深红色": "бордовый",
    "浅红色": "светло-розовый",
    "深灰色": "темно-серый",
    "浅灰色": "светло-серый",
    "军绿色 ": "зеленый",
    "卡其色": "хаки",
    "米色": "бежевый",
    "酒红色": "бордовый",
    "墨绿色": "темно-зеленый",
    "藏青色": "темно-синий",
    "湖蓝色": "бирюзовый",
    "橄榄绿": "оливковый",
}

# ✅ Ozon颜色字典值映射（颜色属性ID因类目而异，dictionary_value_id全局一致）
# 来源: Ozon API /v1/description-category/attribute/values/search
COLOR_RU_TO_DICT_ID: Dict[str, int] = {
    "зеленый": 61583,
    "светло-зеленый": 61589,
    "темно-зеленый": 61602,
    "желтый": 61578,
    "светло-желтый": 970673967,
    "красный": 61579,
    "коричнево-красный": 61603,
    "синий": 61581,
    "светло-синий": 971001201,
    "темно-синий": 61592,
    "черный": 61574,
    "черный матовый": 970671251,
    "белый": 61571,
    "розовый": 61580,
    "светло-розовый": 61596,
    "темно-розовый": 61611,
    "оранжевый": 61585,
    "фиолетовый": 61586,
    "серый": 61576,
    "серый металлик": 61577,
    "светло-серый": 61594,
    "темно-серый": 61600,
    "черно-серый": 61607,
    "коричневый": 61575,
    "светло-коричневый": 61591,
    "темно-коричневый": 61598,
    "прозрачный": 61572,
    "серебристый": 61610,
    "золотой": 61582,
    "бирюзовый": 61595,
    "бежевый": 61573,
    "светло-бежевый": 61593,
    "темно-бежевый": 61604,
    "бордовый": 61590,
    "темно-бордовый": 970832145,
    "хаки": 258411654,
    "оливковый": 61605,
}

# ✅ 颜色去重替代列表：当多个变体颜色相同时，依次使用替代颜色（均为字典有效值）
# 确保每个变体颜色都有 dictionary_value_id > 0
COLOR_DEDUP_ALTS: Dict[str, List[tuple]] = {
    "зеленый": [("светло-зеленый", 61589), ("темно-зеленый", 61602), ("оливковый", 61605)],
    "желтый": [("светло-желтый", 970673967), ("оранжевый", 61585)],
    "красный": [("коричнево-красный", 61603), ("бордовый", 61590)],
    "синий": [("светло-синий", 971001201), ("темно-синий", 61592)],
    "черный": [("черный матовый", 970671251), ("темно-серый", 61600)],
    "белый": [("светло-серый", 61594), ("бежевый", 61573)],
    "розовый": [("светло-розовый", 61596), ("темно-розовый", 61611)],
    "серый": [("серый металлик", 61577), ("светло-серый", 61594), ("темно-серый", 61600)],
    "коричневый": [("светло-коричневый", 61591), ("темно-коричневый", 61598)],
}

# ✅ Fallback颜色列表：当变体"color"字段不是真实颜色名（如产品描述）时使用
# 确保每个变体都有dict_id > 0的有效颜色
FALLBACK_COLORS: List[tuple[str, int]] = [
    ("белый", 61571),
    ("черный", 61574),
    ("серый", 61576),
    ("синий", 61581),
    ("зеленый", 61583),
    ("красный", 61579),
    ("желтый", 61578),
    ("розовый", 61580),
    ("оранжевый", 61585),
    ("фиолетовый", 61586),
]


def _has_cyrillic(text: str) -> bool:
    """检测文本是否包含西里尔字母（俄语）"""
    if not text or not isinstance(text, str):
        return False
    return bool(re.search(r'[а-яА-ЯёЁ]', text))


def _has_chinese(text: str) -> bool:
    """检测文本是否包含中文字符"""
    if not text or not isinstance(text, str):
        return False
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def _get_color_from_dictionary(
    dictionary_values: Dict[str, List[Dict[str, Any]]],
    color_attr_id: int,
    used_dict_ids: set,
    preferred_cn_color: str = ""
) -> tuple[str, int]:
    """
    从Ozon API字典值中动态选择颜色。
    
    参数:
        dictionary_values: Ozon属性字典值缓存 {attribute_id_str: [{id,value,info},...]}
        color_attr_id: 颜色属性ID（如10096/10097）
        used_dict_ids: 已使用的dictionary_value_id集合（确保颜色不重复）
        preferred_cn_color: 优先匹配的中文名称
    
    返回:
        (俄语颜色值, dictionary_value_id) 或 ("", 0)
    """
    if not dictionary_values:
        return ("", 0)
    
    attr_id_str: str = str(color_attr_id)
    color_list: List[Dict[str, Any]] = dictionary_values.get(attr_id_str, [])
    if not color_list:
        return ("", 0)
    
    # 如果提供了中文颜色名，尝试匹配
    if preferred_cn_color:
        preferred_lower: str = preferred_cn_color.strip().lower()
        for item in color_list:
            item_id: int = item.get("id", 0)
            if item_id in used_dict_ids:
                continue
            info_list: List[Dict[str, Any]] = item.get("info", [])
            for info_item in info_list:
                cn_val: str = info_item.get("value", "")
                if cn_val and cn_val.strip().lower() == preferred_lower:
                    return (item.get("value", ""), item_id)
    
    # 无中文匹配或未提供：选第一个未使用的颜色
    for item in color_list:
        item_id: int = item.get("id", 0)
        if item_id not in used_dict_ids and item_id > 0:
            return (item.get("value", ""), item_id)
    
    # 全部已使用：返回第一个（允许重复，但至少有合法dict_id）
    if color_list:
        first_item: Dict[str, Any] = color_list[0]
        return (first_item.get("value", ""), first_item.get("id", 0))
    
    return ("", 0)


def _translate_to_russian_llm(text: str, token: str, source_lang: str = "auto", text_type: str = "description") -> str:
    """
    使用mxou LLM API将文本翻译为俄语。
    token: mxou API密钥（用户输入）
    source_lang: "zh"（中文→俄语）、"en"（英文→俄语）、"auto"（自动检测）
    text_type: "title"（标题翻译，有额外规则）或 "description"（普通翻译）
    返回翻译后的俄语文本；如果翻译失败则返回原文。
    """
    if not text or not isinstance(text, str) or not text.strip():
        return text
    if _has_cyrillic(text):
        return text  # 已经包含俄语，无需翻译

    try:
        cfg_file: str = os.path.join(
            os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"),
            "config/attributes_llm_cfg.json"
        )
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            llm_cfg: Dict[str, Any] = json.load(fd)

        llm_config: Dict[str, Any] = llm_cfg.get("config", {})
        model_id: str = llm_config.get("model", "deepseek-v4-flash")

        if text_type == "title":
            # 标题翻译：有严格的格式规则（标点、长度、去营销词、禁止关键词堆砌）
            if source_lang == "zh" or _has_chinese(text):
                sys_prompt: str = (
                    "你是Ozon电商平台的产品标题翻译专家。将给定的中文产品标题翻译成俄语。\n\n"
                    "严格规则（违反任何一条都会导致Ozon审核拒绝）：\n"
                    "1. 标题长度不超过50个字符（含空格和标点）\n"
                    "2. 标题中必须包含标点符号（逗号或破折号），例如：\"Садовые грабли, пластиковые\"\n"
                    "3. 绝对禁止关键词堆砌：同一标题中不允许出现3个以上连续的名词性关键词\n"
                    "4. 去除所有营销词汇：跨境爆款、现货、亚马逊、爆款、热销、新品、促销、优惠、限时、工厂直供、量大从优等\n"
                    "5. 去除重复的关键词，同一词语不要出现两次\n"
                    "6. 格式：核心产品名 + 逗号 + 1个关键特性（如材质或用途）\n"
                    "7. 返回纯俄语文本，不要添加任何解释或前缀\n\n"
                    "正确示例：\n"
                    "- 输入：\"跨境爆款 现货 Frog Plant Stand 绿色动物宠物青蛙装饰植物架\"\n"
                    "- 输出：\"Подставка для растений, декоративная лягушка\"\n"
                    "- 输入：\"亚马逊创意JungleSpoon绿叶子漏勺龟背叶勺子捞面勺调羹漏勺\"\n"
                    "- 输出：\"Кухонная ложка-шумовка, лист монстеры\"\n\n"
                    "错误示例（关键词堆砌，会被Ozon拒绝）：\n"
                    "- ❌ \"Автоматический разбрызгиватель 360 вращающийся садовый оросительный газонный дождеватель\"\n"
                    "- ✅ \"Садовый разбрызгиватель 360°, для полива\""
                )
            else:
                sys_prompt = (
                    "You are an Ozon e-commerce product title translation expert. Translate the given English product title into Russian.\n\n"
                    "Strict rules (violation of any rule will cause Ozon to reject the product):\n"
                    "1. Title length must NOT exceed 50 characters (including spaces and punctuation)\n"
                    "2. Title must contain punctuation (comma or dash), e.g.: \"Garden rake, plastic\"\n"
                    "3. ABSOLUTELY NO keyword stuffing: do not use 3+ consecutive noun keywords\n"
                    "4. Remove all marketing words: Amazon, hot sale, new, bestseller, etc.\n"
                    "5. Remove duplicate keywords - no word should appear twice\n"
                    "6. Format: core product name + comma + 1 key feature (material or use)\n"
                    "7. Return only Russian text without any explanation\n\n"
                    "Correct examples:\n"
                    "- Input: \"Frog Plant Stand Green Animal Pet Frog Decoration Plant Rack\"\n"
                    "- Output: \"Подставка для растений, декоративная лягушка\"\n"
                    "- Input: \"JungleSpoon Green Leaf Colander Monstera Spoon Noodle Strainer Ladle\"\n"
                    "- Output: \"Кухонная ложка-шумовка, лист монстеры\""
                )
        else:
            # 普通翻译（描述等）
            if source_lang == "zh" or _has_chinese(text):
                sys_prompt = "你是一个专业翻译。将给定的中文产品名称或描述翻译成俄语。只返回翻译后的俄语文本，不要添加任何其他内容、解释或标点符号前缀。"
            else:
                sys_prompt = "You are a professional translator. Translate the given English product text into Russian. Return only the translated Russian text, without any additional content or explanation."

        translated: str = call_mxou_chat_api(
            token=token,
            system_prompt=sys_prompt,
            user_prompt=f"Translate to Russian: {text}",
            model=model_id,
            temperature=0.0,
            max_tokens=500
        ) or ""

        translated = translated.strip()

        if translated and _has_cyrillic(translated):
            logger.info(f"✅ LLM翻译成功: '{text[:50]}' → '{translated[:50]}'")
            return translated
        else:
            logger.error(f"❌ 翻译结果不含西里尔字母，保留原文: '{text[:50]}'")
            return ""  # 不返回中文原文，避免 Ozon 显示乱码
    except Exception as e:
        logger.error(f"❌ LLM翻译异常: {str(e)}")
        return ""  # 不返回中文原文


def _sanitize_title(title: str) -> str:
    """
    标题后校验与修正：确保标题符合Ozon规范。
    1. 截断到50字符（在词边界截断）
    2. 检测关键词堆砌（连续3+名词无标点分隔）并修复
    3. 确保至少有一个标点符号
    4. 去除营销残留词
    """
    if not title or not isinstance(title, str):
        return title

    sanitized: str = title.strip()

    # 1. 去除残留营销词（俄语常见 + 英语常见）
    marketing_words_ru: list = [
        "хит", "распродажа", "акция", "скидка", "новинка", "бестселлер",
        "кроссбордер", "бесплатно", "премиум", "эксклюзив", "ограничено",
        "топ", "лучший", "популярный", "тренд"
    ]
    marketing_words_en: list = [
        "hot", "sale", "bestseller", "new", "premium", "free",
        "amazon", "exclusive", "trending", "top", "best", "popular"
    ]
    all_marketing: list = marketing_words_ru + marketing_words_en
    words: list = sanitized.split()
    filtered_words: list = []
    for w in words:
        w_lower: str = w.lower().strip(".,!?:;\"'()[]{}")
        if w_lower not in all_marketing:
            filtered_words.append(w)
    sanitized = " ".join(filtered_words).strip()
    if not sanitized:
        return title  # 全被过滤了，返回原标题

    # 2. 关键词堆砌检测：检查是否有5个以上连续词无标点分隔
    # 只有标题超过50字符且无标点时才认为是堆砌
    segments: list = re.split(r'[,\-—:;]', sanitized)
    long_segment_found: bool = False
    for seg in segments:
        seg = seg.strip()
        if seg:
            seg_words: int = len(seg.split())
            if seg_words > 5:
                long_segment_found = True
                break

    if long_segment_found and len(sanitized) > 50:
        # 修复：在关键词之间插入逗号，使标题更自然
        all_words_list: list = sanitized.split()
        if len(all_words_list) > 5:
            # 重建标题：前2词 + 逗号 + 第3-4词 + 逗号 + 剩余词
            new_words: list = []
            for i, w in enumerate(all_words_list):
                new_words.append(w)
                if i == 1 or i == 3:
                    new_words.append(",")
            sanitized = " ".join(new_words)
    elif long_segment_found and len(sanitized) <= 50:
        # 标题不超50字符但连续词多，不强制加标点（Ozon允许短标题无标点）
        pass

    # 4. 截断到50字符（在词边界截断）
    if len(sanitized) > 50:
        truncated: str = sanitized[:50]
        # 找到最后一个空格，在词边界截断
        last_space: int = truncated.rfind(" ")
        if last_space > 20:
            truncated = truncated[:last_space]
        # 去除末尾的标点
        truncated = truncated.rstrip(" ,-—:;")
        # 确保截断后仍有标点
        if not re.search(r'[,\-—:]', truncated) and len(truncated.split()) >= 3:
            wl: list = truncated.split()
            if len(wl) >= 3:
                wl.insert(2, ",")
                truncated = " ".join(wl)
        sanitized = truncated

    # 5. 清理多余空格
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    # 清理 " ," → ","
    sanitized = re.sub(r'\s+([,])', r'\1', sanitized)
    sanitized = re.sub(r'([,])(?!\s)', r'\1 ', sanitized)

    if sanitized != title:
        logger.info(f"🔧 标题校验修正: '{title[:60]}' → '{sanitized[:60]}'")

    return sanitized


def prepare_ozon_upload_node(
    state: PrepareOzonUploadInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> PrepareOzonUploadOutput:
    """
    title: Ozon上传数据准备（严格遵守Ozon规范）
    desc: 单位转换、vat固定、俄语标题、1688 SKU_ID、促销价格、完整Ozon结构
    integrations: api.mxou.cn LLM翻译 (deepseek-v4-flash), Ozon API
    """
    ctx = runtime.context
    
    # 获取 mxou API token（用户输入）
    mxou_token: str = state.token
    
    # 添加进度日志
    progress = ProgressLogger()
    progress.log_node_start("prepare_ozon_upload_node", "Ozon上传数据准备节点")
    progress.log_node_action("正在组装Ozon payload（严格遵守Ozon结构规范）")
    
    # Ozon图片上传顺序规范（按俄罗斯电商习惯）
    IMG_ORDER = [
        "main_image",        # 1. 主营销图（最重要）
        "detail",            # 2. 详情图
        "scene_1",           # 3. 场景图1
        "scene_2",           # 4. 场景图2
        "scene_3",           # 5. 场景图3
        "comparison",        # 6. 对比图
        "social_proof",      # 7. 社交证明图
        "multi_angle",       # 8. 多角度展示图
        "white_bg"           # 9. 纯白底图
    ]
    # ⚠️ multi_info 从共享画廊移除：Ozon禁止附加图片包含文字/广告/价格/联系方式
    
    # Step 1: 整理图片顺序
    logger.info("整理图片顺序")
    
    # ✅ 构建共享营销图列表（不含变体白底图）
    shared_marketing_images: List[str] = []
    
    # 添加main_image作为共享画廊第一张
    main_image = getattr(state, "main_image", None)
    if main_image and isinstance(main_image, str) and main_image.strip():
        shared_marketing_images.append(main_image.strip())
    
    # 按IMG_ORDER添加其他营销图片
    for img_key in IMG_ORDER[1:]:  # 从第2个开始（跳过main_image，已处理）
        img_url = getattr(state, f"{img_key}_image", None)
        if img_url and isinstance(img_url, str) and img_url.strip():
            shared_marketing_images.append(img_url.strip())
            logger.info(f"图片 {img_key}: {img_url}")
    
    logger.info(f"共享营销图数量: {len(shared_marketing_images)}")

    # ✅ 营销图为空时不再fallback到alicdn（Ozon无法下载alicdn URL），记录错误让validation_retry处理
    if not shared_marketing_images:
        logger.warning("⚠️ 营销图为空，生图节点可能失败（mxou COS URL未生成），不使用alicdn原始图")

    variant_primary_images_list = state.variant_primary_images if state.variant_primary_images else []
    has_variant_images: bool = any(isinstance(img, str) and img.strip() for img in variant_primary_images_list)
    if has_variant_images:
        logger.info(f"✅ 多SKU产品：{len(variant_primary_images_list)}张变体主图（各SKU独立primary_image）")
    else:
        logger.info(f"✅ 单SKU产品：使用main_image作为primary_image")
    
    # Step 2: 提取draft数据
    draft = state.draft or {}
    source = state.source or {}  # ✅ 提取source数据（采购来源信息）
    attributes_schema = state.attributes_schema if state.attributes_schema else []
    
    # ✅ 关键修复：构建字典属性查找表（attribute_id -> dictionary_id）
    # 用于校验：字典类型属性必须有有效的dictionary_value_id
    dict_attr_lookup: Dict[int, int] = {}
    for schema_attr in attributes_schema:
        if isinstance(schema_attr, dict):
            schema_attr_id = schema_attr.get("id")
            schema_dict_id = schema_attr.get("dictionary_id", 0)
            if schema_attr_id and schema_dict_id and int(schema_dict_id) > 0:
                dict_attr_lookup[int(schema_attr_id)] = int(schema_dict_id)
    # ✅ 补充：从dictionary_values（Ozon API字典值缓存）确认字典属性
    # 某些属性可能在schema中dictionary_id为0但实际有字典值（如10096颜色）
    dictionary_values = state.dictionary_values if state.dictionary_values else {}
    if dictionary_values:
        for attr_id_str, values_list in dictionary_values.items():
            if values_list and isinstance(values_list, list) and len(values_list) > 0:
                attr_id_int = int(attr_id_str)
                if attr_id_int not in dict_attr_lookup:
                    dict_attr_lookup[attr_id_int] = 1  # 标记为字典类型
    logger.info(f"✅ 字典属性查找表：{len(dict_attr_lookup)}个字典类型属性")
    
    # ✅ 提取必填属性ID列表（用于属性匹配对照表和缺失检查）
    required_attr_ids: List[int] = []
    required_attrs: List[Dict[str, Any]] = []
    for schema_attr in attributes_schema:
        if isinstance(schema_attr, dict):
            is_required = schema_attr.get("is_required", False)
            if is_required:
                try:
                    req_id = int(schema_attr.get("id", 0))
                    if req_id > 0:
                        required_attr_ids.append(req_id)
                        required_attrs.append({
                            "id": req_id,
                            "name": schema_attr.get("name", ""),
                            "dictionary_id": int(schema_attr.get("dictionary_id", 0))
                        })
                except (ValueError, TypeError):
                    continue
    logger.info(f"✅ 必填属性：{len(required_attr_ids)}个 — {required_attr_ids}")
    
    # ✅ 策略：仅使用AI生成的图片（myqcloud.com），不使用原始产品图片（alicdn.com已失效404）
    # 原因：alicdn.com原始产品图片URL已失效（返回404），无法重新托管到S3，Ozon也无法访问
    # AI生成的图片在myqcloud.com上，可以成功重新托管到S3供Ozon访问
    logger.info(f"使用AI生成图片：{len(shared_marketing_images)}张（不含原始产品图）")

    # ✅ 限制图片数量为15张（Ozon上限）
    if len(shared_marketing_images) > 15:
        shared_marketing_images = shared_marketing_images[:15]
        logger.info(f"⚠️ 图片超过15张上限，截取前15张")
    
    title_cn = draft.get("title", "")
    description = draft.get("description", "")
    if not description or not description.strip():
        description = title_cn
        logger.warning(f"⚠️ description初始为空，暂用标题占位，后续从属性4191提取")
    
    # ✅ 新增：提取变体商品信息（用于属性9048绑定）
    item_id = draft.get("item_id", "")  # 1688商品ID（用于变体绑定）
    variants = state.variants if state.variants else []
    variant_primary_images = state.variant_primary_images if state.variant_primary_images else []
    
    logger.info(f"商品ID（item_id）：{item_id}")
    logger.info(f"变体SKU数量：{len(variants)}")
    logger.info(f"已生成变体主图数量：{len(variant_primary_images)}")
    
    # 提取重量和尺寸（兼容两种格式：扁平字段 or dimensions嵌套对象）
    weight_raw = draft.get("weight", 0)
    
    # ✅ 尺寸提取：优先从dimensions嵌套对象提取，兼容扁平字段
    dimensions_obj = draft.get("dimensions", {})
    if isinstance(dimensions_obj, dict) and dimensions_obj:
        depth_raw = dimensions_obj.get("length", 0) or dimensions_obj.get("depth", 0)
        width_raw = dimensions_obj.get("width", 0)
        height_raw = dimensions_obj.get("height", 0)
    else:
        depth_raw = draft.get("depth", 0) or draft.get("length", 0)
        width_raw = draft.get("width", 0)
        height_raw = draft.get("height", 0)
    
    # ✅ 重量单位判断：skill 保证发送克，仅当带小数点时判定为 kg
    if weight_raw and isinstance(weight_raw, str) and '.' in str(weight_raw):
        try:
            weight_g = int(float(weight_raw) * 1000)  # kg → g
            logger.info(f"重量单位判断：{weight_raw}kg → {weight_g}g")
        except (ValueError, TypeError):
            weight_g = 100  # 默认 100g
            logger.warning(f"重量无法解析（{weight_raw}），使用默认值 100g")
    else:
        try:
            weight_g = int(float(weight_raw)) if weight_raw else 0
        except (ValueError, TypeError):
            weight_g = 100
            logger.warning(f"重量无法解析（{weight_raw}），使用默认值 100g")
        logger.info(f"重量单位判断：{weight_raw}g（直接使用）")
    
    # ✅ 尺寸单位智能判断：1688数据可能是cm或mm
    # 启发式规则：如果任一维度 < 50，大概率是cm（mm下50mm=5cm太小了）
    # Ozon API 要求 mm 单位
    def _safe_float(val) -> float:
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0
    d_val = _safe_float(depth_raw)
    w_val = _safe_float(width_raw)
    h_val = _safe_float(height_raw)
    
    max_dim = max(d_val, w_val, h_val)
    if max_dim > 0 and max_dim < 50:
        # 大概率是cm，转换为mm
        depth_mm = int(d_val * 10)
        width_mm = int(w_val * 10)
        height_mm = int(h_val * 10)
        logger.info(f"尺寸单位判断：检测为cm（max={max_dim}），转换为mm：{depth_mm}×{width_mm}×{height_mm}")
    else:
        # 已经是mm
        depth_mm = int(d_val)
        width_mm = int(w_val)
        height_mm = int(h_val)
        logger.info(f"尺寸单位判断：检测为mm（max={max_dim}），直接使用：{depth_mm}×{width_mm}×{height_mm}")
    
    # ✅ 尺寸重量验证
    dimension_weight_issues = []
    
    # ✅ 关键修复：dimensions全为0时使用默认值（Ozon API明确要求"不要指定0"）
    if weight_g == 0:
        weight_g = 100  # 默认100g
        dimension_weight_issues.append(f"重量缺失（使用默认值{weight_g}g）")
        logger.warning(f"⚠️ 重量为0，使用默认值: {weight_g}g")
    
    if depth_mm == 0 and width_mm == 0 and height_mm == 0:
        depth_mm = 300
        width_mm = 200
        height_mm = 50
        dimension_weight_issues.append(f"尺寸全为0（使用默认值{depth_mm}×{width_mm}×{height_mm}mm）")
        logger.warning(f"⚠️ 尺寸全为0，使用默认值: {depth_mm}×{width_mm}×{height_mm}mm")
    else:
        if depth_mm == 0:
            depth_mm = 100
            logger.warning(f"⚠️ 长度为0，使用默认值: {depth_mm}mm")
        if width_mm == 0:
            width_mm = 100
            logger.warning(f"⚠️ 宽度为0，使用默认值: {width_mm}mm")
        if height_mm == 0:
            height_mm = 50
            logger.warning(f"⚠️ 高度为0，使用默认值: {height_mm}mm")
    
    # ✅ dimension_weight_issues仅作为日志记录，不再加入validation_errors（已用默认值修复）
    
    if dimension_weight_issues:
        logger.error(f"❌ 尺寸重量问题：{dimension_weight_issues}")
    
    # ✅ 密度验证：Ozon要求密度在 1.293 ~ 13546 kg/m³ 之间
    # density (kg/m³) = weight(g) / 1000 / (depth* width * height(mm) / 1e9)
    if weight_g > 0 and depth_mm > 0 and width_mm > 0 and height_mm > 0:
        volume_m3: float = (depth_mm * width_mm * height_mm) / 1e9
        density_kg_m3: float = (weight_g / 1000.0) / volume_m3 if volume_m3 > 0 else 0.0
        logger.info(f"密度验证：{weight_g}g / {volume_m3:.6f}m³ = {density_kg_m3:.1f} kg/m³ (范围: 1.293~13546)")
        if density_kg_m3 > 13546:
            # 密度过高，重量可能被错误放大，尝试除以1000
            adjusted_weight: int = max(1, weight_g // 1000)
            adjusted_density: float = (adjusted_weight / 1000.0) / volume_m3
            if 1.293 <= adjusted_density <= 13546:
                logger.warning(f"⚠️ 密度{density_kg_m3:.1f}超出范围，重量{weight_g}g→{adjusted_weight}g（可能误将g当kg转换）")
                weight_g = adjusted_weight
            else:
                logger.error(f"❌ 密度{density_kg_m3:.1f}严重超出范围，即使调整重量也无法修正")
        elif density_kg_m3 < 1.293 and density_kg_m3 > 0:
            # 密度过低，重量可能需要放大
            adjusted_weight = weight_g * 10
            adjusted_density = (adjusted_weight / 1000.0) / volume_m3
            if 1.293 <= adjusted_density <= 13546:
                logger.warning(f"⚠️ 密度{density_kg_m3:.1f}低于范围，重量{weight_g}g→{adjusted_weight}g（可能单位需调整）")
                weight_g = adjusted_weight
            else:
                logger.warning(f"⚠️ 密度{density_kg_m3:.1f}低于范围但无法自动修正，保持原值")
    
    logger.info(f"最终尺寸：{depth_mm}×{width_mm}×{height_mm}mm, 重量={weight_g}g")
    
    # 提取1688 SKU_ID（作为offer_id）
    sku_id = draft.get("sku_id", "") or draft.get("offer_id", "")
    if not sku_id:
        # 如果没有SKU_ID，生成一个基于时间戳的临时ID（不建议，但作为兜底）
        sku_id = f"temp_{int(time.time())}"
        logger.warning(f"1688 SKU_ID缺失，使用临时ID: {sku_id}")
    
    # ✅ 提取采购信息（采购链接和采购成本）
    # 从draft中提取采购链接和采购成本（扁平payload直接包含）
    purchase_url = draft.get("purchase_url", "")  # 采购链接
    purchase_cost_raw = draft.get("purchase_cost", "")  # 采购成本（CNY）
    purchase_cost = str(purchase_cost_raw) if purchase_cost_raw else ""  # ✅ 转换为string类型
    
    # 如果draft中没有采购信息，尝试从source中提取
    if not purchase_url and isinstance(source, dict):
        purchase_url = source.get("purchase_url", "")
    if not purchase_cost and isinstance(source, dict):
        purchase_cost_raw = source.get("purchase_cost", "")
        purchase_cost = str(purchase_cost_raw) if purchase_cost_raw else ""  # ✅ 转换为string类型
    
    logger.info(f"采购链接：{purchase_url}")
    logger.info(f"采购成本：{purchase_cost} CNY")
    
    # Step 3: 提取价格数据
    pricing_info = state.pricing_info or {}
    price = pricing_info.get("price", 0)
    old_price = pricing_info.get("old_price", 0)
    currency_code = pricing_info.get("currency_code", "RUB")
    
    # ✅ 新增：提取利润预估（从pricing_info）
    profit_estimation = pricing_info.get("profit_estimation", {})
    
    logger.info(f"价格：{price} {currency_code}")
    logger.info(f"促销价格：{old_price} {currency_code}")
    
    # Step 4: 提取类目和属性数据
    description_category_id = state.description_category_id or 0
    type_id = state.type_id or 0
    final_attributes = state.final_attributes or []

    # ✅ 属性质量校验：去重 + 重量单位 + 字典值
    # 1) 按attribute_id去重（保留第一个）
    seen_attr_ids: set = set()
    deduped_attributes: list = []
    for attr in final_attributes:
        attr_id = attr.get("attribute_id", 0) if isinstance(attr, dict) else 0
        try:
            attr_id_int = int(attr_id)
        except (ValueError, TypeError):
            deduped_attributes.append(attr)
            continue
        if attr_id_int not in seen_attr_ids:
            seen_attr_ids.add(attr_id_int)
            deduped_attributes.append(attr)
    if len(deduped_attributes) < len(final_attributes):
        logger.warning(f"⚠️ 属性去重：{len(final_attributes)}→{len(deduped_attributes)}（移除{len(final_attributes)-len(deduped_attributes)}个重复）")
    final_attributes = deduped_attributes

    # 2) 重量单位校验（4383：克g，若值<100则×1000修正kg→g）
    for attr in final_attributes:
        if not isinstance(attr, dict):
            continue
        try:
            if int(attr.get("attribute_id", 0)) == 4383:
                val_str = str(attr.get("value", ""))
                val_clean = val_str.replace(".", "").replace(",", "")
                if val_clean.isdigit():
                    val_num = float(val_str)
                    if 0 < val_num < 100:
                        old_val = val_str
                        attr["value"] = str(int(val_num * 1000))
                        logger.warning(f"⚠️ 重量单位修正(kg→g)：{old_val}→{attr['value']}")
        except (ValueError, TypeError):
            continue

    # 3) 字典属性校验：已知字典属性(10096/10097等)若缺dictionary_value_id则主动查找缓存
    DICT_ATTR_IDS = {10096, 10097}
    dict_vals = getattr(state, "dictionary_values", {}) or {}
    for attr in final_attributes:
        if not isinstance(attr, dict):
            continue
        try:
            attr_id_int = int(attr.get("attribute_id", 0))
        except (ValueError, TypeError):
            continue
        if attr_id_int in DICT_ATTR_IDS:
            dvid = attr.get("dictionary_value_id")
            if dvid is None or (isinstance(dvid, str) and not dvid.strip()) or (isinstance(dvid, int) and dvid <= 0):
                # 主动从缓存中查找匹配的字典值
                val_text = str(attr.get("value", "")).strip()
                cached = dict_vals.get(str(attr_id_int), [])
                matched = False
                for cv in cached:
                    if str(cv.get("value", "")).strip().lower() == val_text.lower():
                        attr["dictionary_value_id"] = cv.get("id")
                        attr["value"] = cv.get("value")
                        logger.info(f"✅ 字典属性{attr_id_int}从缓存匹配: {cv.get('value')} (id={cv.get('id')})")
                        matched = True
                        break
                if not matched:
                    # 缓存匹配失败，用 _get_color_from_dictionary 兜底取第一个可用颜色
                    color_ru, color_id = _get_color_from_dictionary(dict_vals, attr_id_int, set())
                    if color_id > 0:
                        attr["dictionary_value_id"] = color_id
                        attr["value"] = color_ru
                        logger.info(f"✅ 字典属性{attr_id_int}兜底匹配: {color_ru} (id={color_id})")
                    else:
                        logger.warning(f"⚠️ 字典属性{attr_id_int}无法匹配任何字典值，跳过: value={val_text}")
    
    # ✅ 关键修复：先翻译标题，再处理描述（描述兜底需要title_ru）
    
    # Step 5: 标题翻译成俄语（如果标题是中文或拉丁字母）
    title_ru: str = title_cn  # 默认使用原始标题
    
    # ✅ 关键修复：如果标题包含中文字符或纯拉丁字母，调用LLM翻译为俄语
    if _has_chinese(title_cn):
        logger.warning(f"标题包含中文，调用LLM翻译为俄语：{title_cn[:80]}")
        title_ru = _translate_to_russian_llm(title_cn, mxou_token, source_lang="zh", text_type="title")
        logger.info(f"✅ 标题翻译完成：{title_ru[:80]}")
    elif not _has_cyrillic(title_cn) and title_cn.strip():
        logger.warning(f"标题为纯拉丁字母，调用LLM翻译为俄语：{title_cn[:80]}")
        title_ru = _translate_to_russian_llm(title_cn, mxou_token, source_lang="en", text_type="title")
        logger.info(f"✅ 标题翻译完成：{title_ru[:80]}")
    
    # ✅ 标题后校验：确保标题符合Ozon规范（≤50字符、含标点、无关键词堆砌）
    title_ru = _sanitize_title(title_ru)
    logger.info(f"✅ 标题校验后最终值：{title_ru[:80]}")
    
    # ✅ 关键修复：从LLM生成的属性4191中提取描述，并确保为俄语
    desc_from_4191: str = ""
    for attr in final_attributes:
        attr_id_val: Any = attr.get("attribute_id", 0)
        try:
            if int(attr_id_val) == 4191:
                desc_val_raw: Any = attr.get("value", "")
                if desc_val_raw and str(desc_val_raw).strip():
                    desc_from_4191 = str(desc_val_raw).strip()
                    logger.info(f"✅ 从属性4191提取描述：{desc_from_4191[:80]}...")
                break
        except (ValueError, TypeError):
            continue
    
    # 设置description：优先使用4191的值，其次使用draft.description
    if desc_from_4191:
        description = desc_from_4191
    elif not description or not description.strip() or description == title_cn:
        description = title_cn  # 占位，后续翻译
    
    # ✅ 如果description不是俄语，调用LLM翻译
    if description and not _has_cyrillic(description):
        logger.warning(f"⚠️ 描述不含西里尔字母，调用LLM翻译为俄语：{description[:80]}...")
        description = _translate_to_russian_llm(description, mxou_token, source_lang="auto")
        logger.info(f"✅ 描述翻译完成：{description[:80]}...")
    elif not description or not description.strip():
        # 兜底：如果description仍然为空，用俄语标题作为描述
        description = title_ru if title_ru and _has_cyrillic(title_ru) else "Описание товара"
        logger.warning(f"⚠️ 描述为空，使用标题作为描述：{description[:80]}")
    
    # Step 6: 组装Ozon payload（严格遵守Ozon结构规范）
    logger.info("组装Ozon payload（严格遵守Ozon结构规范）")
    
    # ✅ 关键修复：将final_attributes转换为Ozon官方格式
    # Ozon官方格式要求：
    # {
    #   "complex_id": 0,
    #   "id": 85,          // ← 属性ID（不是attribute_id）
    #   "values": [        // ← 必须是数组
    #     {
    #       "dictionary_value_id": 5060050,  // ← 字典值ID（如果属性有字典）
    #       "value": "Samsung"               // ← 值名称
    #     }
    #   ]
    # }
    
    ozon_attributes: List[Dict[str, Any]] = []
    validation_errors: List[str] = []  # ✅ 提前初始化（属性循环中需要使用）
    
    logger.info(f"开始转换{len(final_attributes)}个属性...")
    
    # ✅ 去重：记录已处理的attribute_id，防止重复
    seen_attr_ids: set = set()
    
    for attr in final_attributes:
        # 验证attr是否为dict类型
        if not isinstance(attr, dict):
            logger.warning(f"⚠️ 属性格式错误（非dict类型），跳过：{type(attr)}")
            continue
        
        # 提取属性字段
        attribute_id: Any = attr.get("attribute_id")
        value: Any = attr.get("value")
        dictionary_value_id: Any = attr.get("dictionary_value_id", 0)
        
        # 验证attribute_id是否存在
        if attribute_id is None:
            logger.warning(f"⚠️ 属性ID缺失，跳过")
            continue
        
        # 类型转换（防御性编程）
        try:
            attribute_id_int: int = int(attribute_id)
            dictionary_value_id_int: int = int(dictionary_value_id) if dictionary_value_id else 0
            value_str: str = str(value) if value else ""
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ 属性类型转换失败，跳过：{e}")
            continue
        
        # ✅ 去重：如果该attribute_id已存在，跳过（保留第一个）
        if attribute_id_int in seen_attr_ids:
            logger.warning(f"⚠️ 属性ID {attribute_id_int} 重复，跳过（保留第一个）")
            continue
        seen_attr_ids.add(attribute_id_int)
        
        # ✅ 关键修复：跳过Ozon不允许编辑或自动设置的属性
        # 9782(危险等级)：Ozon不允许编辑，发送后会被擦除并报错
        # 23536(标记代码)：Ozon根据TN VED自动设置，手动设置"false"不正确
        _skip_attrs = (9782, 23536)
        if attribute_id_int in _skip_attrs:
            logger.info(f"✅ 跳过属性{attribute_id_int}（Ozon不允许编辑或自动设置）")
            continue
        
        # ✅ 关键修复：属性4389(原产国)硬编码为"Китай"（中国）
        # 避免LLM输出英文"China"导致Ozon审核不通过
        if attribute_id_int == 4389:
            value_str = "Китай"
            logger.info(f"✅ 属性4389(原产国)硬编码为：Китай")
        
        # ✅ 关键修复：文本类属性必须为俄语
        # 4191(Описание/描述)、4180(关键字)、9048(Название модели/产品名称) 必须翻译
        # 4384(Комплектация/包装内容)、4389(Страна/原产国) 也需翻译
        # 23171(hashtags)也需要俄语化（Ozon俄罗斯市场要求标签为俄语）
        # 排除：9024(SKU编码) — 允许英文/数字
        _russian_required_attrs = (4191, 4180, 9048, 4384, 4389, 23171)
        _english_allowed_attrs = (9024,)
        if attribute_id_int in _russian_required_attrs and value_str and not _has_cyrillic(value_str):
            logger.warning(f"⚠️ 属性{attribute_id_int}值为拉丁字母，翻译为俄语：{value_str[:60]}...")
            value_str = _translate_to_russian_llm(value_str, mxou_token, source_lang="auto")
        
        # ✅ 属性23171(hashtags)：过滤掉品牌名 + 确保俄语标签格式
        if attribute_id_int == 23171 and value_str:
            try:
                from utils.size_mapper import filter_brand_from_hashtags
                original_tags: str = value_str
                value_str = filter_brand_from_hashtags(value_str)
                # ✅ 确保标签是俄语（如果翻译后仍不含西里尔字母，使用通用俄语标签）
                if value_str and not _has_cyrillic(value_str):
                    value_str = "#сад #огород #инструмент #длядачи #хозяйство"
                    logger.warning(f"⚠️ 标签翻译后仍非俄语，使用默认俄语标签")
                if value_str != original_tags:
                    logger.info(f"✅ hashtags品牌过滤+俄语化: {original_tags[:60]} -> {value_str[:60]}")
            except Exception as e:
                logger.warning(f"⚠️ hashtags过滤失败: {e}")
                # 兜底：确保标签为俄语
                if value_str and not _has_cyrillic(value_str):
                    value_str = "#сад #огород #инструмент #длядачи #хозяйство"
        
        # ✅ 属性9048（Название модели）是必填字段，LLM生成的值直接使用，不跳过
        
        # ✅ 转换为Ozon官方格式
        ozon_attr: Dict[str, Any] = {
            "complex_id": 0,  # ← 固定为0（除非是复杂属性）
            "id": attribute_id_int,  # ← 使用"id"（不是"attribute_id"）
            "values": []  # ← values数组
        }
        
        # ✅ 关键：根据是否有dictionary_value_id决定values格式
        # ✅ 关键修复：对字典类型属性，校验dictionary_value_id是否有效
        is_dict_attr: bool = attribute_id_int in dict_attr_lookup
        
        if dictionary_value_id_int > 0 and is_dict_attr:
            # 有字典值ID：必须填写dictionary_value_id
            ozon_attr["values"].append({
                "dictionary_value_id": dictionary_value_id_int,  # ← 字典值ID
                "value": value_str  # ← 值名称（可选，但建议填写）
            })
            logger.info(f"✅ 转换成功：attr_id={attribute_id_int}, dictionary_value_id={dictionary_value_id_int}, value={value_str}")
        elif is_dict_attr:
            # ✅ 字典类型属性但dictionary_value_id<=0 → 跳过该属性（不加入payload）
            # 不再作为validation_error，只是跳过无法匹配的属性
            dict_id_for_attr: int = dict_attr_lookup[attribute_id_int]
            logger.warning(f"⚠️ 字典属性(attr_id={attribute_id_int}, dict_id={dict_id_for_attr})无法匹配字典值，跳过: value={value_str}")
            continue  # ← 跳过，不加入payload
        else:
            # 无字典值ID：自由文本值
            ozon_attr["values"].append({
                "dictionary_value_id": 0,  # ← 固定为0（表示自由文本）
                "value": value_str  # ← 值名称（必须）
            })
            logger.info(f"✅ 转换成功：attr_id={attribute_id_int}, value={value_str}（自由文本）")
        
        ozon_attributes.append(ozon_attr)
    
    logger.info(f"✅ 属性转换完成：{len(ozon_attributes)}个属性已转换为Ozon官方格式")
    
    # ✅ 输出属性匹配对照表（用于审计和调试）
    try:
        _draft_attrs: Dict[str, Any] = draft.get("attributes", {}) if isinstance(draft, dict) else {}
        attr_table_str: str = build_attribute_matching_table(
            attributes_schema, final_attributes, dict_attr_lookup, _draft_attrs
        )
        logger.info(attr_table_str)
    except Exception as e:
        logger.warning(f"⚠️ 生成属性对照表失败: {e}")
    
    # ✅ 检查必填字典属性是否缺失（检查源数据final_attributes，而非转换后的ozon_attributes）
    _final_attr_ids: set = {int(fa.get("attribute_id", 0)) for fa in final_attributes}
    _converted_attr_ids: set = {int(a.get("id", 0)) for a in ozon_attributes}
    for req_id in required_attr_ids:
        if req_id not in _final_attr_ids:
            # 必填属性在源数据中就不存在 → 阻断
            req_attr_info: str = ""
            for a in required_attrs:
                try:
                    if int(a.get("id", 0)) == req_id:
                        req_attr_info = a.get("name", "")
                        break
                except (ValueError, TypeError):
                    continue
            validation_errors.append(f"必填属性缺失: {req_attr_info} (id={req_id})")
            logger.error(f"❌ 必填属性{req_id}({req_attr_info})在final_attributes中缺失！")
        elif req_id not in _converted_attr_ids:
            # 必填属性在源数据中存在，但转换时被跳过（如缺少dictionary_value_id）→ 记录告警但不阻断
            req_attr_info: str = ""
            for a in required_attrs:
                try:
                    if int(a.get("id", 0)) == req_id:
                        req_attr_info = a.get("name", "")
                        break
                except (ValueError, TypeError):
                    continue
            logger.warning(f"⚠️ 必填属性{req_id}({req_attr_info})在转换时被跳过（可能缺少dictionary_value_id），将尝试用原始值上传")
    
    # ✅ 唯一后缀：用于offer_id和9048属性，避免与旧产品冲突
    offer_id_suffix: str = str(int(time.time()) % 1000000)
    
    # ✅ 属性9048（型号名称）是Ozon必填属性，用于变体绑定
    # 值必须是有意义的型号名称（俄语），不能是纯数字item_id
    # 同一item_id的多个SKU使用相同的型号名称 → 自动合并为一个商品卡片
    # ✅ 添加唯一时间戳后缀避免与旧产品9048冲突导致double_without_merger_offer
    if item_id and item_id.strip():
        # 提取产品类型名称(8229)作为简短型号名，截断到50字符
        product_type_name = ""
        for fa in final_attributes:
            try:
                if int(fa.get("attribute_id", 0)) == 8229:
                    product_type_name = str(fa.get("value", "")).strip()
                    break
            except (ValueError, TypeError):
                continue
        unique_suffix: str = offer_id_suffix
        if product_type_name and _has_cyrillic(product_type_name):
            model_name_9048 = (product_type_name[:40] + f" #{unique_suffix}")[:50]
        elif title_ru and _has_cyrillic(title_ru):
            model_name_9048 = (title_ru[:40] + f" #{unique_suffix}")[:50]
        else:
            model_name_9048 = f"{item_id} #{unique_suffix}"
        
        # ✅ 无论9048是否已存在，都强制覆盖为带唯一后缀的值
        found_9048: bool = False
        for attr in ozon_attributes:
            if isinstance(attr, dict) and attr.get("id") == 9048:
                attr["values"] = [{"dictionary_value_id": 0, "value": model_name_9048}]
                found_9048 = True
                logger.info(f"✅ 覆盖属性9048（型号名称），原值→新值: {model_name_9048[:80]}")
                break
        if not found_9048:
            ozon_attributes.append({
                "complex_id": 0,
                "id": 9048,
                "values": [{"dictionary_value_id": 0, "value": model_name_9048}]
            })
            logger.info(f"✅ 添加属性9048（型号名称），值: {model_name_9048[:80]}")

    # ✅ 属性8962（件数/Единиц в одном товаре）：兜底默认值 "1"
    found_8962: bool = False
    for attr in ozon_attributes:
        if isinstance(attr, dict) and attr.get("id") == 8962:
            found_8962 = True
            break
    if not found_8962:
        ozon_attributes.append({
            "complex_id": 0,
            "id": 8962,
            "values": [{"dictionary_value_id": 0, "value": "1"}]
        })
        logger.info("✅ 兜底添加属性8962（件数），值: 1")

    # ✅ 属性4958（专为/Предназначено для）：兜底 "Универсальный"
    found_4958: bool = False
    for attr in ozon_attributes:
        if isinstance(attr, dict) and attr.get("id") == 4958:
            found_4958 = True
            break
    if not found_4958:
        # 默认 "Универсальный"（通用）；后续可通过 description_category_id 查 Ozon API 精确匹配
        target_value: str = "Универсальный"
        ozon_attributes.append({
            "complex_id": 0,
            "id": 4958,
            "values": [{"dictionary_value_id": 0, "value": target_value}]
        })
        logger.info(f"✅ 兜底添加属性4958（专为），值: {target_value}")
        seen_attr_ids.add(4958)
    
    logger.info(f"最终属性数量：{len(ozon_attributes)}")
    logger.info(f"✅ offer_id唯一后缀: _{offer_id_suffix}")
    
    # ✅ 关键修复：Ozon API /v2/product/import 需要批量上传结构（items数组）
    # 根据Ozon官方文档，每次请求最多可以提交1000种商品的信息
    ozon_payload: Dict[str, Any] = {
        "items": [
            {
                # 核心字段（Ozon要求）
                "name": title_ru,                              # 标题（俄语）
                "description": description,                     # ✅ 产品描述（Ozon必填字段）
                "vat": "0",                                    # ✅ 增值税率：0%（Ozon要求默认为"0"，平台按类目自动计算）
                "offer_id": f"{sku_id}_{offer_id_suffix}",       # ✅ 带唯一后缀避免与旧产品冲突
                
                # 重量和尺寸（单位转换后）
                "weight": weight_g,                             # 重量（克）
                "weight_unit": "g",                             # 重量单位固定为g
                "depth": depth_mm,                              # 深度（毫米）
                "width": width_mm,                              # 宽度（毫米）
                "height": height_mm,                            # 高度（毫米）
                "dimension_unit": "mm",                         # 尺寸单位固定为mm
                
                # 价格（直接字段，不是嵌套结构）
                "currency_code": currency_code,                 # 货币类型（从pricing_info获取）
                "price": str(int(price)) if price else "0",    # 价格（字符串）
                "old_price": str(int(old_price)) if old_price else "0",  # 促销价格（字符串）
                
                # 类目信息
                "description_category_id": int(description_category_id) if description_category_id else 0,
                "type_id": int(type_id) if type_id else 0,
                
                # 属性（包含变体绑定属性9048）
                "attributes": ozon_attributes,
                "complex_attributes": [],                       # 复杂属性（通常为空）
                
                # 其他字段
                "barcode": "",                                  # 条形码（可选）
                "images360": [],                                # 360度图片（可选）
                "pdf_list": [],                                 # PDF文档（可选）
                "promotions": [                                 # 促销信息（Ozon要求）
                    {
                        "operation": "UNKNOWN",
                        "type": "REVIEWS_PROMO"
                    }
                ]
            }
        ]
    }
    
    # ✅ 修复1：添加description_json字段（Ozon结构化描述）
    # Ozon官方文档要求：description_json包含tags、hashtag、materials数组
    # 标签格式：只使用字母、数字、#、下划线，用空格分隔
    # 主题标签格式：每个以#开头，用空格分隔（如 #时尚 #便携）
    # 材料格式：必须从Ozon属性列表选择dictionary_value_id
    description_json = {
        "tags": [],  # 标签数组（暂时为空，后续可以从attributes提取）
        "hashtag": [],  # 主题标签数组（暂时为空，后续可以从description提取）
        "materials": []  # 材料数组（暂时为空，后续可以从attributes提取）
    }
    
    # 将description_json添加到payload中
    ozon_payload["items"][0]["description_json"] = description_json
    
    logger.info("✅ 已添加description_json字段（Ozon结构化描述）")
    
    # ✅ 图片设置（根据Ozon官方文档规范）
    # primary_image单独指定主图（如果为空，images数组第一张为主图）
    # images最多29张（如果primary_image指定），最多30张（如果primary_image为空）
    
    # ✅ 修复3：确保图片顺序符合用户要求（主图第一张，white_bg最后一张，multi_angle倒数第二张）
    logger.info("设置图片顺序（严格遵循IMG_ORDER）")
    
    if has_variant_images:
        # ✅ 修复：主图优先级逻辑（禁止用信息图作主图）
        # 优先级：main_image > white_bg > multi_angle > scene_1 > scene_2 > scene_3 > variant_primary[0]
        main_img = getattr(state, "main_image", None)
        white_bg_url = getattr(state, "white_bg_image", None)
        multi_angle_url = getattr(state, "multi_angle_image", None)
        scene_1_url = getattr(state, "scene_1_image", None)
        scene_2_url = getattr(state, "scene_2_image", None)
        scene_3_url = getattr(state, "scene_3_image", None)
        
        chosen_primary = ""
        primary_source = ""
        if main_img and isinstance(main_img, str) and main_img.strip():
            chosen_primary = main_img.strip()
            primary_source = "main_image"
        elif white_bg_url and isinstance(white_bg_url, str) and white_bg_url.strip():
            chosen_primary = white_bg_url.strip()
            primary_source = "white_bg_image"
        elif multi_angle_url and isinstance(multi_angle_url, str) and multi_angle_url.strip():
            chosen_primary = multi_angle_url.strip()
            primary_source = "multi_angle_image"
        elif scene_1_url and isinstance(scene_1_url, str) and scene_1_url.strip():
            chosen_primary = scene_1_url.strip()
            primary_source = "scene_1_image"
        elif scene_2_url and isinstance(scene_2_url, str) and scene_2_url.strip():
            chosen_primary = scene_2_url.strip()
            primary_source = "scene_2_image"
        elif scene_3_url and isinstance(scene_3_url, str) and scene_3_url.strip():
            chosen_primary = scene_3_url.strip()
            primary_source = "scene_3_image"
        elif variant_primary_images_list and isinstance(variant_primary_images_list[0], str) and variant_primary_images_list[0].strip():
            chosen_primary = variant_primary_images_list[0].strip()
            primary_source = "variant_primary_images[0]"
        
        if chosen_primary:
            ozon_payload["items"][0]["primary_image"] = chosen_primary
            logger.info(f"✅ 多SKU产品：使用{primary_source}作为主图")
        else:
            logger.error("❌ 多SKU产品：无可用主图")
            ozon_payload["items"][0]["primary_image"] = ""
        
        # ✅ 按照IMG_ORDER顺序组装剩余图片（过滤null/空值，排除已用作primary的图片）
        remaining_images = []
        # 添加所有变体图（排除已用作primary的）
        for vimg in variant_primary_images_list:
            if isinstance(vimg, str) and vimg.strip() and vimg.strip() != chosen_primary:
                remaining_images.append(vimg.strip())
        # 添加营销图片（按IMG_ORDER顺序）
        for img_key in IMG_ORDER[1:]:  # 从multi_info开始（跳过main_image）
            img_url = getattr(state, f"{img_key}_image", None)
            if img_url and isinstance(img_url, str) and img_url.strip() and img_url.strip() not in remaining_images and img_url.strip() != chosen_primary:
                remaining_images.append(img_url.strip())
        
        # ✅ 营销图为空时记录警告，不使用alicdn原图（Ozon无法下载）
        if not remaining_images:
            logger.warning("⚠️ 多SKU产品无营销图，生图节点可能失败，不使用alicdn原始图")
            remaining_images = []
        
        # 设置images数组（最多29张）
        ozon_payload["items"][0]["images"] = remaining_images[:29]
        
        logger.info(f"✅ 多SKU产品：primary_image={chosen_primary[:60]}")
        logger.info(f"✅ 多SKU产品：images数量={len(ozon_payload['items'][0]['images'])}")
        
        # 验证图片顺序（white_bg应该在最后）
        images_list = ozon_payload["items"][0]["images"]
        if len(images_list) >= 2:
            last_two_images = images_list[-2:]
            logger.info(f"✅ 最后两张图片：{last_two_images}（应包含multi_angle和white_bg）")
        
    else:
        # 单SKU产品：使用main_image作为primary_image
        main_image = getattr(state, "main_image", None)
        if main_image and main_image.strip():
            ozon_payload["items"][0]["primary_image"] = main_image.strip()  # 主图（单独指定）
            
            # ✅ 使用共享营销图（最多29张，main_image已不在列表中）
            ozon_payload["items"][0]["images"] = [str(img) for img in shared_marketing_images[:29] if isinstance(img, str) and img.strip()]
            
            logger.info(f"✅ 单SKU产品：primary_image={main_image.strip()}")
            logger.info(f"✅ 单SKU产品：images数量={len(ozon_payload['items'][0]['images'])}")
            
            # ✅ 新增：验证图片顺序（white_bg应该在最后）
            images_list = ozon_payload["items"][0]["images"]
            if len(images_list) >= 2:
                last_two_images = images_list[-2:]
                logger.info(f"✅ 最后两张图片：{last_two_images}（应包含multi_angle和white_bg）")
        else:
            # 如果main_image为空，按优先级选择主图（禁止用multi_info信息图作主图）
            # 优先级：white_bg > multi_angle > scene_1 > scene_2 > scene_3 > 原始图
            white_bg_url = getattr(state, "white_bg_image", None)
            multi_angle_url = getattr(state, "multi_angle_image", None)
            scene_1_url = getattr(state, "scene_1_image", None)
            scene_2_url = getattr(state, "scene_2_image", None)
            scene_3_url = getattr(state, "scene_3_image", None)
            chosen_primary = ""
            primary_source = ""
            if white_bg_url and white_bg_url.strip():
                chosen_primary = white_bg_url.strip()
                primary_source = "white_bg_image"
            elif multi_angle_url and multi_angle_url.strip():
                chosen_primary = multi_angle_url.strip()
                primary_source = "multi_angle_image"
            elif scene_1_url and scene_1_url.strip():
                chosen_primary = scene_1_url.strip()
                primary_source = "scene_1_image"
            elif scene_2_url and scene_2_url.strip():
                chosen_primary = scene_2_url.strip()
                primary_source = "scene_2_image"
            elif scene_3_url and scene_3_url.strip():
                chosen_primary = scene_3_url.strip()
                primary_source = "scene_3_image"
            # ✅ 所有AI生图均失败时，不使用alicdn原始图（Ozon无法下载）
            if not chosen_primary:
                logger.warning("⚠️ 无可用AI营销图作为主图，生图节点可能失败，不使用alicdn原始图")

            if chosen_primary:
                ozon_payload["items"][0]["primary_image"] = chosen_primary
                logger.info(f"✅ 单SKU产品（fallback）：使用{primary_source}作为主图")
                # images数组：排除已用作primary的图片，过滤null和空值
                remaining_images = []
                for img in shared_marketing_images:
                    if isinstance(img, str) and img.strip() and img.strip() != chosen_primary:
                        remaining_images.append(img.strip())
                # 如果images为空，添加场景图作为gallery
                if not remaining_images:
                    for surl in [scene_1_url, scene_2_url, scene_3_url, multi_angle_url, white_bg_url]:
                        if surl and isinstance(surl, str) and surl.strip() and surl.strip() != chosen_primary:
                            remaining_images.append(surl.strip())
                ozon_payload["items"][0]["images"] = remaining_images[:29]
                logger.info(f"✅ 单SKU产品（fallback）：images数量={len(ozon_payload['items'][0]['images'])}")
            else:
                # ✅ 所有AI生成图都失败，不使用alicdn原始图（Ozon无法访问）
                logger.error("❌ 所有AI生成图均失败，不使用alicdn原始图（Ozon无法下载），请检查mxou生图节点")
                validation_errors.append("营销图片全部为空，生图节点可能全部失败")
                ozon_payload["items"][0]["primary_image"] = ""
                ozon_payload["items"][0]["images"] = []
    
    logger.info(f"✅ 图片设置完成：primary_image单独指定，images数组按IMG_ORDER顺序")
    
    # ✅ 图片URL直接传给Ozon（COS URL可被Ozon正常访问，无需S3转存）
    # 之前的S3转存逻辑会导致：1)下载图片到内存造成内存泄漏 2)增加处理时间 3)增加故障点
    logger.info(f"✅ 图片URL直接使用COS URL（Ozon可正常访问），共{len(ozon_payload.get('items', []))}个item")
    
    # ✅ 多SKU变体上传：将单item转换为多个variant items
    # Ozon API文档：每个变体是items数组中的独立元素，通过属性9048绑定到同一产品卡
    # 变体之间只能有颜色或尺寸不同，其他属性必须一致
    if variants and isinstance(variants, list) and len(variants) > 0 and (has_variant_images or len(variants) > 1):
        logger.info(f"🔄 多SKU变体上传：将单item转换为{len(variants)}个变体items")
        
        base_item: Dict[str, Any] = ozon_payload["items"][0]
        base_attributes: List[Dict[str, Any]] = base_item.get("attributes", [])
        
        # ✅ 动态检测颜色属性ID（不同类目可能使用不同属性ID，如10096或10097）
        # 方法1：检查已知颜色属性ID集合
        COLOR_ATTR_IDS: set = {10096, 10097, 10098, 10099}
        color_attr_id: int = 10096  # 默认值
        base_color_dict_id: int = 0
        base_color_value: str = ""
        for ba in base_attributes:
            if not isinstance(ba, dict):
                continue
            ba_id: int = int(ba.get("id", 0))
            if ba_id in COLOR_ATTR_IDS:
                color_attr_id = ba_id
                ba_vals: list = ba.get("values", [])
                if ba_vals and isinstance(ba_vals[0], dict):
                    base_color_dict_id = int(ba_vals[0].get("dictionary_value_id", 0))
                    base_color_value = str(ba_vals[0].get("value", ""))
                break
        # 方法2：如果方法1没找到，检查属性值是否为已知颜色名
        if base_color_dict_id == 0 and not base_color_value:
            known_colors_lower: set = {c.lower() for c in COLOR_RU_TO_DICT_ID}
            for ba in base_attributes:
                if not isinstance(ba, dict):
                    continue
                ba_vals2: list = ba.get("values", [])
                if ba_vals2 and isinstance(ba_vals2[0], dict):
                    val_lower: str = str(ba_vals2[0].get("value", "")).strip().lower()
                    if val_lower in known_colors_lower:
                        color_attr_id = int(ba.get("id", 0))
                        base_color_dict_id = int(ba_vals2[0].get("dictionary_value_id", 0))
                        base_color_value = str(ba_vals2[0].get("value", ""))
                        break
        logger.info(f"  颜色属性ID: {color_attr_id}, base颜色: value={base_color_value}, dictionary_value_id={base_color_dict_id}")
        
        # 从共享属性中移除所有颜色属性——每个变体单独设置
        # 必须移除所有 COLOR_ATTR_IDS，否则 ozon_validate_node 会读到共享属性中的颜色值
        shared_attributes: List[Dict[str, Any]] = [
            attr for attr in base_attributes 
            if int(attr.get("id", 0)) not in COLOR_ATTR_IDS
        ]
        logger.info(f"  共享属性数量：{len(shared_attributes)}（已移除所有颜色属性{COLOR_ATTR_IDS}）")
        
        # 从pricing_info获取变体价格
        variant_prices: list = pricing_info.get("variant_prices", []) if isinstance(pricing_info, dict) else []
        
        # ✅ 颜色去重：检测重复颜色，使用Ozon字典中的相近颜色替代
        # Ozon要求同一商品卡内变体颜色必须唯一且都有dictionary_value_id > 0
        color_usage_count: Dict[str, int] = {}
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            cn_color: str = str(variant.get("color", ""))
            color_usage_count[cn_color] = color_usage_count.get(cn_color, 0) + 1
        duplicate_colors: set = {c for c, cnt in color_usage_count.items() if cnt > 1}
        if duplicate_colors:
            logger.warning(f"  ⚠️ 检测到重复颜色: {duplicate_colors}，将使用字典替代颜色区分")
        color_dedup_counter: Dict[str, int] = {}
        
        # 构建变体items
        variant_items: List[Dict[str, Any]] = []
        # ✅ 跟踪已使用的颜色dict_id，确保变体颜色不重复
        used_color_dict_ids: set[int] = set()
        
        for i, variant in enumerate(variants):
            if not isinstance(variant, dict):
                continue
            
            # 变体SKU ID（offer_id）
            var_sku_id: str = str(variant.get("sku_id", f"{sku_id}_{i}"))
            
            # 变体颜色
            var_color_cn: str = str(variant.get("color", ""))
            is_real_color: bool = var_color_cn in COLOR_CN_TO_RU
            
            # 步骤1: 尝试从Ozon API字典值动态匹配
            var_color_ru, var_color_dict_id = _get_color_from_dictionary(
                dictionary_values, color_attr_id, used_color_dict_ids, var_color_cn
            )
            
            # 步骤2: 如果动态匹配失败（dict_id==0），fallback到静态映射
            if var_color_dict_id == 0:
                if is_real_color and var_color_cn in COLOR_CN_TO_RU:
                    var_color_ru = COLOR_CN_TO_RU[var_color_cn]
                    var_color_dict_id = COLOR_RU_TO_DICT_ID.get(var_color_ru, 0)
                    if var_color_dict_id > 0:
                        logger.info(f"  变体{i+1}颜色(静态映射): {var_color_cn}→{var_color_ru}(dict_id={var_color_dict_id})")
                
                if var_color_dict_id == 0 and not is_real_color:
                    # 非真实颜色名：从FALLBACK_COLORS中选未使用的
                    for fc in FALLBACK_COLORS:
                        if fc[1] not in used_color_dict_ids:
                            var_color_ru, var_color_dict_id = fc
                            break
                    if var_color_dict_id == 0:
                        var_color_ru, var_color_dict_id = FALLBACK_COLORS[0]
                    logger.info(f"  变体{i+1}颜色(Fallback): {var_color_cn[:30]}→{var_color_ru}(dict_id={var_color_dict_id})")
                elif var_color_dict_id == 0 and i == 0 and base_color_dict_id > 0 and base_color_value:
                    # 第0个变体且base颜色已匹配，使用base颜色
                    var_color_ru = base_color_value
                    var_color_dict_id = base_color_dict_id
                    logger.info(f"  变体{i+1}颜色(base): {var_color_cn[:30]}→{var_color_ru}(dict_id={var_color_dict_id})")
                elif var_color_dict_id == 0:
                    # 查静态映射
                    var_color_dict_id = COLOR_RU_TO_DICT_ID.get(var_color_ru, 0)
                    if var_color_dict_id == 0:
                        logger.warning(f"  变体{i+1}颜色'{var_color_ru}'未找到字典值，dict_id=0")
                    else:
                        logger.info(f"  变体{i+1}颜色(静态dict): {var_color_cn}→{var_color_ru}(dict_id={var_color_dict_id})")
            
            # 步骤3: 如果颜色重复（dict_id已在used中），从字典值找替代
            if var_color_dict_id > 0 and var_color_dict_id in used_color_dict_ids:
                alt_ru, alt_id = _get_color_from_dictionary(
                    dictionary_values, color_attr_id, used_color_dict_ids
                )
                if alt_id > 0:
                    var_color_ru, var_color_dict_id = alt_ru, alt_id
                    logger.info(f"  变体{i+1}颜色去重(字典值): {var_color_cn[:30]}→{var_color_ru}(dict_id={var_color_dict_id})")
                else:
                    # 从FALLBACK_COLORS找未使用的
                    found_fallback: bool = False
                    for fc in FALLBACK_COLORS:
                        if fc[1] not in used_color_dict_ids:
                            var_color_ru, var_color_dict_id = fc
                            found_fallback = True
                            break
                    if not found_fallback:
                        logger.warning(f"  变体{i+1}颜色重复无法解决: {var_color_ru}(dict_id={var_color_dict_id})")
                    else:
                        logger.info(f"  变体{i+1}颜色去重(Fallback): {var_color_cn[:30]}→{var_color_ru}(dict_id={var_color_dict_id})")
            
            if var_color_dict_id > 0:
                used_color_dict_ids.add(var_color_dict_id)
            
            # 变体价格（从pricing_info.variant_prices获取）
            var_price: str = str(int(price))
            var_old_price: str = str(int(old_price))
            if variant_prices and i < len(variant_prices):
                vp = variant_prices[i]
                if isinstance(vp, dict):
                    var_price = str(vp.get("price", price))
                    var_old_price = str(vp.get("old_price", old_price))
            
            # 变体主图（如果变体图片为空，使用base item的primary_image）
            var_primary_image: str = variant_primary_images_list[i] if i < len(variant_primary_images_list) and variant_primary_images_list[i] and variant_primary_images_list[i].strip() else ozon_payload["items"][0].get("primary_image", "")
            
            # 构建变体属性（共享属性 + 颜色属性）
            var_attributes: List[Dict[str, Any]] = list(shared_attributes)  # 浅拷贝共享属性
            # ✅ 关键修复：检查颜色属性是否是字典类型（dictionary_id > 0）
            # 自由文本属性(dictionary_id=0)必须使用dictionary_value_id=0，否则Ozon会丢弃该属性
            color_attr_dict_id: int = dict_attr_lookup.get(color_attr_id, 0)
            var_attributes.append({
                "complex_id": 0,
                "id": color_attr_id,  # 颜色属性（动态检测的ID）
                "values": [{"dictionary_value_id": var_color_dict_id if color_attr_dict_id > 0 else 0, "value": var_color_ru}]
            })
            
            # 构建变体item（基于base_item，覆盖变体特有字段）
            var_item: Dict[str, Any] = dict(base_item)  # 浅拷贝
            var_item["offer_id"] = f"{var_sku_id}_{offer_id_suffix}"
            var_item["price"] = var_price
            var_item["old_price"] = var_old_price
            var_item["primary_image"] = var_primary_image
            var_item["attributes"] = var_attributes
            # ✅ 变体images继承共享营销图（使用已构建的shared_marketing_images）
            # 变体只有主图不一样，其他图片（multi_info/detail/scene/social_proof/multi_angle/white_bg）都可以复用
            var_item["images"] = list(shared_marketing_images) if shared_marketing_images else []
            
            variant_items.append(var_item)
            logger.info(f"  变体{i+1}: offer_id={var_sku_id}, color={var_color_cn}→{var_color_ru}, price={var_price}, old_price={var_old_price}")
            logger.info(f"    primary_image={var_primary_image[:80]}...")
        
        # 替换items数组为变体items
        ozon_payload["items"] = variant_items
        logger.info(f"✅ 上传：共{len(variant_items)}个item，9048绑定值={model_name_9048}")
        logger.info(f"  offer_id={variant_items[0].get('offer_id', '')}, price={variant_items[0].get('price', '')}")
    else:
        logger.info(f"✅ 单SKU产品：items数组保持1个item")
    
    # Step 7: 验证必填字段（Ozon严格要求）
    logger.info("验证Ozon必填字段")
    # validation_errors已在属性处理阶段初始化
    
    if not title_ru:
        validation_errors.append("产品标题缺失")
    if not description_category_id or description_category_id == 0:
        validation_errors.append("类目ID缺失或无效（Category ID is required）")
    if not sku_id:
        validation_errors.append("1688 SKU_ID缺失（offer_id is required）")
    if not shared_marketing_images:
        validation_errors.append("图片列表为空（images is required）")
    if price == 0:
        validation_errors.append("价格无效（price must be > 0）")
    if weight_g == 0:
        validation_errors.append("重量无效（weight must be > 0）")
    
    # ✅ dimension_weight_issues仅作为日志记录，不加入validation_errors（已用默认值修复）
    if dimension_weight_issues:
        logger.info(f"ℹ️ 尺寸重量默认值应用记录：{dimension_weight_issues}")
    
    if validation_errors:
        logger.error(f"验证失败: {validation_errors}")
        # ✅ P0修复：即使有验证错误，也返回部分构建的payload（而非空dict）
        # 下游ozon_validate + retry loop可以根据validation_errors进行修复
        # 如果ozon_payload尚未构建（前序步骤失败），使用最小有效结构
        payload_to_return: Dict[str, Any] = ozon_payload if ozon_payload else {}
        return PrepareOzonUploadOutput(
            ozon_payload=payload_to_return,
            ordered_images=shared_marketing_images if shared_marketing_images else [],
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            validation_errors=validation_errors,
            error_message="数据准备失败：" + "; ".join(validation_errors),
            failed_stage="prepare_ozon_upload"
        )
    
    # Step 8: 返回准备好的数据
    logger.info("数据准备完成（符合Ozon批量上传规范）")
    logger.info(f"Payload结构验证:")
    logger.info(f"  - items数量：{len(ozon_payload['items'])}")
    first_item = ozon_payload['items'][0]
    logger.info(f"  - name（标题）：{first_item['name']}")
    logger.info(f"  - vat（固定）：{first_item['vat']}")
    logger.info(f"  - offer_id（SKU）：{first_item['offer_id']}")
    logger.info(f"  - weight（克）：{first_item['weight']} {first_item['weight_unit']}")
    logger.info(f"  - dimensions（毫米）：{first_item['depth']}×{first_item['width']}×{first_item['height']} {first_item['dimension_unit']}")
    logger.info(f"  - currency_code：{first_item['currency_code']}")
    logger.info(f"  - price：{first_item['price']} / old_price：{first_item['old_price']}")
    logger.info(f"  - images count：{len(first_item['images'])}")
    logger.info(f"  - attributes count：{len(first_item['attributes'])}")
    
    return PrepareOzonUploadOutput(
        ozon_payload=ozon_payload,
        ordered_images=shared_marketing_images,
        purchase_url=purchase_url,  # ✅ 新增：采购链接（1688）
        purchase_cost=purchase_cost,  # ✅ 新增：采购成本（CNY）
        sku_id=sku_id,  # ✅ 新增：1688 SKU_ID
        profit_estimation=profit_estimation,  # ✅ 新增：利润预估明细
        validation_errors=[],
        error_message="",
        failed_stage=""
    )
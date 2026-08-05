import os
import json
import time
import re
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from graphs.state import PrepareOzonUploadInput, PrepareOzonUploadOutput
from utils.progress_logger import ProgressLogger
from utils.size_mapper import build_attribute_matching_table
from utils.mxou_llm import call_mxou_chat_api
from utils.title_sanitizer import sanitize_title
from utils.attribute_utils import is_customs_attr, is_hazard_attr, get_safe_hazard_default, has_chinese  # ⚠️ v0.16 海关 / v0.21 危险品防御

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
    # ⚠️ v0.13: Ozon API 返回的 info 是字符串（附加描述）而非列表，
    # 旧代码 `for info_item in item.get("info", [])` 对字符串逐字符遍历 → 永远匹配失败，
    # 导致中文色名匹配不到任何颜色，只能随机取第一个未使用颜色。
    if preferred_cn_color:
        preferred_lower: str = preferred_cn_color.strip().lower()
        for item in color_list:
            item_id: int = item.get("id", 0)
            if item_id in used_dict_ids:
                continue
            # 兼容 info 为字符串或列表两种格式
            info_raw = item.get("info", "")
            info_text: str = ""
            if isinstance(info_raw, str):
                info_text = info_raw
            elif isinstance(info_raw, list):
                info_text = " ".join(
                    str(x.get("value", "")) if isinstance(x, dict) else str(x)
                    for x in info_raw
                )
            if info_text and preferred_lower in info_text.lower():
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
            os.getenv("APP_WORKSPACE_PATH", "/app"),
            "config/attributes_llm_cfg.json"
        )
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            llm_cfg: Dict[str, Any] = json.load(fd)

        llm_config: Dict[str, Any] = llm_cfg.get("config", {})
        model_id: str = llm_config.get("model", "deepseek-v4-flash")

        if text_type == "title":
            # 标题翻译：统一「核心词+属性+场景」三段式公式
            _title_formula = (
                "标题公式：[核心词], [属性], [场景]\n"
                "- 核心词：产品是什么（如 Садовый секатор）\n"
                "- 属性：1-2个关键特征（如 профессиональный, с латексным покрытием）\n"
                "- 场景：使用场景（如 для обрезки веток）\n"
            )
            if source_lang == "zh" or _has_chinese(text):
                sys_prompt: str = (
                    "你是Ozon俄罗斯电商平台的产品标题专家。将中文标题翻译为俄语，严格遵循以下公式。\n\n"
                    f"{_title_formula}\n"
                    "严格规则（违反任何一条都会导致Ozon审核拒绝）：\n"
                    "1. 标题长度不超过80个字符（含空格和标点）\n"
                    "2. 标题中必须包含逗号或破折号分隔各部分\n"
                    "3. 绝对禁止关键词堆砌：连续名词性关键词不超过3个\n"
                    "4. 去除所有营销词汇：跨境爆款、现货、亚马逊、爆款、热销、新品、促销等\n"
                    "5. 去除重复关键词\n"
                    "6. 100%西里尔字母，禁止拉丁字母和中文\n"
                    "7. 只返回俄语标题，不要解释\n\n"
                    "示例：\n"
                    "- 输入：\"跨境爆款 现货 Frog Plant Stand 绿色动物宠物青蛙装饰植物架\"\n"
                    "- 输出：\"Подставка для растений, декоративная лягушка, для дома\"\n"
                    "- 输入：\"亚马逊创意JungleSpoon绿叶子漏勺龟背叶勺子捞面勺\"\n"
                    "- 输出：\"Кухонная ложка-шумовка, лист монстеры, для кухни\""
                )
            else:
                sys_prompt = (
                    "You are an Ozon Russia product title expert. Translate the English title into Russian using this formula.\n\n"
                    f"{_title_formula}\n"
                    "Strict rules (violation causes Ozon rejection):\n"
                    "1. Max 80 characters (including spaces and punctuation)\n"
                    "2. Must contain comma or dash separating parts\n"
                    "3. No keyword stuffing: max 3 consecutive noun keywords\n"
                    "4. Remove all marketing words: Amazon, hot sale, new, bestseller, etc.\n"
                    "5. No duplicate keywords\n"
                    "6. 100% Cyrillic, no Latin, no Chinese\n"
                    "7. Return only the Russian title\n\n"
                    "Examples:\n"
                    "- Input: \"Frog Plant Stand Green Animal Pet Frog Decoration Plant Rack\"\n"
                    "- Output: \"Подставка для растений, декоративная лягушка, для дома\"\n"
                    "- Input: \"JungleSpoon Green Leaf Colander Monstera Spoon Noodle Strainer\"\n"
                    "- Output: \"Кухонная ложка-шумовка, лист монстеры, для кухни\""
                )
        else:
            # 普通翻译（描述等）— 加内容净化规则
            _desc_rules = (
                "严格规则：\n"
                "1. 100%西里尔字母，移除所有拉丁字母和中文字符\n"
                "2. 移除营销词汇：爆款、热销、新品、促销、跨境、亚马逊、best、hot、sale、new、premium、top、free\n"
                "3. 移除联系方式：网址、电话、邮箱\n"
                "4. 移除品牌名称引用\n"
                "5. 只返回俄语描述文本，不要添加任何解释或前缀"
            )
            if source_lang == "zh" or _has_chinese(text):
                sys_prompt = f"你是一个专业翻译，专门翻译Ozon俄罗斯电商平台的产品描述。将给定的中文描述翻译成俄语。\n{_desc_rules}"
            else:
                sys_prompt = f"You are a professional translator for Ozon Russia e-commerce. Translate the given product description into Russian.\n{_desc_rules}"

        translated: str = call_mxou_chat_api(
            token=token,
            system_prompt=sys_prompt,
            user_prompt=f"Translate to Russian: {text}",
            model=model_id,
            temperature=0.0,
            max_tokens=1000  # deepseek-v4-flash reasoning 需要更多 token
        ) or ""

        translated = translated.strip()

        if translated and _has_cyrillic(translated):
            logger.info(f"✅ LLM翻译成功: '{text[:50]}' → '{translated[:50]}'")
            return translated
        else:
            # 第一次失败 → 用简化 prompt 重试（去掉严格规则，只要求俄语翻译）
            logger.warning(f"⚠️ 初次翻译失败（非西里尔），用简化 prompt 重试: '{text[:50]}'")
            simple_prompt = (
                "You are a Russian translator. Translate the following Chinese product title into Russian. "
                "Keep it short (under 80 characters). Return ONLY the Russian text, nothing else."
            )
            retry_translated: str = call_mxou_chat_api(
                token=token,
                system_prompt=simple_prompt,
                user_prompt=f"Translate: {text}",
                model=model_id,
                temperature=0.3,
                max_tokens=1000
            ) or ""
            retry_translated = retry_translated.strip()
            if retry_translated and _has_cyrillic(retry_translated):
                logger.info(f"✅ 简化重试翻译成功: '{text[:50]}' → '{retry_translated[:50]}'")
                return retry_translated
            # 最终 fallback：用「核心词+属性+场景」公式生成俄语名称（而非回退到中文）
            logger.warning(f"⚠️ 翻译失败，用公式生成俄语名称: '{text[:50]}'")
            gen_prompt = (
                "你是Ozon俄罗斯电商平台产品命名专家。\n"
                "根据以下产品信息，用「核心词+属性+场景」公式生成俄语标题：\n"
                "- 核心词：产品是什么\n"
                "- 属性：1-2个关键特征\n"
                "- 场景：使用场景\n"
                "要求：≤80字符，必须含逗号，100%西里尔字母，无拉丁/中文。只返回标题。"
            )
            gen_result: str = call_mxou_chat_api(
                token=token,
                system_prompt=gen_prompt,
                user_prompt=f"Product keywords: {text[:200]}",
                model=model_id,
                temperature=0.3,
                max_tokens=1000
            ) or ""
            gen_result = gen_result.strip()
            if gen_result and _has_cyrillic(gen_result):
                logger.info(f"✅ 生成俄语名称成功: '{gen_result[:50]}'")
                return gen_result
            logger.error(f"❌ 所有翻译和生成均失败，使用原文: '{text[:50]}'")
            return text  # 最终回退
    except Exception as e:
        logger.error(f"❌ LLM翻译异常: {str(e)}")
        return text  # 回退到原文


# ── 标题净化（v4: 提取到 utils/title_sanitizer.py）──
# sanitize_title() 已从 utils.title_sanitizer 导入

def _sanitize_description(description: str) -> str:
    """
    描述后净化：确保描述符合Ozon规范。
    1. 移除拉丁字母（保留西里尔）
    2. 移除中文字符
    3. 移除 URL、邮箱、电话
    4. 移除营销词汇
    5. 长度限制 2000 字符
    """
    if not description or not isinstance(description, str):
        return description

    sanitized: str = description.strip()

    # 1. 移除中文字符（含扩展区：CJK统一汉字 + 扩展A + 兼容汉字）
    sanitized = re.sub(r'[\u2e80-\u2eff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+', ' ', sanitized)

    # 2. 移除拉丁单词（2+连续拉丁字母）
    sanitized = re.sub(r'[a-zA-Z]{2,}', ' ', sanitized)

    # 3. 移除 URL
    sanitized = re.sub(r'https?://\S+', '', sanitized)

    # 4. 移除邮箱
    sanitized = re.sub(r'\b[\w.-]+@[\w.-]+\.\w+\b', '', sanitized)

    # 5. 移除电话号码
    sanitized = re.sub(r'\+?\d[\d\s\-()]{7,}\d', '', sanitized)

    # 6. 移除营销词汇（含中文营销词 + 1688常见后缀）
    marketing_words: list = [
        "хит", "распродажа", "акция", "скидка", "новинка", "бестселлер",
        "кроссбордер", "бесплатно", "премиум", "эксклюзив", "ограничено",
        "топ", "лучший", "популярный", "тренд",
        "爆款", "热销", "新品", "促销", "跨境", "亚马逊", "现货",
        "限时", "抢购", "特价", "清仓", "包邮", "满减", "秒杀",
        "同款", "厂家直销", "一件代发", "批发", "抖音", "TikTok",
    ]
    for word in marketing_words:
        sanitized = re.sub(re.escape(word), '', sanitized, flags=re.IGNORECASE)

    # 7. 清理多余空格和标点
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    sanitized = re.sub(r'\s+([,.])', r'\1', sanitized)

    # 8. 长度限制
    if len(sanitized) > 2000:
        # 在词边界截断
        truncated = sanitized[:2000]
        last_space = truncated.rfind(' ')
        if last_space > 1500:
            truncated = truncated[:last_space]
        sanitized = truncated.rstrip(' ,.')

    if sanitized != description.strip():
        logger.info(f"🔧 描述净化: '{description[:60]}...' → '{sanitized[:60]}...'")

    return sanitized


def _sanitize_rich_description(description: str) -> str:
    """
    富文本描述净化：保留 HTML 标签，只清理中文/拉丁正文内容。
    用于属性 4191（支持 HTML 标签的完整描述）。
    """
    if not description or not isinstance(description, str):
        return description

    sanitized = description.strip()

    # 1. 移除中文字符（保留 HTML 标签内的西里尔俄语）
    # 先提取 HTML 标签，清理正文，再拼接
    tag_pattern = re.compile(r'(<[^>]+>)')
    parts = tag_pattern.split(sanitized)

    cleaned = []
    for part in parts:
        if tag_pattern.match(part):
            cleaned.append(part)  # 保留 HTML 标签
        else:
            # 清理正文：去中文、去拉丁单词、去 URL/邮箱/电话
            part = re.sub(r'[\u2e80-\u2eff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+', ' ', part)
            part = re.sub(r'https?://\S+', '', part)
            part = re.sub(r'\b[\w.-]+@[\w.-]+\.\w+\b', '', part)
            part = re.sub(r'\+?\d[\d\s\-()]{7,}\d', '', part)
            cleaned.append(part)

    sanitized = ''.join(cleaned)

    # 2. 移除营销词汇
    marketing_words = [
        "хит", "распродажа", "акция", "скидка", "новинка", "бестселлер",
        "кроссбордер", "бесплатно", "премиум", "эксклюзив", "ограничено",
        "топ", "лучший", "популярный", "тренд",
    ]
    for word in marketing_words:
        sanitized = re.sub(re.escape(word), '', sanitized, flags=re.IGNORECASE)

    # 3. 清理多余空格
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

    # 4. 长度限制
    if len(sanitized) > 2000:
        sanitized = sanitized[:2000]

    return sanitized


def _generate_rich_description(product_name: str, attributes: dict, token: str, image_urls: list = None) -> str:
    """
    LLM 生成俄语 HTML 富文本描述（用于 Ozon 属性 4191）。

    使用 <b>、<ul>/<li>、<p> 等 HTML 标签格式化产品卖点。
    v5: 传入商品图片 URL 作为上下文参考，提升描述准确性。
    """
    if not token:
        return ""

    try:
        from utils.mxou_api import call_mxou_chat_api

        attr_text = ""
        if attributes:
            items = list(attributes.items())[:8]
            attr_text = "\n".join(f"- {k}: {v}" for k, v in items)

        img_context = ""
        if image_urls:
            img_urls = [u for u in image_urls if isinstance(u, str) and u.strip()][:3]
            if img_urls:
                img_context = "\nИзображения товара (ссылки):\n" + "\n".join(img_urls)

        system = """Ты профессиональный копирайтер для Ozon карточек товаров. 
Создай описание товара на русском языке с HTML-разметкой.
Правила:
1. Используй <b>жирный</b> для ключевых характеристик
2. Используй <ul><li>список</li></ul> для технических параметров
3. Используй <p> для абзацев
4. НЕ используй латиницу (английские слова)
5. НЕ используй ссылки, email, телефоны
6. Общая длина: 500-1500 символов

Структура:
<p>Краткое описание товара (1-2 предложения)</p>
<b>Характеристики:</b><ul>...</ul>
<p>Преимущества и особенности</p>"""

        user = f"""Товар: {product_name}

Технические данные:
{attr_text}{img_context}"""

        result = call_mxou_chat_api(
            token=token,
            system_prompt=system,
            user_prompt=user,
            model="deepseek-v4-flash",
            max_tokens=2000,
            temperature=0.3,
        )

        if result:
            return _sanitize_rich_description(result)

    except Exception as e:
        logger.warning(f"LLM 生成富文本描述失败: {e}")

    return ""


def _generate_rich_description_fallback(product_name: str, attributes: dict, description: str = "") -> str:
    """
    v5: LLM 失败时的兜底富文本——用产品名 + 属性组装简单 HTML。

    不依赖 LLM，确保 4191 属性始终有值。
    """
    if not product_name:
        return ""

    parts = [f"<p>{product_name}.</p>"]

    if attributes:
        attr_items = []
        for k, v in list(attributes.items())[:6]:
            if v and str(v).strip():
                # ⚠️ v0.16: 属性名/值含中文的一律跳过该 <li>（Ozon 富文本禁中文，
                # 且该 fallback 结果不经过 _sanitize_rich_description，必须源头清洗）
                if has_chinese(k) or has_chinese(v):
                    continue
                # 净化值：去中文（残留防御）
                clean_v = re.sub(r'[\u4e00-\u9fff]+', '', str(v)).strip()
                if clean_v:
                    attr_items.append(f"<li>{k}: {clean_v}</li>")
        if attr_items:
            parts.append("<b>Характеристики:</b><ul>" + "".join(attr_items) + "</ul>")

    if description and description.strip():
        # 净化：去掉中文和URL
        clean_desc = re.sub(r'[\u4e00-\u9fff]+', ' ', description)
        clean_desc = re.sub(r'https?://\S+', '', clean_desc)
        clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()
        if len(clean_desc) > 20:
            parts.append(f"<p>{clean_desc[:500]}</p>")

    html = "".join(parts)
    # 确保至少 50 字符
    if len(html) < 50:
        html = f"<p>{product_name}. Качественный товар для дома и повседневного использования.</p>"

    return html[:2000]


def _get_category_fallback_title(state: "PrepareOzonUploadInput") -> str:
    """用 Ozon 类目名生成兜底标题，替代固定文案 'Товар для дома'。"""
    try:
        desc_cat_id = getattr(state, "description_category_id", None) or ""
        type_id = getattr(state, "type_id", None) or ""
        if desc_cat_id and type_id:
            from sqlalchemy import text
            from storage.database.db import get_engine
            engine = get_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT full_path FROM category_tree_nodes "
                        "WHERE description_category_id=:cid AND type_id=:tid "
                        "AND language='RU' LIMIT 1"
                    ),
                    {"cid": str(desc_cat_id), "tid": str(type_id)},
                )
                row = result.fetchone()
                if row and row[0]:
                    # 类目路径如 "Дом и сад > Садовые инструменты > Секаторы"
                    # 取最后一段作为产品类型
                    parts = str(row[0]).split(">")
                    last_part = parts[-1].strip() if parts else ""
                    if last_part:
                        return f"{last_part}, универсальный"
    except Exception as e:
        logger.debug(f"获取类目兜底标题失败: {e}")
    return ""


def _fill_missing_required_dict_attrs(items, schema, draft, state):
    """v0.24 F1c: 必填字典属性缺失 → attr_defaults 安全默认补齐（品牌/性别/尺码/8292…）。
    查不到安全默认 → 跳过（留给 F2 暴露可行动错误）。"""
    from utils.attr_defaults import resolve_missing_mandatory_dict_attr
    from utils.attr_defaults import find_dict_value_id
    from utils.ozon_category_query import get_category_query

    schema_list = schema if isinstance(schema, list) else []
    required_dict = [
        a for a in schema_list
        if isinstance(a, dict)
        and a.get("is_required")
        and (
            int(a.get("dictionary_id") or 0) > 0
            or int(a.get("id") or 0) in (22390,)
            or int(a.get("id") or 0) in (8292,)  # 合并卡片：可能是字典也可能是自由文本
            or int(a.get("id") or 0) in (23487,)  # 制造商：自由文本 = supplier
            or "производител" in str(a.get("name") or "").lower()
            or "制造商" in str(a.get("name") or "")
            or "модель" in str(a.get("name") or "").lower()
        )
    ]
    if not required_dict:
        return items

    dict_values = dict(getattr(state, "dictionary_values", None) or {})
    dc = str(getattr(state, "description_category_id", "") or "")
    tp = str(getattr(state, "type_id", "") or "")
    title_cn = str((draft or {}).get("title") or "")
    item_id = str((draft or {}).get("item_id") or "")

    for item in items:
        if not isinstance(item, dict):
            continue
        existing = {int(a.get("id", 0)) for a in item.get("attributes", []) if isinstance(a, dict)}
        attrs = item.get("attributes", [])
        for attr in required_dict:
            aid = int(attr.get("id") or 0)
            if aid in existing:
                continue
            # ✅ v0.26: 9163 无中性词类目「男+女」双值兜底（列表填充）
            _gender_pair: list = []
            # 22390 型号 = 自由文本，填 1688 itemId（同商品所有 SKU 同值）
            if aid in (22390,) or "модель" in str(attr.get("name") or "").lower():
                attrs.append({"id": aid, "values": [{"dictionary_value_id": 0, "value": item_id}]})
                logger.info("✅ 型号 %s(%s) = itemId %s", aid, attr.get("name"), item_id)
                continue
            # 制造商 23487 = 自由文本，填 1688 供应商名
            if aid == 23487 or "производител" in str(attr.get("name") or "").lower() or "制造商" in str(attr.get("name") or ""):
                _sup = str((draft or {}).get("supplier") or "")
                if _sup:
                    _sup_ru = _sup[:100]
                    # ✅ v0.25 FIX: 制造商必须俄语 — 中文供应商名整单被 Ozon 拒
                    # （BR_chinese_hieroglyphs_in_attribute，浴刷 5821877126 wave4 实证，
                    #   错误级导致整单更新失败 → 图片也落不上）
                    if _has_chinese(_sup_ru):
                        _token = getattr(state, "token", "") or ""
                        if _token:
                            try:
                                _tr = _translate_to_russian_llm(
                                    _sup, _token, source_lang="zh", text_type="description"
                                )
                            except Exception:
                                _tr = ""
                            if _tr and not _has_chinese(_tr):
                                _sup_ru = _tr[:100]
                                logger.info("✅ 制造商 supplier 已翻译为俄语: %s", _sup_ru[:40])
                        if _has_chinese(_sup_ru):
                            _sup_ru = "Китайская компания"
                            logger.warning(
                                "⚠️ 制造商 supplier 翻译失败/仍含中文，用安全兜底: Китайская компания"
                            )
                    attrs.append({"id": aid, "values": [{"dictionary_value_id": 0, "value": _sup_ru}]})
                    logger.info("✅ 必填自由文本属性 %s(制造商) 用 supplier 补齐: %s", aid, _sup_ru[:40])
                continue
            # ✅ v0.25 修复: 颜色(10096/10097) — 1688 颜色 → RU 映射 → 字典 id
            if aid in (10096, 10097) or "цвет" in str(attr.get("name") or "").lower() or "颜色" in str(attr.get("name") or ""):
                _cn_color = ""
                _attrs1688 = (draft or {}).get("attributes") or {}
                if isinstance(_attrs1688, dict):
                    _cn_color = str(_attrs1688.get("颜色") or "")
                if _cn_color:
                    # 1688 颜色常带前缀/多值（如 "209中圆点短丝袜 黑色,…"）→ 取串内出现的首个已知颜色词
                    _matched = next((c for c in COLOR_CN_TO_RU if c in _cn_color), "")
                    if _matched:
                        _cn_color = _matched
                    else:
                        _cn_color = ""
                if not _cn_color:
                    for _v in (draft or {}).get("variants") or []:
                        if isinstance(_v, dict) and _v.get("color"):
                            _cn_color = str(_v["color"])
                            break
                if not _cn_color:
                    # ✅ v0.25: 1688 无颜色 → 从竞品标题推断（如 "черные колготки"）
                    from utils.attr_defaults import infer_color_ru
                    _cn_color = infer_color_ru(str(item.get("name") or "") + " " + title_cn) or ""
                _ru_color = COLOR_CN_TO_RU.get(_cn_color, "") if _cn_color else ""
                if not _ru_color and _cn_color:
                    # 已是俄语（来自标题推断 infer_color_ru）→ 直接用
                    _ru_color = _cn_color
                _hit = None
                if _ru_color:
                    _vals = dict_values.get(str(aid)) or []
                    _hit = find_dict_value_id(_vals, _ru_color)
                    if not _hit:
                        try:
                            from utils.ozon_dict_values import search_dictionary_values
                            _hits = search_dictionary_values(
                                getattr(state, "ozon_client_id", "") or "",
                                getattr(state, "ozon_api_key", "") or "",
                                aid, int(dc) if dc else 0, int(tp) if tp else 0, _ru_color,
                            )
                            if _hits:
                                _hit = (int(_hits[0].get("id") or 0), str(_hits[0].get("value") or _ru_color))
                        except Exception:
                            _hit = None
                if not _hit:
                    # ✅ v0.25: 无任何颜色来源 → 中性默认色（прозрачный → белый 依次尝试）
                    for _def_color in ("прозрачный", "белый"):
                        try:
                            from utils.ozon_dict_values import search_dictionary_values as _sdvc
                            _hits_c = _sdvc(
                                getattr(state, "ozon_client_id", "") or "",
                                getattr(state, "ozon_api_key", "") or "",
                                aid, int(dc) if dc else 0, int(tp) if tp else 0, _def_color,
                            )
                            if _hits_c:
                                _hit = (int(_hits_c[0].get("id") or 0), str(_hits_c[0].get("value") or _def_color))
                                logger.info("✅ 必填字典属性 %s(%s) 颜色用中性默认: %s (id=%s)",
                                            aid, attr.get("name"), _hit[1], _hit[0])
                                break
                        except Exception:
                            _hit = None
                if _hit and _hit[0] > 0:
                    attrs.append({"id": aid, "values": [{"dictionary_value_id": _hit[0], "value": _hit[1]}]})
                    logger.info("✅ 必填字典属性 %s(%s) 颜色补齐: %s (id=%s)", aid, attr.get("name"), _hit[1], _hit[0])
                continue
            # 尺码候选（供 4295/尺寸属性搜索用）：优先 1688 属性/变体，其次 SKU 名（排除包装数量）
            size_cn = ""
            sku_name = str(item.get("name") or "")
            _attrs1688sz = (draft or {}).get("attributes") or {}
            if isinstance(_attrs1688sz, dict):
                size_cn = str(_attrs1688sz.get("尺寸") or _attrs1688sz.get("尺码") or "")
            if not size_cn:
                for _v in (draft or {}).get("variants") or []:
                    if isinstance(_v, dict) and (_v.get("size") or _v.get("规格")):
                        size_cn = str(_v.get("size") or _v.get("规格") or "")
                        break
            import re as _re
            if not size_cn:
                m = _re.search(r"\b(\d{1,3}|[xX]{1,2}[sS]?[lL]?|[sS][mM][lL]?|[mM]|[lL])\b", sku_name)
                if m and not _re.search(r"\d+\s*(пар|双|只|件|个)", sku_name):
                    size_cn = m.group(1)

            # ✅ v0.25 修复: 缓存先试 → 解析失败必走 live search（不因缓存有值而跳过）
            vals = dict_values.get(str(aid)) or []
            resolved = None
            # ① 优先竞品 Ozon 属性（俄语值 → search/列表 → id，最准）
            try:
                from utils.attr_defaults import resolve_ozon_attr_value
                _ozon_val = resolve_ozon_attr_value(
                    aid, str(attr.get("name") or ""), (draft or {}).get("ozon_attributes")
                )
                if _ozon_val:
                    from utils.ozon_dict_values import search_dictionary_values as _sdv
                    _hits_o = _sdv(
                        getattr(state, "ozon_client_id", "") or "",
                        getattr(state, "ozon_api_key", "") or "",
                        aid, int(dc) if dc else 0, int(tp) if tp else 0, _ozon_val,
                    )
                    if _hits_o:
                        # ✅ 竞品值精确匹配（如 Размер=36 → 直接命中字典值 36）
                        from utils.attr_defaults import find_dict_value_id as _fdv
                        resolved = _fdv(_hits_o, _ozon_val)
                        if not resolved:
                            resolved = resolve_missing_mandatory_dict_attr(
                                aid, str(attr.get("name") or ""),
                                title_cn=title_cn, product_name_ru=sku_name, size_cn=size_cn,
                                dict_vals=_hits_o,
                            )
                    if not resolved:
                        from utils.ozon_dict_values import list_dictionary_values as _ldv
                        _all_o = _ldv(
                            getattr(state, "ozon_client_id", "") or "",
                            getattr(state, "ozon_api_key", "") or "",
                            aid, int(dc) if dc else 0, int(tp) if tp else 0,
                        )
                        resolved = resolve_missing_mandatory_dict_attr(
                            aid, str(attr.get("name") or ""),
                            title_cn=title_cn, product_name_ru=sku_name, size_cn=size_cn,
                            dict_vals=_all_o,
                        )
            except Exception:
                resolved = None
            # ② 1688 推断（缓存 → 语义关键词 live search）
            if not resolved:
                resolved = resolve_missing_mandatory_dict_attr(
                aid, str(attr.get("name") or ""),
                title_cn=title_cn, product_name_ru=sku_name, size_cn=size_cn, dict_vals=vals,
                )
            if not resolved:
                try:
                    from utils.attr_defaults import dict_search_terms
                    from utils.ozon_dict_values import search_dictionary_values
                    for _term in dict_search_terms(
                        aid, str(attr.get("name") or ""),
                        title_cn=title_cn, product_name_ru=sku_name, size_cn=size_cn,
                    ):
                        if not _term:
                            continue
                        _hits = search_dictionary_values(
                            getattr(state, "ozon_client_id", "") or "",
                            getattr(state, "ozon_api_key", "") or "",
                            aid, int(dc) if dc else 0, int(tp) if tp else 0, _term,
                        )
                        if _hits:
                            resolved = resolve_missing_mandatory_dict_attr(
                                aid, str(attr.get("name") or ""),
                                title_cn=title_cn, product_name_ru=sku_name, size_cn=size_cn,
                                dict_vals=_hits,
                            )
                            if resolved:
                                break
                except Exception:
                    resolved = None
            # 8292: search 兜底仍失败 → 列表模式取「不合并」
            if not resolved and aid in (8292,):
                try:
                    from utils.ozon_dict_values import list_dictionary_values
                    from utils.attr_defaults import resolve_merge_card_default
                    _all = list_dictionary_values(
                        getattr(state, "ozon_client_id", "") or "",
                        getattr(state, "ozon_api_key", "") or "",
                        aid, int(dc) if dc else 0, int(tp) if tp else 0,
                    )
                    resolved = resolve_merge_card_default(_all)
                except Exception:
                    resolved = None
            # 8292 在该类目是自由文本（dict_id=0）→ 填「Нет」不合并（dictionary_value_id=0）
            if not resolved and aid in (8292,):
                try:
                    from utils.attr_defaults import MERGE_CARD_ATTR_IDS
                except Exception:
                    pass
                _schema_attr = next((a for a in schema_list if isinstance(a, dict) and int(a.get("id") or 0) == aid), None)
                if _schema_attr and int(_schema_attr.get("dictionary_id") or 0) == 0:
                    resolved = (0, "Нет")
                    logger.info("✅ 必填自由文本属性 %s(合并至一张卡片) 补齐: Нет", aid)
                elif _schema_attr:
                    # dict_id>0 但列表/搜索全空 → 最后兜底填「Нет」自由文本（比空值好，Ozon 若拒再调整）
                    resolved = (0, "Нет")
                    logger.warning("⚠️ 8292 字典值取不到，最后兜底填自由文本 Нет（可能被 Ozon 拒，但比空值好）")
            # 4295 无尺寸来源 → 类目有「One size/Один размер」则兜底
            if not resolved and aid in (4295, 4411):
                try:
                    from utils.ozon_dict_values import list_dictionary_values as _ldv2
                    _sz_vals = _ldv2(
                        getattr(state, "ozon_client_id", "") or "",
                        getattr(state, "ozon_api_key", "") or "",
                        aid, int(dc) if dc else 0, int(tp) if tp else 0,
                    )
                    for _sv in _sz_vals:
                        _txt = str(_sv.get("value") or "").lower()
                        if any(k in _txt for k in ("один размер", "one size", "универсальн", "free size")):
                            resolved = (int(_sv.get("id") or 0), str(_sv.get("value") or ""))
                            logger.info("✅ 尺码 %s 无来源，用 One size 兜底: %s", aid, resolved[1])
                            break
                except Exception:
                    resolved = None
            # 9163 性别无来源 → 中性默认 Унисекс（同颜色中性默认策略）
            if not resolved and aid in (9163,):
                try:
                    from utils.ozon_dict_values import search_dictionary_values as _sdvg
                    # 依次尝试搜索中性词；搜不到则列表模式按关键词匹配（各品类值名不同）
                    for _gword in ("Унисекс", "Универсальный", "Без разницы"):
                        _hits_g = _sdvg(
                            getattr(state, "ozon_client_id", "") or "",
                            getattr(state, "ozon_api_key", "") or "",
                            aid, int(dc) if dc else 0, int(tp) if tp else 0, _gword,
                        )
                        if _hits_g:
                            _g = find_dict_value_id(_hits_g, _gword)
                            resolved = _g or (int(_hits_g[0].get("id") or 0), str(_hits_g[0].get("value") or _gword))
                            logger.info("✅ 性别无来源，用中性 %s 兜底: %s (id=%s)", _gword, resolved[1], resolved[0])
                            break
                    if not resolved:
                        from utils.ozon_dict_values import list_dictionary_values as _ldvg
                        _all_g = _ldvg(
                            getattr(state, "ozon_client_id", "") or "",
                            getattr(state, "ozon_api_key", "") or "",
                            aid, int(dc) if dc else 0, int(tp) if tp else 0,
                        )
                        for _gv in _all_g:
                            _gt = str(_gv.get("value") or "").lower()
                            if any(k in _gt for k in ("унисекс", "универс", "без разниц", "любой")):
                                resolved = (int(_gv.get("id") or 0), str(_gv.get("value") or ""))
                                logger.info("✅ 性别无来源，列表模式取中性 %s 兜底: (id=%s)", resolved[1], resolved[0])
                                break
                    # ⚠️ v0.26 FIX: 帽类等无中性词类目（字典值只有 Мужской/Женский/童，Ozon 实证
                    # dc=41777465 只有 4 个值）→ 中性兜底永远失败 → 9163 空值 → pending/declined。
                    # 改为取「男+女」双值，保证必填不空（Ozon 支持性别多选）。
                    if not resolved:
                        _all_g2 = _ldvg(
                            getattr(state, "ozon_client_id", "") or "",
                            getattr(state, "ozon_api_key", "") or "",
                            aid, int(dc) if dc else 0, int(tp) if tp else 0,
                        )
                        for _gv in _all_g2:
                            _gt = str(_gv.get("value") or "").lower()
                            if _gt in ("мужской", "женский"):
                                _gender_pair.append((int(_gv.get("id") or 0), str(_gv.get("value") or "")))
                        if len(_gender_pair) >= 2:
                            logger.info("✅ 性别无中性词，取男+女双值兜底: %s", [v for _, v in _gender_pair])
                except Exception:
                    resolved = None
            if not resolved and not _gender_pair:
                logger.warning("⚠️ 必填字典属性 %s(%s) 补齐失败（无安全默认，交给重试/报错）", aid, attr.get("name"))
                continue
            if _gender_pair:
                attrs.append({"id": aid, "values": [
                    {"dictionary_value_id": vid, "value": val} for vid, val in _gender_pair
                ]})
                logger.info("✅ 必填字典属性 %s(%s) 已用男+女双值补齐: %s",
                            aid, attr.get("name"), [v for _, v in _gender_pair])
            elif resolved:
                vid, val = resolved
                attrs.append({"id": aid, "values": [{"dictionary_value_id": vid, "value": val}]})
                logger.info("✅ 必填字典属性 %s(%s) 已用默认值补齐: %s (id=%s)", aid, attr.get("name"), val, vid)
    return items


def _fill_optional_dict_attrs(items, schema, draft, state):
    """v0.26 P1-3: 字典属性全量填满（不限必填）— 同义词 + RU 搜索 + 列表包含匹配。

    对 schema 中 dictionary_id>0 且当前未填的属性：
    ① 同义词规则（attr_synonyms.json，1688 属性名 → Ozon 属性名 + value_map 中文→RU）
    ② /values/search（RU 关键词）精确取 dictionary_value_id
    ③ /values 列表模式在值里做包含匹配（多值属性取全部匹配）
    匹配不到 → 跳过（不盲补首值，防「属性值不正确」）。

    覆盖范围从 v0.25「仅非必填+同义词」扩展为「全部未填字典属性（含必填兜底失败者）」。
    """
    import json as _json
    cfg_path = os.path.join(os.getenv("APP_WORKSPACE_PATH", "/app"), "config", "attr_synonyms.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as _f:
            synonyms = _json.load(_f)
    except Exception:
        synonyms = {}
    draft_attrs = dict((draft or {}).get("attributes") or {})
    if not draft_attrs or not synonyms:
        return items
    dict_values = dict(getattr(state, "dictionary_values", None) or {})
    dc = str(getattr(state, "description_category_id", "") or "")
    tp = str(getattr(state, "type_id", "") or "")
    _cid = getattr(state, "ozon_client_id", "") or ""
    _key = getattr(state, "ozon_api_key", "") or ""
    for item in items:
        if not isinstance(item, dict):
            continue
        existing = {int(a.get("id", 0)) for a in item.get("attributes", []) if isinstance(a, dict)}
        attrs = item.get("attributes", [])
        for attr in schema or []:
            if not isinstance(attr, dict) or int(attr.get("dictionary_id") or 0) <= 0:
                continue
            aid = int(attr.get("id") or 0)
            if aid in existing:
                continue
            aname = str(attr.get("name") or "").lower()
            for rule in synonyms.values():
                if not any(kw in aname for kw in rule.get("ozon_name_keywords", [])):
                    continue
                for zh, zh_val in draft_attrs.items():
                    if not any(kw in zh for kw in rule.get("zh_keywords", [])):
                        continue
                    raw = str(zh_val or "")
                    mapped = (rule.get("value_map") or {}).get(raw, raw)
                    if not mapped:
                        break
                    # ① 缓存字典值精确匹配
                    vals = dict_values.get(str(aid)) or []
                    hits: list = []
                    for v in vals:
                        if str(v.get("value") or "").lower() == mapped.lower():
                            hits.append(v)
                    # ② RU 搜索（/values/search）
                    if not hits:
                        try:
                            from utils.ozon_dict_values import search_dictionary_values
                            _found = search_dictionary_values(_cid, _key, aid, int(dc) if dc else 0, int(tp) if tp else 0, mapped)
                            hits = [h for h in _found if str(h.get("value") or "").strip()]
                        except Exception:
                            hits = []
                    # ③ 列表模式包含匹配（/values 全量，多值属性取全部）
                    if not hits:
                        try:
                            from utils.ozon_dict_values import list_dictionary_values
                            _all = list_dictionary_values(_cid, _key, aid, int(dc) if dc else 0, int(tp) if tp else 0)
                            for _v in _all:
                                _vt = str(_v.get("value") or "").lower()
                                if _vt and (_vt == mapped.lower() or _vt in mapped.lower() or mapped.lower() in _vt):
                                    hits.append(_v)
                        except Exception:
                            hits = []
                    if hits:
                        # 多值：取全部匹配；单值：取第一个（含搜索/缓存命中）
                        attrs.append({"id": aid, "values": [
                            {"dictionary_value_id": int(h.get("id") or 0),
                             "value": str(h.get("value") or mapped)}
                            for h in hits
                        ]})
                        logger.info("✅ 字典属性 %s(%s) 填满: %s", aid, attr.get("name"),
                                    [str(h.get("value") or "") for h in hits])
                    break
    return items


def _append_spec_table(description: str, attrs, weight_g=0, dimensions=None, schema=None) -> str:
    """v0.25 T4: 描述末尾追加规格参数表（俄语属性名/值 + 重量/尺寸）。"""
    name_map = {}
    for a in schema or []:
        if isinstance(a, dict) and a.get("id") is not None:
            name_map[int(a.get("id"))] = str(a.get("name") or "")
    rows = []
    for a in attrs or []:
        if not isinstance(a, dict):
            continue
        aid = int(a.get("id") or a.get("attribute_id") or 0)
        name = name_map.get(aid) or str(a.get("name") or "")
        vals = a.get("values") or []
        val = str(vals[0].get("value") or "") if vals and isinstance(vals[0], dict) else str(a.get("value") or "")
        if name and val and name.lower() != "описание":
            rows.append((name, val))
    dims = dimensions or {}
    if weight_g:
        rows.append(("Вес", f"{int(weight_g)} г"))
    if dims:
        rows.append(("Габариты", f"{dims.get('length','')}×{dims.get('width','')}×{dims.get('height','')} мм"))
    if not rows:
        return description
    lines = ['<table class="ozon-spec"><caption>Характеристики</caption>']
    for k, v in rows:
        lines.append(f"<tr><td>{k}</td><td>{v}</td></tr>")
    lines.append("</table>")
    return f"{description}\n{''.join(lines)}"


def _IMG_ORDER() -> List[str]:
    """Ozon 图片上传顺序（main → social_proof → detail → scene×3 → comparison → multi_angle → white_bg）。"""
    return [
        "main_image",        # 1. 主营销图（抓住眼球）
        "social_proof",      # 2. 社交证明（降低疑虑）
        "detail",            # 3. 详情图（品质信任）
        "scene_1",           # 4. 场景图1（激发购买欲）
        "scene_2",           # 5. 场景图2
        "scene_3",           # 6. 场景图3
        "comparison",        # 7. 对比图（决策信心）
        "multi_angle",       # 8. 多角度（看清各面）
        "white_bg",          # 9. 纯白底图（平台合规）
    ]


def _build_shared_marketing_images(state: Any, is_follow_sell: bool) -> tuple[List[str], str]:
    """构建上传用营销图列表 + 主图。

    ✅ v0.25 FIX: 跟卖绝不用竞品 Ozon 原图（ir.ozone.ru）补位 — Ozon 抓取竞品
    CDN 图失败，实测混入竞品图导致整卡 0 图被下架（wave4 浴刷 5821877126）。
    AI 图不足 10 张就传现有 AI 图，宁缺毋滥（Ozon 允许 1~15 张）。
    竞品图仅保留在 state.original_images 供生图节点做参考，不进上传数组。
    """
    shared_marketing_images: List[str] = []

    # 1. AI main_image 作为画廊第一张
    main_image = getattr(state, "main_image", None)
    if main_image and isinstance(main_image, str) and main_image.strip():
        shared_marketing_images.append(main_image.strip())

    # 2. 按 IMG_ORDER 添加 AI 生成的其他营销图
    for img_key in _IMG_ORDER()[1:]:
        img_url = getattr(state, f"{img_key}_image", None)
        if img_url and isinstance(img_url, str) and img_url.strip():
            shared_marketing_images.append(img_url)
            logger.info(f"图片 {img_key}: {img_url}")

    # 3. 跟卖 AI 图不足 10 张 → 只告警，不补竞品图
    if is_follow_sell and len(shared_marketing_images) < 10:
        logger.warning(
            f"跟卖 AI 图仅 {len(shared_marketing_images)} 张（不足 10 张）— "
            "不再用竞品 ir.ozone.ru 图补位（Ozon 抓取竞品图失败会整卡 0 图）"
        )

    # 4. AI 主图缺失时用第一张 AI 营销图做主图，绝不用竞品图
    if (not main_image or not isinstance(main_image, str) or not main_image.strip()) and shared_marketing_images:
        main_image = shared_marketing_images[0]
        logger.warning(f"⚠️ AI 主图缺失，用第一张 AI 营销图作为主图: {main_image}")

    return shared_marketing_images, (main_image or "")


_COS_REGION_RE = re.compile(r'cos\.[a-z0-9\-]+\.myqcloud\.com')


def _to_ozon_image_url(url: str) -> str:
    """COS 区域域名 → 全球加速域名（Ozon 跨境抓图更稳定，wave4 图抓取失败频发）。

    例：https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/... 
        → https://yss-1256275613.cos.accelerate.myqcloud.com/...
    幂等：加速域名再走一次不变。
    """
    if isinstance(url, str) and "myqcloud.com" in url:
        return _COS_REGION_RE.sub("cos.accelerate.myqcloud.com", url)
    return url


def _rewrite_payload_images_to_accelerate(payload: Dict[str, Any]) -> None:
    """把 payload 所有 item 的 primary_image/images 改写成 COS 全球加速域名。"""
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("primary_image"), str) and item["primary_image"]:
            item["primary_image"] = _to_ozon_image_url(item["primary_image"])
        imgs = item.get("images")
        if isinstance(imgs, list):
            item["images"] = [_to_ozon_image_url(u) for u in imgs if isinstance(u, str)]


def _convert_numeric_attrs(final_attributes: list, attributes_schema) -> list:
    """v0.26 P1-2: 数字属性类型校验/转换 — 按 schema type 强制 INTEGER/DECIMAL。

    Ozon 实证：VALUE_MUST_BE_INTEGER（8205 保质期天数/11650 厂包装数量）、
    VALUE_MUST_BE_DECIMAL（4497 带包装重量/7444 长度 cm）—— 数字属性被填成文本。
    从值中提取数字（"12 месяцев"→12、"1000 г"→1000、"5 шт"→5）；
    无法解析 → 跳过该属性（避免 Ozon 类型错误整单被拒）。
    """
    _attr_type_map: Dict[int, str] = {}
    for _sa in attributes_schema or []:
        if isinstance(_sa, dict):
            try:
                _aid_t = int(_sa.get("id") or 0)
            except (ValueError, TypeError):
                continue
            _tp = str(_sa.get("type") or "")
            if _tp in ("Integer", "Decimal"):
                _attr_type_map[_aid_t] = _tp
    if not _attr_type_map:
        return final_attributes

    _keep_attrs: list = []
    for attr in final_attributes or []:
        if not isinstance(attr, dict):
            _keep_attrs.append(attr)
            continue
        try:
            attr_id_int = int(attr.get("attribute_id", 0))
        except (ValueError, TypeError):
            _keep_attrs.append(attr)
            continue
        _tp = _attr_type_map.get(attr_id_int)
        if not _tp:
            _keep_attrs.append(attr)
            continue
        raw = str(attr.get("value", "") or "").strip()
        if not raw:
            _keep_attrs.append(attr)
            continue
        # 提取数字（允许小数；去单位后缀 g/kg/г/кг/см/mm/шт/个月/дней/年 等）
        _num_match = re.search(r'-?\d+(?:[.,]\d+)?', raw.replace(',', '.'))
        if not _num_match:
            logger.warning(f"⚠️ 数字属性 {attr_id_int}({_tp}) 值无法解析为数字: '{raw}'，跳过该属性")
            continue
        _num_val = _num_match.group()
        if _tp == "Integer":
            _converted = str(int(float(_num_val)))
        else:  # Decimal
            _converted = _num_val
        if _converted != raw:
            logger.info(f"✅ 数字属性 {attr_id_int}({_tp}) 类型转换: '{raw}' → '{_converted}'")
        attr["value"] = _converted
        _keep_attrs.append(attr)
    return _keep_attrs


def prepare_ozon_upload_node(
    state: PrepareOzonUploadInput,
    config: RunnableConfig,
    runtime: Runtime
) -> PrepareOzonUploadOutput:
    """
    title: Ozon上传数据准备（严格遵守Ozon规范）
    desc: 单位转换、vat固定、俄语标题、1688 SKU_ID、促销价格、完整Ozon结构
    integrations: api.mxou.cn LLM翻译 (deepseek-v4-flash), Ozon API
    """
    
    # 获取 mxou API token（用户输入）
    mxou_token: str = state.token
    
    # 添加进度日志
    progress = ProgressLogger()
    progress.log_node_start("prepare_ozon_upload_node", "Ozon上传数据准备节点")
    progress.log_node_action("正在组装Ozon payload（严格遵守Ozon结构规范）")
    
    # Ozon图片上传顺序规范（按俄罗斯电商习惯）
    IMG_ORDER = _IMG_ORDER()
    # ⚠️ multi_info 从共享画廊移除：Ozon禁止附加图片包含文字/广告/价格/联系方式
    
    # Step 1: 整理图片顺序
    logger.info("整理图片顺序")

    # ✅ 构建共享营销图列表（AI 生成图优先，绝不用竞品 Ozon 原图补位）
    original_images = getattr(state, "original_images", []) or []
    competitor_images = [img for img in original_images if isinstance(img, str) and img.strip() and 'ir.ozone.ru' in img]
    is_follow_sell = bool(competitor_images)
    shared_marketing_images, main_image = _build_shared_marketing_images(state, is_follow_sell)
    
    # 4. 如果一张图都没有（AI 全失败 + 无竞品图），标记警告
    if not shared_marketing_images:
        logger.warning("营销图为空，生图节点可能失败（mxou COS URL未生成），不使用alicdn原始图")

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
    # ⚠️ v0.13.1: 品牌属性（85/5076）强制标记为字典属性
    # assemble 已硬编码 "Нет бренда"(126745801) 到 final_attributes，
    # 但 5076 可能不在 schema（品牌属性ID因类目而异）→ 不在 dict_attr_lookup →
    # 转换时被当自由文本 → dictionary_value_id 被置 0 → Ozon 报"请从列表中选择"。
    # 此处强制保留其 dict_id。
    for _brand_id in (85, 5076):
        dict_attr_lookup.setdefault(_brand_id, 0)
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
    # ✅ 提取1688属性关键词（用于标题翻译失败时的兜底生成）
    _draft_attrs_1688: Dict[str, Any] = draft.get("attributes", {}) if isinstance(draft, dict) else {}
    _attr_keywords_cn: str = " ".join(str(v) for v in _draft_attrs_1688.values() if v and len(str(v)) < 20)[:200]
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
    # Ozon API 要求 mm 单位
    def _safe_float(val) -> float:
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    # ✅ 重量合理性检查：如果重量过小（<10g）但尺寸较大（>50mm），可能是kg写成g
    if 0 < weight_g < 10:
        _d = _safe_float(depth_raw)
        _w = _safe_float(width_raw)
        _h = _safe_float(height_raw)
        if max(_d, _w, _h) > 50:
            old_w = weight_g
            weight_g = weight_g * 1000
            logger.warning(f"⚠️ 重量{old_w}g过小而尺寸较大(max={max(_d,_w,_h):.0f})，疑似单位kg→g，修正为{weight_g}g")

    # ✅ 尺寸转换
    d_val = _safe_float(depth_raw)
    w_val = _safe_float(width_raw)
    h_val = _safe_float(height_raw)
    
    max_dim = max(d_val, w_val, h_val)
    # ✅ Skill层已输出mm，直接使用。不做二次cm→mm转换（阈值误判是INCORRECT_DENSITY根因）
    depth_mm = int(d_val)
    width_mm = int(w_val)
    height_mm = int(h_val)
    # 仅当所有维度都极小时（< 5mm），可能是数据异常，设置合理默认值
    if max_dim > 0 and max_dim < 5:
        logger.warning(f"尺寸异常小(max={max_dim}mm)，使用默认100×100×50mm")
        depth_mm = 100
        width_mm = 100
        height_mm = 50
    logger.info(f"尺寸：{depth_mm}×{width_mm}×{height_mm}mm")
    
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
            # 密度过低，可能是尺寸单位错误（cm当mm）或重量偏小
            # 不再盲目乘10——很多轻小物品（手链30g、支架15g）密度天然就低
            logger.warning(f"⚠️ 密度{density_kg_m3:.1f}低于范围({weight_g}g, {depth_mm}×{width_mm}×{height_mm}mm)，可能是尺寸单位或重量数据问题，保持原值")
    
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
    purchase_cost_raw = draft.get("purchase_cost", None)  # 采购成本（CNY），None表示未设置
    purchase_cost = str(purchase_cost_raw) if purchase_cost_raw is not None else ""  # v0.8.0: 修复0被当作falsy
    
    # 如果draft中没有采购信息，尝试从source中提取
    if not purchase_url and isinstance(source, dict):
        purchase_url = source.get("purchase_url", "")
    if not purchase_cost and isinstance(source, dict):
        purchase_cost_raw = source.get("purchase_cost", None)
        purchase_cost = str(purchase_cost_raw) if purchase_cost_raw is not None else ""  # v0.8.0: 修复0被当作falsy
    
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

    # 2) 重量属性数值清洗（4383/4497：去除 g/kg/克/斤 等非数字后缀）
    NUMERIC_WEIGHT_ATTRS = {4383, 4497}
    for attr in final_attributes:
        if not isinstance(attr, dict):
            continue
        try:
            attr_id_int = int(attr.get("attribute_id", 0))
        except (ValueError, TypeError):
            continue
        if attr_id_int in NUMERIC_WEIGHT_ATTRS:
            val = str(attr.get("value", ""))
            # Remove common weight unit suffixes
            val_clean = re.sub(r'(?i)\s*(g|kg|克|斤|公斤|г|кг|gram|kilogram)\s*$', '', val).strip()
            if val_clean != val:
                attr["value"] = val_clean
                logger.info(f"✅ 重量属性 {attr_id_int} 数值清洗: '{val}' → '{val_clean}'")
            # Also validate it's a number
            try:
                float(val_clean)
            except ValueError:
                logger.warning(f"⚠️ 重量属性 {attr_id_int} 非数字: '{val_clean}'，设为空")
                attr["value"] = ""
        # 4383 重量单位修正（kg→g）
        if attr_id_int == 4383:
            val_str = str(attr.get("value", ""))
            val_clean = val_str.replace(".", "").replace(",", "")
            if val_clean.isdigit():
                val_num = float(val_str)
                if 0 < val_num < 100:
                    old_val = val_str
                    attr["value"] = str(int(val_num * 1000))
                    logger.warning(f"⚠️ 重量单位修正(kg→g)：{old_val}→{attr['value']}")

    # 2.5) ⚠️ v0.26 P1-2: 通用数字属性类型校验/转换 — 按 schema type 强制 INTEGER/DECIMAL。
    # Ozon 实证：VALUE_MUST_BE_INTEGER（8205 保质期天数/11650 厂包装数量）、
    # VALUE_MUST_BE_DECIMAL（4497 带包装重量/7444 长度 cm）—— 数字属性被填成文本。
    final_attributes = _convert_numeric_attrs(final_attributes, attributes_schema)

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
    title_ru = sanitize_title(title_ru, token=mxou_token, use_llm=True)

    # 兜底：如果标题仍为空或含拉丁字符，用「核心词+属性+场景」公式生成
    _latin_re_title = re.compile(r'[a-zA-Z]')
    if not title_ru or (title_ru and _latin_re_title.search(title_ru) and not _has_cyrillic(title_ru)):
        logger.warning(f"⚠️ 标题校验后仍不合格（空或含拉丁），用公式生成: '{title_ru[:60]}'")
        try:
            from utils.mxou_api import call_mxou_chat_api
            # ✅ 优先用1688属性关键词（比中文标题更稳定），其次用标题
            keywords = _attr_keywords_cn if _attr_keywords_cn else title_cn[:200]
            logger.info(f"   标题生成关键词：{keywords[:80]}")
            gen_title = call_mxou_chat_api(
                token=mxou_token,
                system_prompt=(
                    "你是Ozon俄罗斯电商平台产品命名专家。\n"
                    "根据以下产品信息，用「核心词+属性+场景」公式生成俄语标题：\n"
                    "- 核心词：产品是什么\n"
                    "- 属性：1-2个关键特征\n"
                    "- 场景：使用场景\n"
                    "要求：≤80字符，必须含逗号，100%西里尔字母，无拉丁/中文。只返回标题。"
                ),
                user_prompt=f"产品信息：{keywords}",
                model="deepseek-v4-flash",
                temperature=0.3,
                max_tokens=1000
            ) or ""
            gen_title = gen_title.strip()
            if gen_title and _has_cyrillic(gen_title) and not _latin_re_title.search(gen_title):
                title_ru = sanitize_title(gen_title, token=mxou_token, use_llm=True) or gen_title
                logger.info(f"✅ 公式生成标题成功：{title_ru[:80]}")
            else:
                # 最终兜底：用 Ozon 类目名代替固定文案
                _fallback_name = _get_category_fallback_title(state)
                title_ru = _fallback_name if _fallback_name else "Товар для дома, универсальный"
                logger.warning(f"⚠️ 公式生成也失败，使用类目兜底标题：{title_ru}")
        except Exception as e:
            logger.error(f"❌ 标题生成异常：{e}")
            _fallback_name_ex = _get_category_fallback_title(state)
            title_ru = _fallback_name_ex if _fallback_name_ex else "Товар для дома, универсальный"

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

    # ✅ 描述净化：移除残留拉丁文/中文/URL/营销词（预防 DESCRIPTION_DECLINE）
    if description:
        description = _sanitize_description(description)

        # ✅ v0.25 T4: 描述追加规格参数表（俄语属性名/值 + 重量/尺寸）
        try:
            description = _append_spec_table(
                description,
                final_attributes,
                weight_g,
                {"length": depth_mm, "width": width_mm, "height": height_mm},
                attributes_schema,
            )
        except Exception:
            pass  # 规格表失败不影响描述主流程

    # ✅ P2 修复：生成富文本 HTML 描述（Ozon 属性 4191）
    rich_desc = ""
    try:
        draft_attrs = (draft or {}).get("attributes", {})
        # v5: 传图片 URL 作为上下文，帮助 LLM 理解商品外观
        product_images = shared_marketing_images if shared_marketing_images else (draft or {}).get("images", [])[:5]
        if mxou_token:
            rich_desc = _generate_rich_description(title_ru, draft_attrs, mxou_token, product_images)
            if rich_desc:
                logger.info(f"✅ 富文本描述已生成: {len(rich_desc)} 字符")
        # v5: LLM 失败或无 token 时用兜底
        if not rich_desc and title_ru:
            rich_desc = _generate_rich_description_fallback(title_ru, draft_attrs, description or "")
            if rich_desc:
                logger.info(f"📝 富文本兜底描述: {len(rich_desc)} 字符")
    except Exception as e:
        logger.warning(f"⚠️ 富文本描述生成失败: {e}")
        # 最终兜底
        if title_ru:
            rich_desc = _generate_rich_description_fallback(title_ru, draft_attrs, description or "")

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

    # ✅ P2 修复：追加富文本描述为属性 4191（支持 HTML 标签的完整描述）
    if rich_desc and len(rich_desc) > 50:
        # 检查 final_attributes 中是否已有 4191，避免重复
        if 4191 not in {int(fa.get("attribute_id", 0)) for fa in final_attributes if fa}:
            final_attributes.append({
                "attribute_id": 4191,
                "value": rich_desc,
                "dictionary_value_id": 0,  # 自由文本属性
            })
            logger.info("✅ 属性 4191（HTML 富文本描述）已追加到 final_attributes")

    # ⚠️ v0.14 B1: 属性合并批量翻译 —— 收集所有含中文/拉丁的普通属性值，一次 LLM 调用翻译全部
    # （省 40-60% LLM 调用；LLM 保留分隔符拆回，失败回退逐条兜底）
    # 排除特殊处理属性（4191 富文本 HTML / 4180 关键字 / 23171 hashtag / 9048 型号名）
    _BATCH_SEP = "\n===\n"
    _batch_translated: Dict[str, str] = {}
    # ✅ v0.25 FIX: 23487(制造商) 加入中文零容忍 — 中文供应商名整单被 Ozon 拒
    # （BR_chinese_hieroglyphs_in_attribute，wave4 浴刷实证）
    _russian_required_attrs = (4191, 4180, 9048, 4384, 4389, 23171, 23487)
    _english_allowed_attrs = (9024,)
    _cn_re_b = re.compile(r'[\u4e00-\u9fff]')
    _batch_pending: List[str] = []
    if mxou_token and final_attributes:
        for _bat in final_attributes:
            if not isinstance(_bat, dict):
                continue
            _bav = str(_bat.get("value", "") or "")
            if not _bav:
                continue
            _baid = _bat.get("attribute_id")
            try:
                _baid_i = int(_baid) if _baid else 0
            except (ValueError, TypeError):
                _baid_i = 0
            if _baid_i in (4191, 4180, 23171, 9048) or _baid_i in _english_allowed_attrs:
                continue  # 特殊处理属性，不走批量
            _need_b = bool(_cn_re_b.search(_bav) or (not _has_cyrillic(_bav) and any(ch.isalpha() for ch in _bav)))
            if _need_b and _bav not in _batch_pending:
                _batch_pending.append(_bav)
        if len(_batch_pending) >= 2:
            try:
                _batch_joined = _BATCH_SEP.join(_batch_pending)
                _batch_res = _translate_to_russian_llm(_batch_joined, mxou_token, source_lang="zh", text_type="description")
                if _batch_res and _has_cyrillic(_batch_res):
                    _parts = _batch_res.split(_BATCH_SEP)
                    if len(_parts) == len(_batch_pending):
                        for _pv, _pr in zip(_batch_pending, _parts):
                            _pr = _pr.strip()
                            if _pr and _has_cyrillic(_pr) and not _cn_re_b.search(_pr):
                                _batch_translated[_pv] = _pr
                        logger.info(f"✅ B1 批量翻译成功: {len(_batch_translated)}/{len(_batch_pending)} 个属性值（1 次 LLM 调用）")
            except Exception as _be:
                logger.warning(f"⚠️ B1 批量翻译异常，逐条兜底: {_be}")

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
        # 23536(标记代码)：Ozon根据TN VED自动设置，手动设置不正确
        # ⚠️ v0.16: 海关编码属性（ТН ВЭД 等）一并跳过——平台/税费系统自动关联，手动乱填会被拒
        # （assemble 侧已按属性名关键词识别剔除，此处按 ID 防御纵深）
        _skip_attrs = (23536,)
        if attribute_id_int in _skip_attrs or is_customs_attr(attribute_id_int):
            logger.info(f"✅ 跳过属性{attribute_id_int}（Ozon不允许编辑/自动设置/海关编码）")
            continue

        # ✅ v0.21 防御纵深：危险品等级(9782)只放行「非危险」安全值，其余一律跳过
        # （assemble 已按新兜底规则处理，此处防止旧信封/重试路径把危险等级带上来）
        if is_hazard_attr(attribute_id_int, ""):
            safe = get_safe_hazard_default([{"id": dictionary_value_id_int, "value": value_str}])
            if not safe:
                logger.warning(f"✅ 跳过危险品等级属性{attribute_id_int}（值非安全默认: {value_str[:40]}）")
                continue
        
        # ✅ 关键修复：属性4389(原产国)硬编码为"Китай"（中国）
        # 避免LLM输出英文"China"导致Ozon审核不通过
        if attribute_id_int == 4389:
            value_str = "Китай"
            logger.info(f"✅ 属性4389(原产国)硬编码为：Китай")
        
        # ✅ 属性22508(品牌注册国)：类似4389，也硬编码为Китай
        if attribute_id_int == 22508:
            value_str = "Китай"
            logger.info(f"✅ 属性22508(品牌注册国)硬编码为：Китай")
        
        # ✅ 关键修复：文本类属性必须为俄语
        # 4191(Описание/描述)、4180(关键字) 必须翻译
        # 4384(Комплектация/包装内容)、4389(Страна/原产国) 也需翻译
        # 23171(hashtags)也需要俄语化（Ozon俄罗斯市场要求标签为俄语）
        # ⚠️ v0.25 FIX: 9048(Название модели) 从本清单移除——型号是数字/字母（如
        #   "4090989133"），LLM 会把数字翻译成道歉文本（"Извините, я не могу..."）→
        #   9048 被跳过为空。数字/拉丁型号 Ozon 接受；若型号含中文仍走下方中文翻译。
        # 排除：9024(SKU编码) — 允许英文/数字（但含中文仍走下方中文检查翻译）
        # ✅ v0.25 FIX: 23487(制造商) 加入中文零容忍（同上）
        _russian_required_attrs = (4191, 4180, 4384, 4389, 23171, 23487)
        if attribute_id_int in _russian_required_attrs and value_str and not _has_cyrillic(value_str):
            logger.warning(f"⚠️ 属性{attribute_id_int}值为拉丁字母，翻译为俄语：{value_str[:60]}...")
            _translated_value = _translate_to_russian_llm(value_str, mxou_token, source_lang="auto")
            # ⚠️ v0.16: 翻译结果必须为俄语（含西里尔且无中文），否则跳过该属性——绝不把
            # 拉丁/英文原文上传（Ozon 要求标准俄语："请用俄文填写该字段"）
            if _translated_value and _has_cyrillic(_translated_value) and not has_chinese(_translated_value):
                value_str = _translated_value
            else:
                logger.error(f"❌ 属性{attribute_id_int}俄语翻译失败或非俄语，跳过该属性: {value_str[:60]}"
                             f" -> '{str(_translated_value)[:40]}'")
                continue

        # ✅ 扩展翻译：所有属性值含中文字符的，翻译为俄语（Ozon禁止中文/日文字符）
        # ⚠️ v0.13.1: 翻译失败/仍含中文 → 跳过该属性，绝不写中文或空值上传！
        # ⚠️ v0.14 B1: 优先查批量翻译映射（一次 LLM 调用翻译全部），未命中才逐条兜底
        # ⚠️ v0.16: 9024(SKU) 不再豁免中文检查——只豁免"非中文值"（拉丁/数字直传），含中文一律翻译
        if value_str and has_chinese(value_str):
            _cached_trans = _batch_translated.get(value_str, "")
            if _cached_trans:
                value_str = _cached_trans
                logger.info(f"  ℹ️ 属性{attribute_id_int}使用批量翻译结果: {value_str[:50]}")
            else:
                logger.warning(f"⚠️ 属性{attribute_id_int}值含中文字符，翻译为俄语：{value_str[:60]}...")
                _translated_value = _translate_to_russian_llm(value_str, mxou_token, source_lang="zh")
                # 翻译成功（含西里尔且无中文）→ 使用翻译结果
                if _translated_value and _has_cyrillic(_translated_value) and not has_chinese(_translated_value):
                    value_str = _translated_value
                    # ✅ v0.9.0 修复: dictionary_value_id 跨语言通用！
                    # 翻译 value 从中文到俄语后，dict_id 仍然有效（同一属性值的不同语言展示）。
                    # 不再清空 dict_id — 它是在 Step 4/5 通过 ZH_HANS 字典精确匹配的。
                    if attribute_id_int in dict_attr_lookup:
                        # 保持原有 dictionary_value_id（跨语言通用）
                        logger.info(f"  ℹ️ 字典属性{attribute_id_int}翻译完成，保留 dict_id={dictionary_value_id_int}, value={value_str[:40]}")
                else:
                    # 翻译失败/仍含中文/非俄语 → 跳过该属性（不写中文、不写空值）
                    logger.error(f"❌ 属性{attribute_id_int}翻译失败或非俄语，跳过该属性: {value_str[:60]}"
                                 f" -> '{str(_translated_value)[:40]}'（避免 Ozon 拒绝中文/空值）")
                    continue
        
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
            # ⚠️ v0.13: 字典属性无有效 dictionary_value_id → 跳过该属性，绝不文本兜底上传！
            # 根因：v0.8.0 Bug#5 曾用 dictionary_value_id=0 + 手填文本上传，Ozon 审核返回
            # "属性值不正确。所有可用值以列表形式被收集了——请仅指定它们"（用途/商品颜色/风格等报错来源）。
            # Ozon 字典属性只接受列表中的 dictionary_value_id，手填文本一律拒绝。
            # 若该属性必需，validation_retry_loop 会走字典 API 匹配后修复。
            dict_id_for_attr: int = dict_attr_lookup.get(attribute_id_int, 0)
            logger.warning(
                f"⚠️ 跳过字典属性(attr_id={attribute_id_int}, dict_id={dict_id_for_attr})"
                f" 值='{value_str}' 无有效 dictionary_value_id（避免文本兜底被Ozon拒绝）"
            )
            continue
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
    
    # ✅ 唯一后缀：用于offer_id（变体区分），不用于9048
    offer_id_suffix: str = str(int(time.time()) % 1000000)
    
    # ✅ 跟卖产品：offer_id = 竞品 ozon_product_id（如 3852000144）
    # 同一竞品永远只有一个商品，重复上传 = UPDATE
    # 同时带 product_id 触发 Ozon UPDATE 模式（而非 CREATE）
    ozon_product_id_from_draft = draft.get("ozon_product_id", "")
    is_follow_sell = bool(ozon_product_id_from_draft)
    if is_follow_sell:
        follow_offer_id = ozon_product_id_from_draft  # 直接用竞品ID，不加前缀
        logger.info(f"🔄 跟卖产品：offer_id={follow_offer_id}（=竞品product_id，UPDATE模式）")
    else:
        follow_offer_id = None

    # ✅ 属性9048（型号名称）= 1688 item_id
    # 同一item_id的多个SKU使用相同的9048 → 自动合并为一个商品卡片
    # 用item_id而非时间戳：确定性生成，重试不变，可溯源到1688来源
    if item_id and item_id.strip():
        model_name_9048 = item_id.strip()

        # ✅ 无论9048是否已存在，都强制覆盖为item_id
        found_9048: bool = False
        for attr in ozon_attributes:
            if isinstance(attr, dict) and attr.get("id") == 9048:
                attr["values"] = [{"dictionary_value_id": 0, "value": model_name_9048}]
                found_9048 = True
                logger.info(f"✅ 覆盖属性9048（型号名称）= item_id: {model_name_9048}")
                break
        if not found_9048:
            ozon_attributes.append({
                "complex_id": 0,
                "id": 9048,
                "values": [{"dictionary_value_id": 0, "value": model_name_9048}]
            })
            logger.info(f"✅ 添加属性9048（型号名称）= item_id: {model_name_9048}")

    # ✅ 属性8962（件数/Единиц в одном товаре）：兜底默认值 "1"
    found_8962: bool = False
    for attr in ozon_attributes:
        if isinstance(attr, dict) and attr.get("id") == 8962:
            found_8962 = True
            # 检查 value 是否为空
            vals = attr.get("values", [])
            if not vals or not any(v.get("value", "") if isinstance(v, dict) else v for v in vals):
                attr["values"] = [{"dictionary_value_id": 0, "value": "1"}]
                logger.info("✅ 属性8962 值为空，兜底填充: 1")
            break
    if not found_8962:
        ozon_attributes.append({
            "complex_id": 0,
            "id": 8962,
            "values": [{"dictionary_value_id": 0, "value": "1"}]
        })
        logger.info("✅ 兜底添加属性8962（件数），值: 1")

    # ✅ 属性4958（专为/Предназначено для）兜底
    # ⚠️ v0.13: 4958 是字典属性，绝不能手填 "Универсальный" 文本
    # （Ozon 报"属性值不正确，请从列表中选择一个属性值"）。
    # 改为从 state.dictionary_values 字典缓存取第一个有效 dictionary_value_id；取不到则跳过。
    found_4958: bool = False
    for attr in ozon_attributes:
        if isinstance(attr, dict) and attr.get("id") == 4958:
            found_4958 = True
            break
    if not found_4958:
        _dict_4958: List[Dict[str, Any]] = []
        if dictionary_values:
            _dict_4958 = dictionary_values.get(str(4958), []) or []
        if _dict_4958:
            _first_4958 = _dict_4958[0]
            _vid_4958: int = int(_first_4958.get("id", 0))
            _vval_4958: str = str(_first_4958.get("value", ""))
            if _vid_4958 > 0:
                ozon_attributes.append({
                    "complex_id": 0,
                    "id": 4958,
                    "values": [{"dictionary_value_id": _vid_4958, "value": _vval_4958}]
                })
                logger.info(f"✅ 兜底添加属性4958（专为），字典值: {_vval_4958} (dict_id={_vid_4958})")
                seen_attr_ids.add(4958)
        else:
            # 无字典缓存 → 跳过（避免文本兜底被Ozon拒绝）
            logger.warning("⚠️ 属性4958（专为）无字典值缓存，跳过（不做文本兜底）")

    # ✅ 补充常见必填自由文本属性的默认值（Ozon 审核拒绝原因：error_attribute_values_empty）
    # ⚠️ v0.13: 9782（Класс опасности товара/危险品等级）是字典属性，已移出本表——文本兜底会被 Ozon 拒绝
    _FALLBACK_FREE_TEXT_ATTRS: dict[int, str] = {
        7578: "365",              # 保质期（天）— 食品/玩具类默认1年
        10350: "40",              # 最高温度 °C
        10351: "0",               # 最低温度 °C
        8787: "сухое место",      # 储存条件
        8050: "полимерные материалы",  # 成分（默认聚合物材料）
    }
    for attr_id, default_val in _FALLBACK_FREE_TEXT_ATTRS.items():
        if attr_id in seen_attr_ids:
            continue
        found = False
        for attr in ozon_attributes:
            if isinstance(attr, dict) and attr.get("id") == attr_id:
                vals = attr.get("values", [])
                if not vals or not any(v.get("value", "") if isinstance(v, dict) else v for v in vals):
                    attr["values"] = [{"dictionary_value_id": 0, "value": default_val}]
                    logger.info(f"✅ 属性{attr_id} 值为空，兜底填充: {default_val}")
                found = True
                break
        if not found:
            ozon_attributes.append({
                "complex_id": 0,
                "id": attr_id,
                "values": [{"dictionary_value_id": 0, "value": default_val}]
            })
            logger.info(f"✅ 兜底添加属性{attr_id}，值: {default_val}")
            seen_attr_ids.add(attr_id)
    
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
                "offer_id": follow_offer_id if is_follow_sell else str(sku_id),  # 1688: item_id 直接做 offer_id，无时间戳
                # ✅ 跟卖产品：指定 product_id 让 Ozon 走 UPDATE 模式（更新已有商品而非创建新的）
                **({"product_id": int(state.product_id)} if is_follow_sell and state.product_id and str(state.product_id) not in ("0", "None", "") else {}),
                
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
                
                # 类目信息（✅ v0.20 A: 类目为空时省略字段——跟卖 UPDATE 由 Ozon 保留
                # 原卡片类目；禁止传 0（会报"类型不属于该类目"整包拒绝）。CREATE 路径
                # 由 follow 节点保证类目已解析，否则不会走到这里）
                **({"description_category_id": int(description_category_id),
                    "type_id": int(type_id)}
                   if description_category_id and type_id else {}),
                
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
        # ✅ 修复：变体主图优先级 — variant_primary > main_image > white_bg
        # 原则：变体产品必须用变体专属图片做主图，不能用共享营销图
        main_img = getattr(state, "main_image", None)
        white_bg_url = getattr(state, "white_bg_image", None)
        multi_angle_url = getattr(state, "multi_angle_image", None)
        scene_1_url = getattr(state, "scene_1_image", None)
        scene_2_url = getattr(state, "scene_2_image", None)
        scene_3_url = getattr(state, "scene_3_image", None)
        
        chosen_primary = ""
        primary_source = ""
        # 优先级1：统一营销主图（所有 SKU 共享同一张主图）
        if main_img and isinstance(main_img, str) and main_img.strip():
            chosen_primary = main_img.strip()
            primary_source = "main_image"
        # 优先级2：变体白底图（兜底）
        elif variant_primary_images_list and len(variant_primary_images_list) > 0 and isinstance(variant_primary_images_list[0], str) and variant_primary_images_list[0].strip():
            chosen_primary = variant_primary_images_list[0].strip()
            primary_source = "variant_primary_images[0]"
        # 优先级3+：其他营销图兜底
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
        
        # 设置images数组（主图永远在第一位，最多29张）
        ozon_payload["items"][0]["images"] = [chosen_primary] + remaining_images[:28] if chosen_primary else remaining_images[:29]
        
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
            
            # ✅ 使用共享营销图（排除main_image，它已作为primary_image单独指定）
            images_for_single = [img for img in shared_marketing_images if img != main_image.strip()]
            ozon_payload["items"][0]["images"] = [str(img) for img in images_for_single[:29] if isinstance(img, str) and img.strip()]
            
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
    
    # ── 变体类型路由：检测 variant_type，决定 Ozon 策略 ──
    first_vt = ""
    if variants:
        for v in variants:
            if isinstance(v, dict) and v.get("variant_type"):
                first_vt = str(v.get("variant_type", ""))
                break
    is_quantity_split = (first_vt == "quantity")
    logger.info(f"🔍 变体类型: variant_type={first_vt}, is_quantity_split={is_quantity_split}")
    
    # ✅ 数量变体拆分：每个数量 SKU 作为独立 Ozon 产品
    if is_quantity_split and variants and len(variants) > 1:
        logger.info(f"🔀 数量变体拆分：将{len(variants)}个数量SKU拆分为独立产品")
        quantity_items: List[Dict[str, Any]] = []
        base_item_qty: Dict[str, Any] = ozon_payload["items"][0]
        
        for i, variant in enumerate(variants):
            if not isinstance(variant, dict):
                continue
            qty_item: Dict[str, Any] = dict(base_item_qty)  # 浅拷贝
            var_sku_id = str(variant.get("sku_id", f"{sku_id}_{i}"))
            qty_item["offer_id"] = f"{var_sku_id}_{offer_id_suffix}"
            
            # 价格 — ⚠️ v0.14 P1-1: 改用 pricing_info.variant_prices（含利润/佣金/物流加成）
            # 旧代码直接用 1688 采购价 variant.get("price") 当售价 → 无加成可能亏本上架
            var_price: float = float(price) if price else 0.0
            var_old_price: float = float(old_price) if old_price else var_price * 1.3
            _variant_prices_q: list = pricing_info.get("variant_prices", []) if isinstance(pricing_info, dict) else []
            if _variant_prices_q and i < len(_variant_prices_q):
                _vp_q = _variant_prices_q[i]
                if isinstance(_vp_q, dict):
                    if _vp_q.get("price"):
                        var_price = float(_vp_q["price"])
                    if _vp_q.get("old_price"):
                        var_old_price = float(_vp_q["old_price"])
            qty_item["price"] = str(int(var_price))
            qty_item["old_price"] = str(int(var_old_price))
            
            # 标题追加数量信息（俄语）
            qty_label = str(variant.get("attributes", {}).get("数量", variant.get("name", "")))
            if qty_label:
                # 数字+单位 → 转俄语 шт.
                import re as _re_qty
                qty_match = _re_qty.search(r'(\d+)', qty_label)
                if qty_match:
                    qty_num = qty_match.group(1)
                    qty_item["name"] = f"{title_ru}, {qty_num} шт."
                else:
                    qty_item["name"] = f"{title_ru}, {qty_label}"
            
            # 图片：变体自己的白底图优先
            var_img = ""
            if i < len(variant_primary_images_list) and variant_primary_images_list[i]:
                var_img = str(variant_primary_images_list[i]).strip()
            if not var_img:
                var_img = str(variant.get("image", ""))
            qty_item["primary_image"] = var_img
            
            # images: 主图第一
            qty_images = [var_img] if var_img else []
            if shared_marketing_images:
                for img in shared_marketing_images:
                    if img and str(img).strip() and img != var_img:
                        qty_images.append(img)
            qty_item["images"] = qty_images[:15]
            
            # 属性：不绑 8292（独立产品），保留其他属性
            qty_attrs: List[Dict[str, Any]] = []
            for ba in base_item_qty.get("attributes", []):
                ba_id = int(ba.get("id", 0)) if isinstance(ba, dict) else 0
                if ba_id == 8292:
                    continue  # 数量变体不绑定
                qty_attrs.append(dict(ba) if isinstance(ba, dict) else ba)
            qty_item["attributes"] = qty_attrs
            
            quantity_items.append(qty_item)
            logger.info(f"  独立产品{i+1}: offer_id={qty_item['offer_id']}, name={qty_item.get('name','')[:60]}, price={qty_item['price']}")
        
        ozon_payload["items"] = quantity_items
        logger.info(f"✅ 数量拆分完成：{len(quantity_items)}个独立产品，未绑定到同一卡片")
    
    # ✅ 多SKU变体上传：将单item转换为多个variant items
    # Ozon API文档：每个变体是items数组中的独立元素，通过属性9048绑定到同一产品卡
    # 变体之间只能有颜色或尺寸不同，其他属性必须一致
    elif variants and isinstance(variants, list) and len(variants) > 0 and (has_variant_images or len(variants) > 1):
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
            
            # 变体颜色 — 优先从 attributes 字典读取，回退到 color 字段
            var_attrs: Dict[str, str] = variant.get("attributes", {}) if isinstance(variant, dict) else {}
            var_color_cn: str = str(var_attrs.get("颜色", variant.get("color", "")))
            var_size_cn: str = str(var_attrs.get("尺寸", variant.get("size", "")))
            var_spec_cn: str = str(var_attrs.get("规格", variant.get("model", "")))
            var_vt: str = str(variant.get("variant_type", ""))
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
            
            # 变体主图：第一个变体用统一营销主图，其余用白底图
            # 降级策略：白底图生成失败 → 统一营销主图（而非1688 alicdn原图，Ozon可能无法下载）
            var_primary_image: str = ""
            if i == 0 and main_img and isinstance(main_img, str) and main_img.strip():
                # 第0个变体（默认展示）：使用统一营销主图
                var_primary_image = main_img.strip()
            elif i < len(variant_primary_images_list) and variant_primary_images_list[i] and str(variant_primary_images_list[i]).strip():
                var_primary_image = str(variant_primary_images_list[i]).strip()
            else:
                # 降级：统一营销主图（Ozon可访问），而非1688 alicdn原图
                if main_img and isinstance(main_img, str) and main_img.strip():
                    var_primary_image = main_img.strip()
                else:
                    var_primary_image = ozon_payload["items"][0].get("primary_image", "")
            
            # 构建变体属性（共享属性 + 颜色属性 + 可选尺寸属性）
            var_attributes: List[Dict[str, Any]] = list(shared_attributes)  # 浅拷贝共享属性
            # ✅ 关键修复：检查颜色属性是否是字典类型（dictionary_id > 0）
            # 自由文本属性(dictionary_id=0)必须使用dictionary_value_id=0，否则Ozon会丢弃该属性
            color_attr_dict_id: int = dict_attr_lookup.get(color_attr_id, 0)
            if color_attr_dict_id > 0 and var_color_dict_id > 0:
                var_attributes.append({
                    "complex_id": 0,
                    "id": color_attr_id,  # 颜色属性（动态检测的ID）
                    "values": [{"dictionary_value_id": var_color_dict_id, "value": var_color_ru}]
                })
            elif color_attr_dict_id == 0:
                # 自由文本颜色属性
                var_attributes.append({
                    "complex_id": 0,
                    "id": color_attr_id,
                    "values": [{"dictionary_value_id": 0, "value": var_color_ru}]
                })
            else:
                # ⚠️ v0.13: 字典颜色属性但 dict_id 未匹配到 → 跳过颜色属性（不做文本兜底，避免 Ozon 拒绝）
                logger.warning(f"  ⚠️ 变体{i+1}颜色属性{color_attr_id}为字典类型但未匹配到 dictionary_value_id，跳过颜色属性")

            # ✅ 尺寸属性：如果 variant 包含尺寸信息，映射到 Ozon 尺码属性
            if var_size_cn and var_size_cn != "one size":
                try:
                    from utils.size_mapper import map_size_to_russian
                    result = map_size_to_russian(var_size_cn)
                    if result:
                        ru_size, size_type = result
                        # 查找类目中的尺码属性 (4295=Russian size, 4411=Размер)
                        size_attr_id = 0
                        for ba in base_attributes:
                            ba_id = int(ba.get("id", 0)) if isinstance(ba, dict) else 0
                            if ba_id in (4295, 4411):
                                size_attr_id = ba_id
                                break
                        if size_attr_id > 0:
                            var_attributes.append({
                                "complex_id": 0,
                                "id": size_attr_id,
                                "values": [{"dictionary_value_id": 0, "value": ru_size}]
                            })
                            logger.info(f"  变体{i+1}尺寸: {var_size_cn}→{ru_size} (table={size_type}, attr={size_attr_id})")
                except Exception as e:
                    logger.debug(f"  变体{i+1}尺寸映射失败: {e}")

            # ✅ 规格变体：规格信息加入 offer_id，不修改 9048（9048 相同才能合并）
            var_offer_id: str = f"{var_sku_id}_{offer_id_suffix}"
            if var_spec_cn and var_vt in ("spec", "color_spec"):
                spec_slug = var_spec_cn.replace(" ", "_")[:20]
                var_offer_id = f"{var_sku_id}_{spec_slug}_{offer_id_suffix}"
            
            # 构建变体item（基于base_item，深拷贝避免嵌套列表共享引用）
            import copy
            var_item: Dict[str, Any] = copy.deepcopy(base_item)
            var_item["offer_id"] = var_offer_id
            var_item["price"] = var_price
            var_item["old_price"] = var_old_price
            var_item["primary_image"] = var_primary_image
            var_item["attributes"] = var_attributes
            # ✅ 变体 images：变体主图 + 共享营销图（而非全相同）
            var_images = [var_primary_image] if var_primary_image else []
            if shared_marketing_images:
                for img in shared_marketing_images:
                    if img and str(img).strip() and img != var_primary_image:
                        var_images.append(img)
            var_item["images"] = var_images[:15]  # Ozon 最多 15 张
            
            variant_items.append(var_item)
            logger.info(f"  变体{i+1}: offer_id={var_sku_id}, color={var_color_cn}→{var_color_ru}, price={var_price}, old_price={var_old_price}")
            logger.info(f"    primary_image={var_primary_image[:80]}...")
        
        # 替换items数组为变体items
        ozon_payload["items"] = variant_items
        logger.info(f"✅ 上传：共{len(variant_items)}个item，9048绑定值={model_name_9048}")
        logger.info(f"  offer_id={variant_items[0].get('offer_id', '')}, price={variant_items[0].get('price', '')}")
    else:
        logger.info(f"✅ 单SKU产品：items数组保持1个item")

    # ✅ v0.24 F1c: 必填字典属性默认值补齐（品牌/性别/尺码/8292/型号）
    try:
        ozon_payload["items"] = _fill_missing_required_dict_attrs(
            ozon_payload.get("items", []), attributes_schema, draft, state
        )
        ozon_payload["items"] = _fill_optional_dict_attrs(
            ozon_payload.get("items", []), attributes_schema, draft, state
        )
    except Exception as _e:
        logger.warning("必填字典属性补齐异常（不影响主流程）: %s", _e)

    # ✅ v0.25 修复: 补齐后重算必填属性缺失（清除补齐前基于 final_attributes 的误报，
    # 否则 Step 7 会用旧的 validation_errors 提前阻断，补齐白跑）
    try:
        _item_attr_ids = {
            int(a.get("id", 0))
            for _it in ozon_payload.get("items", [])
            for a in (_it.get("attributes") or []) if isinstance(a, dict)
        }
        _still_missing = [r for r in required_attr_ids if r not in _item_attr_ids]
        validation_errors = [e for e in validation_errors if not e.startswith("必填属性缺失")]
        for _rid in _still_missing:
            _nm = ""
            for _a in required_attrs:
                try:
                    if int(_a.get("id", 0)) == _rid:
                        _nm = _a.get("name", "")
                        break
                except (TypeError, ValueError):
                    continue
            validation_errors.append(f"必填属性缺失: {_nm} (id={_rid})")
    except Exception as _ve:
        logger.warning("必填属性重算失败（保留原校验）: %s", _ve)
    
    # Step 7: 验证必填字段（Ozon严格要求）
    logger.info("验证Ozon必填字段")
    # validation_errors已在属性处理阶段初始化
    
    if not title_ru:
        validation_errors.append("产品标题缺失")
    if not description_category_id or description_category_id == 0:
        validation_errors.append("类目ID缺失或无效（Category ID is required）")
    if not type_id or type_id == 0:
        validation_errors.append("type_id缺失或无效（TypeId must be > 0）")
    if not sku_id:
        validation_errors.append("1688 SKU_ID缺失（offer_id is required）")
    if not shared_marketing_images:
        validation_errors.append("图片列表为空（images is required）")
    if price == 0:
        validation_errors.append("价格无效（price must be > 0）")
    if weight_g == 0:
        validation_errors.append("重量无效（weight must be > 0）")

    # ✅ v0.25 FIX: COS 区域域名 → 全球加速域名（Ozon 跨境抓图更稳定）
    # wave4 实证：图片在 ap-guangzhou 域名上 Ozon 抓取偶发失败（pics_http_error /
    # primary_image_load_failed / 整卡 0 图）。加速域名经腾讯全球加速网络，
    # Ozon 服务器拉取更稳。幂等改写。
    _rewrite_payload_images_to_accelerate(ozon_payload)
    
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

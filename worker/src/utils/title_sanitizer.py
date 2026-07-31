"""标题净化工具 — 共享于 prepare_ozon_upload_node 和 validation_retry_loop"""

import re
import logging

logger = logging.getLogger(__name__)

# ── 营销词黑名单（俄语 + 英语）──
_MARKETING_WORDS_RU: set = {
    "хит", "распродажа", "акция", "скидка", "новинка", "бестселлер",
    "кроссбордер", "бесплатно", "премиум", "эксклюзив", "ограничено",
    "топ", "лучший", "популярный", "тренд", "супер", "дешевый", "элитный",
}
_MARKETING_WORDS_EN: set = {
    "hot", "sale", "bestseller", "new", "premium", "free",
    "amazon", "exclusive", "trending", "top", "best", "popular", "cheap",
}

# ── 西里尔字符正则 ──
_CYRILLIC_RE = re.compile(r'[а-яА-ЯёЁ]')
_LATIN_RE = re.compile(r'[a-zA-Z]{2,}')
_CJK_RE = re.compile(r'[\u4e00-\u9fff]+')


def sanitize_title(title: str, token: str = "", use_llm: bool = False) -> str:
    """
    标题净化 — 确保符合 Ozon 规范。

    Args:
        title: 原始标题
        token: MXOU token（仅 use_llm=True 时需要，用于 LLM 去拉丁词）
        use_llm: 是否使用 LLM 移除拉丁词（prepare 路径用，retry 路径不用）

    Returns:
        净化后的标题，或空字符串（需要调用方生成兜底标题）
    """
    if not title or not isinstance(title, str):
        return title

    sanitized: str = title.strip()

    # ── Step 0: 去除非西里尔字符 ──
    if use_llm and token:
        sanitized = _remove_latin_llm(sanitized, token)
    else:
        sanitized = _CJK_RE.sub('', sanitized)
        sanitized = _LATIN_RE.sub('', sanitized)

    # 清理多余空格
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

    # 没有西里尔字符 → 返回空，触发调用方兜底
    if not _CYRILLIC_RE.search(sanitized):
        return ""

    # ── Step 1: 去除营销词 ──
    sanitized = _remove_marketing_words(sanitized)
    if not sanitized:
        return title  # 全被过滤，返回原标题

    # ── Step 2: 关键词堆砌检测（仅标题>50字符时）──
    sanitized = _fix_keyword_stuffing(sanitized)

    # ── Step 3: 截断到 80 字符 ──
    sanitized = _truncate_to_80(sanitized)

    # ── Step 4: 标点兜底 ──
    sanitized = _ensure_punctuation(sanitized)

    return sanitized


def _remove_latin_llm(text: str, token: str) -> str:
    """使用 LLM 移除拉丁词（保留西里尔词）"""
    if not token:
        return _LATIN_RE.sub('', _CJK_RE.sub('', text))
    try:
        from utils.mxou_api import call_mxou_chat_api
        prompt = (
            "Удали из названия товара все латинские буквы и английские слова. "
            "Оставь ТОЛЬКО русские (кириллические) слова. "
            "Верни ТОЛЬКО очищенное название, без кавычек и пояснений.\n\n"
            f"Название: {text}"
        )
        result = call_mxou_chat_api(
            token=token,
            system_prompt="Ты редактор названий товаров. Удаляешь всё, кроме кириллицы.",
            user_prompt=prompt,
            model="deepseek-v4-flash",
            max_tokens=200,
            timeout=30,
        )
        if result and _CYRILLIC_RE.search(result):
            return result.strip()
    except Exception as e:
        logger.warning(f"LLM 去拉丁词失败: {e}")
    return _LATIN_RE.sub('', _CJK_RE.sub('', text))


def _remove_marketing_words(text: str) -> str:
    """移除营销词"""
    all_marketing = _MARKETING_WORDS_RU | _MARKETING_WORDS_EN
    words = text.split()
    filtered = [w for w in words if w.lower().strip(".,!?:;\"'()[]{}") not in all_marketing]
    return " ".join(filtered).strip()


def _fix_keyword_stuffing(text: str) -> str:
    """修复关键词堆砌：连续 5+ 词无标点→插入逗号"""
    segments = re.split(r'[,\-—:;]', text)
    long_segment = any(len(seg.strip().split()) > 5 for seg in segments if seg.strip())
    if long_segment and len(text) > 50:
        words_list = text.split()
        if len(words_list) > 5:
            new_words = []
            for i, w in enumerate(words_list):
                new_words.append(w)
                if i == 1 or i == 3:
                    new_words[-1] = new_words[-1] + ","  # ← 修复: 逗号紧跟词尾，避免 "word ," 空格
            return " ".join(new_words)
    return text


def _truncate_to_80(text: str) -> str:
    """截断到 80 字符（词边界）"""
    if len(text) <= 80:
        return text
    truncated = text[:80]
    last_space = truncated.rfind(' ')
    if last_space > 20:
        truncated = truncated[:last_space]
    logger.warning(f"⚠️ 标题超长，截断为：{truncated}")
    return truncated


def _ensure_punctuation(text: str) -> str:
    """确保标题>30字符时有标点"""
    punct_chars = {'.', ',', '-', '°', '(', ')', '/', ':', '–', '—'}
    if len(text) > 30 and not any(ch in punct_chars for ch in text):
        text = text.rstrip() + "."
    return text

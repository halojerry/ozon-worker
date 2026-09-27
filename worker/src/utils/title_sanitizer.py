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
# ≥4 字符西里尔词（判断标题是否含「真实词」而非单位残壳/标点碎屑——Вт/шт 等单位词 ≤3 字符不计）
_CYRILLIC_WORD_RE = re.compile(r'[а-яА-ЯёЁ]{4,}')
# LLM 去拉丁词的破坏性改写守卫用（词 = 连续 ≥2 西里尔字符）
_CYRILLIC_WORD_COUNT_RE = re.compile(r'[а-яА-ЯёЁ]{2,}')
_LATIN_RE = re.compile(r'[a-zA-Z]{2,}')
_CJK_RE = re.compile(r'[\u4e00-\u9fff]+')


def has_cyrillic_word(text: str) -> bool:
    """含 ≥4 字符西里尔词 → True（Вт/шт/мл 等单位残壳不算词）。

    v0.81 止血批新增，sanitize_title_structure 与 ozon_validate_node 名称闸共用
    同一判定，禁止两处各自维护阈值。
    """
    if not text or not isinstance(text, str):
        return False
    return bool(_CYRILLIC_WORD_RE.search(text))


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
        from utils.mxou_api import call_mxou_chat_api, MxouOutOfQuotaError  # v0.63.1
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
            model="deepseek-v4-flash-vision-exp",
            max_tokens=200,
            timeout=30,
        )
        if result and _CYRILLIC_RE.search(result):
            # ✅ v0.81 止血批：破坏性改写守卫——LLM 输出的西里尔词数 < 输入一半
            # 视为丢词残壳（曾产出把多词标题删到只剩单词），弃用走正则兜底。
            _in_words = len(_CYRILLIC_WORD_COUNT_RE.findall(text))
            _out_words = len(_CYRILLIC_WORD_COUNT_RE.findall(result))
            if _out_words * 2 >= _in_words:
                return result.strip()
            logger.warning(
                "LLM 去拉丁词疑似破坏性改写（西里尔词 %d→%d），弃用走正则兜底: %r",
                _in_words, _out_words, result[:60],
            )
    except MxouOutOfQuotaError:
        raise  # v0.63.1: 余额/鉴权/额度永久错误 → 任务明确失败，不回退正则
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


# ── v0.81 上架质量止血：标题结构闸 ──────────────────────────────────────────
# 根因（实锤）：LLM 拿单位词填空槽产出「Портативный вентилятор, Вт, скоростей」
# 「, 1」「Перчатки, , для повседневной носки」「Средство для ухода за волосами,
# 120,3 мл」，全链验收只有 _has_cyrillic → 坏标题原样上卡。本函数按逗号切段
# 剔单位残壳 + 结构不合格判定，让坏标题流入 prepare 既有公式重生成/类目兜底链。
# 契约：纯函数不 raise；空/畸形输入返回 (原样, True)。

# 单位词表（俄语单位，含西里尔拼写；大小写不敏感）
_STRUCTURE_UNIT_WORDS: tuple = (
    "шт", "мл", "л", "г", "кг", "см", "мм", "вт", "мач",
)
_UNIT_TOKEN_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
# 俄语小数逗号模式（「120,3 мл」——逗号被切段器拆开，必须在切段前判定）
_DECIMAL_COMMA_UNIT_RE = re.compile(r"(\d),(\d{1,2})\s*(мл|л|г|кг|см)\b", re.IGNORECASE)
# 整体仅数字/标点（无任何文字）
_ONLY_DIGITS_PUNCT_RE = re.compile(r"^[\d\s,.\-]+$")


def _is_unit_shell_segment(seg: str) -> bool:
    """单位残壳段判定：段内所有 token 都是纯数字或单位词，且**不含「数字+单位」
    组合**——「90 Вт」这类含真实数字的规格段不得剔（除线价/功率是真信息）；
    「Вт」「1」「мл」这类缺另一半的残壳才剔。"""
    tokens = [t.strip(".,") for t in seg.split()]
    if not tokens or any(not t for t in tokens):
        tokens = [t for t in tokens if t]
    if not tokens:
        return True  # 全是点/空白
    has_num = has_unit = False
    for tok in tokens:
        low = tok.lower()
        if _UNIT_TOKEN_RE.match(tok):
            has_num = True
        elif low in _STRUCTURE_UNIT_WORDS:
            has_unit = True
        else:
            return False  # 出现任意真实词 → 非残壳
    return not (has_num and has_unit)  # 数字+单位组合（如 90 Вт）≠ 残壳


def sanitize_title_structure(title: str) -> tuple:
    """标题结构闸：剔单位残壳段 + 结构不合格判定。

    Returns:
        (cleaned_title, structurally_bad: bool)
        - 归一：连续逗号合一、strip 首尾 `,-–— `、多余空格合一
        - 按逗号切段：剔空段；剔「单位残壳段」（仅数字/空白/点/单位词且非唯一段；
          含真实数字的规格段如「90 Вt」不剔）
        - 不合格（True）：清洗后无 ≥4 字符西里尔词 / 总长 <10 / 整体仅数字标点 /
          命中俄语小数逗号单位模式（如「120,3 мл」）
        - 不 raise：空/非字符串输入返回 (原样, True)
    """
    if not isinstance(title, str) or not title.strip():
        return title, True
    try:
        # 归一：连续逗号合一 + 空白合一 + 首尾杂符剥离
        t = re.sub(r',\s*,+', ', ', title)
        t = re.sub(r'\s+', ' ', t).strip()
        t = t.strip(' ,-–—')
        # 小数逗号单位模式必须在逗号切段前判定（切段会把 120,3 拆散）
        decimal_comma_bad = bool(_DECIMAL_COMMA_UNIT_RE.search(t))
        segments = [s.strip() for s in t.split(',')]
        non_empty = [s for s in segments if s]
        # 剔空段；残壳段仅在「非唯一段」时剔（单段残壳保留，交不合格判定兜底）
        kept = [
            s for s in non_empty
            if not _is_unit_shell_segment(s) or len(non_empty) <= 1
        ]
        cleaned = ", ".join(kept).strip(' ,-–—')
        if not cleaned:
            cleaned = t  # 全段被剔 → 保守返回归一原串，由下方不合格判定兜底
        bad = (
            decimal_comma_bad
            or not has_cyrillic_word(cleaned)
            or len(cleaned) < 10
            or bool(_ONLY_DIGITS_PUNCT_RE.fullmatch(cleaned))
        )
        return cleaned, bad
    except Exception:
        return title, True

"""标题公式共享模块 — Ozon 上架标题「核心词+属性+场景」公式唯一入口（v0.40 共享层纪律）。

⚠️ 本模块是标题公式 prompt 的**唯一定义点**。3 处调用方统一接线（后续任务执行，本任务不接线）：
  1. `prepare_ozon_upload_node._translate_to_russian_llm` 主路径（text_type="title" 分支）
  2. `prepare_ozon_upload_node` 兜底生成（_attr_keywords_cn → 公式生成标题）
  3. `ai_field_service._build_prompt`（field == "title" 分支）

**新增标题公式逻辑必须进本模块**，禁止三处各自内联（对齐 v0.40「佣金/属性匹配唯一入口」纪律，
防漂移——历史上有 3 份拷贝已发生措辞分歧：如 prepare 有完整示例而 ai_field_service 只有 1 个）。

本模块只产 prompt 文本与关键词辅助，不发起 LLM 调用、不净化最终标题
（净化走 `utils/title_sanitizer`）。纯 Python 3.12，无新依赖。
"""
from __future__ import annotations

import re

# Ozon 标题硬上限（含空格与标点），LLM prompt 规则 1 使用
TITLE_MAX_LENGTH = 80

# 流量词建议上限：prompt 规则「不得超过 3 个」的常量来源
TRAFFIC_KEYWORDS_MAX = 3

# 流量词长度上限：超过视为噪声词丢弃（俄语长复合词罕见 >20 字符）
TRAFFIC_KEYWORD_MAX_LEN = 20

# ── 营销词黑名单（从现有 3 份拷贝提取：prepare L248/L266/L281 + ai_field_service L76/L90 +
#    title_sanitizer _MARKETING_WORDS_RU/_MARKETING_WORDS_EN）──
_NOISE_ZH: tuple[str, ...] = (
    "跨境爆款", "现货", "亚马逊", "爆款", "热销", "新品", "促销", "跨境",
)
_NOISE_RU: tuple[str, ...] = (
    "хит", "распродажа", "акция", "скидка", "новинка", "бестселлер",
    "кроссбордер", "бесплатно", "премиум", "эксклюзив", "ограничено",
    "топ", "лучший", "популярный", "тренд", "супер", "дешевый", "элитный",
)
_NOISE_EN: tuple[str, ...] = (
    "hot", "sale", "bestseller", "new", "premium", "free", "amazon",
    "exclusive", "trending", "top", "best", "popular", "cheap",
)

# 营销词全集：zh 规则 4 / en 规则 4 分别取对应子集，RU 供净化侧共享
NOISE_KEYWORDS: tuple[str, ...] = _NOISE_ZH + _NOISE_RU + _NOISE_EN

# 纯西里尔词正则（parse_title_formula_keywords 过滤依据）
_CYRILLIC_ONLY_RE = re.compile(r"^[а-яА-ЯёЁ]+$")

# ── zh/en 公式规则段（结构对齐 prepare L233-257，措辞归一到本模块）──

_TITLE_FORMULA_ZH = (
    "标题公式：[核心词], [属性], [场景]\n"
    "- 核心词：产品是什么（如 Садовый секатор）\n"
    "- 属性：1-2个关键特征（如 профессиональный, с латексным покрытием）\n"
    "- 场景：使用场景（如 для обрезки веток）"
)

_TITLE_FORMULA_EN = (
    "Title formula: [Core keyword], [Attribute], [Scene]\n"
    "- Core keyword: what the product is (e.g. Садовый секатор)\n"
    "- Attribute: 1-2 key features (e.g. профессиональный, с латексным покрытием)\n"
    "- Scene: usage scene (e.g. для обрезки веток)"
)

# 流量词建议行（traffic_keywords 非空时追加）。占位符 {kw} 由调用侧以关键词替换。
_TRAFFIC_LINE_ZH = "流量词建议（可融入场景/属性段，保持西里尔，不得超过3个）：{kw}"
_TRAFFIC_LINE_EN = "Traffic keywords (merge into scene/attributes, keep Cyrillic, max 3): {kw}"


def _build_prompt(lang: str) -> str:
    """按 lang 返回公式规则 + 严格规则的完整 system prompt（不含流量词行）。"""
    if lang.lower() == "en":
        noise = ", ".join(_NOISE_EN)
        return (
            "You are an Ozon Russia product title expert. Translate the product title into "
            "Russian using this formula.\n\n"
            f"{_TITLE_FORMULA_EN}\n"
            "Strict rules (violation causes Ozon rejection):\n"
            f"1. Max {TITLE_MAX_LENGTH} characters (including spaces and punctuation)\n"
            "2. Must contain comma or dash separating parts\n"
            "3. No keyword stuffing: max 3 consecutive noun keywords\n"
            f"4. Remove all marketing words: {noise}, etc.\n"
            "5. No duplicate keywords\n"
            "6. 100% Cyrillic, no Latin, no Chinese\n"
            "7. Return only the Russian title\n\n"
            "Examples:\n"
            "- Input: \"Frog Plant Stand Green Animal Pet Frog Decoration Plant Rack\"\n"
            "- Output: \"Подставка для растений, декоративная лягушка, для дома\"\n"
            "- Input: \"JungleSpoon Green Leaf Colander Monstera Spoon Noodle Strainer\"\n"
            "- Output: \"Кухонная ложка-шумовка, лист монстеры, для кухни\""
        )
    noise = "、".join(_NOISE_ZH)
    return (
        "你是Ozon俄罗斯电商平台的产品标题专家。将中文标题翻译为俄语，严格遵循以下公式。\n\n"
        f"{_TITLE_FORMULA_ZH}\n"
        "严格规则（违反任何一条都会导致Ozon审核拒绝）：\n"
        f"1. 标题长度不超过{TITLE_MAX_LENGTH}个字符（含空格和标点）\n"
        "2. 标题中必须包含逗号或破折号分隔各部分\n"
        "3. 绝对禁止关键词堆砌：连续名词性关键词不超过3个\n"
        f"4. 去除所有营销词汇：{noise} 等\n"
        "5. 去除重复关键词\n"
        "6. 100%西里尔字母，禁止拉丁字母和中文\n"
        "7. 只返回俄语标题，不要解释\n\n"
        "示例：\n"
        "- 输入：\"跨境爆款 现货 Frog Plant Stand 绿色动物宠物青蛙装饰植物架\"\n"
        "- 输出：\"Подставка для растений, декоративная лягушка, для дома\"\n"
        "- 输入：\"亚马逊创意JungleSpoon绿叶子漏勺龟背叶勺子捞面勺\"\n"
        "- 输出：\"Кухонная ложка-шумовка, лист монстеры, для кухни\""
    )


def build_title_formula_prompt(
    lang: str,
    traffic_keywords: list[str] | None = None,
) -> str:
    """返回标题公式的完整 prompt 文本（zh/en 两套，单条字符串）。

    结构：公式规则 + 严格规则 + 示例；`traffic_keywords` 非空时追加一行
    「流量词建议（…不得超过3个）」并内嵌关键词。

    约定：返回文本作为 **system prompt** 使用；调用方把实际产品标题/关键词
    作为 user prompt 传入（对齐三处现有调用形态 system_prompt + user_prompt 分离）。
    传入的 traffic_keywords 建议先经 `parse_title_formula_keywords` 过滤。

    Args:
        lang: "zh"（中文公式，默认）/ "en"（英文公式）。
        traffic_keywords: 流量词（俄语）；None/空 → 不追加建议行。

    Returns:
        完整 prompt 文本。
    """
    prompt = _build_prompt(lang)
    if traffic_keywords:
        keyword_str = ", ".join(traffic_keywords)
        if lang.lower() == "en":
            prompt += "\n\n" + _TRAFFIC_LINE_EN.format(kw=keyword_str)
        else:
            prompt += "\n\n" + _TRAFFIC_LINE_ZH.format(kw=keyword_str)
    return prompt


def parse_title_formula_keywords(keywords: list[str]) -> list[str]:
    """把流量词过滤到纯西里尔 + 长度合法，去重并截断到 3 个。

    中文/拉丁/数字词直接丢弃（最终标题禁中文/拉丁）；超长词（>TRAFFIC_KEYWORD_MAX_LEN）
    视为噪声丢弃。返回的列表可直接传入 `build_title_formula_prompt`。
    """
    seen: list[str] = []
    seen_lower: set[str] = set()
    for raw in keywords or []:
        word = str(raw).strip()
        if not _CYRILLIC_ONLY_RE.match(word):
            continue  # 非纯西里尔（含拉丁/中文/数字）→ 丢弃
        if len(word) > TRAFFIC_KEYWORD_MAX_LEN:
            continue  # 超长 → 丢弃
        lower = word.lower()
        if lower in seen_lower:
            continue  # 去重（大小写不敏感）
        seen.append(word)
        seen_lower.add(lower)
        if len(seen) >= TRAFFIC_KEYWORDS_MAX:
            break
    return seen

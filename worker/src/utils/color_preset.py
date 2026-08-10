"""color_preset_router — 品类 → 配色预设的确定性路由（纯函数，无 LLM 无 I/O）

PRD v5 §5.2.2（L307-319）实现：按 draft.category 匹配关键词 → 输出
COLOR_PRESET 名 + BRAND_PRIMARY / ACCENT。让同产品 10 张图风格一致（G5）。

设计要点（对抗定案）：
- 纯函数：不调 API、不读网络、无状态、无外部依赖
- 匹配大小写不敏感、子串匹配（category 可能混合中英/俄文）
- 默认 HOME_LIFESTYLE（PRD 权威，L319）
- 关键词覆盖中英文（category 可能是中文「宠物用品」或英文 pet）
- 常量内置于模块（简化，不建 config JSON）

与 prompt_assembler 的集成（extra 传入 color_preset）属于后续接线，本模块不改
assemble_prompt 签名。
"""

# 品类 → 配色预设映射表（PRD v5 §5.2.2 L309-319 + 对抗补充关键词）
PRESETS: dict[str, dict] = {
    "GARDEN": {
        "primary": "#16A34A",
        "accent": "#A16207",
        "keywords": ["驱蚊", "蚊", "杀虫", "园艺", "花园", "户外"],
    },
    "PET_FUN": {
        "primary": "#3B82F6",
        "accent": "#F97316",
        "keywords": ["宠物", "猫", "狗", "pet"],
    },
    "BEAUTY_PINK": {
        "primary": "#1E3A5F",
        "accent": "#F59E0B",
        "keywords": ["美妆", "护肤", "美容", "个护"],
    },
    "TOY_KIDS": {
        "primary": "#22C55E",
        "accent": "#F97316",
        "keywords": ["母婴", "婴儿", "儿童", "玩具"],
    },
    "HOME_LIFESTYLE": {
        "primary": "#A16207",
        "accent": "#1E40AF",
        "keywords": ["家居", "收纳", "家纺", "厨房"],
    },
    "TECH_BLUE": {
        "primary": "#1E40AF",
        "accent": "#06B6D4",
        "keywords": ["电子", "数码", "3C", "充电", "智能", "风扇"],
    },
    "INDUSTRIAL": {
        "primary": "#000000",
        "accent": "#DC2626",
        "keywords": ["清洁", "化工", "工业", "工具"],
    },
    "WARM_SLEEP": {
        "primary": "#1E293B",
        "accent": "#F59E0B",
        "keywords": ["睡眠", "夜灯", "卧室", "香薰"],
    },
}

DEFAULT_PRESET = "HOME_LIFESTYLE"


def resolve_color_preset(category: str = "") -> str:
    """按 draft.category 匹配关键词返回 preset 名；无命中返回 DEFAULT_PRESET。

    匹配规则：
    - 大小写不敏感（category 与关键词均 lower 后比较）
    - 子串匹配（category 含任一关键词即命中）
    - 遍历 PRESETS 按声明顺序，首个命中生效
    - None / 非字符串输入视为空 → 默认
    """
    if not isinstance(category, str):
        return DEFAULT_PRESET
    haystack = category.lower()
    for name, spec in PRESETS.items():
        for keyword in spec["keywords"]:
            if keyword.lower() in haystack:
                return name
    return DEFAULT_PRESET


def get_preset_colors(preset: str = "") -> dict:
    """返回 {"preset": ..., "primary": HEX, "accent": HEX}；未知 preset 回退 DEFAULT。"""
    if preset not in PRESETS:
        preset = DEFAULT_PRESET
    spec = PRESETS[preset]
    return {
        "preset": preset,
        "primary": spec["primary"],
        "accent": spec["accent"],
    }

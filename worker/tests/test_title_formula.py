"""title_formula 单测 — 标题公式共享模块（任务：抽 3 份拷贝为唯一入口，TDD）。

覆盖：zh/en 两套公式 prompt 结构 / traffic_keywords 注入行 / None 时无注入行 /
parse_title_formula_keywords 过滤非西里尔与超长词 / NOISE_KEYWORDS 营销词清单存在性。
纯函数测试，无外部依赖、不连 PG。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.title_formula import (
    NOISE_KEYWORDS,
    TITLE_MAX_LENGTH,
    build_title_formula_prompt,
    parse_title_formula_keywords,
)


def test_build_prompt_zh_with_traffic_keywords():
    """zh 公式 prompt：含「核心词」公式 + 「流量词建议」行 + 具体流量词。"""
    prompt = build_title_formula_prompt("zh", ["игрушка", "музыкальная"])
    assert "核心词" in prompt
    assert "流量词建议" in prompt
    assert "игрушка" in prompt
    assert "музыкальная" in prompt
    # 标题 ≤80 字符规则必须在 prompt 中
    assert str(TITLE_MAX_LENGTH) in prompt


def test_build_prompt_zh_without_traffic_keywords():
    """traffic_keywords=None → 不出现「流量词建议」行。"""
    prompt = build_title_formula_prompt("zh")
    assert "核心词" in prompt
    assert "流量词建议" not in prompt


def test_build_prompt_en():
    """lang='en' → 英文公式（含英文公式名 + 英文流量词行）。"""
    prompt = build_title_formula_prompt("en", ["игрушка"])
    assert "Core keyword" in prompt
    assert "Traffic keyword" in prompt
    assert "игрушка" in prompt


def test_parse_keywords_filters_non_cyrillic_and_overlong():
    """parse 辅助：丢弃拉丁/中文词与超长词，保留纯西里尔合法词。"""
    result = parse_title_formula_keywords(
        [
            "игрушка",
            "музыкальная",
            "musical",                    # 拉丁 → 丢弃
            "玩具",                        # 中文 → 丢弃
            "оченьдлинноеключевоесловопревышающеелимит",  # 超长 → 丢弃
        ]
    )
    assert "игрушка" in result
    assert "музыкальная" in result
    assert "musical" not in result
    assert "玩具" not in result
    assert "оченьдлинноеключевоесловопревышающеелимит" not in result


def test_parse_keywords_caps_at_three():
    """流量词不得超过 3 个（对齐 prompt 规则）。"""
    result = parse_title_formula_keywords(["один", "два", "три", "четыре"])
    assert len(result) == 3


def test_noise_keywords_present():
    """NOISE_KEYWORDS 从现有代码提取：中/俄/英营销词都有。"""
    assert "爆款" in NOISE_KEYWORDS
    assert "亚马逊" in NOISE_KEYWORDS
    assert "хит" in NOISE_KEYWORDS
    assert "hot" in NOISE_KEYWORDS

# -*- coding: utf-8 -*-
"""
v0.32 Wave 2 — color_preset_router 配色预设路由（纯函数，无 LLM 无 I/O）。

覆盖：
(a) 中文品类「宠物用品」→ PET_FUN
(b) 英文「pet accessories」→ PET_FUN
(c) 「驱蚊棒」→ GARDEN
(d) 「智能风扇」→ TECH_BLUE
(e) 无命中/空字符串 → HOME_LIFESTYLE（默认）
(f) get_preset_colors 返回正确 HEX；未知 preset → 默认
(g) 大小写不敏感
(h) None / 非字符串输入 → 默认（防御 draft.category 缺失）

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_color_preset_router.py -v
      cd worker && PYTHONPATH=src python3 tests/test_color_preset_router.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.color_preset import (  # noqa: E402
    DEFAULT_PRESET,
    PRESETS,
    get_preset_colors,
    resolve_color_preset,
)


# ── (a) 中文品类匹配 ──
def test_chinese_pet_category():
    assert resolve_color_preset("宠物用品") == "PET_FUN"


def test_chinese_pet_category_variant():
    # 单字关键词「猫」也应命中
    assert resolve_color_preset("猫咪自动喂食器") == "PET_FUN"


# ── (b) 英文品类匹配 ──
def test_english_pet_category():
    assert resolve_color_preset("pet accessories") == "PET_FUN"


# ── (c) 驱蚊 → GARDEN ──
def test_mosquito_repellent_garden():
    assert resolve_color_preset("驱蚊棒") == "GARDEN"


# ── (d) 智能风扇 → TECH_BLUE ──
def test_smart_fan_tech_blue():
    assert resolve_color_preset("智能风扇") == "TECH_BLUE"


# ── (e) 无命中 / 空字符串 → 默认 ──
def test_no_match_default():
    assert resolve_color_preset("未收录品类xyz") == DEFAULT_PRESET


def test_empty_category_default():
    assert resolve_color_preset("") == DEFAULT_PRESET


# ── (f) get_preset_colors 返回正确 HEX；未知 preset → 默认 ──
def test_get_preset_colors_known_preset():
    colors = get_preset_colors("GARDEN")
    assert colors == {"preset": "GARDEN", "primary": "#16A34A", "accent": "#A16207"}


def test_get_preset_colors_default_preset():
    colors = get_preset_colors("HOME_LIFESTYLE")
    assert colors == {"preset": "HOME_LIFESTYLE", "primary": "#A16207", "accent": "#1E40AF"}


def test_get_preset_colors_unknown_falls_back_default():
    colors = get_preset_colors("NOT_A_PRESET")
    assert colors["preset"] == DEFAULT_PRESET
    assert colors["primary"] == PRESETS[DEFAULT_PRESET]["primary"]
    assert colors["accent"] == PRESETS[DEFAULT_PRESET]["accent"]


def test_get_preset_colors_empty_falls_back_default():
    colors = get_preset_colors("")
    assert colors["preset"] == DEFAULT_PRESET


# ── (g) 大小写不敏感 ──
def test_case_insensitive_english():
    assert resolve_color_preset("PET ACCESSORIES") == "PET_FUN"


def test_case_insensitive_mixed_chinese():
    assert resolve_color_preset("  Pet 用品 ") == "PET_FUN"


# ── (h) None / 非字符串 → 默认 ──
def test_none_category_default():
    assert resolve_color_preset(None) == DEFAULT_PRESET


def test_non_string_category_default():
    assert resolve_color_preset(123) == DEFAULT_PRESET


if __name__ == "__main__":
    import traceback

    tests = [
        test_chinese_pet_category,
        test_chinese_pet_category_variant,
        test_english_pet_category,
        test_mosquito_repellent_garden,
        test_smart_fan_tech_blue,
        test_no_match_default,
        test_empty_category_default,
        test_get_preset_colors_known_preset,
        test_get_preset_colors_default_preset,
        test_get_preset_colors_unknown_falls_back_default,
        test_get_preset_colors_empty_falls_back_default,
        test_case_insensitive_english,
        test_case_insensitive_mixed_chinese,
        test_none_category_default,
        test_non_string_category_default,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception:
            print(f"  ❌ {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)

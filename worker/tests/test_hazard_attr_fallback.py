"""危险品等级(9782) / 必填字典属性兜底规则单测（v0.21 P0-2）。

旧逻辑：必填字典属性无匹配时「取第一个字典值」→ 9782 被填成
"Категория 1. Взрывчатые вещества"（BR_hazard_class1）。新逻辑：
- 危险属性只挑「非危险」安全默认，取不到则跳过；
- 其他属性仅当字典值唯一才填。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.attribute_utils import (
    is_hazard_attr,
    get_safe_hazard_default,
    pick_dict_fallback_value,
)

EXPLOSIVES = {"id": 970593901, "value": "Категория 1. Взрывчатые вещества"}
SAFE = {"id": 970593900, "value": "Не опасный груз"}


def test_is_hazard_attr():
    assert is_hazard_attr(9782) is True
    assert is_hazard_attr(85) is False
    assert is_hazard_attr(0, "Класс опасности товара") is True


def test_safe_default_picks_non_hazard_when_explosives_first():
    """爆炸物在首位时，仍应选中「非危险」值。"""
    got = get_safe_hazard_default([EXPLOSIVES, SAFE])
    assert got == (970593900, "Не опасный груз")


def test_safe_default_none_when_no_safe_value():
    assert get_safe_hazard_default([EXPLOSIVES]) is None
    assert get_safe_hazard_default([]) is None


def test_hazard_fallback_skips_when_no_safe_value():
    """9782 无安全默认 → 返回 None（跳过，绝不填爆炸物）。"""
    assert pick_dict_fallback_value(9782, "Класс опасности товара", [EXPLOSIVES]) is None


def test_hazard_fallback_safe_value():
    assert pick_dict_fallback_value(9782, "", [EXPLOSIVES, SAFE]) == (970593900, "Не опасный груз")


def test_regular_attr_unique_value_still_filled():
    """普通属性唯一值仍可兜底。"""
    got = pick_dict_fallback_value(8229, "Тип", [{"id": 100, "value": "Единственный"}])
    assert got == (100, "Единственный")


def test_regular_attr_multi_value_skipped():
    """普通属性多值无匹配 → 不再取第一个，返回 None。"""
    assert pick_dict_fallback_value(8229, "Тип", [{"id": 100, "value": "A"}, {"id": 101, "value": "B"}]) is None


# ═══ v0.29.x 新增: ZH_HANS 中文安全值 + attr_defaults 9782 分支 + 8229 语言错位 ═══

SAFE_ZH = {"id": 970593901, "value": "非危险货物"}
UNSAFE_ZH = {"id": 970593902, "value": "爆炸物 Category 1"}


def test_safe_default_zh_hans_value():
    """v0.29.x: ZH_HANS 中文"非危险"值也能识别(缓存预热只存 ZH_HANS 时的场景)。"""
    assert get_safe_hazard_default([UNSAFE_ZH, SAFE_ZH]) == (970593901, "非危险货物")


def test_safe_default_zh_mixed_ru_first():
    """中英混排: RU 值在首位 + 中文安全值在后。"""
    assert get_safe_hazard_default([SAFE, SAFE_ZH]) == (970593900, "Не опасный груз")


def test_attr_defaults_resolve_9782():
    """attr_defaults.resolve_missing_mandatory_dict_attr 增加 9782 分支。"""
    from utils.attr_defaults import resolve_missing_mandatory_dict_attr
    got = resolve_missing_mandatory_dict_attr(9782, "Класс опасности товара", dict_vals=[EXPLOSIVES, SAFE])
    assert got == (970593900, "Не опасный груз")
    got_zh = resolve_missing_mandatory_dict_attr(9782, "Класс опасности товара", dict_vals=[UNSAFE_ZH, SAFE_ZH])
    assert got_zh == (970593901, "非危险货物")
    assert resolve_missing_mandatory_dict_attr(9782, "Класс опасности товара", dict_vals=[EXPLOSIVES]) is None


def test_attr_defaults_resolve_8229_first_value():
    """8229 单值场景仍命中(不回归); 多值时按 type_id/关键词, 绝不取第一个(套娃 bug)。"""
    from utils.attr_defaults import resolve_missing_mandatory_dict_attr
    got = resolve_missing_mandatory_dict_attr(8229, "Тип товара", dict_vals=[{"id": 100, "value": "Носки"}])
    assert got == (100, "Носки")

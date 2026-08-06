"""v0.28.5 A1: REPAIR_STRATEGY 补全回归 — 审计发现 9 个未映射错误码。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.validation_retry_loop import (
    REPAIR_STRATEGY,
    FIX_TYPE_ATTRIBUTES,
    FIX_TYPE_UNFIXABLE,
    classify_fix_type,
)

NEW_CODES = [
    "VALUE_MUST_BE_INTEGER",
    "VALUE_MUST_BE_DECIMAL",
    "ATTRIBUTE_VALUE_COUNT_EXCEEDED",
    "EMPTY_REQUIRED_AFTER_WARNING_DELETING",
    "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT",
    "all_image_failed",
    "warning_attribute_values_empty",
    "erased_attribute_value",
    "CONDITIONAL_ATTRIBUTE_ERROR",
]


def test_all_new_codes_mapped():
    """9 个审计错误码全部进入 REPAIR_STRATEGY。"""
    for c in NEW_CODES:
        assert c in REPAIR_STRATEGY, f"{c} 未映射到 REPAIR_STRATEGY"


def test_all_new_codes_classified():
    """9 个错误码全部有修复类型分类(attributes 或 unfixable)。"""
    for c in NEW_CODES:
        assert (c in FIX_TYPE_ATTRIBUTES) or (c in FIX_TYPE_UNFIXABLE), f"{c} 未分类"


def test_integer_decimal_repair_prepare():
    """数值类型错误 → repair_prepare(强制类型转换, LLM 改不了)。"""
    assert REPAIR_STRATEGY["VALUE_MUST_BE_INTEGER"] == "repair_prepare"
    assert REPAIR_STRATEGY["VALUE_MUST_BE_DECIMAL"] == "repair_prepare"


def test_unfixable_codes():
    """不可修复错误 → unfixable, 不浪费 3 轮重试。"""
    assert REPAIR_STRATEGY["SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"] == "unfixable"
    assert REPAIR_STRATEGY["all_image_failed"] == "unfixable"


def test_classify_fix_type_correct():
    """classify_fix_type 对新码分类正确。"""
    assert classify_fix_type("all_image_failed") == "unfixable"
    assert classify_fix_type("VALUE_MUST_BE_DECIMAL") == "attributes"
    assert classify_fix_type("SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT") == "unfixable"
    assert classify_fix_type("ATTRIBUTE_VALUE_COUNT_EXCEEDED") == "attributes"


def test_marking_auto_corrected_explicit():
    """marking_auto_corrected 明确映射(原走默认, 现显式)。"""
    assert REPAIR_STRATEGY.get("marking_auto_corrected") == "error_repair_llm"


def test_llm_codes_still_llm():
    """需补值的错误码仍走 error_repair_llm(LLM 可搜字典值)。"""
    assert REPAIR_STRATEGY["warning_attribute_values_empty"] == "error_repair_llm"
    assert REPAIR_STRATEGY["erased_attribute_value"] == "error_repair_llm"
    assert REPAIR_STRATEGY["CONDITIONAL_ATTRIBUTE_ERROR"] == "error_repair_llm"

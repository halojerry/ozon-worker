"""v0.28.5 C2: 失败任务 notice(用户可读中文说明)回归。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.validation_retry_loop import ERROR_NOTICE_MAP, _build_notice


def test_success_no_notice():
    """上传成功 → notice 为空。"""
    assert _build_notice("DESCRIPTION_DECLINE", "err", "success") == ""


def test_mapped_error_chinese():
    """已知错误码 → 中文说明。"""
    n = _build_notice("DESCRIPTION_DECLINE", "desc decline", "failed")
    assert "描述被拒绝" in n
    assert "拉丁" in n


def test_unfixable_mapped():
    """危险品/重复上架 → 明确中文说明。"""
    assert "无法自动修复" in _build_notice("BR_hazard_class1", "", "failed")
    assert "无法重复上架" in _build_notice("SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT", "", "failed")


def test_unknown_error_fallback_with_message():
    """未知错误码 → 带原始摘要。"""
    n = _build_notice("SOME_NEW_ERROR", "detail abc", "failed")
    assert "SOME_NEW_ERROR" in n
    assert "detail abc" in n


def test_unknown_error_no_message():
    """未知错误码且无消息 → 通用说明(带错误码)。"""
    n = _build_notice("X", "", "failed")
    assert "X" in n
    assert "重试后仍未通过" in n


def test_all_repair_codes_have_notice_or_classified():
    """A1 补的 9 个错误码全部有中文说明或走通用回退(不抛异常)。"""
    for code in ["VALUE_MUST_BE_INTEGER", "VALUE_MUST_BE_DECIMAL",
                 "ATTRIBUTE_VALUE_COUNT_EXCEEDED",
                 "EMPTY_REQUIRED_AFTER_WARNING_DELETING",
                 "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT", "all_image_failed",
                 "warning_attribute_values_empty", "erased_attribute_value",
                 "CONDITIONAL_ATTRIBUTE_ERROR"]:
        n = _build_notice(code, "", "failed")
        assert n, f"{code} notice 为空"


def test_error_notice_map_has_key_codes():
    """ERROR_NOTICE_MAP 关键码在位。"""
    assert "DESCRIPTION_DECLINE" in ERROR_NOTICE_MAP
    assert "BR_chinese_hieroglyphs" in ERROR_NOTICE_MAP
    assert "pics_http_error" in ERROR_NOTICE_MAP

"""fix/category-bridge-v1 回归：类目无效请求级 400 分流（不进 LLM 修复循环）。

2026-09-24 事故：follow ×5 的 import 400
`Request validation error: invalid Request.Items.TypeId: value must be greater than 0`
被 parse_error_node 的 UNKNOWN 兜底送进 error_repair_llm → LLM 修不了类目 → 原样重炸
→ 终态误标「Ozon 审核拒绝(UNKNOWN): task_id为空」（假失败）。

修复：请求级类目 400 特征 → LOCAL_CATEGORY_INVALID_REQUEST → unfixable 直达终态。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from graphs.validation_retry_loop import (  # noqa: E402
    REPAIR_STRATEGY,
    classify_fix_type,
    parse_error_node,
)

# 2026-09-24 事故原始文案（worker 容器日志逐字）
ACCIDENT_MSG = "Request validation error: invalid Request.Items.TypeId: value must be greater than 0"
SCHEMA_400_MSG = "category with level_3_id=14762 and type=14762 is not found"


def _state(error_message=""):
    return SimpleNamespace(
        errors=[], validation_errors=[], error_message=error_message,
        error_code="", attribute_id=0, error_type="", repair_node="",
        retry_count=0, decline_errors=[],
    )


def test_accident_typeid_400_routes_unfixable():
    """事故原文案 → LOCAL_CATEGORY_INVALID_REQUEST / unfixable / final_result。"""
    st = _state(ACCIDENT_MSG)
    out = parse_error_node(st)
    assert out.error_code == "LOCAL_CATEGORY_INVALID_REQUEST"
    assert out.error_type == "unfixable"
    assert out.repair_node == "final_result"
    assert "非审核拒绝" in (out.error_message or "")
    assert "类目无效" in (out.error_message or "")


def test_schema_400_not_found_routes_unfixable():
    """schema API 400 模板（level_3_id ... is not found）→ 同款分流。"""
    st = _state(SCHEMA_400_MSG)
    out = parse_error_node(st)
    assert out.error_code == "LOCAL_CATEGORY_INVALID_REQUEST"
    assert out.repair_node == "final_result"


def test_official_codes_classified_unfixable():
    """官方错误码（errors dict 形态）→ classify unfixable + REPAIR_STRATEGY 直达。"""
    for code in ("description_category_invalid", "DESCRIPTION_CATEGORY_INVALID",
                 "description_category_has_no_description_type",
                 "DESCRIPTION_CATEGORY_HAS_NO_DESCRIPTION_TYPE",
                 "LOCAL_CATEGORY_INVALID_REQUEST"):
        assert classify_fix_type(code) == "unfixable", code
        assert REPAIR_STRATEGY.get(code) == "unfixable", code


def test_unrelated_unknown_still_goes_llm():
    """无关的 UNKNOWN 文案不受影响——仍走 error_repair_llm（旧行为零变化）。"""
    st = _state("какая-то другая ошибка модерации")
    out = parse_error_node(st)
    assert out.error_code == "UNKNOWN"
    assert out.repair_node == "error_repair_llm"

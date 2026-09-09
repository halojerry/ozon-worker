#!/usr/bin/env python3
"""v0.73 Task2: 本地预检错误码独立化——标题-类目零交集拦截入箱（单测）。

生产实证：ozon_validate 本地预检产生「item[0]标题与类目不一致（Ozon
DESCRIPTION_DECLINE 风险）…」后，parse_error_node 按关键词把含「标题」的
本地错误错归 BR_chinese_hieroglyphs_in_attribute → 走属性批量翻译修复支路
（对本错误无效）→ revalidate 放行 → 子图内 CREATE 重传 → Ozon approved
（错货上架，Issue5a 假成功 + Issue3 误导文案）。

本文件锁定四段行为：
  ① parse_error_node 对「标题与类目不一致」消息 → 独立码
     LOCAL_TITLE_CATEGORY_MISMATCH（必须先于「标题」关键词分支命中）；
  ② REPAIR_STRATEGY → block_to_box，repair_node_selector 直达 final_result；
  ③ final_result 遇该码：upload_status=blocked + notice 含「已拦截」+
     入采集箱（_maybe_create_blocked_draft → create_blocked_draft 被调，
     失败非致命）；
  ④ task_processor._graph_result_is_failed 对 upload_status=blocked 判 failed；
  ⑤ user_id/envelope 经 wrapper Input → 子图 Input/State 透传（langgraph
     input-schema 纪律：缺声明会被 channel 过滤，入箱拿不到租户）。

运行：
    cd worker && PYTHONPATH=src python -m pytest tests/test_local_block_codes_v073.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.validation_retry_loop import (
    REPAIR_STRATEGY,
    ValidationRetryLoopState,
    final_result,
    parse_error_node,
    repair_node_selector,
)

# ozon_validate_node.py:394 的真实文案形状（含「标题」——回归锁：必须命中新码
# 而非 BR_chinese_hieroglyphs_in_attribute）
_MISMATCH_MSG = (
    "item[0]标题与类目不一致（Ozon DESCRIPTION_DECLINE 风险）: "
    "标题「Автоматический дозатор для пластилина」与类目"
    "「Одежда и обувь > Футболки > Футболки оверсайз」无公共西里尔词（≥3字符）"
)


def _base_state(**kw) -> ValidationRetryLoopState:
    defaults = dict(
        ozon_payload={"items": [{"name": "Автоматический дозатор", "offer_id": "x"}]},
        draft={"item_id": "681352312", "title": "自动橡皮泥机"},
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028743", type_id="92780",
        user_id="tenant-1",
        envelope={"draft": {"item_id": "681352312"}, "source": {}, "extensions": {}},
    )
    defaults.update(kw)
    return ValidationRetryLoopState(**defaults)


# ── ① parse_error_node 独立分类 ──────────────────────────────────────────

def test_parse_error_classifies_title_category_mismatch():
    """「标题与类目不一致」本地预检消息 → LOCAL_TITLE_CATEGORY_MISMATCH。

    现状（RED）：消息含「标题」错归 BR_chinese_hieroglyphs_in_attribute，
    走属性翻译修复支路白烧重试后错货重传。
    """
    state = _base_state(validation_errors=[_MISMATCH_MSG])
    out = parse_error_node(state)
    assert out.error_code == "LOCAL_TITLE_CATEGORY_MISMATCH"


def test_parse_error_mismatch_accumulates_decline_text():
    """消费前原样累积（_accumulate_decline_errors 纪律）——留存表有原文可查。"""
    state = _base_state(validation_errors=[_MISMATCH_MSG])
    out = parse_error_node(state)
    msgs = [str((d.get("texts") or {}).get("message") or "") for d in out.decline_errors]
    assert any("标题与类目不一致" in m for m in msgs)


def test_parse_error_plain_title_chinese_still_br_chinese():
    """负向回归：普通「标题」类本地错误（不含「标题与类目不一致」）仍归
    BR_chinese_hieroglyphs_in_attribute——新分支不得劫持既有中文修复支路。"""
    state = _base_state(validation_errors=["item[0]标题含中文字符: 橡皮泥机"])
    out = parse_error_node(state)
    assert out.error_code == "BR_chinese_hieroglyphs_in_attribute"


# ── ② 策略 + 路由 ────────────────────────────────────────────────────────

def test_repair_strategy_maps_to_block_to_box():
    assert REPAIR_STRATEGY.get("LOCAL_TITLE_CATEGORY_MISMATCH") == "block_to_box"


def test_selector_routes_block_to_box_to_final_result():
    """block_to_box 直达 final_result——不进任何 repair/reupload 节点。"""
    state = _base_state(
        error_code="LOCAL_TITLE_CATEGORY_MISMATCH",
        error_type="fixable",
        repair_node="block_to_box",
    )
    assert repair_node_selector(state) == "final_result"


def test_selector_default_routes_unchanged():
    """负向回归：未知码默认仍走 error_repair_llm（不因新分支漂移）。"""
    state = _base_state(error_code="UNKNOWN", error_type="fixable", repair_node="")
    assert repair_node_selector(state) == "error_repair_llm"


# ── ③ final_result 拦截入箱 ──────────────────────────────────────────────

def test_final_result_blocked_to_box():
    """遇 LOCAL_TITLE_CATEGORY_MISMATCH：blocked + 已拦截文案 + 入箱被调。"""
    state = _base_state(
        error_code="LOCAL_TITLE_CATEGORY_MISMATCH",
        error_type="fixable",
        repair_node="final_result",
    )
    with mock.patch("utils.blocked_draft_box.create_blocked_draft") as mk:
        mk.return_value = {"draft_id": "d-1", "reused": False,
                           "top1_name": "Игрушки", "top1_confidence": 0.42}
        out = final_result(state)
    assert mk.called is True
    _tenant, _envelope, _candidates, _reason = mk.call_args.args
    assert _tenant == "tenant-1"
    assert _candidates == []
    assert "类目" in _reason
    assert out.upload_status == "blocked"
    assert out.is_valid is False
    assert "已拦截" in out.notice
    assert "d-1" in out.notice
    assert out.product_id is None


def test_final_result_blocked_box_failure_non_fatal():
    """入箱失败（PG 异常等）不影响拦截终态——notice 仍含「已拦截」。"""
    state = _base_state(
        error_code="LOCAL_TITLE_CATEGORY_MISMATCH",
        error_type="fixable",
        repair_node="final_result",
    )
    with mock.patch("utils.blocked_draft_box.create_blocked_draft",
                    side_effect=RuntimeError("pg down")):
        out = final_result(state)
    assert out.upload_status == "blocked"
    assert "已拦截" in out.notice


def test_final_result_blocked_without_tenant_still_blocked():
    """无租户（state.user_id 空）→ 跳过入箱但仍拦截（不重传）。"""
    state = _base_state(
        error_code="LOCAL_TITLE_CATEGORY_MISMATCH",
        error_type="fixable",
        repair_node="final_result",
        user_id="",
    )
    with mock.patch("utils.blocked_draft_box.create_blocked_draft") as mk:
        out = final_result(state)
    assert mk.called is False
    assert out.upload_status == "blocked"


def test_final_result_normal_path_unchanged():
    """负向回归：其他终态（如 success）不走拦截分支。"""
    state = _base_state(
        error_code="", upload_status="success", product_id="pid-1",
    )
    out = final_result(state)
    assert out.upload_status == "success"
    assert out.product_id == "pid-1"


# ── ③b 子图端到端路由（parse_error → classify → selector → final_result）──

def test_subgraph_end_to_end_blocks_without_repair():
    """整子图 invoke：本地预检消息进来 → blocked 出去，全程零 LLM/零重传。"""
    from graphs.validation_retry_loop import validation_retry_loop
    payload = ValidationRetryLoopState(
        ozon_payload={"items": [{"name": "Автоматический дозатор", "offer_id": "x"}]},
        validation_errors=[_MISMATCH_MSG],
        draft={"item_id": "681352312"},
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028743", type_id="92780",
        user_id="tenant-1",
        envelope={"draft": {"item_id": "681352312"}},
    ).model_dump()
    with mock.patch("utils.blocked_draft_box.create_blocked_draft") as mk:
        mk.return_value = {"draft_id": "d-9", "reused": False,
                           "top1_name": "", "top1_confidence": 0.0}
        result = validation_retry_loop.invoke(payload)
    assert mk.called is True
    assert result.get("upload_status") == "blocked"
    assert "已拦截" in str(result.get("notice") or "")


# ── ④ 终态失败判定 ───────────────────────────────────────────────────────

def test_graph_result_is_failed_covers_blocked():
    """upload_status=blocked 必须判 failed（假成功第二窗口收口）。"""
    from utils.task_processor import _graph_result_is_failed
    assert _graph_result_is_failed({"upload_status": "blocked",
                                    "notice": "已拦截入采集箱"}) is True
    # 回归：原判定语义不变
    assert _graph_result_is_failed({"upload_status": "success"}) is False
    assert _graph_result_is_failed({"upload_status": "failed"}) is True


# ── ⑤ 租户/信封透传（langgraph input-schema 纪律）────────────────────────

def test_wrapper_input_carries_tenant_and_envelope():
    from graphs.state import ValidationRetryWrapperInput
    assert "user_id" in ValidationRetryWrapperInput.model_fields
    assert "envelope" in ValidationRetryWrapperInput.model_fields


def test_subgraph_input_and_state_carry_tenant_and_envelope():
    from graphs.validation_retry_loop import (
        ValidationRetryLoopInput,
        ValidationRetryLoopState,
    )
    for model in (ValidationRetryLoopInput, ValidationRetryLoopState):
        assert "user_id" in model.model_fields, model.__name__
        assert "envelope" in model.model_fields, model.__name__


def test_wrapper_source_passes_tenant_and_envelope():
    """wrapper 节点源码把 user_id/envelope 传进子图 Input（缺透传=静默拿不到）。"""
    import inspect
    from graphs.nodes import validation_retry_wrapper_node as _w
    src = inspect.getsource(_w)
    assert "user_id=state.user_id" in src
    assert "envelope=state.envelope" in src


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

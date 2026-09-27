#!/usr/bin/env python3
"""fix/arch-findings-v1（2026-09-25 架构梳理）retry 子图五处修复回归。

对应 docs/ARCHITECTURE/09-findings.md Top10 #3/#4/#6 与 worker 骨架 PRICE_ERROR 条：
1. 🔴 UnboundLocalError：error_repair_llm 标题修复支路 items_title 未绑定引用
   （强制翻译失败/box_reviewed 清空 repaired_title 时必炸，try 只捕获
   JSONDecodeError → 异常打断整个修复支路）。
2. 9048 形状一致性：revalidate 兜底补 9048 改调 prepare 同源
   _derive_model_name_9048（跟卖保持裸 item_id），删 name[:50]/sku_id 旧形状。
3. PRICE_ERROR 进 REPAIR_STRATEGY → repair_pricing + classify_fix_type → prices。
4. OutOfQuota 吞异常回归：revalidate 两处翻译的 except Exception 前置放行。
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.validation_retry_loop import (
    ERROR_NOTICE_MAP,
    FIX_TYPE_PRICES,
    REPAIR_STRATEGY,
    ValidationRetryLoopState,
    classify_fix_type,
    error_repair_llm_node,
    parse_error_node,
    classify_error_node,
    revalidate_node,
)
from utils.mxou_api import MxouOutOfQuotaError


def _base_state(**kw) -> ValidationRetryLoopState:
    """构造能走通 error_repair_llm 通用 LLM 修复链的最小 state。"""
    defaults = dict(
        error_code="UNKNOWN",
        attribute_id=0,
        error_message="some generic error",
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
        product_name="Тестовый товар",
        ozon_payload={"items": [
            {"name": "Тест", "offer_id": "x", "price": "100", "attributes": []}
        ]},
        final_attributes=[],
        attributes_schema=[],
        draft={"item_id": "123", "supplier": "DemoSupplier", "title": "塑料收纳盒"},
    )
    defaults.update(kw)
    return ValidationRetryLoopState(**defaults)


# ── #3 UnboundLocalError：标题修复支路 ───────────────────────────────────

def test_title_repair_no_unboundlocalerror_when_translation_fails():
    """强制翻译失败把 repaired_title 置空 → 不再抛 UnboundLocalError（Top10 #3）。

    路径：LLM 返回纯拉丁标题 → sanitize_title 无西里尔返回空 → 进强制翻译 →
    mock 翻译调用抛普通异常 → repaired_title="" → 旧代码兄弟行引用未绑定的
    items_title 必炸；新代码跳过 name/4180 写入，卡名保持不变。
    """
    state = _base_state(
        final_attributes=[{"id": 4180, "value": "Тест", "dictionary_value_id": 0}],
    )
    llm_json = json.dumps({"corrected_title": "Kitchen Storage Box Plastic",
                           "repair_explanation": "latin title"})
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm", return_value=llm_json), \
         mock.patch("utils.mxou_api.call_mxou_chat_api",
                    side_effect=RuntimeError("mock: mxou down")):
        out = error_repair_llm_node(state)  # 旧代码此处抛 UnboundLocalError
    assert out.ozon_payload["items"][0]["name"] == "Тест"  # 空标题不写 name
    attr4180 = next(a for a in out.final_attributes if a.get("id") == 4180)
    assert attr4180["value"] == "Тест"  # 空标题不清既有 4180 值


def test_title_repair_still_writes_name_on_success():
    """正向对照：翻译成功 → name/4180 照常写入（修复不破坏既有标题修复）。"""
    state = _base_state(
        final_attributes=[{"id": 4180, "value": "Тест", "dictionary_value_id": 0}],
    )
    llm_json = json.dumps({"corrected_title": "Kitchen Storage Box Plastic",
                           "repair_explanation": "latin title"})
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm", return_value=llm_json), \
         mock.patch("utils.mxou_api.call_mxou_chat_api",
                    return_value="Органайзер для хранения"):
        out = error_repair_llm_node(state)
    assert out.ozon_payload["items"][0]["name"] == "Органайзер для хранения"
    attr4180 = next(a for a in out.final_attributes if a.get("id") == 4180)
    assert attr4180["value"] == "Органайзер для хранения"


def test_title_repair_box_reviewed_clear_no_crash():
    """box_reviewed 清空 LLM 标题重写 → 同样不抛 UnboundLocalError（同支路）。"""
    state = _base_state(extensions={"box_reviewed": True})
    llm_json = json.dumps({"corrected_title": "Органайзер для хранения",
                           "repair_explanation": "rewrite"})
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm", return_value=llm_json):
        out = error_repair_llm_node(state)
    assert out.ozon_payload["items"][0]["name"] == "Тест"  # 采集箱即权威，不改名


# ── #6 9048 形状一致性：revalidate 兜底对齐 prepare ─────────────────────

def _revalidate_state(draft: dict) -> ValidationRetryLoopState:
    """构造进 revalidate 属性合并循环的最小 state（payload 无 9048 基线）。"""
    return ValidationRetryLoopState(
        error_code="UNKNOWN", attribute_id=0,
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
        ozon_payload={"items": [
            {"name": "Органайзер", "offer_id": "x", "price": "100", "attributes": []}
        ]},
        final_attributes=[], attributes_schema=[],
        draft=draft,
    )


def _first_attr_value(state, attr_id: int) -> str | None:
    for a in state.ozon_payload["items"][0]["attributes"]:
        if isinstance(a, dict) and int(a.get("id", 0)) == attr_id:
            return str(a["values"][0]["value"])
    return None


def test_revalidate_9048_fallback_uses_prepare_shape():
    """快照丢 9048 → 兜底值 == prepare 的 _derive_model_name_9048 同源形状。"""
    from graphs.nodes.prepare_ozon_upload_node import _derive_model_name_9048

    draft = {"item_id": "6612345678", "supplier": "DemoSupplier", "title": "塑料收纳盒"}
    out = revalidate_node(_revalidate_state(draft))
    got = _first_attr_value(out, 9048)
    assert got is not None, "缺 9048 时 revalidate 应补"
    assert got == _derive_model_name_9048("6612345678", "DemoSupplier", "塑料收纳盒")
    assert "~" in got  # item_id~sha1[:8] 前缀形状（非跟卖防并卡）


def test_revalidate_9048_fallback_follow_sell_bare_item_id():
    """跟卖（draft.ozon_product_id 在场）→ 裸 item_id，不加前缀（对齐 prepare）。"""
    draft = {"item_id": "6612345678", "supplier": "DemoSupplier",
             "title": "塑料收纳盒", "ozon_product_id": "3852000144"}
    out = revalidate_node(_revalidate_state(draft))
    assert _first_attr_value(out, 9048) == "6612345678"


def test_revalidate_9048_no_item_id_no_fill():
    """draft 无 item_id → 不补 9048（旧 name[:50]/sku_id 形状已删；prepare 同场景也不写）。"""
    draft = {"name": "中文标题不该进9048", "sku_id": "sku-xyz"}
    out = revalidate_node(_revalidate_state(draft))
    assert _first_attr_value(out, 9048) is None


# ── PRICE_ERROR 无策略 → repair_pricing / prices 靶向 ────────────────────

def test_price_error_mapped_to_repair_pricing():
    assert REPAIR_STRATEGY.get("PRICE_ERROR") == "repair_pricing"


def test_price_error_classified_as_prices():
    assert "PRICE_ERROR" in FIX_TYPE_PRICES
    assert classify_fix_type("PRICE_ERROR") == "prices"


def test_parse_error_price_local_error_routes_to_repair_pricing():
    """行为级：本地校验「价格」类错误 → parse_error 造码 PRICE_ERROR →
    classify_error 选 repair_pricing（此前落默认 error_repair_llm 盲修）。"""
    state = ValidationRetryLoopState(
        token="t", ozon_client_id="c", ozon_api_key="k",
        draft={"item_id": "1"},
        validation_errors=["价格缺失或非法"],
    )
    state = parse_error_node(state)
    assert state.error_code == "PRICE_ERROR"
    state = classify_error_node(state)
    assert state.repair_node == "repair_pricing"
    assert state.error_type == "fixable"


# ── #4 OutOfQuota 吞异常回归：revalidate 两处翻译前置放行 ─────────────────

def test_revalidate_translate_outofquota_propagates():
    """TRANSLATE_ATTR_IDS 翻译通道（4180 拉丁值）→ OutOfQuota 必须 raise 不吞。"""
    state = _revalidate_state({"item_id": "1"})
    state.final_attributes = [{"id": 4180, "value": "Kitchen Box", "dictionary_value_id": 0}]
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm",
                    side_effect=MxouOutOfQuotaError("OUT_OF_QUOTA")):
        try:
            revalidate_node(state)
        except MxouOutOfQuotaError:
            pass  # ✅ 前置放行生效
        else:
            raise AssertionError("OutOfQuota 被吞成跳过属性，未向前传播")


def test_revalidate_chinese_value_outofquota_propagates():
    """自由文本中文值翻译通道（非 TRANSLATE_ATTR_IDS）→ OutOfQuota 必须 raise。"""
    state = _revalidate_state({"item_id": "1"})
    state.final_attributes = [{"id": 9999, "value": "塑料收纳盒", "dictionary_value_id": 0}]
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm",
                    side_effect=MxouOutOfQuotaError("OUT_OF_QUOTA")):
        try:
            revalidate_node(state)
        except MxouOutOfQuotaError:
            pass
        else:
            raise AssertionError("OutOfQuota 被吞成置空，未向前传播")


# ── 护栏：既有映射不受本批改动影响 ────────────────────────────────────────

def test_existing_price_codes_unchanged():
    """既有价格码映射不被 PRICE_ERROR 新增破坏（修法要求：不破坏既有映射）。"""
    assert REPAIR_STRATEGY.get("INVALID_PRICE") == "repair_pricing"
    assert REPAIR_STRATEGY.get("price_out_of_range") == "repair_pricing"
    assert classify_fix_type("INVALID_PRICE") == "prices"
    assert classify_fix_type("discount_for_low_price") == "prices"


def test_error_notice_map_count_matches_comment():
    """_build_notice docstring 声称的条数与实际一致（防止再次漂移）。"""
    import inspect
    from graphs.validation_retry_loop import _build_notice
    m = __import__("re").search(r"= (\d+) 条", inspect.getdoc(_build_notice) or "")
    assert m, "docstring 应标注实际条数"
    assert int(m.group(1)) == len(ERROR_NOTICE_MAP)


def test_error_notice_4194_regen_routing_untouched():
    """4194/4195 主图重生成路由不受影响：DESCRIPTION_DECLINE+4194 仍路由
    regen_main_image（载荷含 AI 图且未重生成过）。"""
    from graphs.validation_retry_loop import classify_error_node as _cen
    state = _base_state(
        error_code="DESCRIPTION_DECLINE", attribute_id=4194,
        ozon_payload={"items": [{"name": "Тест", "images": [
            "https://cos.example.com/file/images/abc.jpg"]}]}
    )
    out = _cen(state)
    assert out.repair_node == "regen_main_image"
    assert out.error_type == "fixable"


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

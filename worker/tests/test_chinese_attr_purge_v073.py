#!/usr/bin/env python3
"""v0.73 Task12: 属性中文残留加固（纵深防御）行为测试。

背景：Ozon BR_chinese_hieroglyphs_in_attribute 拒单链上三处真实漏洞（生产实证
它不是本批 reported 案例根因，但链上确有漏洞，修掉防复发）——
(1) error_repair_llm_node 翻译验收只验「有西里尔」不验「无中文」→ LLM 返回
    「中文+西里尔混合」被接受写回；
(2) revalidate_node 兜底通道 `_rv_has_cn and not _rv_has_cyr` → 混合值不翻
    不跳直通重传；翻后仍含中文 → 置空（与 BR_chinese 主路径置空通道一致）；
(3) revalidate_node 无全属性中文终检——上游漏网值直接重传烧轮次（数值属性
    可解析值豁免，对齐 ozon_validate_node 数值清洗契约「'30包' 可解析放行」）。
另：9024(SKU) 硬豁免改为「含中文不豁免」（对齐 prepare v0.16 现状）；中文检测
统一 utils.attribute_utils.has_chinese（扩展假名 U+3040-30FF + CJK 扩展 A
U+3400-4DBF，唯一事实源；attr_value_matcher 同名函数另行统一，本任务不动）。

TDD RED 先行：纯行为断言，禁止 inspect 源码。
运行：cd worker && PYTHONPATH=src python -m pytest tests/test_chinese_attr_purge_v073.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.attribute_utils import has_chinese
from graphs.validation_retry_loop import (
    ValidationRetryLoopState,
    error_repair_llm_node,
    revalidate_node,
)

MIXED_RESULT = "Приёмник 中文混排"  # LLM 坏输出：西里尔+中文混合


def _base_item(name="Приёмник автомобильный", offer_id="cn1", price="500", attributes=None):
    return {
        "name": name,
        "offer_id": offer_id,
        "price": price,
        "weight": 200,
        "depth": 100,
        "width": 100,
        "height": 50,
        "attributes": attributes if attributes is not None else [],
    }


# ═══════════════════════════════════════════════════════════════════════
# (0) has_chinese 覆盖面：假名 + CJK 扩展 A（唯一事实源扩展）
# ═══════════════════════════════════════════════════════════════════════
def test_has_chinese_covers_kana_and_cjk_ext_a():
    """has_chinese 必须命中假名（Ozon 拒「中文/日文」字符）与 CJK 扩展 A。"""
    assert has_chinese("白色"), "基础汉字必须命中"
    assert has_chinese("カテゴリ"), "假名（日文）必须命中——Ozon 拒日文字符"
    assert has_chinese("テスト商品"), "汉字+假名混合必须命中"
    assert has_chinese("\u3400"), "CJK 扩展 A（U+3400）必须命中"
    assert not has_chinese("Приёмник"), "西里尔不命中"
    assert not has_chinese("iPhone 15"), "拉丁不命中"
    assert not has_chinese("5x10x2"), "纯数字不命中"
    assert not has_chinese(""), "空串不命中"
    assert not has_chinese(None), "None 不命中"


# ═══════════════════════════════════════════════════════════════════════
# (1) error_repair_llm_node 翻译验收负检：混合结果不采纳 → 置空
# ═══════════════════════════════════════════════════════════════════════
def test_br_chinese_translation_rejects_mixed_result():
    """LLM 翻译返回「西里尔+中文混合」→ 验收必须拒绝，值置空（不写回混合值）。"""
    state = ValidationRetryLoopState(
        error_code="BR_chinese_hieroglyphs_in_attribute",
        ozon_payload={"items": [_base_item()]},
        final_attributes=[
            {"id": 4384, "value": "接收器包装清单", "dictionary_value_id": 0},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value=MIXED_RESULT):
        out = error_repair_llm_node(state)
    val = out.final_attributes[0]["value"]
    assert val == "", f"混合翻译结果必须拒绝置空，实际: {val!r}"


def test_br_chinese_translation_accepts_pure_cyrillic():
    """对照：纯西里尔翻译结果正常采纳（不误伤）。"""
    state = ValidationRetryLoopState(
        error_code="BR_chinese_hieroglyphs_in_attribute",
        ozon_payload={"items": [_base_item()]},
        final_attributes=[
            {"id": 4384, "value": "接收器包装清单", "dictionary_value_id": 0},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="Комплектация приёмника"):
        out = error_repair_llm_node(state)
    assert out.final_attributes[0]["value"] == "Комплектация приёмника"


# ═══════════════════════════════════════════════════════════════════════
# (2) revalidate 兜底通道：混合值送翻译；翻后仍含中文 → 置空
# ═══════════════════════════════════════════════════════════════════════
def test_revalidate_mixed_value_gets_translated():
    """final 含 «Приёмник 中文»（混合值）→ 必须送翻译产出纯俄语（不再直通）。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
        ])]},
        final_attributes=[
            {"id": 7300, "value": "Приёмник 中文", "dictionary_value_id": 0},
        ],
        attributes_schema=[],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="Приёмник"):
        out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert 7300 in attrs, "混合值属性必须进入合并（不被跳过）"
    val = attrs[7300]["values"][0]["value"]
    assert "中" not in val and "文" not in val, f"混合值必须翻译为纯俄语，实际: {val!r}"
    assert val == "Приёмник"


def test_revalidate_mixed_value_emptied_when_translation_fails():
    """翻译失败/结果仍含中文 → 置空（不再 continue 保留坏值直通重传）。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
        ])]},
        final_attributes=[
            {"id": 7300, "value": "Приёмник 中文", "dictionary_value_id": 0},
        ],
        attributes_schema=[],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="Приёмник 中文"):
        out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert 7300 in attrs, "置空通道必须产出属性行（空值），而不是 continue 丢弃"
    assert attrs[7300]["values"][0]["value"] == "", "翻译仍含中文 → 值必须置空"


def test_revalidate_pure_chinese_value_still_translated():
    """回归锁：纯中文值（旧行为已覆盖）仍走翻译通道不被本次改动破坏。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
        ])]},
        final_attributes=[
            {"id": 7300, "value": "纯中文描述", "dictionary_value_id": 0},
        ],
        attributes_schema=[],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="Чистое описание"):
        out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert attrs[7300]["values"][0]["value"] == "Чистое описание"


# ═══════════════════════════════════════════════════════════════════════
# (3) revalidate TRANSLATE_ATTR_IDS 翻译验收负检
# ═══════════════════════════════════════════════════════════════════════
def test_revalidate_translate_ids_rejects_mixed_result():
    """23171 纯中文值翻译返回混合结果 → 验收拒绝跳过（不把混合值带进 payload）。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
        ])]},
        final_attributes=[
            {"id": 23171, "value": "促销标签", "dictionary_value_id": 0},
        ],
        attributes_schema=[],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value=MIXED_RESULT):
        out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert 23171 not in attrs, f"混合翻译结果必须拒绝（属性跳过），实际: {attrs.get(23171)!r}"


# ═══════════════════════════════════════════════════════════════════════
# (4) revalidate 全属性中文终检（第二道本地闸，数值豁免）
# ═══════════════════════════════════════════════════════════════════════
def test_revalidate_final_gate_blocks_residual_chinese():
    """快照残留中文值（上游漏网）→ is_valid=False + errors 可读指认属性。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
            {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 0, "value": "红色"}]},
        ])]},
        final_attributes=[],
        attributes_schema=[],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    assert out.is_valid is False, "残留中文值必须被终检拦截"
    errs_txt = " | ".join(str(e.get("message", "")) for e in out.validation_errors)
    assert "10096" in errs_txt, f"错误信息必须指认属性 ID，实际: {errs_txt!r}"
    assert "中文" in errs_txt, f"错误信息必须可读（含中文残留语义），实际: {errs_txt!r}"


def test_revalidate_final_gate_passes_pure_cyrillic():
    """对照：纯西里尔/常规值不被终检误拦。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
            {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 61571, "value": "красный"}]},
        ])]},
        final_attributes=[],
        attributes_schema=[],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    errs_txt = " | ".join(str(e.get("message", "")) for e in out.validation_errors)
    assert out.is_valid is True, f"纯西里尔不应被拦，实际 errors: {errs_txt!r}"


def test_revalidate_final_gate_numeric_exemption():
    """数值属性可解析值（«5个»）豁免终检——对齐 ozon_validate 数值清洗契约。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
            {"complex_id": 0, "id": 8962, "values": [{"dictionary_value_id": 0, "value": "5个"}]},
        ])]},
        final_attributes=[],
        attributes_schema=[
            {"id": 8962, "name": "Единиц в одном товаре", "type": "Integer", "dictionary_id": 0},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    errs_txt = " | ".join(str(e.get("message", "")) for e in out.validation_errors)
    assert out.is_valid is True, f"数值属性可解析值应豁免（下游必清洗），实际 errors: {errs_txt!r}"


def test_revalidate_final_gate_numeric_unparseable_still_blocked():
    """数值属性不可解析坏值（«五个装»）仍拦——宁缺毋滥，与 ozon_validate 对齐。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "cn1"}]},
            {"complex_id": 0, "id": 8962, "values": [{"dictionary_value_id": 0, "value": "五个装"}]},
        ])]},
        final_attributes=[],
        attributes_schema=[
            {"id": 8962, "name": "Единиц в одном товаре", "type": "Integer", "dictionary_id": 0},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    assert out.is_valid is False, "数值属性不可解析坏值必须被终检拦截"


# ═══════════════════════════════════════════════════════════════════════
# (5) 9024(SKU) 含中文不豁免（对齐 prepare v0.16）
# ═══════════════════════════════════════════════════════════════════════
def test_9024_with_chinese_goes_through_translation():
    """9024 值含中文 → 不再硬豁免，进翻译通道（拉丁/数字 SKU 仍直传）。"""
    state = ValidationRetryLoopState(
        error_code="BR_chinese_hieroglyphs_in_attribute",
        ozon_payload={"items": [_base_item()]},
        final_attributes=[
            {"id": 9024, "value": "型号X100中文", "dictionary_value_id": 0},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="Модель X100"):
        out = error_repair_llm_node(state)
    val = out.final_attributes[0]["value"]
    assert val == "Модель X100", f"9024 含中文必须进翻译通道，实际: {val!r}"


def test_9024_latin_still_exempt():
    """对照：9024 拉丁/数字值保持硬豁免直传（不调 LLM）。"""
    state = ValidationRetryLoopState(
        error_code="BR_chinese_hieroglyphs_in_attribute",
        ozon_payload={"items": [_base_item()]},
        final_attributes=[
            {"id": 9024, "value": "SKU12345", "dictionary_value_id": 0},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
    )
    with mock.patch("utils.mxou_api.call_mxou_chat_api") as m_llm:
        out = error_repair_llm_node(state)
    m_llm.assert_not_called(), "拉丁 SKU 不应触发 LLM 翻译"
    assert out.final_attributes[0]["value"] == "SKU12345"

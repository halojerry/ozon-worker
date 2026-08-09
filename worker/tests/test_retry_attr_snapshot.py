#!/usr/bin/env python3
"""v0.31 T3: retry 循环属性快照合并（9782 首填值存活）行为测试。

根因（对抗团队实证）：
1. revalidate_node 用 state.final_attributes 整体覆盖 first_item["attributes"]
   —— 把首次 post-fill 的 9782 抹掉（retry 子图不重跑主图 prepare）
2. repair_prepare_node 只修 weight/尺寸/9048，从不重跑字典 post-fill
3. error_repair_llm_node 对空值字典属性盲取 search_result[0]（违反 retry 纪律）

本测试锁定修复后的行为（TDD RED 先行，纯行为断言，禁止 inspect 源码）：
(a) payload 含首填 9782(dict 970661099) + final_attributes 缺 9782 → revalidate 后 9782 存活
(b) 字典属性错误 → repair_prepare_node 产出已解析 dict 值（9782 只放行非危险安全默认）
(c) error_repair_llm_node 对多值字典属性不再盲选 search_result[0]
    (c1) 8229 有 type_id 匹配 → 解析到 type_id 值（非首值）
    (c2) 无任何语义命中 → 返回 None 走跳过（保持空值）
(d) remove_attrs 显式删除在合并中仍生效（不复活）
(e) 变体 items[1+] 同步（9048 复用 payload 已有值）

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_retry_attr_snapshot.py -v
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from graphs.validation_retry_loop import (
    ValidationRetryLoopState,
    revalidate_node,
    repair_prepare_node,
    repair_pricing_node,
    recheck_status_node,
    error_repair_llm_node,
    time as _retry_time,
)
from utils.http_session import session

# ── 危险等级字典值 ─────────────────────────────────────────────────────────
EXPLOSIVES = {"id": 970593901, "value": "Категория 1. Взрывчатые вещества"}
SAFE = {"id": 970593900, "value": "Не опасный груз"}
FIRST_FILLED_9782 = {"dictionary_value_id": 970661099, "value": "Не опасный груз"}


def _base_item(name="Средство от насекомых", offer_id="pest1", price="500", attributes=None):
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
# (a) 快照 9782 在 final_attributes 缺失时存活
# ═══════════════════════════════════════════════════════════════════════
def test_a_revalidate_keeps_snapshot_9782_when_final_missing():
    """payload 含首填 9782(dict 970661099) + final_attributes 缺 9782 →
    revalidate 合并后 9782 必须存活（revalidate 不再整体覆盖抹掉首填值）。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 9782, "values": [FIRST_FILLED_9782]},
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "pest1"}]},
        ])]},
        final_attributes=[
            {"id": 9048, "value": "pest1", "dictionary_value_id": 0},
        ],
        attributes_schema=[
            {"id": 9782, "name": "Класс опасности товара", "dictionary_id": 77, "is_required": True},
            {"id": 9048, "name": "Артикул", "dictionary_id": 0, "is_required": True},
        ],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert 9782 in attrs, "快照 9782 必须保留（final_attributes 缺失不应抹掉首填值）"
    assert attrs[9782]["values"][0]["dictionary_value_id"] == 970661099


def test_a_revalidate_keeps_snapshot_attr_not_in_final_generic():
    """快照有而 final_attributes 无的普通属性（如 10096 颜色）同样保留。"""
    state = ValidationRetryLoopState(
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 61571, "value": "черный"}]},
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "pest1"}]},
        ])]},
        final_attributes=[{"id": 9048, "value": "pest1", "dictionary_value_id": 0}],
        attributes_schema=[{"id": 9048, "name": "Артикул", "dictionary_id": 0, "is_required": True}],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert attrs[10096]["values"][0]["dictionary_value_id"] == 61571


# ═══════════════════════════════════════════════════════════════════════
# (b) 字典属性错误 → repair_prepare_node 重跑字典 post-fill
# ═══════════════════════════════════════════════════════════════════════
def test_b_repair_prepare_dict_postfill_resolves_9782_safe_default():
    """error_attribute_values_empty + attr=9782 → repair_prepare 语义解析出
    「非危险」安全默认并写入 payload（字典值列表含爆炸物在首位也不取首值）。"""
    state = ValidationRetryLoopState(
        error_code="error_attribute_values_empty",
        attribute_id=9782,
        ozon_payload={"items": [_base_item(attributes=[])]},
        dictionary_values={"9782": [EXPLOSIVES, SAFE]},
        attributes_schema=[{"id": 9782, "name": "Класс опасности товара", "dictionary_id": 77, "is_required": True}],
        description_category_id="17028746", type_id="92780",
    )
    out = repair_prepare_node(state)
    attrs = {int(a["id"]): a for a in out.ozon_payload["items"][0]["attributes"]}
    assert 9782 in attrs, "repair_prepare 应产出已解析的 9782"
    assert attrs[9782]["values"][0]["dictionary_value_id"] == 970593900
    assert attrs[9782]["values"][0]["value"] == "Не опасный груз"


# ═══════════════════════════════════════════════════════════════════════
# (c) error_repair_llm_node 空值字典属性：统一语义解析，绝不盲取 search_result[0]
# ═══════════════════════════════════════════════════════════════════════
def _llm_state(attr_id, attr_name, dict_id, dict_vals, type_id, product_name="USB 迷你风扇"):
    return ValidationRetryLoopState(
        error_code="BR_chinese_hieroglyphs_in_attribute",
        attribute_id=attr_id,
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028743", type_id=type_id,
        product_name=product_name,
        ozon_payload={"items": [_base_item()]},
        final_attributes=[{"id": attr_id, "value": "", "dictionary_value_id": 0}],
        attributes_schema=[{"id": attr_id, "name": attr_name, "dictionary_id": dict_id}],
        dictionary_values={str(attr_id): dict_vals},
    )


def test_c1_error_repair_8229_resolves_by_type_id_not_first():
    """多值 8229 空值 → 按 type_id 匹配命中（148495146），绝不取首值 91965「套娃」。"""
    out = error_repair_llm_node(_llm_state(
        8229, "Тип", 504,
        [{"id": 91965, "value": "套娃"}, {"id": 148495146, "value": "手持风扇"}],
        type_id="148495146",
    ))
    attr = next(a for a in out.final_attributes if (a.get("id") or a.get("attribute_id")) == 8229)
    assert attr["dictionary_value_id"] == 148495146, f"应解析到 type_id 值, 实际 {attr}"
    assert attr["value"] == "手持风扇"


def test_c2_error_repair_no_semantic_match_skips():
    """多值字典属性无语义命中（非品牌/性别/尺码/8229/9782）→ 返回 None 走跳过，
    保持空值，绝不取 search_result[0]。"""
    out = error_repair_llm_node(_llm_state(
        9999, "Материал", 13,
        [{"id": 300, "value": "塑料"}, {"id": 301, "value": "金属"}],
        type_id="92780",
    ))
    attr = next(a for a in out.final_attributes if (a.get("id") or a.get("attribute_id")) == 9999)
    assert attr["dictionary_value_id"] == 0
    assert attr["value"] == ""


# ═══════════════════════════════════════════════════════════════════════
# (d) remove_attrs 显式删除在合并中仍生效
# ═══════════════════════════════════════════════════════════════════════
def test_d_revalidate_remove_attrs_not_resurrected_in_merge():
    """repair_metadata.remove_attrs=[85,5076] → 即使 final_attributes 含 85，
    合并后 85/5076 不得复活。"""
    state = ValidationRetryLoopState(
        repair_metadata={"remove_attrs": [85, 5076]},
        ozon_payload={"items": [_base_item(attributes=[
            {"complex_id": 0, "id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
            {"complex_id": 0, "id": 5076, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "pest1"}]},
        ])]},
        final_attributes=[
            {"id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801},
            {"id": 5076, "value": "Нет бренда", "dictionary_value_id": 126745801},
            {"id": 9048, "value": "pest1", "dictionary_value_id": 0},
        ],
        attributes_schema=[{"id": 9048, "name": "Артикул", "dictionary_id": 0, "is_required": True}],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
    )
    out = revalidate_node(state)
    ids = {int(a["id"]) for a in out.ozon_payload["items"][0]["attributes"]}
    assert 85 not in ids, "remove_attrs 属性 85 不得在合并中复活"
    assert 5076 not in ids
    assert 9048 in ids


# ═══════════════════════════════════════════════════════════════════════
# (e) 变体 items[1+] 同步（9048 复用 payload 已有值）
# ═══════════════════════════════════════════════════════════════════════
def test_e_revalidate_variant_sync_keeps_9048_payload_value():
    """多变体：items[1+] 同步共享属性（9048 复用 payload 已有值 ABC123），
    保留变体特有颜色 10096。"""
    item0 = _base_item(name="Носки", offer_id="a", price="100", attributes=[
        {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "ABC123"}]},
        {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 61571, "value": "черный"}]},
    ])
    item1 = _base_item(name="Носки", offer_id="b", price="110", attributes=[
        {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "OLD"}]},
        {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 61572, "value": "белый"}]},
    ])
    state = ValidationRetryLoopState(
        ozon_payload={"items": [item0, item1]},
        final_attributes=[{"id": 9048, "value": "ABC123", "dictionary_value_id": 0}],
        attributes_schema=[{"id": 9048, "name": "Артикул", "dictionary_id": 0, "is_required": True}],
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17027918", type_id="971311385",
    )
    out = revalidate_node(state)
    item1_attrs = {int(a["id"]): a for a in out.ozon_payload["items"][1]["attributes"]}
    assert item1_attrs[9048]["values"][0]["value"] == "ABC123", "变体 9048 必须复用 payload 已有值（防重译不一致）"
    assert item1_attrs[10096]["values"][0]["dictionary_value_id"] == 61572, "变体特有颜色必须保留"


# ═══════════════════════════════════════════════════════════════════════
# (f) ValidationRetryLoopState 缺字段 bug（Pydantic v2 "has no field"）
# 根因：recheck_status_node L2505 `state.moderation_status = mod_status` 抛
#   `"ValidationRetryLoopState" object has no field "moderation_status"` 被
#   L2517 `except Exception` 吞掉 → approved/declined 分支永不执行 → 轮询
#   60×5s 超时置 pending_moderation；repair_pricing L1556 failed_stage 同理崩。
# ═══════════════════════════════════════════════════════════════════════
def test_state_has_moderation_status_field():
    """ValidationRetryLoopState 补字段：赋值 moderation_status 不抛异常且读回正确。"""
    state = ValidationRetryLoopState()
    state.moderation_status = "approved"
    assert state.moderation_status == "approved"


def test_state_has_failed_stage_field():
    """ValidationRetryLoopState 补字段：赋值 failed_stage 不抛异常。"""
    state = ValidationRetryLoopState()
    state.failed_stage = "pricing"
    assert state.failed_stage == "pricing"


class _FakeResp:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


def _run_recheck(moderate_status):
    """跑 recheck_status_node：mock session.post —— import/info 返回 imported+product_id，
    info/list 返回指定 moderate_status。返回修复后的 state。"""
    import_resp = _FakeResp({"result": {"items": [{"status": "imported", "product_id": "111", "errors": []}]}})
    mod_resp = _FakeResp({"items": [{"statuses": {"moderate_status": moderate_status}, "errors": []}]})

    def side_effect(url, **kwargs):
        if "info/list" in url:
            return mod_resp
        return import_resp

    state = ValidationRetryLoopState(
        task_id="1234567890", token="t", ozon_client_id="c", ozon_api_key="k",
        ozon_payload={"items": []},
    )
    with mock.patch.object(session, "post", side_effect=side_effect), \
         mock.patch.object(_retry_time, "sleep"):
        out = recheck_status_node(state)
    return out


def test_recheck_status_sets_moderation_on_approved():
    """mock 返回 moderate_status="approved" → state.moderation_status=="approved"、
    upload_status=="success"（修复前缺字段被 except 吞 → 轮询超时 pending_moderation）。"""
    out = _run_recheck("approved")
    assert out.moderation_status == "approved"
    assert out.upload_status == "success"


def test_recheck_status_sets_moderation_on_declined():
    """mock 返回 moderate_status="declined" → state.moderation_status=="declined"、
    upload_status=="failed"。"""
    out = _run_recheck("declined")
    assert out.moderation_status == "declined"
    assert out.upload_status == "failed"


def test_repair_pricing_sets_failed_stage():
    """pricing_info 空 + force_reprice → PRICING_FAILED 阻断分支写 failed_stage="pricing" 不抛异常。"""
    state = ValidationRetryLoopState(
        error_code="price_out_of_range",
        pricing_info={},
        ozon_payload={"items": [{"price": "25290"}]},
    )
    out = repair_pricing_node(state)
    assert out.failed_stage == "pricing"
    assert "PRICING_FAILED" in out.error_message


if __name__ == "__main__":
    import traceback

    failed = total = 0
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

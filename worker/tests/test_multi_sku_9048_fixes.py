#!/usr/bin/env python3
"""fix/multi-sku-9048-fixes 回归：9048-only 合卡终验暴露的两处管线侧根因。

终验实录（2026-10，双店 50 卡验证）：
① retry-loop「描述含拉丁 → LLM 重生成标题」修复路径在 multi_sku（items>1）
  下把所有 items 的 name 整体抹平成同一个标题——9048-only 模式的变体区分
  恰恰靠 per-item 色尾缀，抹平后 items 同质 Ozon 不并卡（7 变体「✅ 标题已
  修复（所有变体）」→ 6 张独立卡）。守卫 = utils/multi_sku_title_repair：
  只修报错 item + 保原尾缀；不可定位/不可还原 → 降级 per-item 确定性清理，
  绝不抹平。单 SKU 路径零变化（回归锁）。
② utils/multi_sku_expand.resolve_color_attr_id 子串误选：类目 17028935 下
  启发 `"цвет" in name` 选中 5646 «Количество цветов»（颜色**计数**属性，
  dictionary_id=0）→ 7/7 color_no_match 且 schema_has_color_dict_attr 保守
  True 否决 9048-only 降级。收紧 = 名称词形 «цвет» 单数主词（\b 词边界）+
  显式 dictionary_id > 0。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_multi_sku_9048_fixes.py -q
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.multi_sku_title_repair import (
    ACTION_DETERMINISTIC,
    ACTION_TARGETED,
    ACTION_UNCHANGED,
    apply_title_repair,
    derive_variant_suffixes,
    deterministic_title_clean,
    find_errored_item_indexes,
)
from utils.multi_sku_expand import (
    resolve_color_attr_id,
    schema_has_color_dict_attr,
    should_degrade_to_9048_only,
)
from utils.multi_sku_expand import MultiSkuExpansion


# ═══════════════════════════════════════════════════════════
# 修① 纯函数层：尾缀还原 / 报错 item 定位 / 确定性清理
# ═══════════════════════════════════════════════════════════

_MS_NAMES = [
    "Пластик для 3D-принтера, 1 кг, белый",
    "Пластик для 3D-принтера, 1 кг, черный",
    "Пластик для 3D-принтера, 1 кг, красный",
]


def test_derive_variant_suffixes_base_with_comma():
    """base 名自身含「, 」（…, 1 кг）→ 公共前缀收口到最后一个边界，尾缀纯色名。"""
    assert derive_variant_suffixes(_MS_NAMES) == {0: "белый", 1: "черный", 2: "красный"}


def test_derive_variant_suffixes_unrecoverable_shapes():
    # items 全同名（颜色字典模式同名展开 / 已被历史抹平）→ 空（宁缺毋滥）
    assert derive_variant_suffixes(["Набор", "Набор", "Набор"]) == {}
    # 公共前缀内无「, 」分隔边界 → 空
    assert derive_variant_suffixes(["Набор белый", "Набор черный"]) == {}
    # 单 item 无从谈尾缀 → 空
    assert derive_variant_suffixes(["Набор, белый"]) == {}
    # 任一 item 无尾段 → 整体空（结构不可辨认，绝不猜）
    assert derive_variant_suffixes(["Набор, белый", "Набор, черный", "Набор"]) == {}


def test_find_errored_item_indexes_item_ref_and_offer():
    items = [{"offer_id": "sku_a1"}, {"offer_id": "sku_b2"}, {"offer_id": "sku_c3"}]
    assert find_errored_item_indexes(items, ["item[1].name 含拉丁字母"]) == [1]
    assert find_errored_item_indexes(items, ["ошибка в sku_b2"]) == [1]
    # 多引用取并集
    assert find_errored_item_indexes(items, ["item[0]", "offer sku_c3 отклонён"]) == [0, 2]
    # 无引用 → 空
    assert find_errored_item_indexes(items, ["описание содержит латиницу"]) == []
    assert find_errored_item_indexes(items, []) == []
    # 越界引用被过滤
    assert find_errored_item_indexes(items, ["item[9] bad"]) == []


def test_find_errored_item_indexes_base_offer_substring_no_false_positive():
    """base offer 子串不误配：1083073125898 ≠ 1083073125898_1（词边界）。"""
    items = [{"offer_id": "1083073125898_1"}, {"offer_id": "1083073125898_2"}]
    assert find_errored_item_indexes(items, ["sku=1083073125898_2 invalid"]) == [1]
    assert find_errored_item_indexes(items, ["sku=1083073125898 invalid"]) == []


def test_deterministic_title_clean():
    assert deterministic_title_clean("Набор для слайма DIY, белый") == "Набор для слайма, белый"
    assert deterministic_title_clean("Пластик PLA 1 кг, красный  ") == "Пластик 1 кг, красный"
    # 删词后重复分隔归并
    assert deterministic_title_clean("Набор DIY, Kit, белый") == "Набор, белый"
    # 清空/无西里尔 → 原值返回（绝不产生空名）
    assert deterministic_title_clean("DIY Kit Box") == "DIY Kit Box"
    assert deterministic_title_clean("") == ""
    # 无拉丁 → 原样（去尾空白）
    assert deterministic_title_clean("Набор, белый  ") == "Набор, белый"


def test_apply_title_repair_targeted_keeps_suffix():
    items = [{"offer_id": "s1", "name": _MS_NAMES[0]},
             {"offer_id": "s2", "name": _MS_NAMES[1]},
             {"offer_id": "s3", "name": _MS_NAMES[2]}]
    act = apply_title_repair(items, "Новый заголовок набора",
                             error_message="item[1].name содержит латиницу")
    assert act == ACTION_TARGETED
    # 只修报错 item，且修复产物保留该 item 原尾缀
    assert items[1]["name"] == "Новый заголовок набора, черный"
    # 其余 items 原名不动
    assert items[0]["name"] == _MS_NAMES[0]
    assert items[2]["name"] == _MS_NAMES[2]


def test_apply_title_repair_targeted_via_offer_and_no_double_suffix():
    items = [{"offer_id": "sku_s1", "name": _MS_NAMES[0]},
             {"offer_id": "sku_s2", "name": _MS_NAMES[1]}]
    act = apply_title_repair(items, "Новый заголовок, черный",
                             errors=[{"code": "X", "texts": {"message": "offer sku_s2 bad"}}])
    assert act == ACTION_TARGETED
    # repaired_title 已含尾缀 → 不重复追加
    assert items[1]["name"] == "Новый заголовок, черный"


def test_apply_title_repair_targeted_mixed_base_after_prior_repair():
    """重试轮间混合基名（上一轮已 targeted 修过 item1）→ 末段兜底仍可定位
    尾缀，本轮报错 item 照样 targeted 修复（不降级、不抹平）。"""
    names = [
        "Новый заголовок набора, черный",           # 上一轮已修
        "Пластик для 3D-принтера, 1 кг, белый",
        "Пластик для 3D-принтера, 1 кг, красный",
    ]
    items = [{"offer_id": "s1", "name": names[0]},
             {"offer_id": "s2", "name": names[1]},
             {"offer_id": "s3", "name": names[2]}]
    act = apply_title_repair(items, "Второй новый заголовок",
                             error_message="item[2].name bad")
    assert act == ACTION_TARGETED
    assert items[2]["name"] == "Второй новый заголовок, красный"
    assert items[0]["name"] == names[0]
    assert items[1]["name"] == names[1]


def test_apply_title_repair_downgrade_deterministic_no_flatten():
    """终验实录形态：描述含拉丁、错误文本无 item 引用 → 绝不抹平重生成，
    LLM 标题弃用，per-item 确定性清理（各自尾缀原样保留）。"""
    names = ["Набор для слайма DIY, белый", "Набор для слайма DIY, черный"]
    items = [{"offer_id": "s1", "name": names[0]}, {"offer_id": "s2", "name": names[1]}]
    act = apply_title_repair(items, "Совершенно новый заголовок от LLM",
                             error_message="описание содержит латиницу")
    assert act == ACTION_DETERMINISTIC
    # 绝不抹平：两个 name 依旧互异（尾缀保留），且不是 LLM 标题
    assert items[0]["name"] == "Набор для слайма, белый"
    assert items[1]["name"] == "Набор для слайма, черный"
    assert all("Совершенно новый" not in it["name"] for it in items)


def test_apply_title_repair_identical_names_never_flatten():
    """尾缀不可还原（同名 items / 历史抹平后）→ 同样降级，绝不引入抹平。"""
    items = [{"offer_id": "s1", "name": "Одинаковое имя"},
             {"offer_id": "s2", "name": "Одинаковое имя"}]
    act = apply_title_repair(items, "LLM заголовок", error_message="item[0] bad")
    assert act == ACTION_UNCHANGED  # 无拉丁可清 → 无变化
    assert items[0]["name"] == "Одинаковое имя"
    assert items[1]["name"] == "Одинаковое имя"


# ═══════════════════════════════════════════════════════════
# 修① 行为级：error_repair_llm_node 接线（multi_sku 守卫 + 单 SKU 回归锁）
# ═══════════════════════════════════════════════════════════

from graphs.validation_retry_loop import ValidationRetryLoopState, error_repair_llm_node


def _ms_llm_state(items, error_message, llm_result):
    return ValidationRetryLoopState(
        error_code="UNKNOWN", attribute_id=0, error_message=error_message,
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028935", type_id="200001721",
        product_name="Пластик",
        ozon_payload={"items": items},
        final_attributes=[{"id": 4180, "value": "Старый заголовок", "dictionary_value_id": 0}],
        attributes_schema=[],
        draft={"item_id": "1083073125898", "supplier": "Demo", "title": "塑料"},
    ), llm_result


def test_retry_node_multi_sku_targeted_title_repair_keeps_suffixes():
    """items>1 + 错误文本 item[1] 引用 → 只修 item1，尾缀保留，其余不动。"""
    items = [
        {"offer_id": "1083073125898_1", "name": _MS_NAMES[0], "price": "100", "attributes": []},
        {"offer_id": "1083073125898_2", "name": _MS_NAMES[1], "price": "100", "attributes": []},
        {"offer_id": "1083073125898_3", "name": _MS_NAMES[2], "price": "100", "attributes": []},
    ]
    state, llm_json = _ms_llm_state(
        items, "item[1].name содержит латиницу",
        json.dumps({"corrected_title": "Новый заголовок набора", "repair_explanation": "x"}))
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm", return_value=llm_json):
        out = error_repair_llm_node(state)
    names = [it["name"] for it in out.ozon_payload["items"]]
    assert names == [
        "Пластик для 3D-принтера, 1 кг, белый",
        "Новый заголовок набора, черный",
        "Пластик для 3D-принтера, 1 кг, красный",
    ], "只修报错 item + 保留原色尾缀（终验实录 6 张独立卡根因）"
    assert out.final_attributes[0]["value"] == "Новый заголовок набора"  # 4180 同步采纳标题


def test_retry_node_multi_sku_description_decline_never_flattens():
    """终验实录同型：描述含拉丁（无 item 引用）+ LLM 附带 corrected_title →
    绝不抹平 7 变体标题；LLM 标题弃用，per-item 确定性清理保尾缀。"""
    items = [
        {"offer_id": "s1", "name": "Набор для слайма DIY, белый", "price": "100",
         "attributes": [{"complex_id": 0, "id": 4191,
                         "values": [{"dictionary_value_id": 0, "value": "<p>DIY slime kit</p>"}]}]},
        {"offer_id": "s2", "name": "Набор для слайма DIY, черный", "price": "100",
         "attributes": [{"complex_id": 0, "id": 4191,
                         "values": [{"dictionary_value_id": 0, "value": "<p>DIY slime kit</p>"}]}]},
        {"offer_id": "s3", "name": "Набор для слайма DIY, красный", "price": "100",
         "attributes": [{"complex_id": 0, "id": 4191,
                         "values": [{"dictionary_value_id": 0, "value": "<p>DIY slime kit</p>"}]}]},
    ]
    state, llm_json = _ms_llm_state(
        items, "审核被拒: [{'code': 'BR_chinese_hieroglyphs', 'message': 'описание содержит латиницу'}]",
        json.dumps({"corrected_title": "Единый заголовок от LLM",
                    "corrected_description": "<p>Русское описание без латиницы</p>",
                    "repair_explanation": "x"}))
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm", return_value=llm_json):
        out = error_repair_llm_node(state)
    names = [it["name"] for it in out.ozon_payload["items"]]
    assert names == [
        "Набор для слайма, белый",
        "Набор для слайма, черный",
        "Набор для слайма, красный",
    ], "绝不抹平重生成：尾缀保留、items 互异（可并卡），LLM 标题整体弃用"
    # 弃用标题不同步 4180
    assert out.final_attributes[0]["value"] == "Старый заголовок"
    # 描述修复照常生效（本修复的真正靶位）
    assert "Русское описание" in out.ozon_payload["items"][0]["attributes"][0]["values"][0]["value"]


def test_retry_node_single_sku_title_repair_zero_change():
    """单 SKU 路径回归锁：items==1 → name/4180 = LLM 标题（逐字旧行为）。"""
    items = [{"offer_id": "x", "name": "Старый заголовок", "price": "100", "attributes": []}]
    state, llm_json = _ms_llm_state(
        items, "some generic error",
        json.dumps({"corrected_title": "Новый заголовок", "repair_explanation": "x"}))
    with mock.patch("graphs.validation_retry_loop._call_mxou_llm", return_value=llm_json):
        out = error_repair_llm_node(state)
    assert out.ozon_payload["items"][0]["name"] == "Новый заголовок"
    assert out.final_attributes[0]["value"] == "Новый заголовок"


# ═══════════════════════════════════════════════════════════
# 修② 5646 «Количество цветов» 误选 + 词形辨析真值表
# ═══════════════════════════════════════════════════════════

from utils.multi_sku_expand import _is_color_attr_name

# 终验实录 schema 形态（类目 17028935）：5646 计数属性混在表里
SCHEMA_INCIDENT_17028935 = [
    {"id": 4180, "name": "Название", "dictionary_id": 0},
    {"id": 4191, "name": "Аннотация", "dictionary_id": 0},
    {"id": 9048, "name": "Название модели", "dictionary_id": 0},
    {"id": 5646, "name": "Количество цветов", "dictionary_id": 0},
]


def test_word_form_truth_table_color_attr_name():
    """词形辨析真值表：单数主格 «цвет» 独立词命中；复数/派生词形不命中。"""
    # 命中（真颜色属性名）
    for name in ("Цвет", "цвет", "Цвет товара", "Цвет производителя",
                 "цвет бренда", "颜色"):
        assert _is_color_attr_name(name) is True, name
    # 不命中（5646 实录 + 同类复数/派生/自由文本词形）
    for name in ("Количество цветов", "количество цветов", "Название цвета",
                 "цвета", "цветов", "цветной", "многоцветный",
                 "Название модели", "Аннотация", ""):
        assert _is_color_attr_name(name) is False, name


def test_resolve_color_attr_id_rejects_count_attr_5646():
    """5646 «Количество цветов»（dict_id=0）不得被选为颜色属性（终验实录）。
    即便排在真颜色属性之前也不得遮蔽。"""
    schema = [
        {"id": 5646, "name": "Количество цветов", "dictionary_id": 0},
        {"id": 10096, "name": "Цвет", "dictionary_id": 1},
    ]
    assert resolve_color_attr_id([], schema) == 10096
    # 5646 缺 dictionary_id 键同样不选（无法证明是字典属性）
    assert resolve_color_attr_id([], [{"id": 5646, "name": "Количество цветов"}]) == 10096
    # 名称词形命中但 dict_id=0（自由文本颜色名）→ 不选
    assert resolve_color_attr_id([], [{"id": 8271, "name": "Цвет товара", "dictionary_id": 0}]) == 10096
    # 真颜色字典属性仍选中
    assert resolve_color_attr_id([], [{"id": 10097, "name": "Цвет производителя",
                                       "dictionary_id": 4}]) == 10097


def test_schema_has_color_dict_attr_incident_shape_is_false():
    """终验实录 schema（含 5646）→ 确无颜色字典属性 → False（不再保守 True
    否决降级）。"""
    assert schema_has_color_dict_attr(SCHEMA_INCIDENT_17028935) is False


def test_should_degrade_incident_shape_no_longer_vetoed():
    """终验实录链路端到端：5646 被选 → 7/7 color_no_match 全剔除 + 实录
    schema → 降级必须放行（此前被 schema_has_color_dict_attr=True 否决）。"""
    all_dropped = MultiSkuExpansion(items=[], dropped=[
        {"index": i, "reason": "color_no_match"} for i in range(7)])
    ok, why = should_degrade_to_9048_only(all_dropped, SCHEMA_INCIDENT_17028935)
    assert ok and why == "no_color_dict_schema_all_color_dropped"


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

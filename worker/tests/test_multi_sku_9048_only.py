"""9048-only 降级合卡回归（feat/multi-sku-9048-only，双 gate 铁证 1 收尾）。

铁证：3D 耗材类目（17028935/*、200001721/*）schema 无 Цвет 颜色字典属性
（4180 在该类目下是「Название」文本）→ multi_sku_expand 颜色字典路径 7/7
color_no_match 全剔除回退单 SKU；而竞品 4929923490 的多变体形态实测是
**9048 合并**（同 9048 多 items，非颜色字典维度）。

覆盖面：
  1. 纯函数 expand_items_9048_only：items=N / 9048 全 items 同值 / 不写颜色
     属性 / 标题俄语色尾缀 / offer 唯一 / 每 variant 主图 / price delta；
  2. 宁缺毋滥：无法俄语化的色名剔除（绝不中文尾缀）+ 保留 <2 变体放弃降级；
  3. should_degrade_to_9048_only 触发闸真值表（schema 判据 + 全 color_* 剔除）；
  4. prepare 集成：无颜色字典类目 → 9048-only 合卡；有颜色字典类目 → 现行
     路径零变化（全剔除仍回退单 SKU）；全部无法译色 → 回退单 SKU 现状。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_multi_sku_9048_only.py -q
"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.multi_sku_expand import (
    MIN_MERGE_VARIANTS_9048_ONLY,
    derive_ru_color_suffix,
    expand_items_9048_only,
    schema_has_color_dict_attr,
    should_degrade_to_9048_only,
)

COS_AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/gen.jpg"
COS_AI_2 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/gen2.jpg"
ALICDN = "https://cbu01.alicdn.com/img/ibank/color1.jpg"

# 3D 耗材类目 schema 实证形态：无 Цвет 颜色字典属性（4180=Название 文本）
SCHEMA_NO_COLOR_DICT = [
    {"id": 4180, "name": "Название", "dictionary_id": 0, "is_required": True},
    {"id": 4191, "name": "Аннотация", "dictionary_id": 0, "is_required": False},
    {"id": 9048, "name": "Название модели", "dictionary_id": 0, "is_required": True},
]
# 有颜色字典属性类目（现行路径）
SCHEMA_WITH_COLOR_DICT = [
    {"id": 10096, "name": "Цвет", "dictionary_id": 1, "is_required": False},
    {"id": 4191, "name": "Аннотация", "dictionary_id": 0, "is_required": False},
]


def _base_item_9048(**kw):
    """9048-only 场景 base item：**无颜色属性**（该类目 schema 没有），
    4180=Название 文本（3D 耗材实证）。"""
    item = {
        "name": "Пластик для 3D-принтера, 1 кг",
        "offer_id": "1083073125898_0",
        "price": "100",
        "old_price": "130",
        "primary_image": COS_AI,
        "images": [COS_AI, COS_AI_2],
        "attributes": [
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "1083073125898"}]},
            {"complex_id": 0, "id": 4180, "values": [{"dictionary_value_id": 0, "value": "PLA 1 кг"}]},
            {"complex_id": 0, "id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>Описание</p>"}]},
        ],
    }
    item.update(kw)
    return item


def _zh_variants():
    """1688 采集腿典型形态：中文色名、无 color_dict_id（有 id 也验不到——类目无字典）。"""
    return [
        {"sku_id": "1083073125898_1", "color": "白色", "price_delta_cny": 0, "ref_image": ALICDN},
        {"sku_id": "1083073125898_2", "color": "黑色", "price_delta_cny": 2, "ref_image": ALICDN},
        {"sku_id": "1083073125898_3", "color": "红色", "price_delta_cny": -1},
    ]


# ═══════════════════════════════════════════════════════════
# 1. 纯函数：展开形状
# ═══════════════════════════════════════════════════════════

def test_9048_only_expand_shape_no_color_attr_title_suffix():
    ms = expand_items_9048_only(
        _base_item_9048(), _zh_variants(),
        shared_images=[COS_AI, COS_AI_2],
        variant_primary_images=[COS_AI + "?v=1", "", ""],
        main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="1083073125898", base_offer_id="1083073125898",
    )
    assert len(ms.items) == 3
    assert ms.marks["mode"] == "merge_9048_only" and ms.marks["kept"] == 3
    offers = [it["offer_id"] for it in ms.items]
    assert offers == ["1083073125898_1", "1083073125898_2", "1083073125898_3"]
    # 9048 全 items 同值（合卡键）
    v9048 = []
    for it in ms.items:
        got = [a for a in it["attributes"] if a["id"] == 9048]
        assert len(got) == 1 and len(got[0]["values"]) == 1
        v9048.append(got[0]["values"][0]["value"])
    assert v9048 == ["1083073125898"] * 3
    # 该类目无颜色属性 → items 恒无 COLOR_ATTR_IDS 属性（4180=Название 文本保留）
    for it in ms.items:
        assert not any(a["id"] in (10096, 10097, 10098, 10099) for a in it["attributes"])
        assert any(a["id"] == 4180 for a in it["attributes"])
    # 变体区分 = 标题俄语色尾缀（中文经 ZH→RU 桥接；绝不残留中文）
    names = [it["name"] for it in ms.items]
    assert names == [
        "Пластик для 3D-принтера, 1 кг, белый",
        "Пластик для 3D-принтера, 1 кг, черный",
        "Пластик для 3D-принтера, 1 кг, красный",
    ]
    assert not any(any('\u4e00' <= c <= '\u9fff' for c in n) for n in names)
    # price delta 生效（主定价±delta，不重算公式）
    assert [it["price"] for it in ms.items] == ["100", "102", "99"]
    # 每 variant 主图一张（生图失败回落 base 主图）
    assert ms.items[0]["primary_image"] == COS_AI + "?v=1"
    assert ms.items[1]["primary_image"] == COS_AI
    for it in ms.items:
        assert it["images"][0] == it["primary_image"]


def test_9048_only_base_misplaced_color_attr_stripped_defensively():
    """base 误带颜色属性（异常上游）→ 9048-only 防御性剥离（该类目没有）。"""
    base = _base_item_9048(attributes=_base_item_9048()["attributes"] + [
        {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 61571, "value": "белый"}]},
    ])
    ms = expand_items_9048_only(
        base, _zh_variants()[:2],
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="1083073125898", base_offer_id="1083073125898",
    )
    assert len(ms.items) == 2
    for it in ms.items:
        assert not any(a["id"] == 10096 for a in it["attributes"]), "误带颜色属性必须剥离"


# ═══════════════════════════════════════════════════════════
# 2. 宁缺毋滥：无法译色剔除 / 西里尔直用 / <2 变体放弃降级
# ═══════════════════════════════════════════════════════════

def test_9048_only_untranslatable_and_missing_dropped():
    variants = [
        {"sku_id": "s_1", "color": "荧光幻彩"},   # 桥不中 → 剔除（绝不中文尾缀）
        {"sku_id": "s_2", "color": "Metallic Blue"},  # 拉丁 → 剔除（绝不音译编造）
        {"sku_id": "s_3", "color": "белый"},      # 西里尔原文直用
        {"sku_id": "s_4", "color": "深蓝色"},      # 复合色桥接
        {"sku_id": "s_5"},                        # 无色 → color_missing
    ]
    ms = expand_items_9048_only(
        _base_item_9048(), variants,
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="item1", base_offer_id="item1",
    )
    assert [it["offer_id"] for it in ms.items] == ["s_3", "s_4"]
    assert ms.items[0]["name"].endswith(", белый")
    assert ms.items[1]["name"].endswith(", темно-синий")
    reasons = [d["reason"] for d in ms.dropped]
    assert reasons == ["color_untranslatable", "color_untranslatable", "color_missing"]
    for it in ms.items:
        assert not any('\u4e00' <= c <= '\u9fff' for c in it["name"])


def test_9048_only_below_min_two_variants_aborts_degrade():
    """可译变体仅 1 个（<2）→ items 空（调用方回退单 SKU），宁缺毋滥。"""
    variants = [
        {"sku_id": "s_1", "color": "白色"},        # 唯一可译
        {"sku_id": "s_2", "color": "荧光幻彩"},    # 剔除
    ]
    ms = expand_items_9048_only(
        _base_item_9048(), variants,
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="item1", base_offer_id="item1",
    )
    assert ms.items == []
    assert any(d["reason"] == "below_min_merge_variants" for d in ms.dropped)
    assert ms.marks["kept"] == 0
    assert MIN_MERGE_VARIANTS_9048_ONLY == 2


def test_derive_ru_color_suffix_ningque_wulan():
    assert derive_ru_color_suffix("белый") == "белый"          # 西里尔直用
    assert derive_ru_color_suffix("黑色") == "черный"           # ZH→RU 桥
    assert derive_ru_color_suffix("浅灰色") == "светло-серый"   # 复合色桥
    assert derive_ru_color_suffix("荧光幻彩") == ""             # 桥不中 → 空（剔除）
    assert derive_ru_color_suffix("Blue") == ""                # 拉丁 → 空（剔除）
    assert derive_ru_color_suffix("") == ""                    # 缺色 → 空


# ═══════════════════════════════════════════════════════════
# 3. 触发闸真值表（schema 判据 + 全 color_* 剔除）
# ═══════════════════════════════════════════════════════════

def _expansion_factory(items, dropped):
    from utils.multi_sku_expand import MultiSkuExpansion
    return MultiSkuExpansion(items=items, dropped=dropped)


def test_should_degrade_gate_truth_table():
    # 全 color_no_match 剔除 + 无颜色字典 schema → 降级（双 gate 铁证场景）
    all_dropped = _expansion_factory([], [
        {"index": i, "reason": "color_no_match"} for i in range(7)])
    ok, why = should_degrade_to_9048_only(all_dropped, SCHEMA_NO_COLOR_DICT)
    assert ok and why == "no_color_dict_schema_all_color_dropped"
    # schema 有颜色字典（瞬时字典缓存缺失类）→ 不降级（回退单 SKU 等重试）
    ok, why = should_degrade_to_9048_only(all_dropped, SCHEMA_WITH_COLOR_DICT)
    assert not ok and why == "schema_has_color_dict_attr"
    # 混入结构性剔除（offer 重复）→ 不降级
    mixed = _expansion_factory([], [
        {"index": 0, "reason": "color_no_match"},
        {"index": 1, "reason": "offer_duplicate"},
    ])
    ok, why = should_degrade_to_9048_only(mixed, SCHEMA_NO_COLOR_DICT)
    assert not ok and why == "structural_drop_present"
    # 部分保留（items 非空）→ 不降级（颜色字典路径已成功）
    partial = _expansion_factory([{"offer_id": "s_1"}], [{"index": 1, "reason": "color_no_match"}])
    ok, _ = should_degrade_to_9048_only(partial, SCHEMA_NO_COLOR_DICT)
    assert not ok
    # 4180=Название 文本（dictionary_id=0）不算颜色字典属性
    assert schema_has_color_dict_attr(SCHEMA_NO_COLOR_DICT) is False
    assert schema_has_color_dict_attr(SCHEMA_WITH_COLOR_DICT) is True
    assert schema_has_color_dict_attr([]) is False


# ═══════════════════════════════════════════════════════════
# 4. prepare 集成：降级生效 + 有字典类目零变化 + 全无法译色回退
# ═══════════════════════════════════════════════════════════

from graphs.state import PrepareOzonUploadInput
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node


def _draft(**kw):
    d = {
        "item_id": "1083073125898",
        "sku_id": "1083073125898_0",
        "title": "Пластик для 3D-принтера, 1 кг",
        "images": [ALICDN],
        "weight": 1100,
        "dimensions": {"length": 200, "width": 200, "height": 90},
        "attributes": {"材质": "PLA"},
    }
    d.update(kw)
    return d


def _final_attributes_9048():
    # 3D 耗材：无颜色字典属性，4180=Название 文本
    return [
        {"attribute_id": 4180, "value": "PLA 1 кг"},
        {"attribute_id": 4191, "value": "<p>Пластик PLA для 3D-принтера.</p>"},
    ]


def _state_9048(variants=None, schema=None, final_attributes=None):
    return PrepareOzonUploadInput(
        draft=_draft(multi_sku=True),
        source={"purchase_url": "https://detail.1688.com/x.html", "purchase_cost": "60"},
        pricing_info={"price": 100, "old_price": 130, "currency_code": "CNY", "exchange_rate": 1.0},
        description_category_id="17028935",
        type_id="200001721",
        final_attributes=final_attributes if final_attributes is not None else _final_attributes_9048(),
        attributes_schema=schema if schema is not None else SCHEMA_NO_COLOR_DICT,
        dictionary_values={},  # 类目无颜色字典属性 → 缓存无 10096 值（铁证场景）
        token="sk-test",
        original_images=[ALICDN],
        main_image=COS_AI,
        variants=variants or [],
        variant_primary_images=[],
        extensions={},
    )


@contextmanager
def _patches():
    with ExitStack() as st:
        st.enter_context(patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
                               return_value="Товар"))
        st.enter_context(patch("utils.mxou_api.call_mxou_chat_api", return_value=""))
        yield


def test_prepare_no_color_schema_degrades_to_9048_only_merge():
    """双 gate 铁证场景：无颜色字典类目 + 中文色名全 no_match → 9048-only 合卡
    （items=N / 9048 同 / 无颜色属性 / 标题色尾缀），不再整批回退单 SKU。"""
    variants = _zh_variants()
    with _patches():
        out = prepare_ozon_upload_node(_state_9048(variants=variants), None, None)
    assert out.error_message == "", out.error_message
    items = out.ozon_payload["items"]
    assert len(items) == 3, "7/7 color_no_match 不再回退单 SKU——9048-only 合卡"
    assert [it["offer_id"] for it in items] == [v["sku_id"] for v in variants]
    v9048 = {next(a for a in it["attributes"] if a["id"] == 9048)["values"][0]["value"] for it in items}
    assert v9048 == {"1083073125898"}, "9048 全 items 同值（合卡键）"
    for it in items:
        assert not any(a["id"] in (10096, 10097, 10098, 10099) for a in it["attributes"])
        assert ", " in it["name"] and not any('\u4e00' <= c <= '\u9fff' for c in it["name"])
    assert {it["name"].rsplit(", ", 1)[1] for it in items} == {"белый", "черный", "красный"}


def test_prepare_color_dict_schema_all_no_match_keeps_single_fallback():
    """有颜色字典类目 + 全 no_match（字典缓存缺失类瞬时故障）→ 现行现状零变化：
    回退单 SKU 主 item，绝不降级上无颜色区分的 items。"""
    variants = [{"sku_id": "s_1", "color": "荧光幻彩"}, {"sku_id": "s_2", "color": "鬼蓄青"}]
    st = _state_9048(
        variants=variants,
        schema=SCHEMA_WITH_COLOR_DICT,
        final_attributes=[
            {"attribute_id": 4191, "value": "<p>Описание</p>"},
        ],
    )
    with _patches():
        out = prepare_ozon_upload_node(st, None, None)
    items = out.ozon_payload["items"]
    assert len(items) == 1, "schema 有颜色字典 → 不降级（现行回退单 SKU 现状）"
    assert items[0]["offer_id"] == "1083073125898_0", "回退主 item 原 offer"


def test_prepare_9048_only_all_untranslatable_falls_back_single():
    """无颜色字典类目但全部色名无法俄语化 → 降级后仍无可译变体（<2）→
    回退单 SKU 主 item（宁缺毋滥现状）。"""
    variants = [{"sku_id": "s_1", "color": "荧光幻彩"}, {"sku_id": "s_2", "color": "Blue"}]
    with _patches():
        out = prepare_ozon_upload_node(_state_9048(variants=variants), None, None)
    items = out.ozon_payload["items"]
    assert len(items) == 1
    assert items[0]["offer_id"] == "1083073125898_0"

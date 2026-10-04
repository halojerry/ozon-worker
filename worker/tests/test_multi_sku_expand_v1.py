"""多 SKU 合卡 V2 worker 腿回归（feat/multi-sku-worker-v1，PLAN-multi-sku-v1 §V2）。

覆盖面（任务书测试清单逐项）：
  1. 展开形状：items 数=variant 数 / 9048 全 items 同值（=主 item_id 合卡键，
     非 ``item_id~hash`` 防并卡派生形态）/ 颜色属性互异 / offer_id 唯一；
  2. 颜色字典无匹配剔除（不盲补首值/不 FALLBACK_COLORS 编造）+ 歧义多候选剔除
     + skill color_dict_id 权威 + ZH→RU 语言桥接；
  3. price delta：主定价±delta（不重算公式），RUB 经 fx 换算，old_price 过
     enforce_old_price_rule 唯一规则；
  4. 缺省零变化：无 variants 标记 → 单 SKU 路径逐字不变（9048 防并卡派生照旧）；
     variants 无 multi_sku 标记 → 既有 legacy 分支照旧；
  5. 值数闸：展开产物每 item 属性恒 1 值，cap_attribute_values 照过零裁剪；
  6. 变体主图通道：variant.ref_image 喂 variant_primary_loop（1688 图=生图参考）；
  7. 信封契约面：draft.variants/draft.multi_sku 不进 extensions 闸（validate 放行，
     无契约重生成）；未知 extensions 键仍被拒（闸未松动）；
  8. 收尾卡片图断言按 variant 维度（verify_card_images_by_variant）。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_multi_sku_expand_v1.py -q
"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.multi_sku_expand import (
    MAX_VARIANTS,
    expand_multi_sku_items,
    match_variant_color,
    resolve_color_attr_id,
    variant_list_prices,
)

COS_AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/gen.jpg"
COS_AI_2 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/gen2.jpg"
ALICDN = "https://cbu01.alicdn.com/img/ibank/color1.jpg"

DICT_10096 = {
    "10096": [
        {"id": 61571, "value": "белый"},
        {"id": 61574, "value": "черный"},
        {"id": 61579, "value": "красный"},
        {"id": 61583, "value": "зеленый"},
    ]
}


def _base_item(**kw):
    item = {
        "name": "Пластик для 3D-принтера, 1 кг",
        "offer_id": "1083073125898_0",
        "price": "100",
        "old_price": "130",
        "primary_image": COS_AI,
        "images": [COS_AI, COS_AI_2],
        "attributes": [
            {"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "1083073125898"}]},
            {"complex_id": 0, "id": 10096, "values": [{"dictionary_value_id": 61571, "value": "белый"}]},
            {"complex_id": 0, "id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>Описание</p>"}]},
        ],
    }
    item.update(kw)
    return item


def _variants():
    return [
        {"sku_id": "1083073125898_1", "color": "белый", "color_dict_id": 61571, "price_delta_cny": 0,
         "ref_image": ALICDN},
        {"sku_id": "1083073125898_2", "color": "черный", "price_delta_cny": 2, "ref_image": ALICDN},
        {"sku_id": "1083073125898_3", "color": "красный", "price_delta_cny": -1},
    ]


# ═══════════════════════════════════════════════════════════
# 1. 展开形状（items 数 / 9048 全同 / 颜色互异 / offer 唯一）
# ═══════════════════════════════════════════════════════════

def test_expand_shape_items_9048_color_offer():
    ms = expand_multi_sku_items(
        _base_item(), _variants(),
        color_attr_id=10096, dictionary_values=DICT_10096,
        shared_images=[COS_AI, COS_AI_2],
        main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="1083073125898", base_offer_id="1083073125898",
    )
    assert len(ms.items) == 3
    # offer_id = 各 variant sku_id，唯一
    offers = [it["offer_id"] for it in ms.items]
    assert offers == ["1083073125898_1", "1083073125898_2", "1083073125898_3"]
    assert len(set(offers)) == 3
    # 9048 全 items 同值 = 主 item_id（合卡键），绝无 "~" 防并卡 hash 形态
    v9048 = []
    for it in ms.items:
        got = [a for a in it["attributes"] if a["id"] == 9048]
        assert len(got) == 1 and len(got[0]["values"]) == 1
        v9048.append(got[0]["values"][0]["value"])
    assert v9048 == ["1083073125898"] * 3
    assert all("~" not in v for v in v9048)
    # 颜色属性互异（字典值 id 互异）
    colors = []
    for it in ms.items:
        got = [a for a in it["attributes"] if a["id"] == 10096]
        assert len(got) == 1 and len(got[0]["values"]) == 1
        colors.append(got[0]["values"][0]["dictionary_value_id"])
    assert colors == [61571, 61574, 61579]
    assert len(set(colors)) == 3
    # 其余属性共享主 item 值（4191 逐字一致）
    texts = [next(a["values"][0]["value"] for a in it["attributes"] if a["id"] == 4191) for it in ms.items]
    assert len(set(texts)) == 1 and texts[0] == "<p>Описание</p>"
    assert ms.marks["kept"] == 3 and ms.marks["dropped"] == 0


def test_expand_shared_images_and_variant_primary_fallback():
    ms = expand_multi_sku_items(
        _base_item(), _variants()[:2],
        color_attr_id=10096, dictionary_values=DICT_10096,
        shared_images=[COS_AI, COS_AI_2],
        variant_primary_images=[COS_AI + "?v=1", ""],  # 第 2 个生图失败 → 回落 base 主图
        main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="1083073125898", base_offer_id="1083073125898",
    )
    assert ms.items[0]["primary_image"] == COS_AI + "?v=1"
    assert ms.items[1]["primary_image"] == COS_AI, "变体主图缺/败回落 base 主图（AI 图，过图源闸）"
    for it in ms.items:
        assert it["images"][0] == it["primary_image"]
        assert COS_AI_2 in it["images"], "共享营销图全变体复用"
        assert len(it["images"]) == 2 or COS_AI in it["images"]


# ═══════════════════════════════════════════════════════════
# 2. 颜色匹配与剔除（attr_value_matcher 唯一入口，不盲补）
# ═══════════════════════════════════════════════════════════

def test_color_match_skill_dict_id_authoritative():
    vid, val, layer = match_variant_color(
        {"sku_id": "x", "color": "белый", "color_dict_id": 61571}, 10096, DICT_10096)
    assert (vid, layer) == (61571, "skill_dict_id")
    # 幻觉 id（不在缓存）不采信 → 走文本匹配路径（黑色无文本）→ no_match
    vid, _, layer = match_variant_color(
        {"sku_id": "x", "color": "", "color_dict_id": 999999}, 10096, DICT_10096)
    assert vid == 0 and layer == "missing"


def test_color_match_exact_and_bridge_and_drop():
    # RU 色名精确命中
    vid, val, layer = match_variant_color({"color": "черный"}, 10096, DICT_10096)
    assert (vid, layer) == (61574, "exact")
    # 中文色名经 ZH→RU 桥接命中（缓存是 RU 形态）
    vid, val, layer = match_variant_color({"color": "黑色"}, 10096, DICT_10096)
    assert vid == 61574 and layer.startswith("bridge")
    assert not any('\u4e00' <= c <= '\u9fff' for c in val), "payload value 不得含中文"
    # 无匹配 → 剔除（绝不盲补首值）
    vid, _, layer = match_variant_color({"color": "荧光幻彩"}, 10096, DICT_10096)
    assert vid == 0 and layer == "no_match"
    # 完全无色无 id → missing
    vid, _, layer = match_variant_color({"sku_id": "x"}, 10096, DICT_10096)
    assert vid == 0 and layer == "missing"


def test_color_match_ambiguous_dropped_not_blind_first():
    # 缓存含 "белый"/"белый матовый" → "бел" 类包含多候选歧义 → 不盲采
    cached = {"10096": [{"id": 1, "value": "белый матовый"}, {"id": 2, "value": "белый"}, {"id": 3, "value": "белая эмаль"}]}
    vid, _, layer = match_variant_color({"color": "белый матовый глубоки"}, 10096, cached)
    assert vid == 0 and layer == "ambiguous"


def test_expand_drop_reasons_and_empty_fallback():
    variants = [
        {"sku_id": "s_1", "color": "белый"},            # keep
        {"sku_id": "s_2", "color": "荧光幻彩"},          # color_no_match
        {"sku_id": "s_3", "color": ""},                  # color_missing
        {"sku_id": "s_1", "color": "черный"},            # offer_duplicate（offer 查重先于配色）
        {"sku_id": "s_4", "color": "белый"},             # color_duplicate（同 dict_id）
    ]
    ms = expand_multi_sku_items(
        _base_item(), variants,
        color_attr_id=10096, dictionary_values=DICT_10096,
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="item1", base_offer_id="item1",
    )
    reasons = [d["reason"] for d in ms.dropped]
    assert reasons == [
        "color_no_match", "color_missing", "offer_duplicate", "color_duplicate"]
    assert len(ms.items) == 1
    assert ms.items[0]["offer_id"] == "s_1"
    # 全部剔除 → items 空（调用方回退单 SKU）
    ms_empty = expand_multi_sku_items(
        _base_item(), [{"sku_id": "s_9", "color": "荧光幻彩"}],
        color_attr_id=10096, dictionary_values=DICT_10096,
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="item1", base_offer_id="item1",
    )
    assert ms_empty.items == []


def test_expand_limit_15_truncates_with_report():
    # 17 个互异颜色（合成字典缓存）→ 超上限截断保前 15
    cached = {"10096": [{"id": 900000 + i, "value": f"цвет-{i}"} for i in range(20)]}
    variants = [{"sku_id": f"s_{i}", "color": f"цвет-{i}", "color_dict_id": 900000 + i}
                for i in range(17)]
    ms = expand_multi_sku_items(
        _base_item(), variants,
        color_attr_id=10096, dictionary_values=cached,
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="item1", base_offer_id="item1",
    )
    assert len(ms.items) == MAX_VARIANTS == 15
    assert any(d["reason"] == "limit_exceeded" for d in ms.dropped)
    assert len(ms.dropped) == 2


def test_resolve_color_attr_id_trust_order():
    # base item 已有 10096 → 直接采
    assert resolve_color_attr_id([{"id": 10096, "values": []}], []) == 10096
    # schema 名命中 «цвет» 词形 + 显式字典 → 10097
    # （fix/multi-sku-9048-fixes 起 schema 候选必须显式 dictionary_id>0，
    #   5646 «Количество цветов» 类计数属性 dict_id=0 不得入选）
    assert resolve_color_attr_id([{"id": 4191, "values": []}],
                                 [{"id": 10097, "name": "Цвет товара", "dictionary_id": 4}]) == 10097
    # 都无 → 缺省 10096
    assert resolve_color_attr_id([], []) == 10096


# ═══════════════════════════════════════════════════════════
# 3. price delta（主定价 ± delta，不重算公式）
# ═══════════════════════════════════════════════════════════

def test_price_delta_cny_store_identity_rate():
    assert variant_list_prices(100, 130, 0, 1.0) == ("100", "130")
    assert variant_list_prices(100, 130, 2, 1.0) == ("102", "132")
    assert variant_list_prices(100, 130, -1, 1.0) == ("99", "129")


def test_price_delta_rub_converts_via_fx():
    # RUB 店：delta CNY × 12 → price=100+24=124，old 同步平移后过唯一规则
    p, o = variant_list_prices(100, 130, 2, 12.0)
    assert p == "124"
    assert int(o) >= 124, "old_price ≥ price"
    # old_price 被平移到规则下限以下时由 enforce_old_price_rule 抬（唯一规则）
    p2, o2 = variant_list_prices(50, 58, -3, 1.0)  # 47 → min old = max(ceil(56.4)=57, 47+20=67) = 67
    assert p2 == "47" and o2 == "67"


def test_price_delta_invalid_main_price_noop():
    assert variant_list_prices(0, 0, 5, 12.0) == ("", "")
    assert variant_list_prices("abc", None, 5, 12.0) == ("", "")
    # NaN/Inf delta 安全归零
    assert variant_list_prices(100, 130, float("nan"), 1.0) == ("100", "130")


# ═══════════════════════════════════════════════════════════
# 4. 值数闸：展开产物每 item 属性恒 1 值，cap 照过
# ═══════════════════════════════════════════════════════════

def test_expand_values_count_gate_natural_compliance():
    from utils.attr_value_sanitize import cap_attribute_values
    schema = [
        {"id": 10096, "name": "Цвет", "dictionary_id": 1, "max_value_count": 1},
        {"id": 9048, "name": "Название модели", "dictionary_id": 0, "max_value_count": 1},
        {"id": 4191, "name": "Аннотация", "dictionary_id": 0, "max_value_count": 1},
    ]
    ms = expand_multi_sku_items(
        _base_item(), _variants(),
        color_attr_id=10096, dictionary_values=DICT_10096,
        shared_images=[COS_AI], main_price=100, main_old_price=130, fx_rate=1.0,
        merge_9048="1083073125898", base_offer_id="1083073125898",
    )
    for it in ms.items:
        for attr in it["attributes"]:
            assert len(attr["values"]) == 1, "每 item 属性恒 1 值（8229 契约同构）"
        capped, marks = cap_attribute_values(it["attributes"], schema)
        assert marks == [], "值数出口闸对展开产物零裁剪（天然合规）"
        assert capped == it["attributes"]


# ═══════════════════════════════════════════════════════════
# 5. prepare 集成：multi_sku 生效 + 缺省零变化
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


def _final_attributes():
    return [
        {"attribute_id": 10096, "value": "белый", "dictionary_value_id": 61571},
        {"attribute_id": 4191, "value": "<p>Пластик PLA для 3D-принтера.</p>"},
    ]


def _state(multi_sku=False, variants=None, final_attributes=None, pricing=None):
    return PrepareOzonUploadInput(
        draft=_draft(**({"multi_sku": True} if multi_sku else {})),
        source={"purchase_url": "https://detail.1688.com/x.html", "purchase_cost": "60"},
        pricing_info=pricing if pricing is not None else {
            "price": 100, "old_price": 130, "currency_code": "CNY", "exchange_rate": 1.0,
        },
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes if final_attributes is not None else _final_attributes(),
        attributes_schema=[
            {"id": 10096, "name": "Цвет", "dictionary_id": 1, "is_required": False},
            {"id": 4191, "name": "Аннотация", "dictionary_id": 0, "is_required": False},
        ],
        dictionary_values=dict(DICT_10096),
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


def test_prepare_default_single_sku_unchanged():
    """缺省零变化：无 variants 标记 → 单 item + 9048 防并卡派生（item_id~hash）。"""
    with _patches():
        out = prepare_ozon_upload_node(_state(), None, None)
    items = out.ozon_payload["items"]
    assert len(items) == 1
    v9048 = next(a for a in items[0]["attributes"] if a["id"] == 9048)["values"][0]["value"]
    assert v9048.startswith("1083073125898~"), f"防并卡派生形态照旧: {v9048}"
    assert out.error_message == "", out.error_message


def test_prepare_multi_sku_expands_items():
    """multi_sku：items 数=variant 数、9048 全=主 item_id、颜色互异、offer 唯一、delta 生效。"""
    variants = _variants()
    with _patches():
        out = prepare_ozon_upload_node(_state(multi_sku=True, variants=variants), None, None)
    assert out.error_message == "", out.error_message
    items = out.ozon_payload["items"]
    assert len(items) == 3
    assert [it["offer_id"] for it in items] == [v["sku_id"] for v in variants]
    v9048 = {next(a for a in it["attributes"] if a["id"] == 9048)["values"][0]["value"] for it in items}
    assert v9048 == {"1083073125898"}, "9048 全 items 同值=主 item_id（合卡键）"
    colors = [next(a for a in it["attributes"] if a["id"] == 10096)["values"][0]["dictionary_value_id"]
              for it in items]
    assert len(set(colors)) == 3
    prices = [it["price"] for it in items]
    assert prices == ["100", "102", "99"], f"delta 生效（不重算公式）: {prices}"


def test_prepare_multi_sku_all_colors_unmatched_falls_back_single():
    """全部 variant 颜色无匹配 → 如实回退单 SKU 主 item（绝不传空 items）。"""
    variants = [{"sku_id": "s_1", "color": "荧光幻彩"}, {"sku_id": "s_2", "color": "鬼蓄青"}]
    with _patches():
        out = prepare_ozon_upload_node(_state(multi_sku=True, variants=variants), None, None)
    items = out.ozon_payload["items"]
    assert len(items) == 1
    assert items[0]["offer_id"] == "1083073125898_0", "回退主 item 原 offer"


def test_prepare_variants_without_multi_sku_flag_legacy_path():
    """variants 无 multi_sku 标记 → 既有 legacy 分支照旧（防并卡 9048 派生语义不变）。"""
    variants = _variants()
    with _patches():
        out = prepare_ozon_upload_node(_state(variants=variants), None, None)
    items = out.ozon_payload["items"]
    # legacy 分支需要 has_variant_images 或 len>1 → len=3 命中 legacy 展开
    assert len(items) == 3
    v9048 = {next(a for a in it["attributes"] if a["id"] == 9048)["values"][0]["value"] for it in items}
    assert len(v9048) == 1
    val = next(iter(v9048))
    assert val.startswith("1083073125898~"), f"legacy 分支 9048 仍是防并卡派生: {val}"


def test_prepare_multi_sku_flag_without_variants_is_noop():
    """multi_sku=true 但 variants 空 → 单 SKU 路径（开关不误伤）。"""
    with _patches():
        out = prepare_ozon_upload_node(_state(multi_sku=True), None, None)
    items = out.ozon_payload["items"]
    assert len(items) == 1
    v9048 = next(a for a in items[0]["attributes"] if a["id"] == 9048)["values"][0]["value"]
    assert v9048.startswith("1083073125898~")


# ═══════════════════════════════════════════════════════════
# 6. ingest 透传 + 信封契约面（draft 键不进 extensions 闸）
# ═══════════════════════════════════════════════════════════

class _DummyRuntime:
    class _DummyContext:
        pass
    context = _DummyContext()


def test_ingest_passes_variants_and_multi_sku_through():
    from graphs.state import IngestInput
    from graphs.nodes.ingest_node import ingest_node
    envelope = {
        "draft": {"title": "PLA 3D 打印耗材", "item_id": "1083073125898",
                  "multi_sku": True, "variants": _variants()},
        "source": {"purchase_url": "https://detail.1688.com/x.html", "purchase_cost": "60"},
        "extensions": {},
    }
    st = IngestInput(envelope=envelope, user_id="tenant", supabase_url="http://s", supabase_key="k")
    out = ingest_node(st, None, _DummyRuntime())
    assert out.variants == _variants()
    assert out.item_id == "1083073125898"
    assert out.draft.get("multi_sku") is True
    assert out.failed_stage == ""


def test_envelope_contract_draft_keys_not_gated():
    """draft.variants / draft.multi_sku 不进 extensions 闸（draft 深层不类型化）→ 放行；
    未知 extensions 键仍 fail-closed（闸未松动，无契约重生成）。"""
    from utils.envelope_contract import validate_envelope
    env = {
        "draft": {"title": "t", "multi_sku": True, "variants": [{"sku_id": "s"}]},
        "source": {}, "extensions": {},
    }
    assert validate_envelope(env) == []
    bad = {"draft": {"title": "t"}, "source": {}, "extensions": {"variants": []}}
    errors = validate_envelope(bad)
    assert errors and "variants" in errors[0], "extensions 私加键仍被拒"


# ═══════════════════════════════════════════════════════════
# 7. 变体主图通道：ref_image 喂 variant_primary_loop
# ═══════════════════════════════════════════════════════════

def test_variant_loop_reads_ref_image_contract_key():
    """variant.ref_image（V1 契约）> legacy variant.image，作生图参考（1688 图非上卡）。"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput, variant_primary_loop_node
    captured = {}

    def _fake_api(**kwargs):
        captured.setdefault("ref_images", []).append(kwargs.get("ref_images"))
        return COS_AI + "?variant=1"

    st = VariantPrimaryLoopInput(
        variants=[{"sku_id": "s_1", "color": "белый", "ref_image": ALICDN},
                  {"sku_id": "s_2", "color": "черный", "image": ALICDN + "?legacy=1"}],
        draft={"title": "Пластик"},
        token="sk",
        category_name="Пластик",
    )
    with ExitStack() as stx:
        stx.enter_context(patch("graphs.nodes.variant_primary_loop_node.call_mxou_image_api",
                                side_effect=_fake_api))
        out = variant_primary_loop_node(st, {}, _DummyRuntime())
    assert out.variant_primary_images == [COS_AI + "?variant=1", COS_AI + "?variant=1"]
    assert sorted(captured["ref_images"]) == sorted(
        [[ALICDN], [ALICDN + "?legacy=1"]]), "ref_image 契约键优先，legacy image 键兜底"


# ═══════════════════════════════════════════════════════════
# 8. 收尾卡片图断言按 variant 维度
# ═══════════════════════════════════════════════════════════

def test_card_assert_by_variant_ok_and_mismatch():
    from utils.card_image_assert import verify_card_images_by_variant
    payload = [
        {"images": [COS_AI, COS_AI_2]},
        {"images": [COS_AI + "?v=2"]},
    ]
    ok_cards = [
        {"images": ["https://ir-20.ozone.ru/s3/multimedia/a.jpg", "https://ir-20.ozone.ru/s3/multimedia/b.jpg"]},
        {"images": ["https://ir-20.ozone.ru/s3/multimedia/c.jpg"]},
    ]
    status, detail = verify_card_images_by_variant(payload, ok_cards, fetch_size=lambda u: (900, 1200))
    assert status == "ok"

    # variant#1 首图非 3:4（原图覆盖）→ mismatch 带 variant 序号
    bad_cards = [
        {"images": ["https://ir-20.ozone.ru/s3/multimedia/a.jpg"]},
        {"images": ["https://ir-20.ozone.ru/s3/multimedia/c.jpg"]},
    ]
    status, detail = verify_card_images_by_variant(
        payload, bad_cards, fetch_size=lambda u: (1000, 1000) if "c.jpg" in u else (900, 1200))
    assert status == "mismatch" and "variant#1" in detail

    # 卡片变体数 < 载荷变体数 → mismatch（部分变体缺失）
    status, detail = verify_card_images_by_variant(payload, ok_cards[:1], fetch_size=lambda u: (900, 1200))
    assert status == "mismatch" and "变体数" in detail

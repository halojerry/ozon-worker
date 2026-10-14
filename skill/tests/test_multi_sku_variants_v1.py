#!/usr/bin/env python3
"""多 SKU 合卡 V1（PLAN-multi-sku-v1 skill 腿）— variants 展开与 --variants 透传。

五组锁定：
1. expand_multi_sku_variants 展开形状：draft.variants[i] 键集合逐字
   {sku_id, color, color_dict_id, price_delta_cny, ref_image}；颜色取自现有
   SKU 键解析产物 attributes["颜色"]（零再解析零 LLM）；delta=对主 SKU 的
   1688 价差（主 SKU 恒 0，负=减价）；color_dict_id 恒 None（字典在 worker）；
2. 15 截断：20 色 → 前 15 保留（1688 原始顺序）+ dropped 清单 5 条；
3. 无颜色 SKU（数量/尺码/规格）→ variants=[]（调用方不置 draft.multi_sku，
   单 SKU 现状零变化）；
4. --variants flag：parser 缺省 False；cmd_graph 透传
   build_graph_envelope_with_retry(multi_sku=True) → build_graph_envelope；
5. 信封集成（mock 全外部依赖，test_api_only_degraded 同缝）：
   multi_sku=True + 颜色 SKU → draft.multi_sku=True + draft.variants 合卡形状；
   multi_sku 缺省 → 无 draft.multi_sku/无合卡 variants（byte-compat）；
   multi_sku=True + 无颜色 SKU → 同样不置键。

纯 mock，不触网、不启 Chrome。draft.variants/draft.multi_sku 是 draft 通道键
（envelope_contract 只类型化 extensions——本测试兼作该口径的看门）。

运行: cd skill && .venv314/bin/python -m pytest tests/test_multi_sku_variants_v1.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402
from scripts.lib import variants_expander as ve  # noqa: E402

ITEM_ID = "1083073125898"
DETAIL_URL = f"https://detail.1688.com/offer/{ITEM_ID}.html"
IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"


def _color_variant(idx: int, color: str, price: float, image: str = "") -> dict:
    """cloud_probe §5 fallback 路径产出的折叠前 variant 形状（颜色维度）。"""
    return {
        "sku_id": f"{ITEM_ID}_{idx}",
        "name": color,
        "color": color,
        "model": "",
        "size": "one size",
        "image": image,
        "price": price,
        "original_price": price,
        "attributes": {"颜色": color},
        "variant_type": "color",
    }


# ────────────────────────── 1. 展开形状 ──────────────────────────


def test_expand_shape_and_delta_math():
    """颜色 SKU → 合卡形状逐键；主 SKU delta=0，其余对基准价差；ref_image 透传。"""
    src = [
        _color_variant(0, "黑色", 12.5, IMG),          # 主 SKU（代表档）
        _color_variant(1, "白色", 13.0, IMG + "w"),
        _color_variant(2, "红色", 12.0),                # 无图 → ref_image ""
    ]
    res = ve.expand_multi_sku_variants(src, base_sku_id=f"{ITEM_ID}_0")
    assert res["skipped_no_color"] == 0 and res["dropped"] == []
    vs = res["variants"]
    assert len(vs) == 3
    for v in vs:
        assert tuple(v.keys()) == ve._VARIANT_KEYS, f"契约面键集合漂移: {tuple(v.keys())}"
    assert vs[0] == {
        "sku_id": f"{ITEM_ID}_0", "color": "黑色", "color_dict_id": None,
        "price_delta_cny": 0.0, "ref_image": IMG,
    }
    assert vs[1]["price_delta_cny"] == 0.5      # 13.0 - 12.5
    assert vs[1]["ref_image"] == IMG + "w"
    assert vs[2]["price_delta_cny"] == -0.5     # 负 delta = 减价（合法）
    assert vs[2]["ref_image"] == ""


def test_expand_color_is_chinese_passthrough_no_translation():
    """颜色名中文原样透传（俄语翻译在 worker V2，skill 零 LLM）——不做任何改写。"""
    src = [_color_variant(0, "黑色", 10.0), _color_variant(1, "米黄色", 10.5)]
    vs = ve.expand_multi_sku_variants(src, base_sku_id=f"{ITEM_ID}_0")["variants"]
    assert [v["color"] for v in vs] == ["黑色", "米黄色"]


def test_expand_base_fallback_min_price_when_base_missing():
    """base_sku_id 未命中 → 回落全列表最低正价为基准（delta 恒 >= 0）。"""
    src = [_color_variant(0, "黑色", 12.5), _color_variant(1, "白色", 13.0)]
    vs = ve.expand_multi_sku_variants(src, base_sku_id="nonexistent")["variants"]
    assert [v["price_delta_cny"] for v in vs] == [0.0, 0.5]


def test_expand_price_missing_delta_zero():
    """SKU 无价/异常价 → price_delta_cny=0（「无则 0」契约），不 raise。"""
    v = _color_variant(0, "黑色", 12.5)
    v_bad = _color_variant(1, "白色", None)
    vs = ve.expand_multi_sku_variants(
        [v, v_bad], base_sku_id=f"{ITEM_ID}_0")["variants"]
    assert vs[1]["price_delta_cny"] == 0.0


# ────────────────────────── 2. 15 截断 ──────────────────────────


def test_expand_truncates_at_15_with_dropped_list():
    """20 色 → 前 15 保留（1688 原始顺序），dropped 恰为后 5 色（剔除清单）。"""
    src = [_color_variant(i, f"色{i}", 10.0 + i) for i in range(20)]
    res = ve.expand_multi_sku_variants(src, base_sku_id=f"{ITEM_ID}_0")
    assert len(res["variants"]) == ve.MAX_MULTI_SKU_VARIANTS == 15
    assert [v["sku_id"] for v in res["variants"]] == [f"{ITEM_ID}_{i}" for i in range(15)]
    assert [d["sku_id"] for d in res["dropped"]] == [f"{ITEM_ID}_{i}" for i in range(15, 20)]
    assert {d["color"] for d in res["dropped"]} == {f"色{i}" for i in range(15, 20)}


def test_expand_custom_cap():
    src = [_color_variant(i, f"色{i}", 10.0) for i in range(8)]
    res = ve.expand_multi_sku_variants(src, base_sku_id=f"{ITEM_ID}_0", max_variants=3)
    assert len(res["variants"]) == 3 and len(res["dropped"]) == 5


# ────────────────────────── 3. 无颜色不产 ──────────────────────────


def test_expand_no_color_skus_yields_empty():
    """数量/尺码/规格 SKU 无 attributes["颜色"] → variants=[]（不产合卡通道）。"""
    src = [
        {"sku_id": f"{ITEM_ID}_0", "name": "5只装", "color": "5只装",
         "price": 10.0, "image": "", "attributes": {"数量": "5只装"},
         "variant_type": "quantity"},
        {"sku_id": f"{ITEM_ID}_1", "name": "XL", "color": "XL",
         "price": 11.0, "image": "", "attributes": {"尺寸": "XL"},
         "variant_type": "size"},
        {"sku_id": f"{ITEM_ID}_2", "name": "USB款", "color": "USB款",
         "price": 12.0, "image": "", "attributes": {"规格": "USB款"},
         "variant_type": "spec"},
    ]
    res = ve.expand_multi_sku_variants(src, base_sku_id=f"{ITEM_ID}_0")
    assert res["variants"] == [] and res["dropped"] == []
    assert res["skipped_no_color"] == 3


def test_expand_empty_input():
    assert ve.expand_multi_sku_variants([])["variants"] == []
    assert ve.expand_multi_sku_variants(None)["variants"] == []


def test_price_span():
    assert ve.multi_sku_price_span([]) is None
    rows = [{"price_delta_cny": 0.5}, {"price_delta_cny": -1.0},
            {"price_delta_cny": 0.0}]
    assert ve.multi_sku_price_span(rows) == (-1.0, 0.5)


# ────────────────────────── 4. --variants flag 透传 ──────────────────────────


def _graph_parser():
    from scripts.cli import build_arg_parser
    return build_arg_parser()


def test_parser_variants_default_off():
    args = _graph_parser().parse_args(["graph", "--url", DETAIL_URL])
    assert getattr(args, "variants") is False, "--variants 缺省必须关（单 SKU 零变化）"


def test_parser_variants_on():
    args = _graph_parser().parse_args(["graph", "--url", DETAIL_URL, "--variants"])
    assert args.variants is True


def test_retry_passes_multi_sku_through():
    """build_graph_envelope_with_retry(multi_sku=True) → build_graph_envelope 收到。"""
    captured: dict = {}

    def _fake_build(**kw):
        captured.update(kw)
        return {"envelope": {"draft": {}}}

    with mock.patch.object(cloud_probe, "build_graph_envelope", side_effect=_fake_build):
        cloud_probe.build_graph_envelope_with_retry(
            item_id=ITEM_ID, detail_url=DETAIL_URL, max_retries=1, multi_sku=True)
        assert captured.get("multi_sku") is True
        cloud_probe.build_graph_envelope_with_retry(
            item_id=ITEM_ID, detail_url=DETAIL_URL, max_retries=1)
    assert captured.get("multi_sku") is False, "缺省透传必须是 False（零变化）"


# ────────────────────────── 5. 信封集成（mock 全外部依赖） ──────────────────────────


def _enriched_with_color_skus() -> dict:
    """4 色 SKU 的 enrich 返回（fallback sku_details 路径，无 option_groups）。"""
    return {
        "ok": False, "degraded": True,
        "degraded_reason": "浏览器探测失败: mock api_only",
        "user_action": None,
        "source": "api_only",
        "data": {
            "title": "3D打印耗材 PLA 1KG",
            "price": "12.5",
            "images": [IMG],
            "attributes": [],
            "option_groups": [],
            "sku_details": [
                {"name": "黑色", "price": 12.5, "image": IMG + "k"},
                {"name": "白色", "price": 13.0, "image": IMG + "w"},
                {"name": "红色", "price": 12.0, "image": ""},
                {"name": "蓝色", "price": 12.5, "image": IMG + "b"},
            ],
            "shipping": {},
            "description": "",
        },
    }


def _build_envelope(enriched: dict, **kw) -> dict:
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=enriched), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "123", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"):
        return cloud_probe.build_graph_envelope(
            item_id=ITEM_ID, detail_url=DETAIL_URL, poll_category=False, **kw)


def test_envelope_multi_sku_on_expands_variants():
    """--variants + 颜色 SKU → draft.multi_sku=True + draft.variants 合卡形状。"""
    graph = _build_envelope(_enriched_with_color_skus(), multi_sku=True)
    draft = graph["envelope"]["draft"]
    assert draft["multi_sku"] is True
    vs = draft["variants"]
    assert len(vs) == 4
    assert {v["color"] for v in vs} == {"黑色", "白色", "红色", "蓝色"}
    for v in vs:
        assert tuple(v.keys()) == ve._VARIANT_KEYS
        assert v["color_dict_id"] is None
    # 主 SKU = 折叠代表档（中位价 12.5 → 黑色 _0），delta 基准为其折叠前价
    by_color = {v["color"]: v for v in vs}
    assert by_color["黑色"]["sku_id"] == f"{ITEM_ID}_0"
    assert by_color["黑色"]["price_delta_cny"] == 0.0
    assert by_color["白色"]["price_delta_cny"] == 0.5
    assert by_color["红色"]["price_delta_cny"] == -0.5
    # 单 SKU 主键（sku_id/price）不因合卡消失——worker V2 以它为主定价锚
    assert draft["sku_id"] == f"{ITEM_ID}_0"
    assert draft["purchase_cost"] == 12.5
    # draft 通道键不进 extensions 契约闸（envelope_contract 只类型化 extensions）
    assert "multi_sku" not in graph["envelope"]["extensions"]
    assert "variants" not in graph["envelope"]["extensions"]


def test_envelope_default_no_multi_sku_zero_change():
    """缺省（不带 --variants）+ 颜色 SKU → 无 draft.multi_sku/无合卡 variants。

    draft.sku_id/price 单 SKU 现状逐键保留（byte-compat 看门）。
    """
    graph = _build_envelope(_enriched_with_color_skus())
    draft = graph["envelope"]["draft"]
    assert "multi_sku" not in draft
    assert "variants" not in draft
    assert draft["sku_id"] == f"{ITEM_ID}_0"
    assert draft["price"] == 12.5


def test_envelope_multi_sku_on_but_no_color_skus_stays_single():
    """--variants 但 SKU 无颜色维度（数量档）→ 不置 multi_sku（单 SKU 零变化）。"""
    enriched = _enriched_with_color_skus()
    enriched["data"]["sku_details"] = [
        {"name": "1只装", "price": 5.0, "image": IMG},
        {"name": "5只装", "price": 20.0, "image": IMG},
    ]
    graph = _build_envelope(enriched, multi_sku=True)
    draft = graph["envelope"]["draft"]
    assert "multi_sku" not in draft
    assert "variants" not in draft
    assert draft["sku_id"].startswith(ITEM_ID)


def test_envelope_multi_sku_truncates_to_15():
    """--variants + 20 色 → draft.variants 恰 15（截断在信封层生效）。

    SKU 名必须用 _COLOR_WORDS 实词（cloud_probe §5 解析靠它产 attributes["颜色"]）。
    """
    _colors20 = (
        "白色", "黑色", "红色", "蓝色", "绿色", "黄色", "粉色", "紫色",
        "灰色", "橙色", "青色", "棕色", "米色", "卡其", "银色", "金色",
        "浅兰色", "浅绿色", "浅紫色", "深灰色",
    )
    enriched = _enriched_with_color_skus()
    enriched["data"]["sku_details"] = [
        {"name": c, "price": 10.0 + 0.1 * i, "image": IMG}
        for i, c in enumerate(_colors20)
    ]
    graph = _build_envelope(enriched, multi_sku=True)
    draft = graph["envelope"]["draft"]
    assert draft["multi_sku"] is True
    assert len(draft["variants"]) == 15
    assert {v["color"] for v in draft["variants"]} <= set(_colors20)


def test_estimate_print_multi_sku_lines(capsys):
    """预估展示：主价 + 每 variant delta 逐行；无预估时只打数量+区间不打售价。"""
    rows = [
        {"sku_id": f"{ITEM_ID}_0", "color": "黑色", "price_delta_cny": 0.0},
        {"sku_id": f"{ITEM_ID}_1", "color": "白色", "price_delta_cny": 0.5},
    ]
    n = ve.print_multi_sku_estimate(rows, 45.2)
    out = capsys.readouterr().out
    assert n == 2
    assert "🎨 多SKU: 2 变体" in out
    assert "≈45.20~45.70" in out, f"区间行缺失/变形: {out}"
    assert "Δ+0.50 → ≈45.70" in out
    assert "Δ+0.00 → ≈45.20" in out

    n0 = ve.print_multi_sku_estimate(rows, None)
    out0 = capsys.readouterr().out
    assert n0 == 2
    assert "无预估" in out0 and "≈45" not in out0, "无预估态绝不本地补售价"

    assert ve.print_multi_sku_estimate([], 45.2) == 0
    capsys.readouterr()


def test_estimate_and_print_wires_multi_sku(capsys):
    """_estimate_and_print 对 multi_sku 信封补 variants_count（graph 提交链缝）。"""
    from unittest.mock import patch

    draft = {
        "purchase_cost": 12.5, "weight": 1000,
        "multi_sku": True,
        "variants": [
            {"sku_id": f"{ITEM_ID}_0", "color": "黑色", "price_delta_cny": 0.0},
            {"sku_id": f"{ITEM_ID}_1", "color": "白色", "price_delta_cny": 0.5},
        ],
    }
    est = {"price": 45.2, "profit_cny": 8.3, "profit_rate": 0.184,
           "logistics_cost_cny": 3.0, "currency": "CNY"}
    with patch("scripts.lib.estimate_client.estimate_envelope", return_value=est), \
            patch("scripts.lib.config_store.get_store_profile", return_value={}), \
            patch("scripts.lib.config_store.get_store", return_value=None):
        from scripts.cli import _estimate_and_print
        res = _estimate_and_print(draft, "")
    out = capsys.readouterr().out
    assert res is not None and res.get("variants_count") == 2
    assert "🎨 多SKU: 2 变体" in out
    assert "Δ+0.50" in out

    # 非 multi_sku 信封零新增行（单 SKU 现状零变化）
    draft_single = {"purchase_cost": 12.5, "weight": 1000}
    with patch("scripts.lib.estimate_client.estimate_envelope", return_value=est), \
            patch("scripts.lib.config_store.get_store_profile", return_value={}), \
            patch("scripts.lib.config_store.get_store", return_value=None):
        from scripts.cli import _estimate_and_print as _eap
        res2 = _eap(draft_single, "")
    out2 = capsys.readouterr().out
    assert "多SKU" not in out2
    assert "variants_count" not in (res2 or {})


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

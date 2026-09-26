#!/usr/bin/env python3
"""fix/listing-quality-v081 修复1 — 取重优先级链（箱级毛重流入物流定价的源头根治）。

根因锚点:
- cloud_probe.build_graph_envelope 盲取 ``packaging_rows[0].weightGrams``
  （1688 包装表第一行=箱级毛重；实锤 30支香 962g vs 卡属性 50g、吸顶灯 9150g）
- contextPath unitWeight（单件真值）解析块在取重之后 → data["unit_weight"]
  全仓零消费（本批前移到取重之前）
- ak_1688_client._extract_weight_dimensions offer_detail kg 小数被裸 int()
  截断（"9.15"→9g；对齐 worker _parse_weight_g 口径：带小数点按 kg×1000）

信任序（_select_unit_weight_g）:
  contextPath unitWeight > 页面属性重量键（商品重量/含包装重量/净重/单件重量/重量）
  > packaging_rows[0] 箱级毛重（旧行为兜底）。
  sanity：选中值 > 箱级毛重 → 属性是脏数据，回落毛重。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_unit_weight_priority_v081.py -q
"""
from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scripts.cloud_probe as cloud_probe  # noqa: E402
from scripts.lib.ak_1688_client import _extract_weight_dimensions  # noqa: E402

_1688 = "https://detail.1688.com/offer/980815374096.html"


# ── 1688 信封级夹具（全 mock，零网络零 Chrome）──


def _page_data(**over):
    data = {
        "title": "测试香薰挂件 30支装", "price": "10.00",
        "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
        "attributes": [],
        "option_groups": [],
        "sku_details": [{"name": "默认", "price": 10.0, "image": ""}],
        "shipping": {"freightCny": 5.0},
        "seller": "测试店铺",
        "description": "",
        "packaging_rows": [
            {"weightGrams": 9150, "lengthText": "", "widthText": "", "heightText": ""},
        ],
        "pageStructuredData": [],
    }
    data.update(over)
    return data


def _ctx_sd(unit_weight=None, feature_attrs=None) -> dict:
    """contextPath pageStructuredData 条目（sample 为 JSON 字符串，同 1688 页面）。"""
    payload: dict = {}
    if unit_weight is not None:
        payload["unitWeight"] = unit_weight
    if feature_attrs:
        payload["featureAttributes"] = feature_attrs
    return {"name": "contextPath", "sample": json.dumps(payload, ensure_ascii=False)}


def _patch_env(stack: ExitStack, page_data: dict) -> None:
    stack.enter_context(mock.patch("scripts.lib.config_store._require_auth"))
    stack.enter_context(mock.patch(
        "scripts.lib.ak_1688_client.get_product_details",
        return_value={"980815374096": {
            "title": page_data["title"], "price": page_data["price"],
            "images": page_data["images"], "categories": [],
        }}))
    stack.enter_context(mock.patch(
        "scripts.lib.ak_1688_client.enrich_product_with_cdp",
        return_value={"data": page_data, "source": "cdp"}))
    stack.enter_context(mock.patch.object(
        cloud_probe, "_get_ozon_credentials",
        return_value={"client_id": "cid", "api_key": "akey"}))
    stack.enter_context(mock.patch.object(
        cloud_probe, "_get_mxou_token", return_value="tok"))
    stack.enter_context(mock.patch.object(
        cloud_probe, "_get_token", return_value="tok"))
    stack.enter_context(mock.patch(
        "scripts.lib.config_store.get_store_profile", return_value={}))
    stack.enter_context(mock.patch(
        "scripts.lib.config_store.get_template_profile", return_value={}))


def _build_draft(page_data: dict) -> dict:
    with ExitStack() as stack:
        _patch_env(stack, page_data)
        graph = cloud_probe.build_graph_envelope(
            item_id="980815374096", detail_url=_1688, poll_category=False)
    return graph["envelope"]["draft"]


# ── 信封级：优先级链端到端 ──


class TestUnitWeightPriorityEnvelope:
    def test_unit_weight_beats_gross_and_attr(self):
        """毛重 9150 + unitWeight 0.05kg + 属性 50g → draft.weight==50 带标记。"""
        data = _page_data(pageStructuredData=[_ctx_sd(
            unit_weight=0.05,
            feature_attrs=[{"name": "商品重量", "value": "50克"}],
        )])
        draft = _build_draft(data)
        assert draft["weight"] == 50
        assert draft["weight_source_page_attr"] is True

    def test_page_attr_beats_gross_when_no_ctx_unit_weight(self):
        """contextPath 无 unitWeight → 属性重量键接管（「0.05kg」→50g）。"""
        data = _page_data(
            pageStructuredData=[_ctx_sd(
                feature_attrs=[{"name": "单件重量", "value": "0.05kg"}])],
        )
        draft = _build_draft(data)
        assert draft["weight"] == 50
        assert draft["weight_source_page_attr"] is True

    def test_gross_only_is_legacy_byte_identical(self):
        """只有毛重 → 旧行为逐字保持：weight==毛重、零新增标记键。"""
        draft = _build_draft(_page_data())
        assert draft["weight"] == 9150
        assert "weight_source_page_attr" not in draft
        assert "weight_estimated" not in draft  # 真实毛重在埸非兜底
        assert "purchase_cost_representative_sku" not in draft

    def test_dirty_unit_weight_falls_back_to_gross(self):
        """unitWeight > 箱级毛重（12kg vs 9150g）→ 脏数据回落毛重、不打标。"""
        data = _page_data(pageStructuredData=[_ctx_sd(unit_weight=12.0)])
        draft = _build_draft(data)
        assert draft["weight"] == 9150
        assert "weight_source_page_attr" not in draft

    def test_dirty_attr_falls_back_to_gross(self):
        """属性重量 > 箱级毛重（12000g vs 9150g）→ 脏数据回落毛重、不打标。"""
        data = _page_data(pageStructuredData=[_ctx_sd(
            feature_attrs=[{"name": "商品重量", "value": "12000克"}])])
        draft = _build_draft(data)
        assert draft["weight"] == 9150
        assert "weight_source_page_attr" not in draft


# ── 选择器单测：优先级 + sanity + (0,10)g 下探 ──


class TestSelectUnitWeightG:
    def test_priority_context_path(self):
        w, src = cloud_probe._select_unit_weight_g(
            {"weightGrams": 9150}, {"商品重量": "50克"}, {"unit_weight": 50.0})
        assert (w, src) == (50, "contextPath")

    def test_priority_page_attr(self):
        w, src = cloud_probe._select_unit_weight_g(
            {"weightGrams": 9150}, {"净重": "0.05kg"}, {})
        assert (w, src) == (50, "page_attr")

    def test_priority_gross_fallback(self):
        w, src = cloud_probe._select_unit_weight_g({"weightGrams": 9150}, {}, {})
        assert (w, src) == (9150, "packaging_gross")

    def test_gross_missing_falls_to_weight_grams(self):
        """毛重缺 → data.weight_grams（AK offer_detail）兜底（旧 or 链语义）。"""
        w, src = cloud_probe._select_unit_weight_g({}, {}, {"weight_grams": 400})
        assert (w, src) == (400, "packaging_gross")

    def test_sub10g_unit_weight_descends_chain(self):
        """(0,10)g 按 _sanitize_weight_g 归零（Ozon 硬下限）→ 沿链下探属性。"""
        w, src = cloud_probe._select_unit_weight_g(
            {"weightGrams": 9150}, {"商品重量": "50克"}, {"unit_weight": 5.0})
        assert (w, src) == (50, "page_attr")


class TestAttrWeightParse:
    @pytest.mark.parametrize("val,expect", [
        ("50克", 50),
        ("0.05kg", 50),
        ("500g", 500),
        ("净重：53g", 53),
        ("1.2千克", 1200),
        ("2KG", 2000),
        ("40-50克", 40),  # 区间取首段（保守）
        ("", 0),
        ("无重量", 0),
        (None, 0),
    ])
    def test_parse(self, val, expect):
        assert cloud_probe._parse_attr_weight_g(val) == expect


# ── AK offer_detail kg 小数（对齐 worker _parse_weight_g 口径）──


class TestAkWeightDecimalKg:
    def test_kg_decimal_string_multiplied(self):
        """实锤回归："9.15" 旧裸 int() 截断成 9g → 现在 kg×1000=9150g。"""
        assert _extract_weight_dimensions({"weight": "9.15"})["weight_grams"] == 9150

    def test_plain_grams_untouched(self):
        assert _extract_weight_dimensions({"weight": "500"})["weight_grams"] == 500
        assert _extract_weight_dimensions({"weightGrams": 400})["weight_grams"] == 400

    def test_gram_suffix_string_untouched(self):
        assert _extract_weight_dimensions({"weight": "500g"})["weight_grams"] == 500

    def test_garbage_stays_none(self):
        assert _extract_weight_dimensions({"weight": "abc"})["weight_grams"] is None

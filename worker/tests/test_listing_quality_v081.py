# -*- coding: utf-8 -*-
"""v0.81 上架质量止血批（worker 侧）——四主题回归：

1. 箱级毛重 reconcile（唯一入口 utils/weight_dimension_normalizer.
   reconcile_weight_with_attrs）：skill 信封 draft.weight 是 1688 包装表第一行
   =箱级毛重（30支香 final_weight_g=962g 而卡属性「商品重量=50g」实锤）→
   pricing 运费计费重 / prepare 上卡重虚高数倍。判定：候选 ≥10g 且
   weight_g ≥ 3×候选 → 采信候选；证据不足原样返回零 marks（不 raise）。
2. 采购价低值标疑（pricing_node）：cost_cny <1 CNY → pricing_info 打
   purchase_cost_suspect=True（非阻断，真实单件商品存在）。
3. 默认表锁：8050（成分）硬编码「полимерные материалы」从 prepare
   _FALLBACK_FREE_TEXT_ATTRS 与 retry _KNOWN_DEFAULTS_RETRY 双删防复活。
4. 干扰类型属性判别词交叉验证（attr_defaults）：风扇类型关键词命中后同样过
   _discriminant_conflict——标题含「桌面/настольн」不得返回「Напольный」。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_listing_quality_v081.py -q
纯 mock（pricing 重依赖 monkeypatch），无 PG/网络。
"""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from utils.weight_dimension_normalizer import (  # noqa: E402
    reconcile_weight_with_attrs as _reconcile,
)

# ═══════════════ 1. 箱级毛重 reconcile（纯函数契约）═══════════════


class TestReconcileWeightPure:
    def test_lot_weight_with_attr_50g_reconciled(self):
        """实锤主案例：962g 信封重 + 「商品重量=50克」→ 50g 且带 reconcile mark。"""
        w, marks = _reconcile(962, {"商品重量": "50克"})
        assert w == 50.0
        assert marks == ["weight_lot_suspected_reconciled:962->50"]

    def test_kg_decimal_attr_parsed(self):
        """「0.05kg」→ 50g；「0.05」裸小数按 kg 纪律 → 50g。"""
        w, marks = _reconcile(962, {"商品重量": "0.05kg"})
        assert w == 50.0 and marks
        w2, _ = _reconcile(962, {"商品重量": "0.05"})
        assert w2 == 50.0

    def test_bare_number_attr_is_grams(self):
        w, marks = _reconcile(9150, {"净重": 50})
        assert w == 50.0 and marks == ["weight_lot_suspected_reconciled:9150->50"]

    def test_ratio_not_met_keeps_original(self):
        """120g vs 候选 50g（<3×）→ 不纠偏、零 marks（轻/中物保护）。"""
        w, marks = _reconcile(120, {"商品重量": "50克"})
        assert w == 120 and marks == []

    def test_candidate_below_10g_floor_ignored(self):
        """候选 <10g（Ozon 硬下限）→ 不采信（防 1g 裸数字 A3 老坑回潮）。"""
        w, marks = _reconcile(962, {"商品重量": "5克"})
        assert w == 962 and marks == []

    def test_no_attrs_returns_original(self):
        assert _reconcile(500, {}) == (500, [])
        assert _reconcile(500, None) == (500, [])
        assert _reconcile(500, "not-a-dict") == (500, [])

    def test_malformed_inputs_never_raise(self):
        """畸形输入契约：不得 raise，一律原样返回。"""
        assert _reconcile(None, {"商品重量": "50克"}) == (None, [])
        assert _reconcile("abc", {"商品重量": "50克"}) == ("abc", [])
        assert _reconcile(500, {"商品重量": object()}) == (500, [])
        assert _reconcile(500, {None: None}) == (500, [])
        assert _reconcile(-3, {"商品重量": "50克"}) == (-3, [])

    def test_package_level_attr_confirms_no_reconcile(self):
        """唯一证据是「含包装重量」且与信封一致（962 vs 962）→ 比值闸挡住，不纠偏。"""
        w, marks = _reconcile(962, {"含包装重量": "962克"})
        assert w == 962 and marks == []

    def test_priority_single_item_key_wins(self):
        """「商品重量」优先于「含包装重量」（单件语义 > 箱级语义）。"""
        w, _ = _reconcile(962, {"含包装重量": "962克", "商品重量": "50克"})
        assert w == 50.0


# ═══════════════ 1b. pricing 侧接线（运费计费重）═══════════════


class _DummyRuntime:
    class _DummyContext:
        pass

    def __init__(self):
        self.context = self._DummyContext()


def _make_pricing_state(draft=None, extensions=None, currency="RUB"):
    return SimpleNamespace(
        draft=draft
        or {
            "cost_cny": 5.5,
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        extensions=extensions or {},
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code=currency,
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id="17028830",
        task_id="",
        tenant_id="",
        token="sk-test",
    )


def _call_pricing(monkeypatch, state, logistics_cost=10.0, rate=12.0):
    from graphs.nodes import pricing_node as pn
    from utils import logistics_quote

    captured = {}

    def _fake_logistics(weight, depth, width, height, tpl, svc):
        captured["weight"] = weight
        return (logistics_cost, "mock_channel", {})

    monkeypatch.setattr(logistics_quote, "query_logistics_cost", _fake_logistics)
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: rate)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None, raising=False)
    out = pn.pricing_node(state, None, _DummyRuntime())
    return out, captured


class TestPricingWiring:
    def test_pricing_uses_reconciled_weight_for_logistics(self, monkeypatch):
        """信封 962g + 商品重量 50克 → 物流查询收到 50g，wd_audit 留 reconcile mark。"""
        state = _make_pricing_state(
            draft={
                "cost_cny": 5.5,
                "weight": 962,
                "dimensions": {"length": 120, "width": 80, "height": 60},
                "attributes": {"商品重量": "50克"},
            }
        )
        out, captured = _call_pricing(monkeypatch, state)
        assert out.error_message == "", f"不应失败: {out.error_message}"
        assert captured["weight"] == 50, f"运费计费重应为 50g，实际 {captured['weight']}"
        reasons = out.pricing_info["wd_audit"]["reasons"]
        assert any("weight_lot_suspected_reconciled:962->50" in r for r in reasons), reasons

    def test_pricing_no_reconcile_below_ratio(self, monkeypatch):
        """120g + 候选 50g（<3×）→ 物流查询仍收 120g，零 reconcile marks。"""
        state = _make_pricing_state(
            draft={
                "cost_cny": 5.5,
                "weight": 120,
                "dimensions": {"length": 120, "width": 80, "height": 60},
                "attributes": {"商品重量": "50克"},
            }
        )
        out, captured = _call_pricing(monkeypatch, state)
        assert out.error_message == ""
        assert captured["weight"] == 120
        reasons = out.pricing_info["wd_audit"]["reasons"]
        assert not any("weight_lot_suspected_reconciled" in r for r in reasons), reasons


# ═══════════════ 1c. prepare 侧接线（上卡重）═══════════════


class TestPrepareWiring:
    def test_prepare_reconciles_and_marks(self):
        """_resolve_weight_dimensions：962g→50g，marks 双通道留痕（reasons + 专用键）。"""
        from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions

        draft = {
            "weight": 962,
            "attributes": {"商品重量": "50克"},
            # 50×60×40mm=120cm³，50g 密度 0.417 ≥ 0.40 → 体积密度兜底不触发，
            # 保证断言只覆盖 reconcile
            "dimensions": {"length": 50, "width": 60, "height": 40},
        }
        weight_g, d, w, h = _resolve_weight_dimensions(draft, {})
        assert weight_g == 50
        marks = _resolve_weight_dimensions._wd_marks
        assert marks["weight_lot_reconciled"] == {"from": 962, "to": 50}
        assert any("weight_lot_suspected_reconciled:962->50" in r for r in marks["reasons"])

    def test_prepare_no_reconcile_keeps_weight(self):
        from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions

        draft = {
            "weight": 120,
            "attributes": {"商品重量": "50克"},
            "dimensions": {"length": 50, "width": 60, "height": 40},
        }
        weight_g, *_ = _resolve_weight_dimensions(draft, {})
        assert weight_g == 120
        assert "weight_lot_reconciled" not in _resolve_weight_dimensions._wd_marks


# ═══════════════ 2. 采购价低值标疑 ═══════════════


class TestPurchaseCostSuspect:
    def test_low_cost_flagged_but_priced(self, monkeypatch):
        """cost=0.13 → purchase_cost_suspect=True 且正常出价（非阻断）。"""
        state = _make_pricing_state(draft={"cost_cny": 0.13, "weight": 100})
        out, _ = _call_pricing(monkeypatch, state)
        assert out.error_message == "", f"低值标疑不得阻断: {out.error_message}"
        assert out.pricing_info.get("purchase_cost_suspect") is True
        assert out.pricing_info["price"] > 0, "标疑同时必须正常出价"

    def test_normal_cost_not_flagged(self, monkeypatch):
        """cost=8.5 → 不打标（零行为噪音，防回归既有 pricing 测试族）。"""
        state = _make_pricing_state()
        out, _ = _call_pricing(monkeypatch, state)
        assert out.error_message == ""
        assert "purchase_cost_suspect" not in out.pricing_info

    def test_zero_cost_default_path_not_flagged(self, monkeypatch):
        """cost=0 → 走既有 10.0 默认值分支（<=0 分支），不重复打低值标。"""
        state = _make_pricing_state(draft={"cost_cny": 0, "weight": 100})
        out, _ = _call_pricing(monkeypatch, state)
        assert out.error_message == ""
        assert "purchase_cost_suspect" not in out.pricing_info


# ═══════════════ 3. 默认表锁（8050 防复活）═══════════════


class TestCompositionDefaultRemoved:
    # 键值条目级匹配（注释里的历史说明不参与判定）
    _KEY_ENTRY_RE = None

    @staticmethod
    def _has_key_entry(src: str, attr_id: str) -> bool:
        import re
        return bool(re.search(rf"^\s*{attr_id}\s*:", src, re.M))

    def test_8050_removed_from_prepare_fallback_table(self):
        """prepare _FALLBACK_FREE_TEXT_ATTRS（函数内局部表）→ 源码级锁定无 8050 键。"""
        from graphs.nodes import prepare_ozon_upload_node as prep

        src = inspect.getsource(prep.prepare_ozon_upload_node)
        assert not self._has_key_entry(src, "8050"), "8050 成分硬编码默认不得在 prepare 兜底表复活"
        assert '"полимерные материалы"' not in src, "成分默认值文本不得复活"

    def test_8050_removed_from_retry_known_defaults(self):
        """retry _KNOWN_DEFAULTS_RETRY（error_repair_llm_node 局部表）→ 同锁。"""
        from graphs import validation_retry_loop as vrl

        src = inspect.getsource(vrl.error_repair_llm_node)
        assert not self._has_key_entry(src, "8050"), "8050 成分硬编码默认不得在 retry 已知默认表复活"
        assert '"полимерные материалы"' not in src

    def test_other_fallback_keys_kept(self):
        """爆炸半径控制：其余键（7578/10350/10351/8787）保持不动。"""
        from graphs.nodes import prepare_ozon_upload_node as prep

        src = inspect.getsource(prep.prepare_ozon_upload_node)
        for key in ("7578", "10350", "10351", "8787"):
            assert key in src, f"{key} 是既有兜底键，不得被误删"


# ═══════════════ 4. 干扰类型属性判别词交叉验证 ═══════════════


class TestInterferingTypeDiscriminant:
    """风扇类型关键词命中后必须过 _discriminant_conflict（与 8229 主分支同款）。"""

    _DICT_VALS = [
        {"id": 11, "value": "Напольный вентилятор"},
        {"id": 22, "value": "Настольный вентилятор"},
    ]

    def test_desktop_title_never_returns_floor_value(self):
        """产品名 настольн（桌面）作为关键词源时，2-gram「На」会先撞上 Напольный——
        无判别词验证时错返落地形态（修复前实际行为），修复后必须返回 None。"""
        from utils.attr_defaults import resolve_missing_mandatory_dict_attr

        res = resolve_missing_mandatory_dict_attr(
            1225, "Тип вентилятора",
            title_cn="",
            product_name_ru="Настольный мини вентилятор",
            dict_vals=self._DICT_VALS,
        )
        assert res is None, f"桌面风扇不得命中落地形态值，实际 {res}"

    def test_desktop_chinese_title_never_returns_floor_value(self):
        """中文判别词同义覆盖：标题「桌面」+ 产品名 настольн → 互斥落地值恒不返回。"""
        from utils.attr_defaults import resolve_missing_mandatory_dict_attr

        res = resolve_missing_mandatory_dict_attr(
            1225, "Тип вентилятора",
            title_cn="桌面迷你风扇",
            product_name_ru="Настольный мини вентилятор",
            dict_vals=self._DICT_VALS,
        )
        assert res is None or res[1] == "Настольный вентилятор", (
            f"桌面风扇不得命中 Напольный，实际 {res}"
        )

    def test_matching_form_still_resolves(self):
        """正面对照：产品名就是 Напольный（落地）→ 正常命中该值（判别词不误杀）。"""
        from utils.attr_defaults import resolve_missing_mandatory_dict_attr

        res = resolve_missing_mandatory_dict_attr(
            1225, "Тип вентилятора",
            title_cn="",
            product_name_ru="Напольный вентилятор",
            dict_vals=self._DICT_VALS,
        )
        assert res == (11, "Напольный вентилятор"), f"落地风扇应命中 Напольный，实际 {res}"

    def test_desktop_exact_hit_unaffected(self):
        """单值字典 + 形态一致（Настольный）→ 判别词验证不误杀，正常命中。"""
        from utils.attr_defaults import resolve_missing_mandatory_dict_attr

        res = resolve_missing_mandatory_dict_attr(
            1225, "Тип вентилятора",
            title_cn="",
            product_name_ru="Настольный вентилятор",
            dict_vals=[{"id": 22, "value": "Настольный вентилятор"}],
        )
        assert res == (22, "Настольный вентилятор"), f"实际 {res}"

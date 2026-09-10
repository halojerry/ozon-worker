#!/usr/bin/env python3
"""discover 跨平台静默货源匹配 v1 批2 — 同款确认与选优（source_matcher）TDD。

纯函数零 I/O（无 Chrome/CDP/网络），全 mock 数据覆盖：
- confirm_same_product：同款置信度阈值两侧 / None 标题 / 规格词（容量·材质）
  加权降分 / 完全无关标题
- pick_best_source：换源（超阈值）/ 不换（便宜但未达线）/ 不换（非同款）/
  平手保 1688 / None 价跳过 / None 运费标记 unknown / 销量 tie-break /
  空候选 / 基线 ≤0 不换源
- extract_search_keyword：修饰词剥离 + 核心词（_MODIFIER_WORDS worker 先例的
  平台无关瘦身口径）
- env 覆盖：CROSS_SOURCE_SAME_MIN / CROSS_SOURCE_SWITCH_RATIO（调用时读取，
  monkeypatch.setenv 即时生效——test_metrics_pool_client 同款 idioms）

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_source_matcher.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import source_matcher as sm  # noqa: E402
from scripts.lib.cross_source_search import SourceOffer  # noqa: E402

# 1688 命中标题参照真值（pick_best_source candidate_title 用）
BASE = "304不锈钢保温杯500ml"


# ──────────────────── confirm_same_product ────────────────────


class TestConfirmSameProduct:
    def test_identical_titles_full_score(self):
        t = "304不锈钢保温杯500ml带茶滤"
        assert sm.confirm_same_product(t, t) == pytest.approx(1.0)

    def test_none_offer_title_zero(self):
        assert sm.confirm_same_product("保温杯500ml", None) == 0.0

    def test_empty_offer_title_zero(self):
        assert sm.confirm_same_product("保温杯500ml", "   ") == 0.0

    def test_empty_candidate_zero(self):
        assert sm.confirm_same_product("", "保温杯500ml") == 0.0

    def test_same_product_different_wording_above_threshold(self):
        # 304 vs 316 材质不同会压分，但容量 500ml=500毫升 归一命中 + 「保温杯」
        # 核心词重叠 → 高于 SAME_PRODUCT_MIN（同款确认过闸）。
        score = sm.confirm_same_product("304不锈钢保温杯500ml", "500毫升316保温杯")
        assert score >= sm.SAME_PRODUCT_MIN
        assert score < 1.0

    def test_unrelated_titles_below_threshold(self):
        score = sm.confirm_same_product("304不锈钢保温杯500ml", "苹果手机壳透明防摔")
        assert score < sm.SAME_PRODUCT_MIN

    def test_volume_mismatch_lowers_score(self):
        same = sm.confirm_same_product("保温杯500ml", "保温杯500ml大容量")
        diff = sm.confirm_same_product("保温杯500ml", "保温杯1500ml大容量")
        assert diff < same  # 容量不符压分（未必拦下，但必须降分）

    def test_material_grade_mismatch_lowers_below_threshold(self):
        # 材质牌号是硬规格：316 vs 304 无其他强信号时应低于换源同款线
        # （错换材质同款是静默模式最贵错误——宁保 1688）。
        score = sm.confirm_same_product("316保温杯", "304保温杯")
        assert score < sm.SAME_PRODUCT_MIN

    def test_material_grade_match_helps(self):
        match = sm.confirm_same_product("316保温杯500ml", "316不锈钢杯500ml")
        other = sm.confirm_same_product("316保温杯500ml", "201不锈钢杯500ml")
        assert match > other

    def test_model_code_mismatch_lowers(self):
        same = sm.confirm_same_product("保温杯型号A500", "保温杯型号A500官方")
        diff = sm.confirm_same_product("保温杯型号A500", "保温杯型号B700官方")
        assert diff < same

    def test_score_bounded(self):
        for offer in ("", "杯", "500ml", " completely unrelated text"):
            assert 0.0 <= sm.confirm_same_product("保温杯500ml", offer) <= 1.0


# ──────────────────── pick_best_source ────────────────────


def _offer(platform, title, price=None, freight=None, sold=None, url=""):
    return SourceOffer(
        platform=platform,
        title=title,
        price=price,
        freight=freight,
        sold=sold,
        url=url or f"https://x.example/{platform}/{title or 'no-title'}",
    )


class TestPickBestSource:
    def test_switch_when_significantly_cheaper(self):
        offers = {
            "taobao": [_offer("taobao", "500毫升316保温杯", 15.0, 0.0, 2300)],
            "pdd": [_offer("pdd", "500毫升316保温杯", 9.9, None, 500)],
        }
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.switched is True
        assert d.winner is not None and d.winner.platform == "pdd"
        assert d.reason.startswith("pdd 换源")
        # None freight → 计 0 但必须带 freight_unknown 标记
        best = d.platform_bests["pdd"]
        assert best.landed_cost == pytest.approx(9.9)
        assert best.freight_unknown is True

    def test_no_switch_cheaper_but_below_threshold(self):
        # 便宜 5%（18.0 < 18.5）但未达 0.90 换源线 → 平手保 1688
        offers = {"taobao": [_offer("taobao", BASE, 18.0, 0.0, 100)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.switched is False
        assert d.winner is None
        assert "1688 保持" in d.reason

    def test_no_switch_not_same_product(self):
        offers = {"taobao": [_offer("taobao", "苹果手机壳透明防摔", 3.0, 0.0, 9000)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.switched is False
        assert d.winner is None
        assert "同款确认" in d.reason
        # 未过确认的报价不得进入 platform_bests（快照只有过闸平台）
        assert "taobao" not in d.platform_bests
        # 但确认分照记（快照透明度）
        assert d.confirm_scores

    def test_tie_keeps_1688(self):
        # 到手价恰好等于基线×0.9 → 严格小于不成立 → 保 1688（安全底线）
        offers = {"taobao": [_offer("taobao", BASE, 16.65, 0.0, 100)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.switched is False
        assert d.winner is None

    def test_none_price_skipped(self):
        offers = {"taobao": [_offer("taobao", BASE, None, 0.0, 100)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.switched is False
        assert d.winner is None
        assert "taobao" not in d.platform_bests

    def test_none_freight_counts_zero_but_flagged(self):
        offers = {"taobao": [_offer("taobao", BASE, 10.0, None, 100)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        best = d.platform_bests["taobao"]
        assert best.freight_unknown is True
        assert best.landed_cost == pytest.approx(10.0)
        assert d.switched is True

    def test_freight_added_to_landed_cost(self):
        offers = {"taobao": [_offer("taobao", BASE, 8.0, 6.0, 100)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        best = d.platform_bests["taobao"]
        assert best.freight_unknown is False
        assert best.landed_cost == pytest.approx(14.0)

    def test_sold_tiebreak_between_platforms(self):
        # 两平台到手价同为 9.9 → 销量高者胜；None sold 排最低
        offers = {
            "taobao": [_offer("taobao", BASE, 9.9, 0.0, None)],
            "pdd": [_offer("pdd", BASE, 9.9, 0.0, 800)],
        }
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.winner is not None and d.winner.platform == "pdd"

    def test_cheapest_wins_over_sold(self):
        # 销量 tie-break 只在平价时生效——更便宜者无条件胜出
        offers = {
            "taobao": [_offer("taobao", BASE, 9.0, 0.0, 10)],
            "pdd": [_offer("pdd", BASE, 9.5, 0.0, 99999)],
        }
        d = sm.pick_best_source(offers, 18.5, BASE)
        assert d.winner is not None and d.winner.platform == "taobao"

    def test_empty_offers_keep_1688(self):
        d = sm.pick_best_source({}, 18.5)
        assert d.switched is False
        assert d.winner is None
        assert d.platform_bests == {}
        assert d.confirm_scores == {}

    def test_empty_platform_list_keep_1688(self):
        d = sm.pick_best_source({"taobao": [], "pdd": []}, 18.5)
        assert d.switched is False
        assert d.winner is None

    def test_baseline_non_positive_never_switches(self):
        offers = {"taobao": [_offer("taobao", BASE, 0.5, 0.0, 100)]}
        for baseline in (0.0, -3.0):
            d = sm.pick_best_source(offers, baseline)
            assert d.switched is False
            assert d.winner is None
            assert "1688 保持" in d.reason

    def test_decision_carries_effective_thresholds(self):
        d = sm.pick_best_source({}, 18.5)
        assert d.baseline_1688_cost == pytest.approx(18.5)
        assert d.same_min == pytest.approx(sm.SAME_PRODUCT_MIN)
        assert d.switch_ratio == pytest.approx(sm.SWITCH_MIN_RATIO)

    def test_platform_bests_carries_confirm_score(self):
        offers = {"pdd": [_offer("pdd", "500毫升316保温杯", 9.9, None, 500)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        best = d.platform_bests["pdd"]
        assert 0.0 <= best.confirm_score <= 1.0
        assert best.confirm_score >= d.same_min
        # 全量确认分快照（按 url 键）覆盖该 offer
        assert best.offer.url in d.confirm_scores


# ──────────────────── extract_search_keyword ────────────────────


class TestExtractSearchKeyword:
    def test_real_1688_title_strips_modifiers(self):
        # 家用/ins 是修饰词（worker _MODIFIER_WORDS 先例口径）；核心 = 卡吞餐盘
        kw = sm.extract_search_keyword("卡吞餐盘 家用陶瓷 北欧 ins")
        assert kw is not None
        assert "卡吞餐盘" in kw
        assert "ins" not in kw
        assert "家用" not in kw

    def test_none_and_empty_return_none(self):
        assert sm.extract_search_keyword(None) is None
        assert sm.extract_search_keyword("") is None
        assert sm.extract_search_keyword("   ") is None

    def test_modifier_only_title_returns_none(self):
        assert sm.extract_search_keyword("新款 ins 网红 爆款") is None

    def test_modifier_substring_stripped_from_token(self):
        # 修饰词内嵌在 token 里（无空格分隔的长标题）也要剥
        kw = sm.extract_search_keyword("儿童保温杯316不锈钢")
        assert kw is not None
        assert "儿童" not in kw
        assert "保温杯" in kw

    def test_length_capped(self):
        kw = sm.extract_search_keyword("卡吞餐盘 家用陶瓷 北欧 ins 网红餐具套装")
        assert kw is not None
        assert len(kw) <= 12

    def test_keeps_spec_tokens(self):
        kw = sm.extract_search_keyword("保温杯500ml 316不锈钢")
        assert kw is not None
        assert "500ml" in kw


# ──────────────────── env 覆盖 ────────────────────


class TestEnvOverrides:
    def test_same_min_env_raises_bar(self, monkeypatch):
        offers = {"taobao": [_offer("taobao", "500毫升316保温杯", 9.9, 0.0, 100)]}
        d0 = sm.pick_best_source(offers, 18.5, BASE)
        assert "taobao" in d0.platform_bests  # 默认阈值下过确认
        monkeypatch.setenv("CROSS_SOURCE_SAME_MIN", "0.99")
        d1 = sm.pick_best_source(offers, 18.5, BASE)
        assert "taobao" not in d1.platform_bests  # 阈值抬高 → 同款确认不过
        assert d1.same_min == pytest.approx(0.99)

    def test_switch_ratio_env_lowers_bar(self, monkeypatch):
        # 默认 0.90：便宜 ~5% 不换源
        offers = {"taobao": [_offer("taobao", "500毫升316保温杯", 17.0, 0.0, 100)]}
        d0 = sm.pick_best_source(offers, 18.5, BASE)
        assert d0.switched is False
        monkeypatch.setenv("CROSS_SOURCE_SWITCH_RATIO", "0.95")
        d1 = sm.pick_best_source(offers, 18.5, BASE)
        assert d1.switched is True
        assert d1.switch_ratio == pytest.approx(0.95)

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CROSS_SOURCE_SAME_MIN", "not-a-number")
        monkeypatch.setenv("CROSS_SOURCE_SWITCH_RATIO", "")
        d = sm.pick_best_source({}, 18.5)
        assert d.same_min == pytest.approx(sm.SAME_PRODUCT_MIN)
        assert d.switch_ratio == pytest.approx(sm.SWITCH_MIN_RATIO)

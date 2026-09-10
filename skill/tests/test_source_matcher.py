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
        # 同规格（304+500ml）异措辞：「500毫升」归一 = 「500ml」，核心词重叠
        # → 高于 SAME_PRODUCT_MIN（同款确认过闸）；带尾缀不至满分。
        score = sm.confirm_same_product("304不锈钢保温杯500ml", "500毫升304保温杯带茶滤")
        assert score >= sm.SAME_PRODUCT_MIN
        assert score < 1.0

    def test_unrelated_titles_below_threshold(self):
        score = sm.confirm_same_product("304不锈钢保温杯500ml", "苹果手机壳透明防摔")
        assert score < sm.SAME_PRODUCT_MIN

    # ── Fix Round 1（Important #1）：同类型规格矛盾一票否决 ──
    # 真实标题场景：base 被「不锈钢保温杯500ml」整段重叠抬高后，×0.65 乘法
    # 惩罚压不住（fix 前实测 0.7071 过阈）——牌号/容量双方都显式声明且不同 →
    # 直接封顶 ≤0.30（深掉阈下），不是调 SAME_PRODUCT_MIN 治标。

    def test_real_title_grade_mismatch_vetoed(self):
        score = sm.confirm_same_product("316不锈钢保温杯500ml", "304不锈钢保温杯500ml")
        assert score <= 0.30

    def test_real_title_grade_and_volume_double_mismatch_vetoed(self):
        score = sm.confirm_same_product("304不锈钢保温杯1500ml", "316不锈钢保温杯500ml")
        assert score <= 0.30

    def test_real_title_volume_mismatch_vetoed(self):
        score = sm.confirm_same_product("保温杯500ml大容量", "保温杯1500ml大容量")
        assert score <= 0.30

    def test_same_spec_no_false_veto(self):
        # 归一等价（500ml=500毫升）与「offer 牌号命中候选多个声明之一」不得误伤。
        assert sm.confirm_same_product(
            "316保温杯500ml", "500毫升316不锈钢杯") >= sm.SAME_PRODUCT_MIN
        assert sm.confirm_same_product(
            "304/316不锈钢保温杯500ml", "316不锈钢保温杯500ml") >= sm.SAME_PRODUCT_MIN

    def test_volume_mismatch_lowers_score(self):
        same = sm.confirm_same_product("保温杯500ml", "保温杯500ml大容量")
        diff = sm.confirm_same_product("保温杯500ml", "保温杯1500ml大容量")
        assert diff < same  # 容量不符压分（Fix Round 1 起为矛盾一票否决）

    def test_material_grade_mismatch_lowers_below_threshold(self):
        # 材质牌号是硬规格：316 vs 304 无其他强信号时深掉阈下（Fix Round 1 起
        # 为矛盾封顶 0.30，不再依赖乘法惩罚在退化标题上的假安全感）。
        score = sm.confirm_same_product("316保温杯", "304保温杯")
        assert score <= 0.30
        assert score < sm.SAME_PRODUCT_MIN

    def test_material_grade_match_helps(self):
        match = sm.confirm_same_product("316保温杯500ml", "316不锈钢杯500ml")
        other = sm.confirm_same_product("316保温杯500ml", "201不锈钢杯500ml")
        assert match > other

    def test_model_code_mismatch_lowers(self):
        same = sm.confirm_same_product("保温杯型号A500", "保温杯型号A500官方")
        diff = sm.confirm_same_product("保温杯型号A500", "保温杯型号B700官方")
        assert diff < same

    # ── Fix Round 1（Minor #3）：spec 加权方向不对称固化（防无意翻转）──

    def test_spec_penalty_is_candidate_side_only(self):
        # 候选声明规格、offer 缺失 → 压分（证据不足按保守口径 ×0.65）；反向
        # （候选没写、offer 声明）→ 中性不罚。数值钉死防实现翻转。
        cand_declares = sm.confirm_same_product("保温杯500ml", "保温杯带茶滤")
        offer_declares = sm.confirm_same_product("保温杯", "保温杯500ml大容量")
        assert cand_declares == pytest.approx(0.4333)
        assert offer_declares == pytest.approx(1.0)

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
            "taobao": [_offer("taobao", "500毫升304保温杯", 15.0, 0.0, 2300)],
            "pdd": [_offer("pdd", "500毫升304保温杯", 9.9, None, 500)],
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
        offers = {"pdd": [_offer("pdd", "500毫升304保温杯", 9.9, None, 500)]}
        d = sm.pick_best_source(offers, 18.5, BASE)
        best = d.platform_bests["pdd"]
        assert 0.0 <= best.confirm_score <= 1.0
        assert best.confirm_score >= d.same_min
        # 全量确认分快照（按 url 键）覆盖该 offer
        assert best.offer.url in d.confirm_scores

    def test_no_candidate_title_reason_distinguishable(self):
        # Fix Round 1（Minor #1）：candidate_title 空 = 无从确认 → 专门文案
        # （批4 gate 调试要分得清「没参照」vs「没过确认」）。
        offers = {"taobao": [_offer("taobao", "任意标题", 9.9, 0.0, 10)]}
        d = sm.pick_best_source(offers, 18.5)  # candidate_title 默认空
        assert d.switched is False
        assert d.winner is None
        assert "无参照标题" in d.reason


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

    # ── Gate Fix（批4 实机 gate 揪出）：无空格长 CJK 连写标题是 1688 常态 ──
    # 修前：单 token 超 12 字上限 → 主路径空手 → None → 快照 {"skipped":
    # "无参照标题"}、跨源比价整段跳过（3 个 profitable 候选全中）。修后回退
    # 清洗标题前缀截断（精度由 confirm_same_product 把关，分层职责）。

    def test_long_cjk_title_without_spaces(self):
        # 批4 gate 真实复现（profitable 候选的 1688 标题形态）
        kw = sm.extract_search_keyword(
            "卡通可爱双饮保温杯高颜值萌趣儿童水杯吸管杯316不锈钢便携")
        assert kw is not None
        assert "保温杯" in kw
        assert "儿童" not in kw
        assert len(kw) <= 16

    def test_grade_volume_title_without_spaces(self):
        # 批4 gate 真实复现第二条：规格词占前缀且无空格
        kw = sm.extract_search_keyword("316不锈钢保温杯500ml")
        assert kw is not None
        assert "保温杯" in kw

    def test_fallback_still_strips_modifiers(self):
        # 回退路径也必须剥修饰词——全修饰词标题仍 None（不因回退放垃圾进搜索框）
        assert sm.extract_search_keyword("新款ins网红爆款") is None


# ──────────────────── env 覆盖 ────────────────────


class TestEnvOverrides:
    def test_same_min_env_raises_bar(self, monkeypatch):
        # offer 取「同规格带尾缀」变体（confirm≈0.57，落在默认阈上/0.99 阈下）
        offers = {"taobao": [_offer("taobao", "500毫升304保温杯带茶滤", 9.9, 0.0, 100)]}
        d0 = sm.pick_best_source(offers, 18.5, BASE)
        assert "taobao" in d0.platform_bests  # 默认阈值下过确认
        monkeypatch.setenv("CROSS_SOURCE_SAME_MIN", "0.99")
        d1 = sm.pick_best_source(offers, 18.5, BASE)
        assert "taobao" not in d1.platform_bests  # 阈值抬高 → 同款确认不过
        assert d1.same_min == pytest.approx(0.99)

    def test_switch_ratio_env_lowers_bar(self, monkeypatch):
        # 默认 0.90：便宜 ~5% 不换源
        offers = {"taobao": [_offer("taobao", "500毫升304保温杯", 17.0, 0.0, 100)]}
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

    def test_ratio_env_above_one_clamped(self, monkeypatch):
        # Fix Round 1（Minor #2）：ratio > 1.0 会「换更贵的也换源」——clamp 到 1.0
        monkeypatch.setenv("CROSS_SOURCE_SWITCH_RATIO", "1.5")
        d = sm.pick_best_source({}, 18.5)
        assert d.switch_ratio == pytest.approx(1.0)

    def test_same_min_env_above_one_clamped(self, monkeypatch):
        # same_min > 1.0 无意义（分数上限 1.0）——clamp 到 1.0
        monkeypatch.setenv("CROSS_SOURCE_SAME_MIN", "2.5")
        d = sm.pick_best_source({}, 18.5)
        assert d.same_min == pytest.approx(1.0)

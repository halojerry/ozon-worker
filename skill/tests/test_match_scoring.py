#!/usr/bin/env python3
"""P0-4 货源匹配评分信号换轨单测（2026-09-09 审计靶点二）。

用户实机症状：类目/货源匹配置信度长期 0.11~0.38 → worker 宁入箱不自动上。
根因：`_title_conf` 纯标题文本相关性独占 confidence——aibuy 视觉官方信号
（normalizationScore）只微调排序分，1688 类目 ↔ Ozon 类目一致性完全不参与。

修复契约（本文件锁定，skill/scripts/lib/match_scoring.py）：
  - 复合评分 = 类目一致性(0.45) + 图搜官方信号(0.35) + 标题文本(0.20)；
  - 类目上下文缺失时按比例回落 visual/title（不低于旧纯文本口径）；
  - matchBadgeFull 徽章直通 visual=1.0 且 trusted=True；
  - 纯函数零依赖（不引 jieba，编译面最小），ozon_discovery 后续以
    score_match(...) 替换 _conf_of_best（集成步骤见 plan P0-4，因目标文件
    有他会话 WIP 暂缓接线）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_match_scoring.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.match_scoring import (
    DEFAULT_WEIGHTS,
    category_consistency,
    score_match,
    visual_signal,
)


# ── 类目一致性 ──


def test_category_consistency_full_overlap():
    assert category_consistency("保温杯", "厨房家居 > 保温杯 > 不锈钢保温杯") == pytest.approx(1.0)


def test_category_consistency_partial_overlap():
    # 候选 2 个 token 命中 1 个 → 0.5
    assert category_consistency("汽车 暖风机", "家居家居 > 取暖电器 > 暖风机") == pytest.approx(0.5)


def test_category_consistency_zero_or_missing():
    assert category_consistency("手机壳", "厨房家居 > 保温杯") == 0.0
    assert category_consistency("", "厨房家居 > 保温杯") == 0.0
    assert category_consistency("保温杯", "") == 0.0
    assert category_consistency(None, None) == 0.0


def test_category_consistency_russian_path():
    assert category_consistency("Посуда", "Дом и сад > Посуда > Термосы") == pytest.approx(1.0)


def test_category_consistency_no_stemming_by_design():
    """俄语复数（термос/термосы）不做词干化——零依赖分词宁缺毋滥，
    复数误配交给 aibuy 官方信号兜底。"""
    assert category_consistency("Термос", "Дом и сад > Термосы") == 0.0


# ── 图搜官方信号 ──


def test_visual_badge_full_shortcut():
    assert visual_signal(1.0, 0.01) == 1.0
    assert visual_signal(1.0, 0.0) == 1.0


def test_visual_normalization_only():
    assert visual_signal(0.0, 0.8) == pytest.approx(0.8)
    assert visual_signal(0.0, -1) == 0.0
    assert visual_signal(0.0, None) == 0.0


def test_visual_partial_badge_is_evidence_itself():
    """F-B02 用户口径：CDP 官方部分徽章（符合2/3 → badge_eff=0.667）是 1688
    官方图搜判定本身，恒优先于归一相似度——此前只认 matchBadgeFull 字符串，
    CDP 官方「全部符合」进不了分，跨语言标题 0 分被误杀（真单 15 单实证）。"""
    assert visual_signal(0.667, 0.0) == pytest.approx(0.667)
    assert visual_signal(0.667, 0.9) == pytest.approx(0.667)  # 徽章优先，不被相似度覆盖
    assert visual_signal(0.5, 0.0) == pytest.approx(0.5)


# ── 复合评分 ──


def test_score_full_context_formula():
    cand = {"category_name": "保温杯", "badge_eff": 0.0, "normalization_score": 0.6}
    out = score_match(title_conf=0.2, candidate=cand,
                      ozon_category_path="厨房家居 > 保温杯")
    # 0.45*1.0 + 0.35*0.6 + 0.20*0.2 = 0.45+0.21+0.04
    assert out["confidence"] == pytest.approx(0.70)
    assert out["components"]["category"] == pytest.approx(1.0)


def test_score_without_category_context_falls_back():
    """类目上下文缺失但有官方视觉信号：权重按在场分量归一（visual:title=0.35:0.20）。"""
    out = score_match(title_conf=0.2, candidate={"normalization_score": 0.0})
    assert out["confidence"] == pytest.approx(0.2 * (0.20 / 0.55))
    assert "category" not in out["components"]


def test_cdp_badge_text_eff_rescues_cross_language_zero_title():
    """端到端锁 F-B02 主诉：CDP 徽章「符合3/3」（badge_eff=1.0 注入）+ 类目缺席 +
    跨语言标题 0 分 → 视觉分量独扛，conf = 0.35/0.55 ≈ 0.636 过 0.30 护栏，
    不再被误杀。"""
    cand = {"badge_eff": 1.0, "normalization_score": 0.0}
    out = score_match(title_conf=0.0, candidate=cand, ozon_category_path="")
    assert out["confidence"] == pytest.approx(0.35 / 0.55)
    assert out["trusted"] is True
    # 部分徽章 2/3：conf = 0.35*0.667/0.55 ≈ 0.424，同样过线
    out2 = score_match(title_conf=0.0, candidate={"badge_eff": 2 / 3}, ozon_category_path="")
    assert out2["confidence"] == pytest.approx((0.35 * (2 / 3)) / 0.55)


def test_score_no_signals_at_all_preserves_title_only():
    """CDP 通道候选（零官方信号零类目）：conf==title——与旧 _title_conf 口径
    逐字一致，纯文本匹配不被无信号压分（回归锁定）。"""
    out = score_match(title_conf=0.5, candidate={"title": "whatever"})
    assert out["confidence"] == pytest.approx(0.5)
    out2 = score_match(title_conf=0.1, candidate={})
    assert out2["confidence"] == pytest.approx(0.1)


def test_similarity_score_counts_as_visual_signal():
    """AK 通道 similarity_score（0-100 或 0-1 都可能）在候选侧归一后进视觉分量。
    模块只认 normalization_score 键——AK 0-100 归一由接线方完成（见 plan）。"""
    out = score_match(title_conf=0.0, candidate={"normalization_score": 0.8})
    # 在场分量归一：(0.35*0.8 + 0.2*0.0) / 0.55
    assert out["confidence"] == pytest.approx(0.28 / 0.55)


def test_score_low_everything_stays_low_but_reported():
    """旧行为对照：纯文本低分候选不再被拉高——换轨只加信号，不做无中生有。"""
    out = score_match(title_conf=0.15,
                      candidate={"category_name": "手机壳", "normalization_score": 0.1},
                      ozon_category_path="厨房家居 > 保温杯")
    assert out["confidence"] < 0.2
    assert out["trusted"] is False


def test_badge_full_is_trusted_and_boosts():
    cand = {"badge": "matchBadgeFull", "normalization_score": 0.2,
            "category_name": "保温杯"}
    out = score_match(title_conf=0.1, candidate=cand,
                      ozon_category_path="厨房家居 > 保温杯")
    assert out["components"]["visual"] == 1.0
    assert out["trusted"] is True
    assert out["confidence"] == pytest.approx(0.45 + 0.35 + 0.02)


def test_confidence_clamped():
    out = score_match(title_conf=99, candidate={"normalization_score": 99},
                      ozon_category_path="保温杯")
    assert 0.0 <= out["confidence"] <= 1.0


def test_weights_override():
    out = score_match(title_conf=1.0,
                      candidate={"category_name": "保温杯", "normalization_score": 0.0},
                      ozon_category_path="厨房家居 > 保温杯",
                      weights={"category": 0.0, "visual": 0.0, "title": 1.0})
    assert out["confidence"] == pytest.approx(1.0)


def test_default_weights_sum_to_one():
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


# ── ozon_discovery 接线级（P0-4 收尾）──


def test_pick_best_match_composite_rescues_official_signal_candidate():
    """接线级：aibuy 官方视觉信号在场 + RU 面包屑（与 ZH 类目名跨语言零重叠
    → 类目不加分也不稀释）→ 弱标题候选经 LLM 同品确认后携带复合置信度
    （旧口径附加 conf≈标题分，喂给 worker 即 0.11~0.38 误低分）。"""
    from unittest import mock

    from scripts.lib import ozon_discovery as od

    cand = {"id": "1", "title": "保温杯女学生可爱", "price": 9.9,
            "normalization_score": 0.6, "category_name": "保温杯"}
    with mock.patch.object(od, "_llm_semantic_match", return_value=True):
        best = od._pick_best_match(
            [cand], "Термос для воды нержавеющая сталь",
            ozon_category_path="Дом и сад > Посуда > Термосы")
    assert best is not None, "LLM 同品确认 + 官方视觉信号应放行"
    assert best["confidence"] >= 0.3, f"复合置信度应≥0.3, got {best.get('confidence')}"


def test_pick_best_match_category_boost_when_same_language():
    """类目同语种命中：类目一致性分量真实加分（ZH 类目名 × ZH 路径）。"""
    from unittest import mock

    from scripts.lib import ozon_discovery as od

    cand = {"id": "2", "title": "咖啡随行杯", "price": 5.0,
            "normalization_score": 0.2, "category_name": "保温杯"}
    with mock.patch.object(od, "_llm_semantic_match", return_value=True):
        best = od._pick_best_match(
            [cand], "便携咖啡杯 小巧", ozon_category_path="厨房家居 > 保温杯")
    assert best is not None
    # conf = 0.45*1 + 0.35*0.2 + 0.2*title(≈低) ≥ 0.5（旧纯文本口径会被拒）
    assert best["confidence"] >= 0.5


# 护栏「标题门控」语义由 test_aibuy_search.py::test_pick_best_match_trusted_rank3_still_guardrailed
# 锁定（rank≥3 + 弱标题 → 复合分不放行）——该用例在本批语义切分后保持绿。


# ── AK 详情回填 1688 类目（F-B02 延伸）──

def test_backfill_1688_category_from_ak_detail():
    """过闸匹配类目名缺失 → AK 详情「商品类目」表各级名拼接回填。"""
    from unittest import mock
    from scripts.lib import ozon_discovery as od

    cand = od.ProductCandidate(
        ozon_product_id="1", ozon_title="Термос", ozon_price=1000.0,
        ozon_images=["x"],
    )
    cand.match_1688_url = "https://detail.1688.com/offer/123456.html"
    cand.match_1688_category_name = ""
    with mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={"123456": {"categories": [
                        {"level": "一级类目", "name": "家居用品"},
                        {"level": "二级类目", "name": "餐厨 Water Cups"},
                        {"level": "三级类目", "name": "保温杯"},
                    ]}}):
        od._backfill_1688_category(cand)
    assert cand.match_1688_category_name == "家居用品 > 餐厨 Water Cups > 保温杯"


def test_backfill_skips_when_name_present_or_url_bad():
    from unittest import mock
    from scripts.lib import ozon_discovery as od
    cand = od.ProductCandidate(
        ozon_product_id="1", ozon_title="x", ozon_price=1.0, ozon_images=["x"],
    )
    cand.match_1688_url = "https://detail.1688.com/offer/1.html"
    cand.match_1688_category_name = "已有类目"
    with mock.patch("scripts.lib.ak_1688_client.get_product_details") as gp:
        od._backfill_1688_category(cand)
        gp.assert_not_called()
    # URL 无 offer id → 不触发
    cand2 = od.ProductCandidate(
        ozon_product_id="2", ozon_title="x", ozon_price=1.0, ozon_images=["x"],
    )
    cand2.match_1688_url = "https://example.com/nothing"
    with mock.patch("scripts.lib.ak_1688_client.get_product_details") as gp:
        od._backfill_1688_category(cand2)
        gp.assert_not_called()


def test_backfill_failure_is_silent():
    from unittest import mock
    from scripts.lib import ozon_discovery as od
    cand = od.ProductCandidate(
        ozon_product_id="1", ozon_title="x", ozon_price=1.0, ozon_images=["x"],
    )
    cand.match_1688_url = "https://detail.1688.com/offer/9.html"
    with mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    side_effect=RuntimeError("ak down")):
        od._backfill_1688_category(cand)  # 不 raise
    assert not cand.match_1688_category_name


# ── 类目一致性二次复核（F-B02 收口）──

def _mk_cand(od, conf, zh_cat, ru_cat):
    c = od.ProductCandidate(
        ozon_product_id="1", ozon_title="Термос", ozon_price=1000.0,
        ozon_images=["x"],
    )
    c.match_confidence = conf
    c.match_1688_category_name = zh_cat
    c.page_category_path = ru_cat
    return c


def _llm_resp(content, code=200):
    m = mock.Mock()
    m.status_code = code
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    return m


def test_category_review_downweights_on_divergence():
    """类目语义不一致 → conf 封顶 0.5 + match_category_divergent 标记。"""
    from unittest import mock as _mock
    from scripts.lib import ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    cand = _mk_cand(od, 0.945, "日用餐厨饮具 > 咖啡具 > 咖啡杯",
                    "Дом и сад > Посуда > Термосы")
    # 全路径对比 + category mode 缓存键（F-B02 修正后口径）
    with _mock.patch.object(od, "_llm_semantic_match", return_value=False) as mm, \
         _mock.patch.dict(od._LLM_SEMANTIC_CACHE,
                          {("category", "Дом и сад > Посуда > Термосы"[:60],
                            "日用餐厨饮具 > 咖啡具 > 咖啡杯"[:60]): False}):
        od._category_semantic_review(cand, "token")
        assert mm.called
        assert mm.call_args.kwargs.get("mode") == "category"
    assert cand.match_confidence == 0.5
    assert cand.match_category_divergent is True


def test_category_review_consistent_keeps_confidence():
    from unittest import mock as _mock
    from scripts.lib import ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    cand = _mk_cand(od, 0.885, "日用餐厨饮具 > 饮水用具 > 保温杯",
                    "Дом и сад > Посуда > Термосы")
    with _mock.patch.dict(od._LLM_SEMANTIC_CACHE,
                          {("category", "Дом и сад > Посуда > Термосы"[:60],
                            "日用餐厨饮具 > 饮水用具 > 保温杯"[:60]): True}):
        od._category_semantic_review(cand, "token")
    assert cand.match_confidence == 0.885
    assert cand.match_category_divergent is False


def test_category_review_llm_failure_does_not_penalize():
    """LLM 调用失败（返回 False 且缓存未写）→ 不降权（宁缺毋滥只在有真实判定时作用）。"""
    from unittest import mock as _mock
    from scripts.lib import ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    cand = _mk_cand(od, 0.9, "咖啡杯", "Термосы")
    with _mock.patch.object(od, "_llm_semantic_match", return_value=False):
        od._category_semantic_review(cand, "token")  # mock 返回 False 但缓存未写
    assert cand.match_confidence == 0.9
    assert cand.match_category_divergent is False


def test_category_review_skips_missing_inputs_or_low_conf():
    from unittest import mock as _mock
    from scripts.lib import ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    # 缺 Ozon 面包屑
    c1 = _mk_cand(od, 0.9, "咖啡杯", "")
    with _mock.patch.object(od, "_llm_semantic_match") as mm:
        od._category_semantic_review(c1, "token")
        mm.assert_not_called()
    # conf < 0.3 已在拒带
    c2 = _mk_cand(od, 0.2, "咖啡杯", "Термосы")
    with _mock.patch.object(od, "_llm_semantic_match") as mm:
        od._category_semantic_review(c2, "token")
        mm.assert_not_called()
    # 无 token
    c3 = _mk_cand(od, 0.9, "咖啡杯", "Термосы")
    with _mock.patch.object(od, "_llm_semantic_match") as mm:
        od._category_semantic_review(c3, "")
        mm.assert_not_called()

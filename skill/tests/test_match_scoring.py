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
    assert visual_signal(0.3, 0.8) == pytest.approx(0.8)  # 非满徽章取归一相似度
    assert visual_signal(0.0, -1) == 0.0
    assert visual_signal(0.0, None) == 0.0


# ── 复合评分 ──


def test_score_full_context_formula():
    cand = {"category_name": "保温杯", "badge_eff": 0.0, "normalization_score": 0.6}
    out = score_match(title_conf=0.2, candidate=cand,
                      ozon_category_path="厨房家居 > 保温杯")
    # 0.45*1.0 + 0.35*0.6 + 0.20*0.2 = 0.45+0.21+0.04
    assert out["confidence"] == pytest.approx(0.70)
    assert out["components"]["category"] == pytest.approx(1.0)


def test_score_without_category_context_falls_back():
    """类目上下文缺失：权重按比例回落 visual/title（0.35:0.20 → 归一）。"""
    out = score_match(title_conf=0.2, candidate={"normalization_score": 0.0})
    assert out["confidence"] == pytest.approx(0.2 * (0.20 / 0.55))
    assert "category" not in out["components"]


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

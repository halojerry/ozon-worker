#!/usr/bin/env python3
"""v0.19 图搜护栏修复单测：FULL 徽标识别、无徽标降级、词映射、0/N 跳过。

运行：
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_image_search_guardrail.py -v
    cd skill && PYTHONPATH=. python3 tests/test_image_search_guardrail.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.ozon_discovery import (  # noqa: E402
    _badge_effectiveness,
    _pick_best_match,
    _ru_zh_title_overlap,
)
from scripts.lib.ozon_image_search import _get_badge_score  # noqa: E402

WRENCH_RU = "Ключ комбинированный трещоточный шарнирный 13 мм"
WRENCH_CN = "活动头棘轮扳手梅花开口双头两用快速活头镜面扳手五金汽修工具"
VIBRO_RU = "Виброплатформа для похудения 150 кг с музыкой"
VIBRO_CN = "家用懒人抖抖机减肥健身器材甩脂机"


# ── badge 评分/有效性 ─────────────────────────────────────────────────────

def test_get_badge_score_full_match():
    assert _get_badge_score("全部符合") == 100
    assert _get_badge_score("符合2/3个条件") == 2
    assert _get_badge_score("符合0/1个条件") == 0
    assert _get_badge_score("") == 0


def test_badge_effectiveness_full_match():
    assert _badge_effectiveness("全部符合") == 1.0
    assert abs(_badge_effectiveness("符合2/3个条件") - 2 / 3) < 1e-6
    assert _badge_effectiveness("") == 0.0


# ── _pick_best_match 护栏矩阵 ─────────────────────────────────────────────

def test_full_badge_direct_pass():
    """matchBadgeFull（全部符合）→ 直接放行，不再被标题相关性否决。"""
    results = [{"title": WRENCH_CN, "price": 25.0, "badge": "全部符合"}]
    best = _pick_best_match(results, WRENCH_RU)
    assert best is not None and best["badge"] == "全部符合"


def test_zero_match_skipped():
    """'符合0/N个条件' 候选一律跳过 → 无候选返回 None。"""
    results = [
        {"title": "不相关商品甲", "price": 10.0, "badge": "符合0/1个条件"},
        {"title": "不相关商品乙", "price": 12.0, "badge": "符合0/1个条件"},
    ]
    assert _pick_best_match(results, WRENCH_RU) is None


def test_badge_less_accepts_good_title():
    """无徽标（未登录/未渲染）→ 标题相关性 conf≥0.4 放行。"""
    results = [{"title": "两用棘轮扳手 棘轮快速扳手 双头棘轮扳手 活头棘轮扳手",
                "price": 15.0, "badge": ""}]
    best = _pick_best_match(results, WRENCH_RU)
    assert best is not None


def test_badge_less_rejects_weak_title():
    """无徽标但标题完全不相关 → 拒绝。"""
    results = [{"title": "纯棉毛巾加厚家用吸水洗脸巾", "price": 9.9, "badge": ""}]
    assert _pick_best_match(results, WRENCH_RU) is None


def test_partial_badge_weak_conf_rejected():
    """有徽标（1/3）但标题相关性极弱 → 保留原护栏拒绝（防错配）。"""
    results = [{"title": "纯棉毛巾加厚家用吸水洗脸巾", "price": 9.9,
                "badge": "符合1/3个条件"}]
    assert _pick_best_match(results, WRENCH_RU) is None


def test_no_price_candidate_skipped():
    """无价格候选跳过（价格是利润核心）。"""
    results = [{"title": WRENCH_CN, "price": 0, "badge": "全部符合"}]
    assert _pick_best_match(results, WRENCH_RU) is None


# ── RU→ZH 词映射（v0.19 新增五金/健身词）─────────────────────────────────

def test_word_map_wrench():
    conf = _ru_zh_title_overlap(WRENCH_RU, "两用棘轮扳手 棘轮快速扳手 双头棘轮扳手 活头棘轮扳手")
    assert conf > 0.5


def test_word_map_vibro_platform():
    conf = _ru_zh_title_overlap(VIBRO_RU, VIBRO_CN)
    assert conf > 0.5


# ── 独立运行入口 ──────────────────────────────────────────────────────────

def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())

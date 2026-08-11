#!/usr/bin/env python3
"""D5-B: _pick_best_match 护栏阈值经 settings.json 参数化（TDD RED→GREEN）。

- match_min_conf（默认 0.3）: 无徽标路径 conf ≥ 阈值放行；主护栏 conf 下限
- match_badge_eff_min（默认 0.5）: 主护栏 badge 有效性下限
- get_setting 返回 None / 空 / 非数值 → 回退默认（test_image_search_guardrail 保持绿）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_settings_guardrail.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402

WRENCH_RU = "Ключ комбинированный трещоточный шарнирный 13 мм"


def _result(**overrides):
    r = {"title": "两用棘轮扳手 棘轮快速扳手 双头棘轮扳手 活头棘轮扳手",
         "price": 15.0, "badge": "符合1/3个条件",
         "id": "1001", "detail_url": "https://detail.1688.com/offer/1001.html"}
    r.update(overrides)
    return r


def _settings(**values):
    """get_setting fake：只覆盖显式给出的 key，其余返回默认（None）。"""

    def _get(key, default=None):
        return values.get(key, default)

    return mock.patch("scripts.lib.config_store.get_setting", side_effect=_get)


def _assert_guardrail_blocked(m_log, reason):
    """断言拦截出口：decision=block 且带指定 reject_reason。"""
    assert m_log.call_count == 1, m_log.call_args_list
    rec = m_log.call_args[0][0]
    assert rec["decision"] == "block"
    assert rec["reject_reason"] == reason


# ── ① match_min_conf 调高 → 原先放行的候选被拦截 ──────────────────────────

def test_match_min_conf_blocks_previously_passing_candidate():
    """match_min_conf=0.9 → conf=0.5 + badge_eff<0.5 的候选（默认放行）改为拦截。

    默认阈值下 badge_eff(0.333)<0.5 但 conf(0.5)>=0.3 → 主护栏 AND 不触发 → 放行；
    match_min_conf=0.9 后 0.5<0.9 → 护栏触发 → LLM 判定不同品 → guardrail_blocked。
    """
    with _settings(match_min_conf=0.9), \
         mock.patch.object(od, "_title_conf", return_value=0.5), \
         mock.patch.object(od, "_llm_semantic_match", return_value=False), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        best = od._pick_best_match([_result()], WRENCH_RU)
    assert best is None
    _assert_guardrail_blocked(m_log, "guardrail_blocked")


# ── ② match_badge_eff_min 调高 → 原先放行的候选被拦截 ─────────────────────

def test_match_badge_eff_min_blocks_previously_passing_candidate():
    """match_badge_eff_min=0.9 → badge_eff(0.667) + conf=0.2 的候选（默认放行）改为拦截。

    默认阈值下 badge_eff(0.667)>=0.5 → 主护栏 AND 不触发 → 放行；
    match_badge_eff_min=0.9 后 0.667<0.9 且 conf(0.2)<0.3 → 护栏触发 → 拦截。
    """
    with _settings(match_badge_eff_min=0.9), \
         mock.patch.object(od, "_title_conf", return_value=0.2), \
         mock.patch.object(od, "_llm_semantic_match", return_value=False), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        best = od._pick_best_match(
            [_result(badge="符合2/3个条件")], WRENCH_RU)
    assert best is None
    _assert_guardrail_blocked(m_log, "guardrail_blocked")


# ── ③ match_min_conf 调高 → 无徽标路径（conf≥阈值放行）同样生效 ───────────

def test_match_min_conf_blocks_badge_less_pass():
    """match_min_conf=0.9 → 无徽标 conf=0.5（默认放行）改为 badge_less_conf_weak 拦截。"""
    with _settings(match_min_conf=0.9), \
         mock.patch.object(od, "_title_conf", return_value=0.5), \
         mock.patch.object(od, "_llm_semantic_match", return_value=False), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        best = od._pick_best_match([_result(badge="")], WRENCH_RU)
    assert best is None
    _assert_guardrail_blocked(m_log, "badge_less_conf_weak")


# ── ④ get_setting 返回 None → 默认 0.5/0.3 原样生效 ────────────────────────

def test_defaults_preserved_when_get_setting_none():
    """get_setting 无配置（None）→ 默认阈值：弱候选拦截、强候选放行，与历史一致。"""
    with _settings(), \
         mock.patch.object(od, "_title_conf", return_value=0.2), \
         mock.patch.object(od, "_llm_semantic_match", return_value=False), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        weak = od._pick_best_match([_result()], WRENCH_RU)  # badge_eff=0.333, conf=0.2
    assert weak is None
    _assert_guardrail_blocked(m_log, "guardrail_blocked")

    with _settings(), \
         mock.patch.object(od, "_title_conf", return_value=0.5):
        strong = od._pick_best_match(
            [_result(badge="符合2/3个条件")], WRENCH_RU)  # badge_eff=0.667, conf=0.5
    assert strong is not None
    assert strong["confidence"] == 0.5


# ── ⑤ 非法/空数值 → 回退默认，不崩 ────────────────────────────────────────

def test_invalid_numeric_setting_falls_back_to_default():
    """match_min_conf="abc"（非数值）+ match_badge_eff_min=""（空）→ 回退 0.3/0.5。"""
    with _settings(match_min_conf="abc", match_badge_eff_min=""), \
         mock.patch.object(od, "_title_conf", return_value=0.5):
        best = od._pick_best_match(
            [_result(badge="符合2/3个条件")], WRENCH_RU)  # badge_eff=0.667>=0.5 → 放行
    assert best is not None

    with _settings(match_min_conf="abc", match_badge_eff_min=""), \
         mock.patch.object(od, "_title_conf", return_value=0.2), \
         mock.patch.object(od, "_llm_semantic_match", return_value=False), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        weak = od._pick_best_match([_result()], WRENCH_RU)  # badge_eff=0.333<0.5, conf=0.2
    assert weak is None
    _assert_guardrail_blocked(m_log, "guardrail_blocked")


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

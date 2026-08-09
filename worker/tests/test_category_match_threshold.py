# -*- coding: utf-8 -*-
"""
v0.31.x 类目匹配最低接受门槛单测（纯函数，无需 PG）。

背景：09:37 笔筒任务日志实证 —— sim=0.200 的低分错配
（`儿童用品 > 教学玩具 > 儿童多功能学习挂图`）被直接采用，因为主路径
`best = candidates[0]` 无最低 sim 接受阈值，且 match_confidence 硬编码 0.5，
graph.py 的 `<0.3` 阻断永不触发。

规则（测试锁定，防回归）:
- jieba (ZH_HANS): similarity = 匹配token数/总token数 → 门槛 0.5
- pg_trgm (RU): similarity = func.similarity 0-1 实数 → 门槛 0.3
- ILIKE fallback: similarity = 匹配词数/总词数 → 门槛 0.5
- 无 similarity 的候选不阻断（兼容无分候选）
- L0/Skill 命中直接放行（调用方判断，不在纯函数内）

运行: cd worker && PYTHONPATH=src /Volumes/os/dev/ozon-worker/skill/.venv314/bin/python tests/test_category_match_threshold.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import (
    MIN_SIM_BY_MATCHER,
    _acceptable_match,
    _confidence_from_sim,
)


# ── 门槛常量 ──

def test_threshold_constants_defined():
    assert MIN_SIM_BY_MATCHER == {"jieba": 0.5, "pg_trgm": 0.3, "ili": 0.5}


# ── jieba（带 matched_tokens key）──

def test_jieba_sim_020_rejected():
    """笔筒案例：只有 1/5 token 命中（sim=0.2）→ 拒绝（门槛 0.5）。"""
    assert _acceptable_match({"matched_tokens": ["笔筒"], "similarity": 0.2}) is False


def test_jieba_sim_050_accepted():
    """jieba sim=0.5 → 达标。"""
    assert _acceptable_match({"matched_tokens": ["笔筒"], "similarity": 0.5}) is True


def test_jieba_matcher_explicit():
    """显式 matcher=jieba 与 matched_tokens 推断等价。"""
    assert _acceptable_match({"matcher": "jieba", "similarity": 0.4}) is False
    assert _acceptable_match({"matcher": "jieba", "similarity": 0.5}) is True


# ── pg_trgm（无 matched_tokens，默认标尺 0.3）──

def test_pgtrgm_sim_030_accepted():
    assert _acceptable_match({"similarity": 0.3}) is True


def test_pgtrgm_sim_010_rejected():
    assert _acceptable_match({"similarity": 0.1}) is False


# ── ILIKE fallback（门槛 0.5，需显式 matcher=ili 区分）──

def test_ili_sim_050_accepted():
    assert _acceptable_match({"matcher": "ili", "similarity": 0.5}) is True


def test_ili_sim_030_rejected():
    """ILIKE 0.3 不达标（pg_trgm 0.3 达标，语义不同不能共用门槛）。"""
    assert _acceptable_match({"matcher": "ili", "similarity": 0.3}) is False


# ── 无分候选 ──

def test_no_similarity_not_blocked():
    assert _acceptable_match({}) is True
    assert _acceptable_match({"matcher": "pg_trgm"}) is True


def test_none_similarity_not_blocked():
    assert _acceptable_match({"similarity": None}) is True


# ── match_confidence 挂钩真实 sim ──

def test_match_confidence_equals_sim():
    assert _confidence_from_sim(0.2) == 0.2
    assert _confidence_from_sim(0.5) == 0.5
    assert _confidence_from_sim(1.5) == 1.0
    assert _confidence_from_sim(-0.5) == 0.0
    assert _confidence_from_sim(None) == 0.5


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: 异常")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)

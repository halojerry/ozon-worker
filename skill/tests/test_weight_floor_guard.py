#!/usr/bin/env python3
"""v0.68.1: 信封重量硬下限守卫回归（A3 实证 1g → Ozon INCORRECT_DIMENSION 拒单）。

Ozon 契约下限 10g：(0,10) 区间必拒。1688 包装表无单位裸数字常是 kg 语义
（parseWeightGrams 无单位按克），抓取层出口守卫归零 → 走 50g 缺省兜底。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_weight_floor_guard.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.cloud_probe import _sanitize_weight_g  # noqa: E402


def test_below_ozon_min_zeroed():
    """1g（A3 实证）→ 0（缺失语义，走 50g 缺省）。"""
    assert _sanitize_weight_g(1) == 0
    assert _sanitize_weight_g(9) == 0


def test_valid_weights_untouched():
    """≥10g 真实轻量（12g 手机壳）与正常值绝不改写。"""
    assert _sanitize_weight_g(10) == 10
    assert _sanitize_weight_g(12) == 12
    assert _sanitize_weight_g(80) == 80
    assert _sanitize_weight_g(1200) == 1200


def test_zero_and_negative_passthrough():
    """0/负值本就是缺失语义，原样透传（下游 50g 兜底接管）。"""
    assert _sanitize_weight_g(0) == 0
    assert _sanitize_weight_g(-5) == -5

"""佣金率分段解析（_to_rate_segments）回归。

背景：what_to_sell 响应的 fbp_rate / rfbs_rate 是分段对象
{fbp_leq_1500, fbp_leq_5000, fbp_gt_5000}（或 rfbs 前缀），旧 _to_rate
只取中段 leq_5000，丢失分段信息。本测试锁定：
1. _to_rate_segments 三种输入形态（dict 分段 / 标量 / 空）的完整三段输出
2. _to_rate 向后兼容（仍取中段，行为不变）
3. _extract_metrics 暴露 commission_fbp_segments / commission_rfbs_segments
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.lib.ozon_seller_analytics as osa


# ─────────────────────────────────────────────────────────────────────────────
# _to_rate_segments 三种输入形态
# ─────────────────────────────────────────────────────────────────────────────
def test_to_rate_segments_full_dict():
    """rfbs 前缀分段 dict → 三段完整保留。"""
    result = osa._to_rate_segments(
        {"rfbs_leq_1500": 8, "rfbs_leq_5000": 12, "rfbs_gt_5000": 18}
    )
    assert result == {"leq_1500": 8.0, "leq_5000": 12.0, "gt_5000": 18.0}


def test_to_rate_segments_fbp_prefix_dict():
    """fbp 前缀分段 dict → 同样正确提取。"""
    result = osa._to_rate_segments(
        {"fbp_leq_1500": 5, "fbp_leq_5000": 10, "fbp_gt_5000": 15}
    )
    assert result == {"leq_1500": 5.0, "leq_5000": 10.0, "gt_5000": 15.0}


def test_to_rate_segments_missing_keys_default_zero():
    """分段 dict 缺 key → 缺失段补 0.0。"""
    result = osa._to_rate_segments({"rfbs_leq_5000": 12})
    assert result == {"leq_1500": 0.0, "leq_5000": 12.0, "gt_5000": 0.0}


def test_to_rate_segments_scalar():
    """标量输入 → 三段全为该标量值。"""
    result = osa._to_rate_segments(12)
    assert result == {"leq_1500": 12.0, "leq_5000": 12.0, "gt_5000": 12.0}


def test_to_rate_segments_scalar_string():
    """字符串标量 → 三段全为该数值。"""
    result = osa._to_rate_segments("12.5")
    assert result == {"leq_1500": 12.5, "leq_5000": 12.5, "gt_5000": 12.5}


def test_to_rate_segments_empty():
    """None / 空 / 无法解析 → 三段全 0.0。"""
    assert osa._to_rate_segments(None) == {"leq_1500": 0.0, "leq_5000": 0.0, "gt_5000": 0.0}
    assert osa._to_rate_segments({}) == {"leq_1500": 0.0, "leq_5000": 0.0, "gt_5000": 0.0}
    assert osa._to_rate_segments("abc") == {"leq_1500": 0.0, "leq_5000": 0.0, "gt_5000": 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# _to_rate 向后兼容（行为不变：dict 取中段 leq_5000）
# ─────────────────────────────────────────────────────────────────────────────
def test_to_rate_backward_compat():
    """fbp 分段 dict → 中段 12.0（与旧行为一致）。"""
    assert osa._to_rate({"fbp_leq_1500": 8, "fbp_leq_5000": 12, "fbp_gt_5000": 18}) == 12.0


def test_to_rate_backward_compat_scalar():
    """标量 → 原样返回（与旧行为一致）。"""
    assert osa._to_rate(12) == 12.0
    assert osa._to_rate(None) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# _extract_metrics 暴露分段
# ─────────────────────────────────────────────────────────────────────────────
def test_extract_metrics_exposes_segments():
    """含 rfbs_rate 分段对象的 item → metrics 含 commission_rfbs_segments 且三段正确。"""
    item = {
        "sku": "12345",
        "rfbs_rate": {"rfbs_leq_1500": 8, "rfbs_leq_5000": 12, "rfbs_gt_5000": 18},
    }
    metrics = osa._extract_metrics(item)
    assert metrics["commission_rfbs_segments"] == {
        "leq_1500": 8.0,
        "leq_5000": 12.0,
        "gt_5000": 18.0,
    }
    # 向后兼容：标量字段仍取中段
    assert metrics["commission_rfbs"] == 12.0


def test_extract_metrics_exposes_fbp_segments():
    """含 fbp_rate 分段对象的 item → metrics 含 commission_fbp_segments。"""
    item = {
        "sku": "12345",
        "fbp_rate": {"fbp_leq_1500": 5, "fbp_leq_5000": 10, "fbp_gt_5000": 15},
    }
    metrics = osa._extract_metrics(item)
    assert metrics["commission_fbp_segments"] == {
        "leq_1500": 5.0,
        "leq_5000": 10.0,
        "gt_5000": 15.0,
    }
    assert metrics["commission_fbp"] == 10.0


def test_extract_metrics_segments_scalar_fallback():
    """标量 fbp_rate → segments 三段全为标量值。"""
    item = {"sku": "12345", "fbp_rate": 10}
    metrics = osa._extract_metrics(item)
    assert metrics["commission_fbp_segments"] == {
        "leq_1500": 10.0,
        "leq_5000": 10.0,
        "gt_5000": 10.0,
    }
    assert metrics["commission_fbp"] == 10.0

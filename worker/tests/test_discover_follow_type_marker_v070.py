"""v0.70 实机 gate 回归：discover 信封字面 follow_type="discover" 不判真跟卖。

背景（2026-09-08 实机 gate 实证）：v0.69 P-D 起 skill discover 信封带
extensions.follow_type="discover"（真值字符串）。learning_record 的 truthy 判定
把 discover 单判成真跟卖 → mapping 恒压 0.6 弱档，v0.67 实证的 discover L0
学习闭环失效。修复后三处同语义（learning_record / listing_result_log /
follow_sell_import_node discover 分支）。纯函数，无需 PG。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.nodes.learning_record_node import _is_true_follow_envelope  # noqa: E402


def test_discover_literal_marker_not_true_follow():
    """gate 主案例：follow_sell=True + follow_type="discover" → discover 变体。"""
    env = {"extensions": {"follow_sell": True, "follow_type": "discover"}}
    assert _is_true_follow_envelope(env, {}) is False


def test_hand_and_api_are_true_follow():
    for ft in ("hand", "api"):
        env = {"extensions": {"follow_sell": True, "follow_type": ft}}
        assert _is_true_follow_envelope(env, {}) is True, ft


def test_follow_sell_without_follow_type_is_discover():
    """v0.66.1 旧形态：follow_sell=True 无 follow_type → discover 变体。"""
    env = {"extensions": {"follow_sell": True}}
    assert _is_true_follow_envelope(env, {}) is False


def test_legacy_ozon_product_id_is_true_follow():
    """兼容旧信封：无任何 follow 标记但 draft.ozon_product_id 在场 → 真跟卖。"""
    assert _is_true_follow_envelope({}, {"ozon_product_id": "123"}) is True


def test_no_markers_not_true_follow():
    assert _is_true_follow_envelope({}, {}) is False
    assert _is_true_follow_envelope(None, None) is False


def test_malformed_envelope_falls_back_to_draft():
    """envelope 非 dict / extensions 非 dict 不炸，回落 draft 判定。"""
    assert _is_true_follow_envelope({"extensions": "bad"}, {}) is False
    assert _is_true_follow_envelope("bad", {"ozon_product_id": "9"}) is True

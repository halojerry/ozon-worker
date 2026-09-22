"""v0.77.3（gate 发现修复）：VALUE_MAX/MIN_LIMIT 无界值可夹 → 丢弃可选属性。

实测（2026-09-19 gate，task c9b6d16f）：沥水篮 6949 «Количество предметов»=97
（上游 1688 属性误映射）→ Ozon VALUE_MAX_LIMIT 拒；拒单原文
«не входит в установленный диапазон» 不含具体界值 → bounds 学习置信门不学
（正确）；repair_prepare 无界值可夹 → 原值 97 重传 → 再拒 → 卡被 Ozon 移除。
正确行为：无界值可夹时丢弃该可选属性（错填→不填，仓库既有哲学），
8962 例外（有静态界值恒可夹取）。
"""
from utils.attr_numeric_sanitize import limit_error_attr_needs_drop


def test_drop_when_no_bounds_available():
    """未知属性（静态白名单/学习表都无界值）+ VALUE_MAX_LIMIT → 应丢弃。"""
    assert limit_error_attr_needs_drop("VALUE_MAX_LIMIT", 6949) is True
    assert limit_error_attr_needs_drop("VALUE_MIN_LIMIT", 6949) is True


def test_keep_when_static_bounds_exist():
    """8962 有静态界值 (1,10000) → 夹取路径可用，不丢弃。"""
    assert limit_error_attr_needs_drop("VALUE_MAX_LIMIT", 8962) is False


def test_keep_for_non_limit_codes():
    """非 VALUE_MAX/MIN_LIMIT 错误码 → 不触发丢弃逻辑。"""
    assert limit_error_attr_needs_drop("VALUE_MUST_BE_INTEGER", 6949) is False
    assert limit_error_attr_needs_drop("", 6949) is False


def test_keep_when_learned_bounds_exist(monkeypatch):
    """学习表已有界值 → 夹取可用，不丢弃（静态/学习任一即够）。"""
    from utils import attr_numeric_sanitize as ans

    monkeypatch.setattr(ans, "get_learned_bounds", lambda aid: (1, 10))
    assert limit_error_attr_needs_drop("VALUE_MAX_LIMIT", 424242) is False

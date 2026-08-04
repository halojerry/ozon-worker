#!/usr/bin/env python3
"""RU→ZH 标题相关性单测（v0.22 图搜准确率提升）。

运行：
    cd skill && python3 tests/test_title_overlap.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import _RU_ZH_PRODUCT_WORDS, _ru_zh_title_overlap


def test_new_hardware_words_covered():
    """用户反馈的五金词（套筒/撬棍/水平仪）必须进映射。"""
    for ru, zh in [
        ("головк", "套筒"),
        ("монтировк", "撬棍"),
        ("уровень", "水平仪"),
        ("вентилятор", "风扇"),
        ("набор", "套装"),
        ("фонар", "手电"),
    ]:
        assert ru in _RU_ZH_PRODUCT_WORDS, f"缺少映射: {ru}"
        assert any(z in zh for z in _RU_ZH_PRODUCT_WORDS[ru]), f"{ru} 缺中文: {zh}"


def test_overlap_multiword_weighted():
    """多产品词命中更可信：3 词命中 conf > 1 词命中 conf，且 1 词命中 < 1.0。"""
    conf3 = _ru_zh_title_overlap(
        "Набор гаечных ключей трещоточных 13 мм",
        "棘轮扳手套装 13mm",
    )
    conf1 = _ru_zh_title_overlap(
        "Набор гаечных ключей трещоточных 13 мм",
        "工具箱",
    )
    assert conf3 > conf1
    assert conf1 < 1.0
    assert conf3 > 0.5


def test_overlap_zero_when_no_words():
    """俄语标题无映射词 → 0（不误判）。"""
    assert _ru_zh_title_overlap("Аксессуар для ванной", "浴室用品") == 0.0


def test_overlap_full_hits():
    """全命中 → conf 高（2 词全命中 ≥0.85）。"""
    conf = _ru_zh_title_overlap(
        "Вентилятор настольный USB",
        "USB桌面风扇",
    )
    assert conf >= 0.8


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
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

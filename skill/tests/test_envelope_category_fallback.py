#!/usr/bin/env python3
"""skill 信封类目兜底单测（v0.32 修复）：draft.category 在 Ozon 类目解析失败时
回退 1688 面包屑末级（_last_seg），再回退 category_query。

运行（pytest 或独立脚本均可）：
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_envelope_category_fallback.py -v
    cd skill && python3 tests/test_envelope_category_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cloud_probe import _last_seg, _resolve_envelope_category  # noqa: E402


def test_last_seg_returns_leaf_category():
    """面包屑「办公文化 > 文具 > 笔筒」→ 末级「笔筒」。"""
    assert _last_seg("办公文化 > 文具 > 笔筒") == "笔筒"


def test_last_seg_strips_whitespace():
    """末段带空白 → 去空白。"""
    assert _last_seg("办公文化 > 文具 > 笔筒  ") == "笔筒"
    assert _last_seg("办公文化 > 文具 >  笔筒") == "笔筒"


def test_last_seg_empty_when_no_path():
    """无路径 / 空白 / None → "". """
    assert _last_seg("") == ""
    assert _last_seg("   ") == ""
    assert _last_seg(None) == ""


def test_category_fallback_uses_source_path_last_seg():
    """category_name 空 → 用 1688 面包屑末级「笔筒」（category_query 垫底）。"""
    assert _resolve_envelope_category("", "办公文化 > 文具 > 笔筒", "pen holder") == "笔筒"


def test_category_priority_ozon_name_first():
    """Ozon 类目名非空（俄语 type_name）→ 优先于面包屑末级。"""
    assert _resolve_envelope_category("Канцелярия", "办公文化 > 文具 > 笔筒", "笔筒") == "Канцелярия"


def test_category_fallback_to_query_when_no_path():
    """category_name 与面包屑均空 → 回退 category_query。"""
    assert _resolve_envelope_category("", "", "笔筒") == "笔筒"


def test_category_all_empty():
    """三者全空 → "". """
    assert _resolve_envelope_category("", "", "") == ""


if __name__ == "__main__":
    import traceback

    tests = [
        test_last_seg_returns_leaf_category,
        test_last_seg_strips_whitespace,
        test_last_seg_empty_when_no_path,
        test_category_fallback_uses_source_path_last_seg,
        test_category_priority_ozon_name_first,
        test_category_fallback_to_query_when_no_path,
        test_category_all_empty,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception:
            print(f"  ❌ {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)

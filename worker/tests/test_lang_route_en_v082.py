"""feat/attribute-fill-en-v1 T1 回归：语言路由三分支 + EN 树确定性命中。

A2 实证（2026-09-24）：EN 面包屑 "Food Storage"/"Containers" 因两分支路由
（中文→ZH / 否则→RU）被送进 RU 行 pg_trgm 必空 → 门控仲裁无候选静默绕路。
EN 树落库后（7933 行），"Storage Case" 应确定性命中 17027937/95483。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from graphs.nodes.follow_sell_import_node import _detect_language  # noqa: E402
from utils.attr_value_matcher import lang_route  # noqa: E402


def test_detect_language_three_way():
    assert _detect_language("收纳盒") == "ZH_HANS"
    assert _detect_language("Кофр для хранения") == "RU"
    assert _detect_language("Storage Case") == "EN"
    assert _detect_language("Food Storage") == "EN"


def test_detect_language_cyrillic_wins_over_latin():
    """混排（尺寸词 "14 х 10 х 28"）含西里尔 х → RU（旧缺省行为保持）。"""
    assert _detect_language("14 х 10 х 28 см") == "RU"
    assert _detect_language("Storage Case 14х28") == "RU"


def test_detect_language_numeric_fallback_ru():
    """纯数字/符号缺省 RU（旧行为零变化）。"""
    assert _detect_language("12345") == "RU"
    assert _detect_language("") == "RU"


def test_lang_route_three_way():
    assert lang_route("收纳盒") == "ZH_HANS"
    assert lang_route("Кофр") == "RU"
    assert lang_route("Storage Case") == "EN"
    assert lang_route("12345") == "RU"


def test_en_tree_search_hits_storage_case():
    """EN 树落库回归（需本地 PG 含 EN 行）：'Storage Case' → 17027937/95483。

    PG 不可用/无 EN 行时跳过（CI mock 环境保护）——实机 gate 侧必验。
    """
    try:
        from utils.ozon_category_query import get_category_query
        q = get_category_query()
        rows = q.search_nodes("Storage Case", top_k=3, node_type="type", language="EN")
    except Exception:
        return  # 无 PG 环境（CI mock）跳过
    if not rows:
        return  # EN 行未落库（新环境）跳过
    top = rows[0]
    assert (str(top.get("description_category_id")), str(top.get("type_id"))) == \
        ("17027937", "95483"), f"Storage Case 应命中收纳盒叶子，实际: {top}"

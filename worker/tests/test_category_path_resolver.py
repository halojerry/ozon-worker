# -*- coding: utf-8 -*-
"""
v0.63 确定性路径解析器单测（纯函数 + 查询 mock，无需 PG）。

- _build_full_path_candidates：规范化/去泛化词/去品牌段/最长前缀候选顺序。
- follow_sell_import_node._resolve_category_by_id：路径精配优先；dc 下多 type 且无路径不盲取。

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python3 -m pytest tests/test_category_path_resolver.py -q
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ozon_category_query import _build_full_path_candidates


def test_candidates_longest_prefix_first():
    # 「汽车用品 > 轮辋 > 车轮总成」→ 最长前缀优先（完整 → 去尾 → 末段）
    c = _build_full_path_candidates("汽车用品 > 轮辋 > 车轮总成")
    assert c[0] == "汽车用品 > 轮辋 > 车轮总成"
    assert "汽车用品 > 轮辋" in c
    assert "车轮总成" in c


def test_candidates_normalize_separators():
    c = _build_full_path_candidates("汽车用品/轮辋/车轮总成")
    assert c[0] == "汽车用品 > 轮辋 > 车轮总成"
    c2 = _build_full_path_candidates("汽车用品、轮辋→车轮总成")
    assert c2[0] == "汽车用品 > 轮辋 > 车轮总成"


def test_candidates_drop_generic_and_brand_segments():
    # 品牌段（纯拉丁无西里尔）与泛化词段应剔除
    c = _build_full_path_candidates("汽车用品 > 轮辋 > 车轮总成")
    assert "汽车用品" in c[0]
    # 品牌段：末段为纯拉丁 → 剔除，保留品牌前一段
    c2 = _build_full_path_candidates("汽车用品 > 轮辋 > Canevia")
    assert c2[0] == "汽车用品 > 轮辋"  # 品牌段被剔除


def test_candidates_leaf_only():
    c = _build_full_path_candidates("轮毂")
    assert c == ["轮毂"]


def test_candidates_empty():
    assert _build_full_path_candidates("") == []
    assert _build_full_path_candidates(None) == []


def _seg(s):
    return s.split(" > ")


# ── _resolve_category_by_id：路径精配优先 + dc 多 type 守卫 ──


def _make_query(path_result=None, types_under=None):
    """造一个假 OzonCategoryQuery，返回 path/type 结果。"""
    class _FakeQuery:
        def __init__(self):
            self.path_result = path_result
            self.types_under = types_under or []

        def get_node_by_full_path(self, path, language=None):
            # 仅当路径包含「车轮总成」才命中（模拟 Ozon 页面路径）
            if self.path_result and "车轮总成" in (path or ""):
                return self.path_result
            return None

        def get_node_by_description_category_id(self, dc):
            return {"description_category_id": 17028758, "type_id": 970619447}

        def get_types_under(self, dc):
            return self.types_under
    return _FakeQuery()


def test_resolve_category_by_id_path_first(monkeypatch):
    from graphs.nodes import follow_sell_import_node as m
    q = _make_query(path_result={"description_category_id": 17028758, "type_id": 970619447, "full_path": "汽车用品 > 轮辋 > 车轮总成"})
    # monkeypatch get_category_query
    import utils.ozon_category_query as oq
    monkeypatch.setattr(oq, "get_category_query", lambda: q)
    dc, tp = m._resolve_category_by_id(17028758, type_name_hint="汽车用品 > 轮辋 > 车轮总成")
    assert (dc, tp) == ("17028758", "970619447")


def test_resolve_category_by_id_multi_type_no_path_does_not_guess(monkeypatch):
    from graphs.nodes import follow_sell_import_node as m
    # dc 下有 2 个 type 且无路径 → 不盲取第一个
    q = _make_query(path_result=None, types_under=[
        {"description_category_id": 17028758, "type_id": 970619447, "full_path": "A > 车轮总成"},
        {"description_category_id": 17028758, "type_id": 970702677, "full_path": "A > 轮毂盖"},
    ])
    import utils.ozon_category_query as oq
    monkeypatch.setattr(oq, "get_category_query", lambda: q)
    dc, tp = m._resolve_category_by_id(17028758, type_name_hint="")
    assert (dc, tp) == ("", "")


def test_resolve_category_by_id_unique_type_uses_it(monkeypatch):
    from graphs.nodes import follow_sell_import_node as m
    q = _make_query(path_result=None, types_under=[
        {"description_category_id": 17028758, "type_id": 970619447, "full_path": "A > 车轮总成"},
    ])
    import utils.ozon_category_query as oq
    monkeypatch.setattr(oq, "get_category_query", lambda: q)
    dc, tp = m._resolve_category_by_id(17028758, type_name_hint="")
    assert (dc, tp) == ("17028758", "970619447")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

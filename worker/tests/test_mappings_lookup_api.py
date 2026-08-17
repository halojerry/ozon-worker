"""W11: /api/v1/mappings/lookup 类目映射查询端点单测（mock 鉴权 + lookup，不真连库）。

覆盖：
1. 无 token → 401
2. token 无效 → 401
3. lookup_mapping 命中 → {found: true, mappings: [{dc, tp, confidence}]}
4. lookup_mapping 未命中 + keywords 兜底空 → {found: false, mappings: []}
5. keywords 兜底命中（success/confidence 过门槛）→ found: true
6. keywords 兜底结果不足门槛 → 过滤 → found: false
7. 缺 keyword 参数 → {found: false, mappings: []}
"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


class FakeQueryParams:
    def __init__(self, q):
        self._q = q or {}

    def get(self, key, default=None):
        return self._q.get(key, default)


class FakeRequest:
    def __init__(self, query=None, headers=None):
        self.query_params = FakeQueryParams(query)
        self.headers = headers or {}


def _call(monkeypatch, *, query=None, headers=None, lookup=None, kw_rows=None):
    """直接调用端点函数（mock 鉴权放行 + lookup 逻辑，不真连库）。"""
    import main

    monkeypatch.setattr(main, "_verify_analytics_token", lambda token: None)
    with mock.patch("utils.category_mapping_learn.lookup_mapping", return_value=lookup), \
         mock.patch("utils.ozon_category_query.OzonCategoryQuery.get_category_mapping_by_keywords",
                    return_value=kw_rows if kw_rows is not None else []):
        return asyncio.run(main.v1_mappings_lookup(
            FakeRequest(query=query, headers=headers or {"Authorization": "Bearer sk-test"})))


def test_no_token_returns_401(monkeypatch):
    """无 Authorization 头 → 401。"""
    import main

    with pytest.raises(main.HTTPException) as ei:
        asyncio.run(main.v1_mappings_lookup(FakeRequest(query={"keyword": "宠物用品"}, headers={})))
    assert ei.value.status_code == 401


def test_invalid_token_returns_401(monkeypatch):
    """token 无效（_verify_analytics_token 拒绝）→ 401。"""
    import main

    def _reject(token):
        raise main.HTTPException(status_code=401, detail="token_invalid or account_inactive")

    monkeypatch.setattr(main, "_verify_analytics_token", _reject)
    with pytest.raises(main.HTTPException) as ei:
        asyncio.run(main.v1_mappings_lookup(FakeRequest(
            query={"keyword": "宠物用品"}, headers={"Authorization": "Bearer sk-bad"})))
    assert ei.value.status_code == 401


def test_lookup_hit_returns_mappings(monkeypatch):
    """lookup_mapping 命中 → {found: true, mappings: [{dc, tp, confidence}]}。"""
    result = _call(monkeypatch, query={"keyword": "宠物用品"},
                   lookup={"dc": "17028929", "tp": "504866264", "confidence": 0.9})
    assert result == {"found": True, "mappings": [{"dc": "17028929", "tp": "504866264", "confidence": 0.9}]}


def test_lookup_miss_returns_empty(monkeypatch):
    """lookup_mapping 未命中 + keywords 兜底空 → {found: false, mappings: []}。"""
    result = _call(monkeypatch, query={"keyword": "宠物用品"}, lookup=None, kw_rows=[])
    assert result == {"found": False, "mappings": []}


def test_keyword_fallback_hit(monkeypatch):
    """lookup_mapping 未命中但 keywords 兜底命中（过门槛）→ found: true。"""
    rows = [{"description_category_id": 17028929, "type_id": 504866264,
             "confidence": 0.9, "success_count": 5}]
    result = _call(monkeypatch, query={"keyword": "宠物用品"}, lookup=None, kw_rows=rows)
    assert result == {"found": True, "mappings": [{"dc": "17028929", "tp": "504866264", "confidence": 0.9}]}


def test_keyword_fallback_filters_low_confidence(monkeypatch):
    """keywords 兜底结果 success_count/confidence 不足 → 过滤 → found: false。"""
    rows = [{"description_category_id": 17028929, "type_id": 504866264,
             "confidence": 0.4, "success_count": 1}]
    result = _call(monkeypatch, query={"keyword": "宠物用品"}, lookup=None, kw_rows=rows)
    assert result == {"found": False, "mappings": []}


def test_missing_keyword_returns_empty(monkeypatch):
    """缺 keyword 参数 → {found: false, mappings: []}（不查库）。"""
    result = _call(monkeypatch, query={}, lookup=None, kw_rows=[])
    assert result == {"found": False, "mappings": []}

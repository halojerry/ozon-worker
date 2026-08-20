# -*- coding: utf-8 -*-
"""SEO 流量关键词公开读端点单测（mock 鉴权 + mock session 注入，不连真实 PG）。

覆盖：
1. 无效 token → 401（无 Bearer；Supabase 校验拒绝）
2. 有数据 → 200 + keywords 数组 + total
3. q 过滤生效（ILIKE '%q%' 传给 service）
4. 异常不泄漏内部细节（service 抛错 → 500 通用消息）
5. 限流超限 → 429（与 commissions lookup 同 RateLimiter）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_seo_keywords_endpoint.py -q
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from routes import seo_keywords_routes  # noqa: E402
from services import queries_service  # noqa: E402


class FakeGetRequest:
    """GET 请求 fake：Bearer token + query_params dict（对齐 test_commissions_lookup_endpoint）。"""

    def __init__(self, token, query=None):
        self._token = token
        self.query_params = query or {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


class FakeResult:
    """execute() 返回：fetchall 给行、scalar 给 count。"""

    def __init__(self, rows, scalar_value=None):
        self._rows = rows
        self._scalar = scalar_value

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


class FakeConn:
    """session.connect() 上下文管理器；execute 记录 SQL/params 供断言。"""

    captured: dict = {}

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        FakeConn.captured["sql"] = str(sql)
        FakeConn.captured["params"] = params
        return FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return FakeConn(self._rows)


def _seed_row(query, count=10, uniq_queries_wca=5, uniq_sellers=3, source="fetched"):
    """service SELECT 列序：query, count, uniq_queries_wca, uniq_sellers, source。"""
    return (query, count, uniq_queries_wca, uniq_sellers, source)


def _mock_auth(monkeypatch):
    """Supabase 未配置 → _verify_analytics_token 本地放行（生产同款降级路径）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)


def _mock_engine(monkeypatch, rows):
    """mock queries_service.get_engine（session 注入，不连真实 PG）。"""
    FakeConn.captured = {}
    monkeypatch.setattr(queries_service, "get_engine", lambda: FakeEngine(rows))
    return FakeConn.captured


def _call(token, query, monkeypatch):
    _mock_auth(monkeypatch)
    return asyncio.run(seo_keywords_routes.http_seo_keywords(
        FakeGetRequest(token, query)
    ))


# ── 1. 无效 token → 401 ──
def test_no_token_401(monkeypatch):
    import main

    _mock_auth(monkeypatch)
    with pytest.raises(main.HTTPException) as ei:
        _call("", {}, monkeypatch)
    assert ei.value.status_code == 401


def test_invalid_token_401(monkeypatch):
    import main

    def _reject(_token):
        raise main.HTTPException(status_code=401, detail="token_invalid")

    monkeypatch.setattr(main, "_verify_analytics_token", _reject)
    with pytest.raises(main.HTTPException) as ei:
        _call("sk-bad", {}, monkeypatch)
    assert ei.value.status_code == 401


# ── 2. 有数据 → 200 + keywords 数组 + total ──
def test_with_data_200(monkeypatch):
    rows = [
        _seed_row("宠物饮水机", count=120, uniq_queries_wca=45, uniq_sellers=12),
        _seed_row("猫砂盆", count=80, uniq_queries_wca=30, uniq_sellers=8),
    ]
    _mock_engine(monkeypatch, rows)
    resp = _call("sk-ok", {}, monkeypatch)
    assert isinstance(resp, dict)
    assert resp["total"] == 2
    assert len(resp["keywords"]) == 2
    first = resp["keywords"][0]
    assert first["query"] == "宠物饮水机"
    assert first["count"] == 120
    assert first["uniq_queries_wca"] == 45
    assert first["uniq_sellers"] == 12
    assert first["source"] == "fetched"


# ── 3. q 过滤生效（ILIKE '%q%'）──
def test_q_filter_propagates(monkeypatch):
    captured = _mock_engine(monkeypatch, [_seed_row("宠物饮水机")])
    resp = _call("sk-ok", {"q": "饮水"}, monkeypatch)
    assert resp["total"] == 1
    assert "ILIKE" in captured["sql"]
    assert captured["params"]["q"] == "%饮水%"
    # 过滤排序：uniq_queries_wca DESC NULLS LAST, count DESC
    assert "uniq_queries_wca DESC NULLS LAST" in captured["sql"]
    assert "count DESC" in captured["sql"]


# ── 4. 异常不泄漏内部细节 → 500 通用消息 ──
def test_db_error_no_leak_500(monkeypatch):
    import main

    _mock_auth(monkeypatch)
    _mock_engine(monkeypatch, [])
    monkeypatch.setattr(
        queries_service, "search_public",
        lambda q="", limit=20: (_ for _ in ()).throw(RuntimeError("secret pg traceback")),
    )
    with pytest.raises(main.HTTPException) as ei:
        _call("sk-ok", {}, monkeypatch)
    assert ei.value.status_code == 500
    assert "secret pg traceback" not in ei.value.detail
    assert "pg traceback" not in ei.value.detail


# ── 5. 限流超限 → 429 ──
def test_rate_limited_429(monkeypatch):
    import main

    _mock_auth(monkeypatch)
    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    with pytest.raises(main.HTTPException) as ei:
        _call("sk-ok", {}, monkeypatch)
    assert ei.value.status_code == 429

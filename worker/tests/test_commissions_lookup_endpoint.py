# -*- coding: utf-8 -*-
"""任务 2.1: /api/v1/commissions/lookup 佣金查询端点单测（mock 鉴权 + mock 佣金解析，不真连网络）。

覆盖：
1. 命中：mock get_category_commission 返回 6 段 dict → 200 + found:true + fbs/fbo 嵌套正确
2. 未命中：mock 返回 None → 200 + found:false
3. 无 token → 401
4. category_id 缺失 / 非整数 → 400
5. 限流超限 → 429（契约 v0.58 安全模式：与 analytics 端点同 RateLimiter）
6. 端点把解析后的 int category_id 传给 get_category_commission（防字符串直传）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_commissions_lookup_endpoint.py -q
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

HIT_ROW = {
    "fbs_leq_1500": 8.0,
    "fbs_leq_5000": 7.0,
    "fbs_gt_5000": 6.0,
    "fbo_leq_1500": 9.0,
    "fbo_leq_5000": 8.0,
    "fbo_gt_5000": 7.0,
    "source": "what_to_sell",
}


class FakeGetRequest:
    """GET 请求 fake：Bearer token + query_params dict（对齐 test_analytics_endpoints.FakeGetRequest）。"""

    def __init__(self, token, query=None):
        self._token = token
        self.query_params = query or {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


def _mock_auth(monkeypatch):
    """Supabase 未配置 → _verify_analytics_token 本地放行（生产同款降级路径）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)


def _mock_resolver(monkeypatch, result):
    """mock utils.commission_resolver.get_category_commission（端点内 lazy import → 打模块属性）。"""
    from utils import commission_resolver

    captured = {}
    if result is not None:

        def fake(category_id):
            captured["category_id"] = category_id
            return dict(result)

    else:

        def fake(category_id):
            captured["category_id"] = category_id
            return None

    monkeypatch.setattr(commission_resolver, "get_category_commission", fake)
    return captured


def _call(token, query, monkeypatch):
    import main

    return asyncio.run(main.http_commissions_lookup(FakeGetRequest(token, query)))


# ── 1. 命中：200 + found:true + fbs/fbo 嵌套结构 ──
def test_lookup_found(monkeypatch):
    _mock_auth(monkeypatch)
    captured = _mock_resolver(monkeypatch, HIT_ROW)
    resp = _call("sk-ok", {"category_id": "17028929"}, monkeypatch)
    assert resp["found"] is True
    assert resp["fbs"] == {"leq_1500": 8.0, "leq_5000": 7.0, "gt_5000": 6.0}
    assert resp["fbo"] == {"leq_1500": 9.0, "leq_5000": 8.0, "gt_5000": 7.0}
    assert resp["source"] == "what_to_sell"
    # 端点必须把解析后的 int 传给解析器（防字符串直传）
    assert captured["category_id"] == 17028929
    assert isinstance(captured["category_id"], int)


# ── 2. 未命中：200 + found:false ──
def test_lookup_not_found(monkeypatch):
    _mock_auth(monkeypatch)
    _mock_resolver(monkeypatch, None)
    resp = _call("sk-ok", {"category_id": "99999"}, monkeypatch)
    assert resp == {"found": False}


# ── 3. 无 token → 401 ──
def test_lookup_no_token_401(monkeypatch):
    import main

    _mock_auth(monkeypatch)
    with pytest.raises(main.HTTPException) as ei:
        _call("", {"category_id": "1"}, monkeypatch)
    assert ei.value.status_code == 401


# ── 4. category_id 缺失 / 非整数 → 400 ──
def test_lookup_invalid_category_400(monkeypatch):
    import main

    _mock_auth(monkeypatch)
    # 非整数
    with pytest.raises(main.HTTPException) as ei:
        _call("sk-ok", {"category_id": "abc"}, monkeypatch)
    assert ei.value.status_code == 400
    # 缺失
    with pytest.raises(main.HTTPException) as ei:
        _call("sk-ok", {}, monkeypatch)
    assert ei.value.status_code == 400


# ── 5. 限流超限 → 429 ──
def test_lookup_rate_limited_429(monkeypatch):
    import main

    _mock_auth(monkeypatch)
    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    with pytest.raises(main.HTTPException) as ei:
        _call("sk-ok", {"category_id": "1"}, monkeypatch)
    assert ei.value.status_code == 429


# ── 6. sk- 前缀剥离后仍命中 ──
def test_lookup_token_with_sk_prefix(monkeypatch):
    _mock_auth(monkeypatch)
    captured = _mock_resolver(monkeypatch, HIT_ROW)
    resp = _call("sk-abc123", {"category_id": "1"}, monkeypatch)
    assert resp["found"] is True
    assert captured["category_id"] == 1

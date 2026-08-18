"""W10 D12: /api/v1/discovery/runs 选品结果归档端点单测（mock Supabase + mock DB，不真连网络）。

覆盖：
1. POST 合法上报 → 200 + {status: ok, inserted: 1}；插入行含 tenant/keyword/filters/candidates
2. GET → 按 tenant 隔离（A 的 token 查不到 B 的行）
3. 无 token → 401；错误 token → 401；限流 → 429
4. 缺 keyword → 400（Pydantic 校验失败，错误脱敏）
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0] if self._rows else 0


class FakeConn:
    """写路径 fake：捕获 insert 语句编译参数，按 rowcounts 序列吐 rowcount。"""

    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        try:
            self._engine.compiled_params.append(stmt.compile().params)
        except Exception:
            self._engine.compiled_params.append({})
        rc = self._engine.rowcounts.pop(0) if self._engine.rowcounts else 0
        return FakeResult(rowcount=rc)

    def commit(self):
        pass


class FakeEngine:
    """模拟 PG engine（写路径）：捕获插入语句 + 按顺序吐 rowcount。"""

    def __init__(self, rowcounts=None):
        self.rowcounts = list(rowcounts or [])
        self.calls = []
        self.compiled_params = []

    def connect(self):
        return FakeConn(self)


class FakeReadConn:
    """读路径 fake：T4b.2 GET 全局共享——返回全部预置行（不再按 tenant 过滤）。"""

    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        rows = self._engine.all_rows
        if "COUNT(*)" in str(stmt):
            return FakeResult(rows=[len(rows)])
        return FakeResult(rows=rows)

    def commit(self):
        pass


class FakeReadEngine:
    def __init__(self, rows):
        self.all_rows = rows
        self.calls = []

    def connect(self):
        return FakeReadConn(self)


class FakeSupabase:
    """模拟 Supabase tokens 表查询：data 可配（[] = 查无记录）。"""

    def __init__(self, data):
        self._data = data

    def table(self, name):
        return self

    def select(self, *cols):
        return self

    def eq(self, *a):
        return self

    def is_(self, *a):
        return self

    def execute(self):
        return type("Resp", (), {"data": self._data})()


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class FakeGetRequest:
    def __init__(self, token):
        self._token = token
        self.query_params = {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


VALID_BODY = {
    "token": "sk-tenant-a",
    "keyword": "宠物饮水机",
    "filters": {"min_margin": 0.2, "max_products": 50},
    "candidates": [
        {"offerId": "111", "title": "饮水机A", "price": 19.9, "monthly_sales": 120},
        {"offerId": "222", "title": "饮水机B", "price": 25.0, "monthly_sales": 80},
    ],
}


def _param(compiled_params, col):
    """SQLAlchemy 编译参数键可能带 _m0 后缀（多值 insert），按列名去掉后缀后匹配。"""
    for k, v in compiled_params.items():
        if k == col or k.split("_m")[0] == col:
            return v
    return None


def _post(body, monkeypatch, rowcounts=None, supabase=None):
    """直接调用 POST 端点（FakeRequest + mock get_supabase_client/get_engine）。"""
    import main

    if supabase is None:
        monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    else:
        monkeypatch.setattr(main, "get_supabase_client", lambda: supabase)
    engine = FakeEngine(rowcounts or [1])
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    result = asyncio.run(main.v1_discovery_report_run(FakeRequest(body)))
    return result, engine


def _get(token, monkeypatch, rows_by_tenant, supabase=None):
    import main

    if supabase is None:
        monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    else:
        monkeypatch.setattr(main, "get_supabase_client", lambda: supabase)
    engine = FakeReadEngine(rows_by_tenant)
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    return asyncio.run(main.v1_discovery_list_runs(FakeGetRequest(token)))


def test_post_inserts_row(monkeypatch):
    """合法上报 → 200 + {status: ok, inserted: 1}；插入行含 tenant/keyword/filters/candidates。"""
    resp, engine = _post(VALID_BODY, monkeypatch, rowcounts=[1])
    assert resp == {"status": "ok", "inserted": 1, "upserted": 0}
    assert len(engine.calls) == 1, "应为单次 INSERT"
    assert "discovery_runs" in engine.calls[0]
    params = engine.compiled_params[0]
    assert _param(params, "tenant_id") == "tenant-a"
    assert _param(params, "keyword") == "宠物饮水机"
    assert _param(params, "filters_json") == {"min_margin": 0.2, "max_products": 50}
    assert _param(params, "candidates_json") == VALID_BODY["candidates"]


def test_post_no_token_returns_401(monkeypatch):
    import main

    body = {"keyword": "x", "candidates": []}
    with pytest.raises(main.HTTPException) as ei:
        _post(body, monkeypatch, rowcounts=[])
    assert ei.value.status_code == 401


def test_post_invalid_token_returns_401(monkeypatch):
    import main

    body = {**VALID_BODY, "token": "sk-bad"}
    with pytest.raises(main.HTTPException) as ei:
        _post(body, monkeypatch, rowcounts=[], supabase=FakeSupabase([]))
    assert ei.value.status_code == 401


def test_post_rate_limited_returns_429(monkeypatch):
    import main

    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    with pytest.raises(main.HTTPException) as ei:
        _post(VALID_BODY, monkeypatch, rowcounts=[])
    assert ei.value.status_code == 429


def test_post_missing_keyword_rejected(monkeypatch):
    """缺 keyword → 400 错误 JSON（错误脱敏，不回显内部异常）。"""
    body = {"token": "sk-ok", "candidates": [{"offerId": "1"}]}
    resp, _ = _post(body, monkeypatch, rowcounts=[])
    assert getattr(resp, "status_code", None) == 400
    body_s = resp.body.decode() if hasattr(resp, "body") else str(resp)
    assert "INVALID_REQUEST" in body_s


def test_get_global_sharing(monkeypatch):
    """T4b.2：GET 全局共享——A 的 token 可见 A+B 全部归档，含贡献者列。"""
    rows = [
        ("1", "宠物饮水机", {"min_margin": 0.2}, [{"offerId": "111"}], datetime(2026, 8, 17, 10, 0, 0), "tenant-a"),
        ("2", "猫玩具", None, [{"offerId": "222"}], datetime(2026, 8, 17, 9, 0, 0), "tenant-a"),
        ("3", "化妆刷", None, [{"offerId": "333"}], datetime(2026, 8, 17, 8, 0, 0), "tenant-b"),
    ]

    resp_a = _get("sk-tenant-a", monkeypatch, rows)
    keywords_a = [it["keyword"] for it in resp_a["items"]]
    assert keywords_a == ["宠物饮水机", "猫玩具", "化妆刷"]  # 含 B 的归档
    assert resp_a["total"] == 3
    # 贡献者列：每条带 contributed_by_token_id
    contributors = {it["contributed_by_token_id"] for it in resp_a["items"]}
    assert contributors == {"tenant-a", "tenant-b"}

    resp_b = _get("sk-tenant-b", monkeypatch, rows)
    assert resp_b["total"] == 3  # B 同样可见全部


def test_get_requires_token(monkeypatch):
    import main

    engine = FakeReadEngine({})
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    with pytest.raises(main.HTTPException) as ei:
        asyncio.run(main.v1_discovery_list_runs(FakeGetRequest("")))
    assert ei.value.status_code == 401

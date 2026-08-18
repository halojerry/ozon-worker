"""v0.34 C5: /api/v1/analytics/* 上报端点单测（mock Supabase + mock DB，不真连网络）。

覆盖：
1. 无 token → 401
2. 错误 token（Supabase 查无记录/状态非 active）→ 401
3. 空数组 → 400 合理错误
4. 合法上报 → 200 + inserted 计数；重复上报 → upserted（不报错）
5. 大批量（1000 条）解析/组包性能 < 2s（真实 DB 吞吐由本地 Docker 实测）
6. 非法条目结构 → 400 错误 JSON
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


class FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        rc = self._engine.rowcounts.pop(0) if self._engine.rowcounts else 0
        return FakeResult(rc)

    def commit(self):
        pass


class FakeEngine:
    """模拟 PG engine：按顺序吐出 rowcount（第一趟 DO NOTHING / 第二趟 DO UPDATE）。"""

    def __init__(self, rowcounts):
        self.rowcounts = list(rowcounts)
        self.calls = []

    def connect(self):
        return FakeConn(self)


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
    def __init__(self, token, query=None):
        self._token = token
        self.query_params = query or {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


class FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0] if self._rows else 0


class FakeReadConn:
    """GET 榜单读路径 fake：返回全部预置行（验证无 tenant 过滤）。"""

    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        if "COUNT(*)" in str(stmt):
            return FakeRows([len(self._engine.rows)])
        return FakeRows(self._engine.rows)

    def commit(self):
        pass


class FakeReadEngine:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def connect(self):
        return FakeReadConn(self)


ENDPOINTS = {
    "queries": "v1_analytics_queries",
    "ozon-bestsellers": "v1_analytics_ozon_bestsellers",
    "market-bestsellers": "v1_analytics_market_bestsellers",
}

VALID_BODIES = {
    "queries": {"queries": [{"query": "宠物用品", "count": 120, "ca": 3.2, "uniq_sellers": 8.0}]},
    "ozon-bestsellers": {"items": [{"sku_or_id": "SKU-1", "brand": "Xiaomi", "ordering_count": 500, "avg_price_rub": 1999.0}]},
    "market-bestsellers": {"items": [{"product_name": "无线吸尘器", "daily_avg": 42.5, "other_platform_price": 899.0}]},
}


def _call(kind, body, monkeypatch, rowcounts=None, supabase=None):
    """直接调用端点函数（FakeRequest + mock get_supabase_client/get_engine）。"""
    import main

    if supabase is None:
        monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    else:
        monkeypatch.setattr(main, "get_supabase_client", lambda: supabase)
    engine = FakeEngine(rowcounts or [])
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    fn = getattr(main, ENDPOINTS[kind])
    result = asyncio.run(fn(FakeRequest(body)))
    return result, engine


def _assert_error(resp, status_code):
    assert getattr(resp, "status_code", None) == status_code, f"expected {status_code}, got {resp}"
    body = resp.body.decode() if hasattr(resp, "body") else str(resp)
    assert "error" in body or "INVALID_REQUEST" in body


def test_no_token_returns_401(monkeypatch):
    """无 token → HTTPException 401。"""
    import main

    body = {"token": "", "queries": [{"query": "x"}]}
    with pytest.raises(main.HTTPException) as ei:
        _call("queries", body, monkeypatch)
    assert ei.value.status_code == 401


def test_invalid_token_returns_401(monkeypatch):
    """错误 token（Supabase 查无记录）→ 401。"""
    import main

    body = {"token": "sk-bad-token", "queries": [{"query": "x"}]}
    with pytest.raises(main.HTTPException) as ei:
        _call("queries", body, monkeypatch, supabase=FakeSupabase([]))
    assert ei.value.status_code == 401


def test_inactive_token_returns_401(monkeypatch):
    """token 状态非 active（status=2 disabled）→ 401。"""
    import main

    body = {"token": "sk-disabled", "queries": [{"query": "x"}]}
    with pytest.raises(main.HTTPException) as ei:
        _call("queries", body, monkeypatch, supabase=FakeSupabase([{"status": 2}]))
    assert ei.value.status_code == 401


def test_empty_list_rejected(monkeypatch):
    """空数组 → 400 合理错误（不是 200）。"""
    body = {"token": "sk-ok", "queries": []}
    resp, engine = _call("queries", body, monkeypatch)
    _assert_error(resp, 400)


def test_invalid_item_shape_rejected(monkeypatch):
    """条目非对象/缺必填字段 → 400 错误 JSON。"""
    resp, _ = _call("queries", {"token": "sk-ok", "queries": [{"count": 1}]}, monkeypatch)
    _assert_error(resp, 400)


@pytest.mark.parametrize("kind", ["queries", "ozon-bestsellers", "market-bestsellers"])
def test_valid_report_inserted(kind, monkeypatch):
    """合法上报 → 200 + inserted=批内条数；SQL 含 ON CONFLICT（upsert 路径）。"""
    n = 3
    resp, engine = _call(kind, {"token": "sk-ok", **{k: v * n for k, v in VALID_BODIES[kind].items()}},
                         monkeypatch, rowcounts=[n, n])
    assert resp == {"status": "ok", "inserted": n, "upserted": 0}
    assert len(engine.calls) == 2, "应执行两趟 upsert"
    assert "ON CONFLICT" in engine.calls[0] and "DO NOTHING" in engine.calls[0]
    assert "ON CONFLICT" in engine.calls[1] and "DO UPDATE" in engine.calls[1]


@pytest.mark.parametrize("kind", ["queries", "ozon-bestsellers", "market-bestsellers"])
def test_duplicate_report_upserted(kind, monkeypatch):
    """重复上报（同一自然键已存在）→ 不报错，upserted=批内条数。"""
    n = 2
    resp, _ = _call(kind, {"token": "sk-ok", **{k: v * n for k, v in VALID_BODIES[kind].items()}},
                    monkeypatch, rowcounts=[0, n])
    assert resp == {"status": "ok", "inserted": 0, "upserted": n}


def test_valid_token_via_supabase_passes(monkeypatch):
    """Supabase 返回 active token → 放行，正常落库计数。"""
    resp, _ = _call("queries", {"token": "sk-active", "queries": [{"query": "геймпад"}]},
                    monkeypatch, rowcounts=[1, 1], supabase=FakeSupabase([{"status": 1}]))
    assert resp["status"] == "ok" and resp["inserted"] == 1


def test_bulk_1000_rows_fast(monkeypatch):
    """1000 条批量上报：解析/组包/计数在 2s 内完成（DB 吞吐由本地 Docker 实测）。"""
    items = [{"query": f"关键词{i}", "count": i, "ca": float(i % 7), "avg_ca_rub": 100.0 + i,
              "avg_count_items": 5.0, "items_views": 10000.0 + i,
              "uniq_queries_wca": i % 3, "uniq_sellers": 10.0} for i in range(1000)]
    t0 = time.monotonic()
    resp, engine = _call("queries", {"token": "sk-bulk", "queries": items}, monkeypatch,
                         rowcounts=[1000, 1000])
    elapsed = time.monotonic() - t0
    assert resp == {"status": "ok", "inserted": 1000, "upserted": 0}
    assert elapsed < 2.0, f"1000 条处理耗时 {elapsed:.2f}s 超过 2s 上限"


# ── v0.34 security 加固：限流 / 条数上限 / 错误脱敏 ──

def test_rate_limit_exceeded_returns_429(monkeypatch):
    """单 token 超限 → 429（防批量打爆共享 PG）。"""
    import main
    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    body = {"token": "sk-ok", "queries": [{"query": "x"}]}
    with pytest.raises(main.HTTPException) as ei:
        _call("queries", body, monkeypatch)
    assert ei.value.status_code == 429


def test_too_many_items_rejected(monkeypatch):
    """单次上报 > 2000 条 → 400（防超大 JSON 内存/DB 压力）。"""
    items = [{"query": f"k{i}"} for i in range(2001)]
    resp, _ = _call("queries", {"token": "sk-ok", "queries": items}, monkeypatch)
    _assert_error(resp, 400)


def test_invalid_item_error_not_leak_internal(monkeypatch):
    """Pydantic 校验失败 → 响应不含内部异常细节（防泄露字段/结构）。"""
    resp, _ = _call("queries", {"token": "sk-ok", "queries": [{"count": "not-a-number"}]}, monkeypatch)
    _assert_error(resp, 400)
    body = resp.body.decode() if hasattr(resp, "body") else str(resp)
    assert "int_parsing" not in body and "Input should" not in body


# ── T4b.1：GET /analytics/bestsellers 全局共享（无 tenant 过滤）+ 贡献者列 ──

def test_bestsellers_get_global_sharing(monkeypatch):
    """A 用户 token 可见 B 采集的榜单（全局共享），每条带 contributed_by_token_id。"""
    import main
    from services import analytics_service
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    rows = [
        ("sku-a", "品牌A", "宠物用品", 100.0, 10, 99.9, "tok-a"),
        ("sku-b", "品牌B", "家居", 200.0, 20, 199.9, "tok-b"),
    ]
    engine = FakeReadEngine(rows)
    monkeypatch.setattr(analytics_service, "get_engine", lambda: engine)
    resp = asyncio.run(main.v1_analytics_list_bestsellers(
        FakeGetRequest("sk-ok", {"limit": "50"})))
    assert resp["total"] == 2
    assert {i["sku_or_id"] for i in resp["items"]} == {"sku-a", "sku-b"}
    assert {i["contributed_by_token_id"] for i in resp["items"]} == {"tok-a", "tok-b"}
    sql = " ".join(engine.calls)
    assert "contributed_by_token_id = " not in sql  # 无 tenant 过滤
    assert "WHERE contributed_by_token_id" not in sql


def test_bestsellers_get_requires_token(monkeypatch):
    """无 token → 401。"""
    import main
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    with pytest.raises(main.HTTPException) as ei:
        asyncio.run(main.v1_analytics_list_bestsellers(FakeGetRequest("")))
    assert ei.value.status_code == 401

"""A8 F6 / BL-06（repo-gov B5）: MXOU key 明文落库 → token_fp 指纹化回归。

覆盖（写侧双写 / 展示脱敏 / 迁移与回填幂等）：
1. tenant_service.token_fingerprint 唯一入口：确定性 sha256 前 16 / sk- 自剥 / 空防御
2. 五贡献表 model 元数据：token_fp String(16) 可空 + ix_<table>_token_fp 索引
3. 三 analytics 上报写侧双写 token_fp（编译参数级断言）+ data_cols 冲突刷新
4. discovery_runs 写侧双写（tenant_id 明文判定见 model.py 注释）+ GET 展示 contributed_by_fp
5. selection_insight upsert 双写（列 + ON CONFLICT 刷新）
6. init_data.migrate_token_fp DDL 契约（IF NOT EXISTS ×5 表 + 版本登记）
7. migrate_token_fp_backfill 真库分页回填 + 幂等（重跑 0 行）+ 空明文跳过
   （PG 直连探测守卫，不可达自动 skip——对齐 AGENTS「勿读 env 判存」纪律）

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_token_fp_v1.py -q
    # 回填真库用例：PGDATABASE_URL=postgresql://postgres:localdev123@localhost:5433/ozon
"""
import asyncio
import hashlib
import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tenant_service import token_fingerprint  # noqa: E402
from storage.database.shared.model import (  # noqa: E402
    BlueOceanQuery,
    DiscoveryRun,
    MarketBestseller,
    OzonBestseller,
    SelectionInsight,
)


# ============================================================
# 1. token_fingerprint 唯一入口
# ============================================================

def test_token_fingerprint_shape_and_determinism():
    """sha256(clean) hex 前 16 位；同输入恒同输出（等值检索键前提）。"""
    expected = hashlib.sha256("tok-a".encode("utf-8")).hexdigest()[:16]
    assert token_fingerprint("tok-a") == expected
    assert token_fingerprint("tok-a") == token_fingerprint("tok-a")
    assert len(expected) == 16
    int(expected, 16)  # 纯 hex


def test_token_fingerprint_strips_sk_prefix():
    """sk- 前缀自剥（与 resolve_tenant 同语义）——上报 raw/clean 同指纹。"""
    assert token_fingerprint("sk-tok-a") == token_fingerprint("tok-a")


def test_token_fingerprint_empty_defensive():
    """空/None → ''（防御值；调用方均在 401 闸后，不得落库当合法指纹）。"""
    assert token_fingerprint("") == ""
    assert token_fingerprint(None) == ""


# ============================================================
# 2. 五表 model 元数据
# ============================================================

@pytest.mark.parametrize("model,table", [
    (BlueOceanQuery, "blue_ocean_queries"),
    (OzonBestseller, "ozon_bestsellers"),
    (MarketBestseller, "market_bestsellers"),
    (SelectionInsight, "selection_insights"),
    (DiscoveryRun, "discovery_runs"),
])
def test_five_tables_have_token_fp_column(model, table):
    """token_fp：可空 String(16) + ix_<table>_token_fp 索引（与 init_data DDL 同名）。"""
    t = model.__table__
    col = t.columns["token_fp"]
    assert col.nullable is True, "存量行未回填前为 NULL——必须可空"
    assert col.type.length == 16
    assert f"ix_{table}_token_fp" in {ix.name for ix in t.indexes}


# ============================================================
# 3/4. 写侧双写（复用既有端点测试的 fake 形态：mock Supabase + 捕获编译参数）
# ============================================================

class _FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(str(stmt))
        try:
            self._engine.compiled.append(stmt.compile().params)
        except Exception:
            self._engine.compiled.append({})
        if params is not None:
            # text() 语句的运行期绑定参数不在 compile().params 里——单独捕获
            self._engine.exec_params.append(dict(params))
        rc = self._engine.rowcounts.pop(0) if self._engine.rowcounts else 0
        return type("R", (), {"rowcount": rc, "scalar": lambda self: 0})()

    def commit(self):
        pass


class _FakeEngine:
    def __init__(self, rowcounts=None):
        self.rowcounts = list(rowcounts or [])
        self.calls = []
        self.compiled = []
        self.exec_params = []
        self.conn = _FakeConn(self)

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn  # selection_insight upsert 走 begin() 上下文


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _param(compiled_params, col):
    """多值 insert 编译参数键带 _m0 后缀——按列名去后缀匹配（既有测试同款）。"""
    for k, v in compiled_params.items():
        if k == col or k.split("_m")[0] == col:
            return v
    return None


@pytest.mark.parametrize("kind,fn,entry", [
    ("queries", "v1_analytics_queries", {"queries": [{"query": "宠物用品", "count": 3}]}),
    ("ozon-bestsellers", "v1_analytics_ozon_bestsellers",
     {"items": [{"sku_or_id": "SKU-1", "avg_price_rub": 99.0}]}),
    ("market-bestsellers", "v1_analytics_market_bestsellers",
     {"items": [{"product_name": "无线吸尘器"}]}),
])
def test_analytics_report_double_writes_token_fp(monkeypatch, kind, fn, entry):
    """三 analytics 上报：每行双写 token_fp = fp(clean token)（编译参数级）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    engine = _FakeEngine([1, 1])
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    resp = asyncio.run(getattr(main, fn)(_FakeRequest({"token": "sk-tok-a", **entry})))
    assert resp == {"status": "ok", "inserted": 1, "upserted": 0}
    assert any("token_fp" in c for c in engine.calls), "INSERT 必须含 token_fp 列"
    assert _param(engine.compiled[0], "token_fp") == token_fingerprint("tok-a")


def test_analytics_upsert_refreshes_token_fp_on_conflict():
    """token_fp 进三 kind 的 data_cols——重复上报（冲突行）刷新指纹自愈存量 NULL。"""
    import main

    for kind in ("queries", "ozon-bestsellers", "market-bestsellers"):
        _model, _conflict, data_cols, _item, _key = main._ANALYTICS_KINDS[kind]
        assert "token_fp" in data_cols, f"{kind} 缺 token_fp 冲突刷新"


def test_discovery_run_double_writes_token_fp(monkeypatch):
    """discovery_runs（tenant_id 实为明文 key，grep 判定）：INSERT 双写 token_fp。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    engine = _FakeEngine([1])
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    body = {"token": "sk-tenant-a", "keyword": "宠物饮水机",
            "filters": {"min_margin": 0.2}, "candidates": [{"offerId": "1"}]}
    resp = asyncio.run(main.v1_discovery_report_run(_FakeRequest(body)))
    assert resp == {"status": "ok", "inserted": 1, "upserted": 0}
    assert _param(engine.compiled[0], "tenant_id") == "tenant-a"
    assert _param(engine.compiled[0], "token_fp") == token_fingerprint("tenant-a")


class _FakeGetRequest:
    def __init__(self, token, query=None):
        self._token = token
        self.query_params = query or {}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}


def test_discovery_get_exposes_contributed_by_fp(monkeypatch):
    """GET /discovery/runs：新增 contributed_by_fp（fp 前 8 位）；明文列灰度保留。"""
    import main

    rows = [
        ("1", "宠物饮水机", None, [], datetime(2026, 9, 11, 10, 0, 0), "tenant-a"),
        ("2", "猫玩具", None, [], datetime(2026, 9, 11, 9, 0, 0), "tenant-b"),
    ]

    class _ReadConn:
        def __init__(self, eng):
            self._eng = eng

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params=None):
            self._eng.calls.append(str(stmt))
            if "COUNT(*)" in str(stmt):
                return type("R", (), {"scalar": lambda self: len(rows)})()
            return type("R", (), {"fetchall": lambda self: rows, "scalar": lambda self: 0})()

        def commit(self):
            pass

    engine = _FakeEngine()
    engine.calls = []
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    monkeypatch.setattr(engine, "connect", lambda: _ReadConn(engine))
    resp = asyncio.run(main.v1_discovery_list_runs(_FakeGetRequest("sk-tenant-a")))
    fp8 = token_fingerprint("tenant-a")[:8]
    assert {it["contributed_by_fp"] for it in resp["items"]} == {fp8, token_fingerprint("tenant-b")[:8]}
    assert len(fp8) == 8
    # 明文列灰度期保留（既有消费方/测试锁定）
    assert {it["contributed_by_token_id"] for it in resp["items"]} == {"tenant-a", "tenant-b"}


# ============================================================
# 5. selection_insights upsert 双写
# ============================================================

def test_selection_insight_upsert_carries_token_fp(monkeypatch):
    """upsert_from_discovery_run：INSERT 列/参数带 token_fp；冲突分支刷新指纹。"""
    from services import selection_insight_service as svc

    engine = _FakeEngine()
    monkeypatch.setattr(svc, "get_engine", lambda: engine)
    ok = svc.upsert_from_discovery_run(
        "tok-xyz", "宠物饮水机",
        [{"ozon_price": 100.0, "profit_margin": 0.3, "match_1688_price": 5.5, "monthly_sales": 10}],
    )
    assert ok is True
    sql = " ".join(engine.calls)
    assert "token_fp" in sql and "INSERT INTO selection_insights" in sql
    assert "token_fp = EXCLUDED.token_fp" in sql, "冲突行必须刷新指纹"
    # text() 语句运行期绑定参数走 execute(params)（compile().params 不含运行值）
    all_params = {}
    for c in engine.exec_params:
        all_params.update(c)
    assert all_params.get("token_fp") == token_fingerprint("tok-xyz")


# ============================================================
# 6/7. init_data 迁移 + 回填
# ============================================================

class _RecConn:
    def __init__(self, rec):
        self._rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._rec.append((str(sql), dict(params or {})))
        return MagicMock()

    def commit(self):
        pass


class _RecEngine:
    def __init__(self):
        self.executed = []
        self.conn = _RecConn(self.executed)

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


def test_migrate_token_fp_ddl_contract_and_registration():
    """五表幂等 DDL（ADD COLUMN / CREATE INDEX 均 IF NOT EXISTS）+ 版本登记。"""
    from scripts import init_data as init_mod

    eng = _RecEngine()
    init_mod.migrate_token_fp(eng)
    sqls = [s for s, _ in eng.executed]
    for table in ("blue_ocean_queries", "ozon_bestsellers", "market_bestsellers",
                  "selection_insights", "discovery_runs"):
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS token_fp VARCHAR(16)" in sqls
        assert any(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_token_fp" in s and table in s
            for s in sqls
        ), f"{table} 缺同名索引"
    reg = [(s, p) for s, p in eng.executed if "schema_migrations" in s]
    assert reg and reg[0][1]["version"] == "2026-09-repo-gov-b5-tokenfp"


def _pg_available():
    """直连探测（勿读 env 判存——import main 会注入容器风格 URL，AGENTS 纪律）。"""
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine("postgresql://postgres:localdev123@localhost:5433/ozon")
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception:
        return None


@pytest.fixture()
def pg_engine():
    eng = _pg_available()
    if eng is None:
        pytest.skip("本地 PG 5433 不可达——回填真库用例跳过")
    yield eng
    eng.dispose()


def test_backfill_updates_idempotent_and_skips_empty(pg_engine):
    """真库：回填补指纹 / 重跑 0 行（幂等）/ 空明文行保持 NULL / sk- 前缀剥除。"""
    from sqlalchemy import text

    from scripts import init_data as init_mod

    eng = pg_engine
    init_mod.migrate_token_fp(eng)  # 真库幂等 DDL（新装/半迁移库兜底路径顺带验证）
    marker = f"__fp_t_{uuid.uuid4().hex[:8]}"
    with eng.begin() as conn:
        row_id = conn.execute(text(
            "INSERT INTO blue_ocean_queries (query, count, contributed_by_token_id, source) "
            "VALUES (:q, 1, :tok, 'fetched') RETURNING id"
        ), {"q": marker, "tok": "sk-fp-test-token"}).scalar_one()
        # 第二条真 token 行：batch_size=1 时强制跨页（回填分页路径确定性覆盖）
        row2_id = conn.execute(text(
            "INSERT INTO blue_ocean_queries (query, count, contributed_by_token_id, source) "
            "VALUES (:q, 1, :tok, 'fetched') RETURNING id"
        ), {"q": marker + "_b", "tok": "fp-test-token-2"}).scalar_one()
        empty_id = conn.execute(text(
            "INSERT INTO blue_ocean_queries (query, count, contributed_by_token_id, source) "
            "VALUES (:q, 1, '', 'fetched') RETURNING id"
        ), {"q": marker + "_e"}).scalar_one()
    try:
        n1 = init_mod.migrate_token_fp_backfill(eng, batch_size=1)  # batch=1 强制多页
        assert n1 >= 2
        with eng.connect() as conn:
            fp = conn.execute(text(
                "SELECT token_fp FROM blue_ocean_queries WHERE id = :i"), {"i": row_id}).scalar()
            fp2 = conn.execute(text(
                "SELECT token_fp FROM blue_ocean_queries WHERE id = :i"), {"i": row2_id}).scalar()
            fp_empty = conn.execute(text(
                "SELECT token_fp FROM blue_ocean_queries WHERE id = :i"), {"i": empty_id}).scalar()
        assert fp == token_fingerprint("fp-test-token"), "回填值必须与唯一入口同源（含 sk- 剥除）"
        assert fp2 == token_fingerprint("fp-test-token-2")
        assert fp_empty is None, "空明文无 key 可指——保持 NULL 跳过"
        # 幂等：重跑不再命中（全表无 NULL 且明文非空的行——本库刚初始化）
        n2 = init_mod.migrate_token_fp_backfill(eng, batch_size=500)
        assert n2 == 0
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM blue_ocean_queries WHERE query LIKE :m"),
                         {"m": marker + "%"})

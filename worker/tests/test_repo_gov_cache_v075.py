"""repo-gov 一期·缓存治理单测（BL-25/S9-03 + BL-24 部分，TDD）。

覆盖三项一期改动：
1. db.py：statement_timeout=30s connect_args + 池配比 pool_size=20/max_overflow=20
   （design-b2b-perf-hardening.md §2.2A）；
2. dict_value_cache.py：写侧 TTL ±10% 抖动（防雪崩）+ 60s 负缓存
   （防穿透，命中不回源 / 过期放行回源 / PG sentinel 空行识别）；
3. refresh_category_tree.py：category_cache 名义 TTL 10 年 → 90d
   （design-b2b-cache-ttl-governance.md §2 选项 2）。

全部 mock 层单测不触网；真实 PG 会话级 statement_timeout 断言用直连探测 skip 守卫
（不读 env 判存——PGDATABASE_URL 可能被注入容器风格 URL，直连 5433 才作数）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_repo_gov_cache_v075.py -q
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.dict_value_cache import (  # noqa: E402
    BUCKET_SCOPED,
    DICT_CACHE_TTL_SECONDS,
    _jitter_ttl,
    _NEG_CACHE,
    record_negative,
    routed_get,
    routed_set,
)


# ═══════════ 1a. db.py 引擎参数（fake engine 捕获 kwargs，不真连） ═══════════

def test_engine_kwargs_statement_timeout_and_pool():
    """create_engine 必须带 statement_timeout=30s connect_args + 池 20/20。"""
    import storage.database.db as dbmod

    captured = {}

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a, **_k):
            return None

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    def _fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeEngine()

    old_engine = dbmod._engine
    try:
        with mock.patch.object(dbmod, "get_db_url",
                               return_value="postgresql://u:p@localhost:5433/ozon"), \
             mock.patch.object(dbmod, "create_engine", _fake_create_engine):
            eng = dbmod._create_engine_with_retry()
            assert eng is not None
    finally:
        dbmod._engine = old_engine  # 防 fake 引擎污染全局单例

    kw = captured["kwargs"]
    assert kw["pool_size"] == 20, f"pool_size 应 20（BL-25 池配比），实际 {kw['pool_size']}"
    assert kw["max_overflow"] == 20, f"max_overflow 应 20，实际 {kw['max_overflow']}"
    assert kw["connect_args"] == {"options": "-c statement_timeout=30000"}, \
        f"statement_timeout=30s connect_args 缺失: {kw['connect_args']}"


def test_engine_kwargs_skip_connect_args_for_non_pg():
    """非 postgresql URL 不注入 connect_args（防御：sqlite 等方言不认 options）。"""
    import storage.database.db as dbmod

    captured = {}

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a, **_k):
            return None

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    def _fake_create_engine(url, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeEngine()

    old_engine = dbmod._engine
    try:
        with mock.patch.object(dbmod, "get_db_url", return_value="sqlite:///:memory:"), \
             mock.patch.object(dbmod, "create_engine", _fake_create_engine):
            dbmod._create_engine_with_retry()
    finally:
        dbmod._engine = old_engine

    assert "connect_args" not in captured["kwargs"]


def _parse_pg_time_ms(raw) -> int:
    """SHOW statement_timeout 返回值归一为 ms（PG 自适应单位：'30s' / '500ms' / 裸数值）。"""
    s = str(raw).strip()
    if s.endswith("ms"):
        return int(float(s[:-2]))
    if s.endswith("s"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))


def test_statement_timeout_live_session():
    """真实 PG：经 db.py 构造的会话 SHOW statement_timeout = 30s（设计验收探针）。"""
    import sqlalchemy

    import storage.database.db as dbmod

    url = os.environ.get("PGDATABASE_URL",
                         "postgresql://postgres:localdev123@localhost:5433/ozon")
    # 直连探测作 skip 守卫（不读 env 判存——可能被注入容器风格 URL，直连 5433 才作数）
    try:
        probe = sqlalchemy.create_engine(url)
        with probe.connect():
            pass
        probe.dispose()
    except Exception:
        import pytest
        pytest.skip("本地 PG 不可达")

    # _create_engine_with_retry 不写全局单例（get_engine 才写）——零污染拿真实引擎
    env_backup = os.environ.get("PGDATABASE_URL")
    os.environ["PGDATABASE_URL"] = url
    try:
        eng = dbmod._create_engine_with_retry()
        with eng.connect() as conn:
            val = conn.exec_driver_sql("SHOW statement_timeout").scalar()
        eng.dispose()
    finally:
        if env_backup is None:
            os.environ.pop("PGDATABASE_URL", None)
        else:
            os.environ["PGDATABASE_URL"] = env_backup
    assert int(_parse_pg_time_ms(val)) == 30000, f"会话级 statement_timeout 应 30000ms，实际 {val}"


# ═══════════ 2a. 写侧 TTL 抖动（防雪崩） ═══════════

def test_jitter_ttl_within_bounds():
    """±10% 边界：结果恒在 [int(0.9T), int(1.1T)]。"""
    for ttl in (DICT_CACHE_TTL_SECONDS, 3600, 60, 1):
        lo, hi = int(ttl * 0.9), int(ttl * 1.1)
        for _ in range(200):
            v = _jitter_ttl(ttl)
            assert lo <= v <= hi, f"ttl={ttl} 抖出边界: {v} ∉ [{lo},{hi}]"
            assert v >= 1


def test_jitter_ttl_zero_and_negative_passthrough():
    """非正 TTL（调用方自定义瞬时语义）不抖，原样返回。"""
    assert _jitter_ttl(0) == 0
    assert _jitter_ttl(-5) == -5


def test_jitter_ttl_disperses_batch():
    """同批写入抖散：200 次采样 max > min（集中过期打散的设计目标）。"""
    samples = {_jitter_ttl(DICT_CACHE_TTL_SECONDS) for _ in range(200)}
    assert len(samples) > 1, "抖动退化：200 次采样全同值"


def test_routed_set_applies_jitter():
    """routed_set 落库的 expires_in 必须是抖动值（三处写入点统一入口）。"""
    recorded = {}

    def _rec(self, attribute_id, description_category_id, type_id, values_data,
             language="ZH_HANS", expires_in=30 * 86400):
        recorded["expires_in"] = expires_in

    with mock.patch("utils.local_db_manager.LocalDBManager.set_dictionary_value_cache", _rec):
        routed_set(8229, [{"id": 9, "value": "Ларь"}], 85282223, 970988646,
                   attr_row={"category_dependent": True})
    got = recorded["expires_in"]
    lo, hi = int(DICT_CACHE_TTL_SECONDS * 0.9), int(DICT_CACHE_TTL_SECONDS * 1.1)
    assert lo <= got <= hi, f"routed_set 未过 _jitter_ttl: {got} ∉ [{lo},{hi}]"


# ═══════════ 2b. 负缓存（防穿透） ═══════════

def _clear_neg():
    _NEG_CACHE.clear()


def test_negative_hit_short_circuits_no_refetch():
    """负缓存命中 → routed_get 返回 []（确认空），且不再触发 PG 正读（不回源）。"""
    _clear_neg()
    calls = {"get": 0, "set": 0}

    def _get(self, *a, **k):
        calls["get"] += 1
        return None

    def _set(self, *a, **k):
        calls["set"] += 1

    with mock.patch("utils.local_db_manager.LocalDBManager.get_dictionary_value_cache", _get), \
         mock.patch("utils.local_db_manager.LocalDBManager.set_dictionary_value_cache", _set):
        record_negative(8229, 1, 2)  # 回源确认空后登记
        assert calls["set"] == 1, "record_negative 应写 PG sentinel 行"
        assert routed_get(8229, 1, 2) == [], "负缓存命中应返回 []"
        assert calls["get"] == 0, "负缓存命中不得触发 PG 正读（进程内短路）"
    _clear_neg()


def test_negative_expired_allows_refetch():
    """负缓存过期（60s）→ routed_get 返回 None（调用方获准回源）。"""
    _clear_neg()
    from utils.dict_value_cache import _neg_key

    record_negative(8229, 3, 4)
    # 直接把进程内负缓存项改成已过期（避免 patch 全局 time.time 的副作用）
    _NEG_CACHE[_neg_key(8229, 3, 4, "ZH_HANS")] = time.time() - 1
    with mock.patch("utils.local_db_manager.LocalDBManager.get_dictionary_value_cache",
                    lambda self, *a, **k: None):
        assert routed_get(8229, 3, 4) is None, "过期负缓存应放行回源（返回 None）"
    _clear_neg()


def test_negative_pg_sentinel_row_recognized():
    """PG sentinel 空行（values_data=[]，未过期）→ routed_get 返回 []（跨重启有效）。"""
    _clear_neg()
    # 进程内无记录（模拟重启），PG 有 sentinel 行
    def _get(self, attribute_id, dc, tp, language="ZH_HANS"):
        if (dc, tp) == (5, 6):
            return {"values_data": [], "expires_at": int(time.time()) + 60}
        return None  # global 回退也无

    with mock.patch("utils.local_db_manager.LocalDBManager.get_dictionary_value_cache", _get):
        assert routed_get(8229, 5, 6) == [], "PG sentinel 空行应识别为负命中返回 []"
    _clear_neg()


def test_negative_does_not_mask_global_data():
    """scoped 负 sentinel 不遮挡 global 桶正数据（对齐 get_dictionary_values 回退序）。"""
    _clear_neg()

    def _get(self, attribute_id, dc, tp, language="ZH_HANS"):
        if (dc, tp) == (7, 8):
            return {"values_data": [], "expires_at": int(time.time()) + 60}
        if (dc, tp) == (0, 0):
            return {"values_data": [{"id": 1, "value": "GLOBAL"}],
                    "expires_at": int(time.time()) + 3600}
        return None

    with mock.patch("utils.local_db_manager.LocalDBManager.get_dictionary_value_cache", _get):
        assert routed_get(8229, 7, 8) == [{"id": 1, "value": "GLOBAL"}]
    _clear_neg()


def test_no_negative_returns_none_for_refetch():
    """无负缓存、PG 无行 → 返回 None（调用方自行回源；回源空后应 record_negative）。"""
    _clear_neg()
    with mock.patch("utils.local_db_manager.LocalDBManager.get_dictionary_value_cache",
                    lambda self, *a, **k: None):
        assert routed_get(8229, 9, 10) is None
    _clear_neg()


def test_routed_set_positive_write_does_not_poison_negative():
    """正数据写入 routed_set（scoped 桶）与负缓存互不干扰（键不同 / 行为独立）。"""
    _clear_neg()
    recorded = {}

    def _set(self, attribute_id, dc, tp, values_data, language="ZH_HANS", expires_in=30 * 86400):
        recorded[(dc, tp)] = (list(values_data), expires_in)

    def _get(self, attribute_id, dc, tp, language="ZH_HANS"):
        row = recorded.get((dc, tp))
        if row:
            return {"values_data": row[0], "expires_at": int(time.time()) + 60}
        return None

    with mock.patch("utils.local_db_manager.LocalDBManager.set_dictionary_value_cache", _set), \
         mock.patch("utils.local_db_manager.LocalDBManager.get_dictionary_value_cache", _get):
        assert routed_set(8229, [{"id": 1}], 11, 12) == BUCKET_SCOPED
        assert routed_get(8229, 11, 12) == [{"id": 1}], "正数据命中不受负缓存影响"
    _clear_neg()


# ═══════════ 3. refresh_category_tree TTL 90d ═══════════

def _load_refresh_module():
    spec = importlib.util.spec_from_file_location(
        "refresh_category_tree_test",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "refresh_category_tree.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_refresh_category_cache_ttl_90d():
    """category_cache 写入 TTL = 90d 常量 + set_category_cache 调用点显式传参。"""
    mod = _load_refresh_module()
    assert mod.CATEGORY_CACHE_TTL_SECONDS == 90 * 86400, (
        "BL-24: category_cache 名义 TTL 应 90d（旧 10 年是虚假安全感）")

    # 锁调用点：apply 分支的 set_category_cache 必须显式带 expires_in（防回归丢参）
    import inspect
    src = inspect.getsource(mod)
    assert "expires_in=CATEGORY_CACHE_TTL_SECONDS" in src, \
        "set_category_cache 调用必须显式传 expires_in=CATEGORY_CACHE_TTL_SECONDS"

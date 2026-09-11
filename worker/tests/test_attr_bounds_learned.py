"""数值属性 bounds 拒单学习闭环单测（v0.75 Task C4，audit A4 F-P1-1）。

Ozon 平台不下发数值属性 bounds，唯一来源是 VALUE_MAX/MIN_LIMIT 拒单原文回流：
parse（置信门槛：抽不到 attr_id+界 → 宁可不学）→ learn（PG upsert 收紧并集，
DB 不可用静默 0 绝不 raise 进重试主链）→ 读侧合成（静态 NUMERIC_ATTR_BOUNDS
恒赢 > 学习表 > None 现状不夹取）。

纯 mock：monkeypatch storage.database.db.get_engine（参照 test_commission_resolver
「惰性导入 + 注入」手法），不连真实 PG。

运行：cd worker && PYTHONPATH=src python -m pytest tests/test_attr_bounds_learned.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import storage.database.db as db_mod
import utils.attr_numeric_sanitize as ans
from utils.attr_numeric_sanitize import (
    get_learned_bounds,
    learn_bounds_from_decline,
    parse_numeric_bounds_from_decline,
    sanitize_numeric_attr_value,
)


# ==================== mock 基建（惰性导入 + 注入，不连真 PG） ====================

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """最小 PG 语义替身：SELECT 返回 store 行；INSERT..ON CONFLICT 覆写 store。

    刻意「哑」——收紧并集逻辑必须在被测实现里（读到旧行 → Python 合并 → 写回），
    本替身只做存取，不复制实现语义（否则测试与实现循环论证）。
    """

    def __init__(self, store=None):
        self.store = dict(store or {})  # attr_id -> (min_value, max_value)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def execute(self, sql, params=None):
        s = str(sql)
        self.executed.append((s, params))
        if "SELECT" in s:
            return _FakeResult(self.store.get(params["aid"]))
        if "INSERT" in s:
            self.store[params["aid"]] = (params["lo"], params["hi"])
            return _FakeResult(None)
        raise AssertionError(f"FakeConn 不认识的 SQL: {s[:80]}")


class FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


class BoomEngine:
    def connect(self):
        raise RuntimeError("pg down")

    def __call__(self):
        return self


def _autoclear_cache():
    ans._BOUNDS_CACHE.clear()


# ==================== parse：decline 原文 → (attr_id, lo|None, hi|None) ====================

def test_parse_confident_pair():
    # 正例① max 形态（Ozon VALUE_MAX_LIMIT 惯用句式；蓝本=PLAN C4 示例形态）
    got = parse_numeric_bounds_from_decline(
        "Значение характеристики 22333 не должно превышать 5000"
    )
    assert got == [(22333, None, 5000.0)], f"max 文案应解析出 (22333, None, 5000): {got}"

    # 正例② min 形态（VALUE_MIN_LIMIT）
    got = parse_numeric_bounds_from_decline(
        "Значение характеристики 4180 должно быть не менее 1"
    )
    assert got == [(4180, 1.0, None)], f"min 文案应解析出 (4180, 1, None): {got}"

    # 一条消息同时两界
    got = parse_numeric_bounds_from_decline(
        "Значение характеристики 8962 должно быть не менее 1 и не должно превышать 10000"
    )
    assert got == [(8962, 1.0, 10000.0)], f"双界文案应解析出两界: {got}"

    # attr_id 的 id: 形态 + 多条产出全部返回
    got = parse_numeric_bounds_from_decline(
        "id: 22333 не должно превышать 5000. Значение характеристики 12345 не меньше 10"
    )
    assert got == [(22333, None, 5000.0), (12345, 10.0, None)], f"多条应全部返回: {got}"


def test_parse_unconfident_returns_empty():
    # 只有界定位不到 attr_id → 不学（宁可不学）
    assert parse_numeric_bounds_from_decline(
        "Значение не должно превышать 5000"
    ) == []
    # 只有 attr_id 定位不到界 → 不学
    assert parse_numeric_bounds_from_decline(
        "Проверьте значение характеристики 22333"
    ) == []
    # 空 / 纯英文无关文案 → []
    assert parse_numeric_bounds_from_decline("") == []
    assert parse_numeric_bounds_from_decline("some english error text 42") == []


# ==================== 读侧合成：静态白名单恒赢 ====================

def test_static_whitelist_wins(monkeypatch):
    # 学习表即便有别的值（8962 → (0, 500)），静态 (1, 10000) 恒赢：
    # 9999 在静态区间内 → 不夹取（若学习表赢了会被夹到 500）
    monkeypatch.setattr(ans, "get_learned_bounds", lambda aid: (0.0, 500.0))
    cleaned, reason = sanitize_numeric_attr_value(8962, "9999", "Integer")
    assert cleaned == "9999", f"静态白名单恒赢，9999 不应被夹取: {cleaned!r}"
    assert reason is None


def test_sanitize_uses_learned_bounds(monkeypatch):
    # 学习表 22333 → (0, 5000)：9999 被夹取到 5000
    monkeypatch.setattr(ans, "get_learned_bounds", lambda aid: (0.0, 5000.0))
    cleaned, reason = sanitize_numeric_attr_value(22333, "9999", "Integer")
    assert cleaned == "5000", f"学习上限应夹取 9999→5000: {cleaned!r}"
    assert reason and "夹取" in reason, f"夹取应有调整说明: {reason!r}"

    # 学习表无该属性（None）→ 与现状一致：不夹取
    monkeypatch.setattr(ans, "get_learned_bounds", lambda aid: None)
    cleaned, reason = sanitize_numeric_attr_value(22333, "9999", "Integer")
    assert cleaned == "9999" and reason is None, f"无 bounds 不应夹取: ({cleaned!r}, {reason!r})"


# ==================== learn：upsert 收紧并集 + 永不 raise ====================

def test_learn_bounds_upsert_merges_tightening(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(db_mod, "get_engine", lambda: FakeEngine(conn))
    _autoclear_cache()

    # 首次学习：只学到上界
    assert learn_bounds_from_decline(
        "Значение характеристики 22333 не должно превышать 5000"
    ) == 1
    assert conn.store[22333] == (None, 5000.0)

    # 二次学习下界 → 收紧并集（min 取 max，max 取 min）
    assert learn_bounds_from_decline(
        "Значение характеристики 22333 должно быть не менее 100"
    ) == 1
    assert conn.store[22333] == (100.0, 5000.0), "二次学习应合并出 [100, 5000]"

    # 放松方向的界不生效：新 min 50 < 已学 100 → 保持 100；新 max 9000 > 已学 5000 → 保持 5000
    learn_bounds_from_decline(
        "Значение характеристики 22333 должно быть не менее 50 и не должно превышать 9000"
    )
    assert conn.store[22333] == (100.0, 5000.0), "学习只收紧不放松"


def test_learn_never_raises(monkeypatch):
    # 引擎抛异常 → 返回 0 不抛（学习失败绝不进重试主链）
    monkeypatch.setattr(db_mod, "get_engine", lambda: BoomEngine())
    assert learn_bounds_from_decline(
        "Значение характеристики 22333 не должно превышать 5000"
    ) == 0
    # 不可解析文案 → 0（连 DB 都不碰）
    assert learn_bounds_from_decline("не парсится") == 0
    # 同样异常下读侧返回 None（表不存在/PG 不可用视同未学习）
    _autoclear_cache()
    assert get_learned_bounds(22333) is None


# ==================== get_learned_bounds：PG 读 + 60s TTL 缓存 ====================

def test_get_learned_bounds_reads_and_caches(monkeypatch):
    conn = FakeConn({22333: (0.0, 5000.0)})
    monkeypatch.setattr(db_mod, "get_engine", lambda: FakeEngine(conn))
    _autoclear_cache()

    assert get_learned_bounds(22333) == (0.0, 5000.0)
    # 命中缓存后不再查库（store 改脏值仍返回缓存）
    conn.store[22333] = (None, None)
    assert get_learned_bounds(22333) == (0.0, 5000.0)

    # 无行 / 两界全空 → None
    assert get_learned_bounds(999999) is None
    conn.store[88888] = (None, None)
    assert get_learned_bounds(88888) is None
    # 非数字 attr_id → None
    assert get_learned_bounds("abc") is None


def test_get_learned_bounds_missing_table_returns_none(monkeypatch):
    class NoTableConn(FakeConn):
        def execute(self, sql, params=None):
            raise RuntimeError('relation "attr_bounds_learned" does not exist')

    monkeypatch.setattr(db_mod, "get_engine", lambda: FakeEngine(NoTableConn()))
    _autoclear_cache()
    assert get_learned_bounds(22333) is None

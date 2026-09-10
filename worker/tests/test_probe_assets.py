"""probe_assets.py（A8 §7 七探针只读 CLI）形状锁定：mock conn 断言 SQL 形状含 LIMIT 与正确 JOIN。

纯 mock，零 PG 依赖：脚本经 importlib 按文件路径加载（与 worktree 路径无关，
见 docs/SUBAGENT-SPEC.md §6「-e venv 测错源码」教训）。
"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "probe_assets.py"
_spec = importlib.util.spec_from_file_location("probe_assets", _SCRIPT)
pa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    """按调用顺序吐预设行集的假 conn（耗尽后默认空行集）；记录每条已执行 SQL。"""

    def __init__(self, rows_queue=None):
        self._rows_queue = list(rows_queue or [])
        self.executed = []

    def cursor(self):
        rows = self._rows_queue.pop(0) if self._rows_queue else []
        cur = _FakeCursor(rows)
        self.executed.append(cur)
        return cur

    def close(self):
        pass


# ---------------------------------------------------------------------------
# SQL 形状锁定（与 A8 §7 逐字对齐；只读纪律 = SELECT/COUNT + LIMIT）
# ---------------------------------------------------------------------------

def test_s1_orphan_shape():
    assert "draft_submissions" in pa.S1_COUNT_SQL and "COUNT(*)" in pa.S1_COUNT_SQL
    predicate = ("ds.submitted_task_id IS NOT NULL AND NOT EXISTS "
                 "(SELECT 1 FROM ozon_product_tasks t WHERE t.id::text = ds.submitted_task_id)")
    assert predicate in pa.S1_COUNT_SQL and predicate in pa.S1_SAMPLE_SQL
    assert "LIMIT 5" in pa.S1_SAMPLE_SQL


def test_s2_union_join_shape():
    # 4 张 store-scoped 表各一个分支，全部按 credential_id LEFT JOIN credentials 且认 status='revoked'
    assert pa.S2_SQL.count("UNION ALL") == 3
    assert pa.S2_SQL.count("LEFT JOIN credentials c ON c.id = s.credential_id") == 4
    assert pa.S2_SQL.count("c.status='revoked'") == 4
    for t in ("ozon_sessions", "ozon_orders_cache", "ozon_products_cache", "credential_sync_state"):
        assert f"'{t}'" in pa.S2_SQL


def test_s3_s4_s5_s6_shapes():
    assert "GROUP BY ozon_client_id HAVING COUNT(DISTINCT tenant_id) > 1 LIMIT 20" in pa.S3_DOUBLE_BIND_SQL
    assert "LIKE '%:revoked:%'" in pa.S3_REVOKED_SQL
    # 双口径（任务表 + error_reports）都含哈希/数字两正则
    for sql in (pa.S4_TASKS_DIST_SQL, pa.S4_ERRREPORTS_DIST_SQL):
        assert "'^user_[0-9a-f]{16}$'" in sql and "'^[0-9]+$'" in sql
    assert "error_reports" in pa.S4_ERRREPORTS_DIST_SQL and "LIMIT 10" in pa.S4_TOP_SQL
    assert "discovery_runs" in pa.S5_SQL and "FILTER (WHERE tenant_id ~ '^sk?')" in pa.S5_SQL
    assert "!~ '^[0-9]+$'" in pa.S6_SQL and "!~ '^user_[0-9a-f]{16}$'" in pa.S6_SQL
    assert "LIMIT 20" in pa.S6_SQL


# ---------------------------------------------------------------------------
# 行为：结论先行（✅/⚠️）+ 查询编排
# ---------------------------------------------------------------------------

def test_probe_conclusions_green_and_red():
    clean = _FakeConn([[(0,)]])
    res = pa.probe_s1(clean)
    assert "✅" in res["conclusion"] and len(clean.executed) == 1  # 0 行不追抽样
    dirty = _FakeConn([[(2,)], [("id1", None, "t9", "failed", "2026-09-11")]])
    res2 = pa.probe_s1(dirty)
    assert "⚠️" in res2["conclusion"] and "2" in res2["conclusion"]
    assert [c.sql for c in dirty.executed] == [pa.S1_COUNT_SQL, pa.S1_SAMPLE_SQL]
    # S2 红：残留 >0 触发 ⚠️；S5 红：token 形态计数
    res_s2 = pa.probe_s2(_FakeConn([[("ozon_sessions", 3), ("ozon_orders_cache", 0)]]))
    assert "⚠️" in res_s2["conclusion"] and "ozon_sessions" in str(res_s2["rows"])
    res_s5 = pa.probe_s5(_FakeConn([[(50, 7, 0)]]))
    assert "⚠️" in res_s5["conclusion"] and "7" in res_s5["conclusion"]


def test_s7_needs_no_connection():
    res = pa.probe_s7(None)  # 不连库
    assert any("docker logs" in c for c in res["commands"])
    assert any("grep" in c for c in res["commands"])


def test_main_all_dispatch_and_render(monkeypatch, capsys):
    monkeypatch.setenv("PGDATABASE_URL", "postgresql://fake:fake@localhost:0/fake")
    conn = _FakeConn([[(0,)]])  # s1 干净即全绿；其余探针默认空行集
    monkeypatch.setattr(pa.psycopg2, "connect", lambda url: conn)
    assert pa.main(["--probe", "all"]) == 0
    out = capsys.readouterr().out
    for pid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7"):
        assert pid in out
    assert out.count("结论:") == 7
    # 干净库查询编排：s1=1 s2=1 s3=2 s4=3 s5=1 s6=1
    assert len(conn.executed) == 9


def test_main_requires_url_for_db_probes(monkeypatch):
    monkeypatch.delenv("PGDATABASE_URL", raising=False)
    assert pa.main(["--probe", "s1"]) == 2  # 连库探针缺 URL 显式退出
    assert pa.main(["--probe", "s7"]) == 0  # S7 不需要连库

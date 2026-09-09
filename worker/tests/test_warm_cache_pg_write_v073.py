"""v0.73 W1/W2/W3: warm 写库 CAST / --export-from-pg / 400 死节点跳过 回归。

PG 用例守卫用直连探测（勿读 env 判存——import main 会注入容器风格 URL）。

⚠️ 与 brief 的三处偏差（实测本地 PG 5433 后必要修正）：
1. 裸 INSERT VALUES 会撞 attribute_cache/dictionary_value_cache 首列 id
   （Identity 列）——改为显式列清单；
2. expires_at 是 int4，brief 的 9999999999 越界（integer out of range）——
   改 2147483647（int4 max，仍 > now）；
3. _pg_cleanup 先幂等建 warm_dead_nodes 再 DELETE（首跑/未跑过 warm 的库
   teardown 不炸）。
"""
import json
import os
import re
import sys
import time
import pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

_PG_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)


def _pg_ready() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(_PG_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_ready(), reason="local PG 5433 不可达")

warm = pytest.importorskip("warm_category_cache")

# ── 纯 mock：静态锁死「bind 名紧跟 ::cast」禁入 ──

def test_warm_sql_has_no_bare_bind_cast():
    src = pathlib.Path(warm.__file__).read_text(encoding="utf-8")
    hits = re.findall(r":[A-Za-z_]\w*::[a-zA-Z]+", src)
    assert hits == [], f"发现裸 bind cast（SQLAlchemy 不识别，必炸 syntax error）: {hits}"


def test_export_from_pg_flag_exists():
    parser = warm.build_arg_parser()
    opts = {a.option_strings[0] for a in parser._actions if a.option_strings}
    assert "--export-from-pg" in opts


def test_export_from_pg_rejects_conflicting_flags(capsys):
    with pytest.raises(SystemExit) as ei:
        warm.parse_args(["--export-from-pg", "--force"])
    assert ei.value.code == 2
    with pytest.raises(SystemExit):
        warm.parse_args(["--export-from-pg", "--limit", "5"])


# ── PG 用例 ──

@pytest.fixture()
def _pg_cleanup():
    yield
    from sqlalchemy import create_engine, text
    eng = create_engine(_PG_URL)
    with eng.begin() as conn:
        try:  # 首跑/未跑过 warm 的库可能还没有死节点表
            conn.execute(text(warm.DEAD_NODES_DDL))
        except Exception:
            pass
        conn.execute(text(
            "DELETE FROM attribute_cache WHERE description_category_id < 0"))
        conn.execute(text(
            "DELETE FROM dictionary_value_cache WHERE description_category_id < 0"))
        conn.execute(text(
            "DELETE FROM warm_dead_nodes WHERE description_category_id < 0"))


@pg
def test_write_node_to_pg_no_syntax_error(_pg_cleanup):
    """W1 核心：写库路径不再报 syntax error（此前 :schema::jsonb 100% 炸）。"""
    from sqlalchemy import text
    from storage.database.db import get_session
    # warm 模块 get_session 读 worker 自己的 db 配置——测试进程已设 PYTHONPATH=src，
    # storage.db 读 PGDATABASE_URL env；缺省时显式注入。
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    warm._write_node_to_pg(
        dc=-999001, tid=-999001,
        schema=[{"id": 85, "name": "品牌"}],
        dict_values={"85:-999001:-999001": [{"id": 1, "value": "x"}]},
        now=int(time.time()),
    )
    s = get_session()
    try:
        row = s.execute(text(
            "SELECT attributes_schema FROM attribute_cache "
            "WHERE description_category_id=-999001 AND type_id=-999001"
        )).fetchone()
        assert row is not None
        assert row[0][0]["name"] == "品牌"
    finally:
        s.close()


@pg
def test_export_from_pg_streams_both_tables(_pg_cleanup, tmp_path, monkeypatch):
    """W2 核心：export-from-pg 从 PG 读缓存导出（此前 export-only 只导本次 API 拉取）。"""
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    from sqlalchemy import create_engine, text
    eng = create_engine(_PG_URL)
    with eng.begin() as conn:
        # 显式列清单：两表首列 id 为 Identity，裸 VALUES 撞列序；expires_at int4 取 max
        conn.execute(text(
            "INSERT INTO attribute_cache (description_category_id, type_id, language,"
            " attributes_schema, expires_at, created_at) VALUES "
            "(-999002,-999002,'ZH_HANS',CAST(:s AS jsonb), 2147483647, 0) "
            "ON CONFLICT DO NOTHING"),
            {"s": json.dumps([{"id": 1}])})
        conn.execute(text(
            "INSERT INTO dictionary_value_cache (attribute_id, description_category_id,"
            " type_id, language, values_data, expires_at, created_at) VALUES "
            "(-999002,-999002,-999002,'ZH_HANS',CAST(:v AS jsonb), 2147483647, 0) "
            "ON CONFLICT DO NOTHING"), {"v": json.dumps([{"id": 2}])})
    monkeypatch.setattr(warm, "SCHEMAS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(warm, "DICT_VALUES_FILE", str(tmp_path / "d.json"))
    warm.export_from_pg()
    schemas = json.loads((tmp_path / "s.json").read_text())
    dicts = json.loads((tmp_path / "d.json").read_text())
    assert "-999002:-999002" in schemas
    assert "-999002:-999002:-999002" in dicts


@pg
def test_mark_and_load_dead_nodes_and_coverage(_pg_cleanup):
    """W3 核心：400 死节点进表被跳过，coverage 分母剔除（100% 恢复可达）。"""
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    from storage.database.db import get_session
    s = get_session()
    try:
        warm.ensure_dead_nodes_table(s)
        warm.mark_dead_node(s, -999003, -999003, "400 category not found")
        dead = warm.load_dead_nodes(s)
        assert (-999003, -999003) in dead
        rep = warm.collect_coverage()
        assert rep["dead_excluded"] >= 1
    finally:
        s.close()


@pg
def test_schema_fetch_400_marks_dead(monkeypatch):
    """fetch 返回 400 → (schema,400) 状态外露（主循环据此 mark dead）。"""
    class _Resp:
        status_code = 400
        text = '{"error":"category not found"}'
        def json(self):
            return {}
    monkeypatch.setattr(warm._session, "post",
                        lambda *a, **k: _Resp(), raising=True)
    schema, status = warm._fetch_attribute_schema_with_status(1, 2)
    assert status == 400 and schema == []

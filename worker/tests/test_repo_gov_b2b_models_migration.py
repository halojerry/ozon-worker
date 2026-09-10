"""repo-gov B2-β（BL-09 / BL-16）：治理面模型与迁移登记测试（纯 mock，无需 PG）。

覆盖：
1. 三张新表元数据：schema_migrations（version PK）/ backup_heartbeat / mxou_call_ledger
   （列型 / 主键 / 索引命名，对齐 model.py 既有表风格）
2. DraftSubmission.tenant_id 列（可空 String(50) + ix_draft_submissions_tenant_id 索引，
   与 init_data.migrate_repo_gov_b2b 的手工 DDL 同名）
3. register_schema_migration 幂等 SQL（ON CONFLICT (version) DO NOTHING）+ 失败吞错
4. migrate_repo_gov_b2b DDL 契约（ALTER ... IF NOT EXISTS + CREATE INDEX IF NOT EXISTS）
   + 版本登记 "2026-09-repo-gov-b2b"
5. create_tables 装配面：三个既有迁移调用点（webui/sync-erp/drafts-batch）各登记版本

运行（mock 模式，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_repo_gov_b2b_models_migration.py -q
"""
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import init_data as init_mod  # noqa: E402
from storage.database.shared.model import (  # noqa: E402
    BackupHeartbeat,
    DraftSubmission,
    MxouCallLedger,
    SchemaMigration,
)


class _FakeConn:
    """记录全部 executed (sql, params)；上下文管理器形态（connect/begin 共用）。"""

    def __init__(self, executed):
        self.executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), dict(params or {})))
        return MagicMock()

    def commit(self):
        self.executed.append(("__commit__", {}))


class _FakeEngine:
    """connect()/begin() 返回共享记录 conn（register 走 begin，DDL 走 connect+commit）。"""

    def __init__(self):
        self.executed = []
        self.conn = _FakeConn(self.executed)

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


def _cols(model):
    return {c.name: c for c in model.__table__.columns}


# ============================================================
# 1. 三张新表元数据
# ============================================================

def test_schema_migrations_table():
    """BL-09: schema_migrations — version 主键 String(50) / applied_at / note 可空。"""
    assert SchemaMigration.__tablename__ == "schema_migrations"
    cols = _cols(SchemaMigration)
    assert set(cols) == {"version", "applied_at", "note"}
    pk = set(SchemaMigration.__table__.primary_key.columns)
    assert pk == {cols["version"]}, "version 必须是主键"
    assert cols["version"].type.length == 50
    assert cols["note"].nullable is True


def test_backup_heartbeat_table():
    """BL-08: backup_heartbeat — finished_at/ok 非空 + detail 可空 + 收尾时间索引。"""
    assert BackupHeartbeat.__tablename__ == "backup_heartbeat"
    cols = _cols(BackupHeartbeat)
    assert set(cols) == {"id", "finished_at", "ok", "detail"}
    assert cols["id"].primary_key, "id 自增主键"
    assert cols["finished_at"].nullable is False
    assert cols["ok"].nullable is False
    assert cols["detail"].nullable is True
    idx_names = {ix.name for ix in BackupHeartbeat.__table__.indexes}
    assert "idx_backup_heartbeat_finished" in idx_names


def test_mxou_call_ledger_table():
    """BL-10: mxou_call_ledger — tenant_id 可空+索引 / token_fp 索引 / endpoint String(200)。"""
    assert MxouCallLedger.__tablename__ == "mxou_call_ledger"
    cols = _cols(MxouCallLedger)
    assert set(cols) == {"id", "called_at", "tenant_id", "token_fp", "endpoint"}
    assert cols["id"].primary_key
    assert cols["called_at"].nullable is False
    assert cols["tenant_id"].nullable is True
    assert cols["token_fp"].nullable is False
    assert cols["token_fp"].type.length == 64
    assert cols["endpoint"].type.length == 200
    idx_names = {ix.name for ix in MxouCallLedger.__table__.indexes}
    assert {"idx_mxou_call_ledger_tenant", "idx_mxou_call_ledger_token_fp"} <= idx_names


def test_draft_submissions_tenant_id_column():
    """BL-16: draft_submissions.tenant_id — 可空 String(50) + ix 同名索引（与迁移 DDL 对齐）。"""
    col = _cols(DraftSubmission)["tenant_id"]
    assert col.nullable is True, "存量行无值——可空不回填不阻塞"
    assert col.type.length == 50
    idx_names = {ix.name for ix in DraftSubmission.__table__.indexes}
    assert "ix_draft_submissions_tenant_id" in idx_names


# ============================================================
# 2. register_schema_migration（幂等 + 吞错）
# ============================================================

def test_register_schema_migration_idempotent_sql():
    """登记 SQL 必须带 ON CONFLICT (version) DO NOTHING（重复初始化 no-op）。"""
    eng = _FakeEngine()
    init_mod.register_schema_migration(eng, "2026-09-webui-v1", "webui v1 迁移")
    assert len(eng.executed) == 1  # 单 INSERT（engine.begin() 上下文自管提交）
    sql_str, params = eng.executed[0]
    assert "INSERT INTO schema_migrations" in sql_str
    assert "ON CONFLICT (version) DO NOTHING" in sql_str
    assert params["version"] == "2026-09-webui-v1"
    assert params["note"] == "webui v1 迁移"

    # 二次登记同版本：仍是纯 DO NOTHING（无 UPDATE/DELETE——登记不覆盖首登时间语义）
    init_mod.register_schema_migration(eng, "2026-09-webui-v1", "webui v1 迁移")
    sql2, _ = eng.executed[1]
    assert "ON CONFLICT" in sql2
    assert "DO UPDATE" not in sql2 and "DELETE" not in sql2


def test_register_schema_migration_note_truncated():
    """note 截 500（对齐列型 String(500)）。"""
    eng = _FakeEngine()
    init_mod.register_schema_migration(eng, "v-x", "n" * 600)
    _, params = eng.executed[0]
    assert len(params["note"]) == 500


def test_register_schema_migration_failure_swallowed():
    """登记失败（DB 不可达）不抛异常——登记是观测面不是闸门。"""

    class _Boom:
        def begin(self):
            raise RuntimeError("db down")

    init_mod.register_schema_migration(_Boom(), "2026-09-webui-v1", "")  # 不抛即过


# ============================================================
# 3. migrate_repo_gov_b2b（DDL 契约 + 版本登记）
# ============================================================

def test_migrate_repo_gov_b2b_ddl_and_registration():
    """ALTER/CREATE INDEX 都必须 IF NOT EXISTS（幂等）+ 登记 2026-09-repo-gov-b2b。"""
    eng = _FakeEngine()
    init_mod.migrate_repo_gov_b2b(eng)
    sqls = [s for s, _ in eng.executed]
    assert any(
        "ALTER TABLE draft_submissions ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)" in s
        for s in sqls
    ), "缺幂等 ADD COLUMN"
    assert any(
        "CREATE INDEX IF NOT EXISTS ix_draft_submissions_tenant_id" in s and "draft_submissions" in s
        for s in sqls
    ), "缺同名索引（须与 model.py index=True 生成的默认名一致）"
    reg = [(s, p) for s, p in eng.executed if "schema_migrations" in s]
    assert reg, "迁移执行成功后必须登记版本"
    assert reg[0][1]["version"] == "2026-09-repo-gov-b2b"


def test_migrate_repo_gov_b2b_failure_propagates():
    """DDL 失败要向上抛（建表失败必须响——与 register 的观测面吞错语义相反）。"""

    class _BoomConn:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    class _Boom:
        def connect(self):
            return _BoomConn()

    with pytest.raises(RuntimeError):
        init_mod.migrate_repo_gov_b2b(_Boom())


# ============================================================
# 4. create_tables 装配面（三个既有迁移登记 + b2b 接线）
# ============================================================

def test_create_tables_registers_existing_migrations():
    """结构断言（防回归）：三个既有迁移调用点各登记版本 + migrate_repo_gov_b2b 接线。"""
    src = inspect.getsource(init_mod.create_tables)
    for version in ("2026-09-webui-v1", "2026-09-sync-erp-v1", "2026-09-drafts-batch-v1"):
        assert f'register_schema_migration(\n        engine, "{version}"' in src or (
            f'register_schema_migration(engine, "{version}"' in src
        ), f"create_tables 未登记 {version}"
    assert "migrate_repo_gov_b2b(engine)" in src, "create_tables 未接线 migrate_repo_gov_b2b"


def test_register_helper_present_in_init_data():
    """init_data 必须自带 register_schema_migration（不建独立 migration_registry 模块——最小版）。"""
    assert callable(init_mod.register_schema_migration)
    assert callable(init_mod.migrate_repo_gov_b2b)

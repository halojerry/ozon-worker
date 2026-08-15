"""T1: WebUI v1 数据层迁移测试（真实 PG，本地 Docker 5433）。

断言（契约 C1/C1b/C2/C3）：
1. 4 张新表存在：product_drafts / draft_submissions / credentials / product_task_index
2. credentials 三个绑定弹窗字段存在：shop_name / currency / is_default（C2 "+3 字段"）
3. credentials 两个唯一索引：uq_credentials_tenant_client / uq_credentials_default（部分唯一，WHERE is_default）
4. product_task_index FK：task_id → ozon_product_tasks、credential_id → credentials
5. task_generated_images 迁移列存在：version / params / image_parent_task_id
6. task_generated_images 主键含 version：(task_id, slot, version)，version 默认 1（存量回填语义）

PG 不可达时 skip（纯 mock 环境不阻断回归）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from sqlalchemy import create_engine, inspect, text

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

WEBUI_TABLES = {
    "product_drafts",
    "draft_submissions",
    "credentials",
    "product_task_index",
}
CREDENTIALS_EXTRA_FIELDS = {"shop_name", "currency", "is_default"}
IMAGE_MIGRATION_COLUMNS = {"version", "params", "image_parent_task_id"}


@pytest.fixture(scope="module")
def inspector():
    try:
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        yield inspect(engine)
        engine.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过迁移断言")


def test_webui_tables_exist(inspector):
    """C1/C1b/C2: 4 张 WebUI 新表全部存在。"""
    tables = set(inspector.get_table_names())
    missing = WEBUI_TABLES - tables
    assert not missing, f"WebUI 新表缺失: {sorted(missing)}"


def test_credentials_binding_fields(inspector):
    """C2: credentials 的 3 个绑定弹窗字段（shop_name/currency/is_default）。"""
    cols = {c["name"] for c in inspector.get_columns("credentials")}
    assert CREDENTIALS_EXTRA_FIELDS <= cols, (
        f"credentials 缺失绑定字段: {sorted(CREDENTIALS_EXTRA_FIELDS - cols)}"
    )


def test_credentials_core_columns(inspector):
    """C2: credentials 三层防御核心列（密文 BYTEA / 掩码 / 状态机列）。"""
    cols = {c["name"] for c in inspector.get_columns("credentials")}
    core = {"tenant_id", "ozon_client_id", "ozon_api_key_enc", "api_key_masked",
            "credential_type", "status", "last_validated_at", "last_rotated_at"}
    assert core <= cols, f"credentials 缺失核心列: {sorted(core - cols)}"


def test_credentials_unique_indexes(inspector):
    """C2: uq_credentials_tenant_client（租户×店铺）+ uq_credentials_default（部分唯一 WHERE is_default）。"""
    idxs = {i["name"]: i for i in inspector.get_indexes("credentials")}
    assert "uq_credentials_tenant_client" in idxs
    assert "uq_credentials_default" in idxs
    assert idxs["uq_credentials_default"]["unique"] is True


def test_product_task_index_fks(inspector):
    """C1b: product_task_index FK → ozon_product_tasks(id) 和 credentials(id)。"""
    fks = {tuple(fk["constrained_columns"]) for fk in inspector.get_foreign_keys("product_task_index")}
    assert ("task_id",) in fks, "product_task_index 缺 task_id FK → ozon_product_tasks"
    assert ("credential_id",) in fks, "product_task_index 缺 credential_id FK → credentials"


def test_draft_submissions_fk_cascade(inspector):
    """C1: draft_submissions.draft_id FK → product_drafts ON DELETE CASCADE。"""
    fks = inspector.get_foreign_keys("draft_submissions")
    draft_fk = [fk for fk in fks if fk["constrained_columns"] == ["draft_id"]]
    assert draft_fk, "draft_submissions 缺 draft_id FK → product_drafts"
    assert draft_fk[0].get("options", {}).get("ondelete") == "CASCADE"


def test_image_migration_columns(inspector):
    """C3: task_generated_images 迁移 3 列存在。"""
    cols = {c["name"] for c in inspector.get_columns("task_generated_images")}
    missing = IMAGE_MIGRATION_COLUMNS - cols
    assert not missing, f"task_generated_images 缺失迁移列: {sorted(missing)}"


def test_image_pk_includes_version(inspector):
    """C3: 主键改为 (task_id, slot, version)。"""
    pk = inspector.get_pk_constraint("task_generated_images")
    assert set(pk["constrained_columns"]) == {"task_id", "slot", "version"}, (
        f"task_generated_images 主键应为 (task_id, slot, version)，实际 {pk['constrained_columns']}"
    )


def test_image_version_default_one(inspector):
    """C3: version 列默认 1（存量行 ADD COLUMN 自动回填）。"""
    for c in inspector.get_columns("task_generated_images"):
        if c["name"] == "version":
            assert c["default"] == "1", f"version 默认值应为 1，实际 {c['default']!r}"
            assert c["nullable"] is False, "version 应为 NOT NULL"
            return
    pytest.fail("task_generated_images 无 version 列")

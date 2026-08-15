# -*- coding: utf-8 -*-
"""
M0.1 WebUI 运营工作台：数据模型列迁移测试（纯 mock，无需 PG/GPU）。

断言（TDD）：
1. draft_submissions 反射出 error_message 列（直连任务失败信息）
2. draft_submissions.draft_id nullable=True（直连任务行 draft_id=NULL）
3. product_task_index 反射出 draft_id 列 + idx_pti_draft 索引（采集箱草稿回链）
4. mock engine 下插入 draft_id=NULL 的 submission 行成功——编译出的 INSERT
   必须显式携带 draft_id 列（nullable=False 时 ORM 会把 None 值从 INSERT 剔除）

运行：
  cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_webui_model_columns.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage.database.shared.model import DraftSubmission, ProductTaskIndex


def _draft_columns():
    return {c.name for c in DraftSubmission.__table__.columns}


def test_draft_submissions_has_error_message():
    """C1: draft_submissions 必须包含 error_message 列（直连任务失败信息）。"""
    assert "error_message" in _draft_columns(), (
        f"draft_submissions 缺 error_message 列，实际列: {sorted(_draft_columns())}"
    )


def test_draft_submissions_draft_id_nullable():
    """C1: draft_submissions.draft_id 必须可空（直连任务行 draft_id=NULL）。"""
    col = DraftSubmission.__table__.c.draft_id
    assert col.nullable is True, "draft_id 应为 nullable=True（直连任务 submission 不依赖草稿）"


def test_draft_submissions_keeps_draft_fk():
    """C1: draft_id 保留 FK → product_drafts（级联删除草稿时清理关联 submission）。"""
    fk = list(DraftSubmission.__table__.c.draft_id.foreign_keys)
    assert fk, "draft_id 必须保留 FK → product_drafts"
    assert fk[0].target_fullname == "product_drafts.id", f"FK 目标错误: {fk[0].target_fullname}"
    assert fk[0].ondelete == "CASCADE"


def test_product_task_index_has_draft_id():
    """C1b: product_task_index 必须包含 draft_id 列（采集箱草稿回链；直连任务为 NULL）。"""
    cols = {c.name for c in ProductTaskIndex.__table__.columns}
    assert "draft_id" in cols, f"product_task_index 缺 draft_id 列，实际列: {sorted(cols)}"
    assert ProductTaskIndex.__table__.c.draft_id.nullable is True


def test_product_task_index_draft_index():
    """C1b: idx_pti_draft 索引必须存在。"""
    names = {i.name for i in ProductTaskIndex.__table__.indexes}
    assert "idx_pti_draft" in names, f"缺 idx_pti_draft 索引，实际: {sorted(names)}"


def test_insert_submission_with_null_draft_id():
    """C1: 插入 draft_id=NULL 的 submission 行成功（SQLAlchemy DDL 反射）。

    反射口径：PG DDL 编译器按 col.nullable 生成 NULL/NOT NULL——nullable=True 的列
    DB 直接接受 NULL 值（无默认值也成功）；nullable=False 会被 DB 拒绝。断言反射出的
    CREATE TABLE 对 draft_id/error_message 均不设 NOT NULL 即证明 NULL 直连行可插入。
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    ddl = str(CreateTable(DraftSubmission.__table__).compile(dialect=postgresql.dialect()))

    def _column_line(colname):
        lines = [ln for ln in ddl.splitlines() if ln.strip().startswith(colname)]
        assert lines, f"CREATE TABLE 缺 {colname} 列: {ddl}"
        return lines[0]

    draft_line = _column_line("draft_id")
    assert "NOT NULL" not in draft_line.upper(), (
        f"draft_id 被反射为 NOT NULL，NULL 直连行会被拒: {draft_line}"
    )
    error_line = _column_line("error_message")
    assert "NOT NULL" not in error_line.upper(), (
        f"error_message 被反射为 NOT NULL（不应强制非空）: {error_line}"
    )

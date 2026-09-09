# -*- coding: utf-8 -*-
"""采集箱运营备注 notes（A 批次）：列在场、PATCH 更新、导入导出透传、不进信封。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_draft_notes_v070.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_product_draft_model_has_notes_column():
    from storage.database.shared.model import ProductDraft

    assert "notes" in ProductDraft.__table__.columns
    col = ProductDraft.__table__.columns["notes"]
    assert col.nullable is True
    assert col.comment and "不进信封" in col.comment


def test_init_data_contains_notes_alter():
    text = Path(__file__).resolve().parent.parent.joinpath("scripts/init_data.py").read_text("utf-8")
    assert "ALTER TABLE product_drafts ADD COLUMN IF NOT EXISTS notes TEXT" in text


def test_draft_patch_model_accepts_notes():
    from api.schemas import DraftPatch

    p = DraftPatch(version=1, payload={}, notes="利润高，主图偏色待确认")
    assert p.notes.startswith("利润高")
    assert DraftPatch(version=1, payload={}).notes is None  # None=不修改


def test_patch_sql_preserves_notes_when_none():
    from pathlib import Path
    from services import draft_service

    src = Path(draft_service.__file__).read_text("utf-8")
    # patch_draft 的 UPDATE 用 COALESCE：notes=None 时保留原值（不覆盖）
    assert "notes=COALESCE(:notes, notes)" in src

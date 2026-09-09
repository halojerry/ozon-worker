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


def test_create_body_notes_extracted(monkeypatch):
    from services import draft_service

    src = Path(draft_service.__file__).read_text("utf-8")
    assert 'body.get("notes")' in src or "body.get('notes')" in src
    src_import = src
    assert '"notes"' in src_import  # 导入行映射含 notes


def test_import_row_notes_stripped_and_capped():
    from services.draft_service import _norm_notes

    assert _norm_notes("  待确认  ") == "待确认"
    assert _norm_notes(None) == ""
    assert _norm_notes("x" * 5000) == "x" * 2000


def test_export_contains_notes_column(monkeypatch):
    from services import draft_service
    import csv as _csv
    import io as _io

    drafts = [{"id": "d1", "tenant_id": "t1", "payload": {"draft": {}, "source": {},
               "extensions": {}}, "source": "skill", "version": 1,
               "submission_status": None, "notes": "高利润", "created_at": "t", "updated_at": "t"}]
    monkeypatch.setattr(draft_service, "list_drafts", lambda t: drafts)
    rows = list(_csv.DictReader(_io.StringIO(draft_service.export_drafts_csv("t1"))))
    assert rows[0]["notes"] == "高利润"

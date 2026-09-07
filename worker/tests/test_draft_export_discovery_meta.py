# -*- coding: utf-8 -*-
"""采集箱 CSV 导出 discovery_meta 选品元数据列（纯 mock，无需 PG）。

drafts.payload 只存 envelope；discover 信封经 skill 注入扁平
extensions.discovery_meta（缺失键省略）。导出对带/不带 discovery_meta 两种
形态都不得报错：带 → 四列取值（0 是真实数据保留）；不带 → 列头在场、值留空。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_draft_export_discovery_meta.py -q
"""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import draft_service  # noqa: E402


def _draft(envelope: dict) -> dict:
    return {
        "id": "d1",
        "tenant_id": "t1",
        "payload": envelope,
        "source": "skill",
        "version": 1,
        "submission_status": None,
        "created_at": "2026-09-07T00:00:00",
        "updated_at": "2026-09-07T00:00:00",
    }


def _export(monkeypatch, drafts: list[dict]) -> list[dict]:
    monkeypatch.setattr(draft_service, "list_drafts", lambda tenant_id: drafts)
    body = draft_service.export_drafts_csv("t1")
    return list(csv.DictReader(io.StringIO(body)))


def test_export_with_discovery_meta(monkeypatch):
    rows = _export(monkeypatch, [_draft({
        "draft": {"title": "遮阳帽", "item_id": "a1"},
        "source": {},
        "extensions": {"discovery_meta": {
            "ozon_product_id": 123,
            "blue_ocean_score": 87.5,
            "monthly_sales": 1234,
            "profit_margin": 23.5,
            "match_confidence": 0.8,
        }},
    })])
    assert len(rows) == 1
    row = rows[0]
    assert row["blue_ocean_score"] == "87.5"
    assert row["monthly_sales"] == "1234"
    assert row["profit_margin"] == "23.5"
    assert row["match_confidence"] == "0.8"


def test_export_without_discovery_meta_columns_blank(monkeypatch):
    rows = _export(monkeypatch, [_draft({
        "draft": {"title": "无元数据", "item_id": "a2"},
        "source": {},
        "extensions": {},
    })])
    row = rows[0]
    assert row["blue_ocean_score"] == ""
    assert row["monthly_sales"] == ""
    assert row["profit_margin"] == ""
    assert row["match_confidence"] == ""


def test_export_partial_meta_zero_kept_missing_blank(monkeypatch):
    # discovery_meta 缺失键省略：部分键时其余列留空；月销 0 是真实数据须保留
    rows = _export(monkeypatch, [_draft({
        "draft": {"title": "部分元数据", "item_id": "a3"},
        "source": {},
        "extensions": {"discovery_meta": {"monthly_sales": 0}},
    })])
    row = rows[0]
    assert row["monthly_sales"] == "0"
    assert row["blue_ocean_score"] == ""
    assert row["match_confidence"] == ""

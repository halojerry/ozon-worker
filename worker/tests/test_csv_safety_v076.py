# -*- coding: utf-8 -*-
"""v0.76 T22(cicd-M2): CSV 导出公式注入中和。

worker 侧唯一 CSV 导出口 export_drafts_csv（drafts/export）；用户可控文本
（title/supplier/notes/images/purchase_url + discovery_meta 透传值）以
= + - @ \t \r 开头时前缀 ' 令 Excel/WPS 按文本处理（OWASP CSV Injection）。
discovery runs 全局共享（W11）→ webui DiscoveryPanel 导出同样口径（safeCsvCell）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_csv_safety_v076.py -q
"""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.csv_safety import neutralize_csv_cell as n  # noqa: E402


def test_formula_prefixes_neutralized():
    for p in ("=cmd|'/c calc'!A1", "+1", "-1", "@SUM(1)", "\t=1"):
        assert n(p).startswith("'")
    assert n("=HYPERLINK(\"http://evil\",\"x\")").startswith("'=")


def test_normal_text_untouched():
    assert n("留香珠 200ml") == "留香珠 200ml"
    assert n("") == ""
    assert n("x=y") == "x=y"          # 非 首 字符不中和


def test_non_string_passthrough():
    # 数字/None 等非字符串原样返回——数字/日期列天然不受影响（逐列判断的函数语义保障）
    assert n(123) == 123
    assert n(None) is None
    assert n(0.5) == 0.5
    assert n("\r=1").startswith("'")


def _draft(envelope: dict, notes: str = "") -> dict:
    return {
        "id": "d1",
        "tenant_id": "t1",
        "payload": envelope,
        "source": "skill",
        "version": 1,
        # notes 是 product_drafts 独立列（v0.72 A 批），不在 payload——按行级真实形态伪造
        "notes": notes,
        "submission_status": None,
        "created_at": "2026-09-16T00:00:00",
        "updated_at": "2026-09-16T00:00:00",
    }


def test_export_drafts_csv_neutralizes_formula_title(monkeypatch):
    """端到端：含公式注入 title 的 draft 走 export_drafts_csv，导出单元格 '= 前缀。"""
    from services import draft_service

    poison_source = _draft({
        "draft": {"title": "正常标题", "item_id": "a2"},
        "source": {},
        "extensions": {},
    })
    # T22 评审 F1：source 列是客户端任意可写（POST /drafts 手拆 str(body.get("source"))
    # 无白名单；DraftPatch.source 同样落库），并非系统枚举——导出必须中和
    poison_source["source"] = "=cmd|'/c calc'!A1"
    monkeypatch.setattr(draft_service, "list_drafts", lambda tenant_id: [_draft({
        "draft": {
            "title": "=HYPERLINK(\"http://evil\",\"点我\")",
            "item_id": "a1",
            "supplier": "@SUM(1)",
            "images": ["https://img.example/1.jpg"],
        },
        "source": {},
        "extensions": {"discovery_meta": {
            "match_1688_title": "=	cmd注入",
            "blue_ocean_score": 87.5,   # 数字列不受影响
        }},
    }, notes="=cmd|'/c calc'!A1"), poison_source])
    body = draft_service.export_drafts_csv("t1")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert len(rows) == 2
    assert rows[0]["title"].startswith("'=")
    assert rows[0]["supplier"].startswith("'@")
    assert rows[0]["notes"].startswith("'=")
    # meta 透传文本键同样中和；数字键原样
    assert rows[0]["match_1688_title"].startswith("'=")
    assert rows[0]["blue_ocean_score"] == "87.5"
    # 正常文本列不受影响
    assert rows[0]["images"] == "https://img.example/1.jpg"
    # F1：source 列同样中和
    assert rows[1]["source"].startswith("'=")

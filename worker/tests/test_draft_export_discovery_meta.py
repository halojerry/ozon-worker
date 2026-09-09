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


def test_export_v070_meta_columns_and_commission_segments(monkeypatch):
    """v0.70 扩列：漏斗扩容/货源标题/主图/物流佣金透传 + 佣金分段紧凑文本。"""
    rows = _export(monkeypatch, [_draft({
        "draft": {"title": "扩列", "item_id": "a4"},
        "source": {},
        "extensions": {
            "discovery_meta": {
                "ozon_url": "https://www.ozon.ru/product/1",
                "ozon_price": 953,
                "ozon_image": "https://ir-20.ozonstatic.cn/s3/x.jpg",
                "match_1688_title": "卡吞餐盘",
                "match_1688_category_name": "餐具",
                "min_competing_price": 953,
                "competing_sellers": 1,
                "sales_schema": "FBS",
                "session_count": 0,       # 真实 0 保留
                "conv_to_cart_pdp": 5.62,
                "days_in_promo": 12,
                "return_cancel_rate": 3.1,
            },
            "commission_segments": {
                "fbs": {"leq_1500": 8, "leq_5000": 10, "gt_5000": 12},
                "fbo": {"leq_1500": 6},
            },
        },
    })])
    row = rows[0]
    assert row["ozon_url"] == "https://www.ozon.ru/product/1"
    assert row["ozon_price"] == "953"
    assert row["ozon_image"] == "https://ir-20.ozonstatic.cn/s3/x.jpg"
    assert row["match_1688_title"] == "卡吞餐盘"
    assert row["match_1688_category_name"] == "餐具"
    assert row["session_count"] == "0"
    assert row["conv_to_cart_pdp"] == "5.62"
    assert row["days_in_promo"] == "12"
    assert row["return_cancel_rate"] == "3.1"
    assert "≤1500:8%" in row["commission_rfbs"] and ">5000:12%" in row["commission_rfbs"]
    assert "≤1500:6%" in row["commission_fbo"]
    # 未扩段的通道留空；无 meta 的旧键列不受影响
    assert row["blue_ocean_score"] == ""


def test_export_commission_segments_blank_without_extensions(monkeypatch):
    rows = _export(monkeypatch, [_draft({
        "draft": {"title": "无分段", "item_id": "a5"},
        "source": {},
        "extensions": {},
    })])
    assert rows[0]["commission_rfbs"] == ""
    assert rows[0]["commission_fbo"] == ""


def test_export_b_batch_meta_keys_in_header():
    from services.draft_service import _DRAFT_META_CSV_KEYS

    for key in ("follow_profit_cny", "follow_margin", "ozon_old_price", "match_1688_freight_cny"):
        assert key in _DRAFT_META_CSV_KEYS, f"缺 B 契约列: {key}"

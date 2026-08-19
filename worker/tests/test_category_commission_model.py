"""CategoryCommission 表模型单测 — 类目佣金缓存表（全局共享，无 tenant_id）。

对齐 category_mapping W11 约定：description_category_id 全局共享。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.database.shared.model import CategoryCommission


def test_category_commission_model_exists():
    assert CategoryCommission is not None


def test_tablename():
    assert CategoryCommission.__tablename__ == "category_commission"


def test_segment_columns():
    names = [c.name for c in CategoryCommission.__table__.columns]
    for col in (
        "fbs_leq_1500",
        "fbs_leq_5000",
        "fbs_gt_5000",
        "fbo_leq_1500",
        "fbo_leq_5000",
        "fbo_gt_5000",
        "source",
        "updated_at",
    ):
        assert col in names, f"缺少列 {col}"


def test_no_tenant_id():
    names = [c.name for c in CategoryCommission.__table__.columns]
    assert "tenant_id" not in names


def test_category_id_unique():
    col = [
        c for c in CategoryCommission.__table__.columns
        if c.name == "description_category_id"
    ][0]
    assert col.unique is True

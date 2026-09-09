# worker/tests/test_sku_metrics_pool_v1.py
"""数据池 v1：sku_metrics_pool 表 + 服务 + 双端点。
运行：PGDATABASE_URL=... PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sku_metrics_pool_v1.py -q
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _pg_available() -> bool:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(os.environ["PGDATABASE_URL"])
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="local PG 5433 未启动")


def test_sku_metrics_pool_table_roundtrip():
    from storage.database.db import init_db, get_engine
    from storage.database.shared.model import SkuMetricsPool
    from sqlalchemy.orm import Session

    init_db()
    eng = get_engine()
    with Session(eng) as s:
        row = SkuMetricsPool(sku=3171397439, sales_payload={"monthsales": 140},
                             source_company_ids=["5381204"],
                             contributed_by_token_ids=["tok-a"])
        s.add(row)
        s.commit()
        got = s.query(SkuMetricsPool).filter_by(sku=3171397439).one()
        assert got.sales_payload["monthsales"] == 140
        s.delete(got)
        s.commit()

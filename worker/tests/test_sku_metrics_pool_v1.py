# worker/tests/test_sku_metrics_pool_v1.py
"""数据池 v1：sku_metrics_pool 表 + 服务 + 双端点。
运行：PGDATABASE_URL=... PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sku_metrics_pool_v1.py -q
"""
import datetime
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
    sku = 3171397439
    with Session(eng) as s:
        # 先删后建：防上次运行残留触发 uq_sku_metrics_pool_sku
        s.query(SkuMetricsPool).filter_by(sku=sku).delete()
        s.commit()
        try:
            row = SkuMetricsPool(sku=sku, sales_payload={"monthsales": 140},
                                 source_company_ids=["5381204"],
                                 contributed_by_token_ids=["tok-a"])
            s.add(row)
            s.commit()
            got = s.query(SkuMetricsPool).filter_by(sku=sku).one()
            assert got.sales_payload["monthsales"] == 140
        finally:
            s.query(SkuMetricsPool).filter_by(sku=sku).delete()
            s.commit()


def test_upsert_merge_and_attribution_cap():
    from storage.database.db import get_engine
    from storage.database.shared.model import SkuMetricsPool
    from services.sku_metrics_pool_service import upsert_seller_sync_items
    from sqlalchemy.orm import Session

    sku = 3171397439
    with Session(get_engine()) as s:
        # 先删后建：防上次运行残留触发 uq_sku_metrics_pool_sku
        s.query(SkuMetricsPool).filter_by(sku=sku).delete()
        s.commit()
        try:
            r1 = upsert_seller_sync_items(
                s, [{"sku": "3171397439_0", "sales_payload": {"monthsales": 140}}],
                source_company_id="5381204", contributed_by="tok-a")
            assert r1 == {"accepted": 1, "skipped": 0}
            # 二次上报：variant 补充 + 归因并集
            r2 = upsert_seller_sync_items(
                s, [{"sku": 3171397439, "variant_payload": {"weight": 1840}}],
                source_company_id="5381204", contributed_by="tok-b")
            assert r2["accepted"] == 1
            row = s.query(SkuMetricsPool).filter_by(sku=sku).one()
            assert row.sales_payload["monthsales"] == 140      # 覆盖语义：未提供的保留
            assert row.variant_payload["weight"] == 1840
            assert sorted(row.contributed_by_token_ids) == ["tok-a", "tok-b"]
        finally:
            s.query(SkuMetricsPool).filter_by(sku=sku).delete()
            s.commit()


def test_query_needs_markers_and_zh_name():
    from storage.database.db import get_engine
    from storage.database.shared.model import SkuMetricsPool
    from services.sku_metrics_pool_service import query_sku_metrics
    from sqlalchemy.orm import Session

    with Session(get_engine()) as s:
        s.add(SkuMetricsPool(sku=111, sales_payload={"monthsales": 1}))
        s.add(SkuMetricsPool(sku=222))  # 无 sales_payload
        s.commit()
        rows = {r["sku"]: r for r in query_sku_metrics(s, [111, 222, 333])}
        assert rows[111]["needs_sales_sync"] is False      # 新鲜
        assert rows[222]["needs_sales_sync"] is True       # 缺 payload
        assert 333 not in rows                             # 未入库不返回
        for r in rows.values():
            assert r["needs_variant_sync"] is True
        s.query(SkuMetricsPool).filter(SkuMetricsPool.sku.in_([111, 222])).delete()
        s.commit()


def test_stale_drives_needs_sales_sync():
    from storage.database.db import get_engine
    from storage.database.shared.model import SkuMetricsPool
    from services.sku_metrics_pool_service import query_sku_metrics, STALE_DAYS
    from sqlalchemy.orm import Session

    with Session(get_engine()) as s:
        row = SkuMetricsPool(sku=444, sales_payload={"monthsales": 2})
        s.add(row); s.commit()
        row.updated_at = datetime.datetime.now(datetime.timezone.utc) - \
            datetime.timedelta(days=STALE_DAYS + 1)
        s.commit()
        (r,) = query_sku_metrics(s, [444])
        assert r["needs_sales_sync"] is True
        s.delete(row); s.commit()


def test_attribution_cap_keeps_newest_ten():
    from storage.database.db import get_engine
    from storage.database.shared.model import SkuMetricsPool
    from services.sku_metrics_pool_service import upsert_seller_sync_items
    from sqlalchemy.orm import Session

    sku = 5550042
    with Session(get_engine()) as s:
        # 先删后建：防上次运行残留触发 uq_sku_metrics_pool_sku
        s.query(SkuMetricsPool).filter_by(sku=sku).delete()
        s.commit()
        try:
            for i in range(12):
                r = upsert_seller_sync_items(
                    s, [{"sku": sku, "sales_payload": {"i": i}}],
                    source_company_id=f"comp-{i}", contributed_by=f"tok-{i}")
                assert r == {"accepted": 1, "skipped": 0}
            row = s.query(SkuMetricsPool).filter_by(sku=sku).one()
            # cap 10：只留最新 10 个，最早 2 个被 [-cap:] 截断
            assert row.contributed_by_token_ids == [f"tok-{i}" for i in range(2, 12)]
            assert row.source_company_ids == [f"comp-{i}" for i in range(2, 12)]
        finally:
            s.query(SkuMetricsPool).filter_by(sku=sku).delete()
            s.commit()

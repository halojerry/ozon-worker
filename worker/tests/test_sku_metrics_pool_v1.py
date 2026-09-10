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


# ============================================================
# B2a-3: SAVEPOINT 原子化（并发同 sku 不再整批丢）+ 路由 except 收窄
# ============================================================

def test_upsert_savepoint_retry_on_integrity_error():
    """用例 A（mock）：SAVEPOINT 内唯一键冲突 → 回滚后 re-select 已存在行合并，条目不丢。

    旧实现逐条 SELECT-then-INSERT + 末尾单 commit：并发同 sku 两请求后 commit 方
    IntegrityError 整批（≤12 条）丢失。唯一键冲突在写库时暴露——mock 里以
    SAVEPOINT 内 flush 抛 IntegrityError 复现（真库等价于 flush/commit 时刻撞键）。
    """
    from unittest import mock
    from sqlalchemy.exc import IntegrityError
    from services.sku_metrics_pool_service import upsert_seller_sync_items

    existing = mock.MagicMock()
    existing.source_company_ids = ["c1"]
    existing.contributed_by_token_ids = ["t1"]
    session = mock.MagicMock()
    # 第一次 query（行不存在）→ None；重试 re-select → 已存在行
    session.query.return_value.filter_by.return_value.one_or_none.side_effect = [None, existing]
    # SAVEPOINT 内 flush：第一次撞唯一键抛 IntegrityError，重试成功
    session.flush.side_effect = [IntegrityError("dup", None, None), None]

    result = upsert_seller_sync_items(
        session, [{"sku": 9001, "sales_payload": {"monthsales": 3}}],
        source_company_id="c2", contributed_by="t2")

    assert result == {"accepted": 1, "skipped": 0}
    assert session.query.call_count == 2                       # 重试路径 re-select 发生
    assert session.begin_nested.call_count == 2                # 每次尝试各自 SAVEPOINT
    assert existing.sales_payload == {"monthsales": 3}         # 覆盖语义合并不丢条目
    assert existing.contributed_by_token_ids == ["t1", "t2"]   # 归因 _cap_append 不覆盖
    assert session.commit.call_count == 1                      # 批末单 commit 保持


def test_upsert_gives_up_after_bounded_retry():
    """有界重试 1 次：两次尝试都撞键 → 计 skipped（不抛、不炸整批）。"""
    from unittest import mock
    from sqlalchemy.exc import IntegrityError
    from services.sku_metrics_pool_service import upsert_seller_sync_items

    session = mock.MagicMock()
    session.query.return_value.filter_by.return_value.one_or_none.return_value = None
    session.flush.side_effect = IntegrityError("dup", None, None)

    result = upsert_seller_sync_items(
        # sales_payload 必须非空（空 dict 走既有「缺 payload 计 skipped」分支，到不了冲突路径）
        session, [{"sku": 9002, "sales_payload": {"monthsales": 1}}],
        source_company_id="c", contributed_by="t")

    assert result == {"accepted": 0, "skipped": 1}
    assert session.flush.call_count == 2   # 初次 + 1 次有界重试
    assert session.commit.call_count == 1


def test_seller_sync_route_integrity_error_not_422():
    """用例 B（路由）：写入冲突（IntegrityError）≠ 畸形 item——不给误导性 422，
    走诚实文案非 422（冲突是瞬态，提示重试）。"""
    from unittest import mock
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import IntegrityError
    import main
    import routes.analytics_routes as ar

    def _post():
        with TestClient(main.app) as c:
            return c.post("/api/v1/analytics/seller-sync",
                          headers={"Authorization": "Bearer testtok"},
                          json={"items": [{"sku": 1, "sales_payload": {}}]})

    with mock.patch.object(ar, "upsert_seller_sync_items",
                           side_effect=IntegrityError("dup", None, None)), \
         mock.patch.object(ar, "get_session", return_value=mock.MagicMock()):
        resp = _post()
    assert resp.status_code == 503
    assert "写入冲突" in resp.json()["detail"]


def test_seller_sync_route_value_error_still_422():
    """用例 B 对照：真正畸形 item（ValueError）仍 422 固定文案。"""
    from unittest import mock
    from fastapi.testclient import TestClient
    import main
    import routes.analytics_routes as ar

    with mock.patch.object(ar, "upsert_seller_sync_items",
                           side_effect=ValueError("invalid literal for int()")), \
         mock.patch.object(ar, "get_session", return_value=mock.MagicMock()):
        with TestClient(main.app) as c:
            resp = c.post("/api/v1/analytics/seller-sync",
                          headers={"Authorization": "Bearer testtok"},
                          json={"items": [{"sku": 1, "category_dc": "not-a-number"}]})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid seller-sync item"
    assert "not-a-number" not in resp.text  # analytics 错误纪律：原文不回显

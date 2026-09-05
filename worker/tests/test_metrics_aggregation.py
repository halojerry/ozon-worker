"""PRD M3: 指标日聚合 + 保留清理测试(真实 PG)。"""
import datetime
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import metrics_aggregation as ma  # noqa: E402


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")


@pytest.fixture()
def data():
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    cred = uuid.uuid4()
    eng = create_engine(DB_URL)
    today = datetime.date.today()
    with eng.begin() as conn:
        # 两条今日快照 + 一条 40 天前快照(应被 prune)
        for days_ago, orders, sales in ((0, 3, 300.0), (0, 2, 200.0), (40, 1, 100.0)):
            conn.execute(text(
                """
                INSERT INTO store_metrics_history
                    (tenant_id, credential_id, store_id, snapshot_at, order_count,
                     sales_amount, commission_amount, product_count, low_stock_count,
                     active_discount_count, profit_rate)
                VALUES (:t, :c, :s, CURRENT_DATE - make_interval(days => :d), :o, :sales,
                        10.0, 5, 1, 2, 0.25)
                """
            ), {"t": tenant, "c": cred, "s": str(cred), "d": days_ago,
                "o": orders, "sales": sales})
        # 订单(真实利润 → 日利润)
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status,
                raw_status, products, product_count, total_amount, commission_amount,
                real_profit, warehouse, delivery_method, cancel_reason, cancellation,
                order_created_at)
            VALUES (:t, :c, 'PN-1', 'delivered', 'delivered', '[]'::jsonb, 1, 100, 10,
                    50.0, '', '', '', '', CURRENT_DATE)
            """
        ), {"t": tenant, "c": cred})
        # 一条 40 天前的 job 与订单(应被 prune)
        conn.execute(text(
            """
            INSERT INTO store_sync_jobs (tenant_id, credential_id, kind, trigger, status, created_at)
            VALUES (:t, :c, 'incremental', 'scheduler', 'ok', NOW() - interval '40 days')
            """
        ), {"t": tenant, "c": cred})
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status,
                raw_status, products, product_count, warehouse, delivery_method,
                cancel_reason, cancellation, order_created_at)
            VALUES (:t, :c, 'PN-OLD', 'delivered', 'delivered', '[]'::jsonb, 1,
                    '', '', '', '', NOW() - interval '200 days')
            """
        ), {"t": tenant, "c": cred})
    yield tenant, str(cred)
    with eng.begin() as conn:
        for t in ("store_metrics_history", "store_daily_metrics", "ozon_orders_cache",
                  "store_sync_jobs"):
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})


def test_aggregate_and_prune(data, monkeypatch):
    tenant, cred = data
    ma.run_aggregation()
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT order_count, sales_amount, product_count, low_stock_count, "
            "active_discount_count, profit_rate, profit_amount "
            "FROM store_daily_metrics WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant, "c": cred}).fetchone()
    assert int(row[0] or 0) == 5            # 3+2
    assert float(row[1] or 0) == 500.0      # 300+200
    assert int(row[2] or 0) == 5 and int(row[3] or 0) == 1
    assert int(row[4] or 0) == 2
    assert float(row[5] or 0) == 0.25       # AVG(0.25)
    assert float(row[6] or 0) == 50.0       # 订单 real_profit 聚合
    # prune:40 天前快照/job 删除,200 天前订单删除
    monkeypatch.setattr(ma, "METRICS_RETENTION_DAYS", 30)
    monkeypatch.setattr(ma, "JOB_RETENTION_DAYS", 30)
    monkeypatch.setattr(ma, "ORDER_RETENTION_DAYS", 180)
    pruned = ma.prune()
    assert pruned["metrics_history"] >= 1
    assert pruned["sync_jobs"] >= 1
    assert pruned["orders_cache"] >= 1
    with eng.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM store_metrics_history WHERE tenant_id=:t"), {"t": tenant}).scalar() == 2
        assert conn.execute(text(
            "SELECT COUNT(*) FROM store_sync_jobs WHERE tenant_id=:t"), {"t": tenant}).scalar() == 0
        assert conn.execute(text(
            "SELECT COUNT(*) FROM ozon_orders_cache WHERE tenant_id=:t"), {"t": tenant}).scalar() == 1


def test_profit_amount_session_timezone_consistent(monkeypatch):
    """P1-2 时区回归:+08 会话下跨界订单必须落本地 stat_date, profit_amount 不恒 NULL。

    snapshot_at=2026-09-01 12:00(+08) → 本地 stat_date 09-01;
    order_created_at=2026-09-01 00:30(+08) = UTC 08-31 16:30 —— 旧代码 UPDATE 侧
    (order_created_at AT TIME ZONE 'UTC')::date 落 08-31, 与 INSERT 侧 snapshot_at::date
    的 09-01 永不匹配 → profit_amount 恒 NULL。修复后两侧都 ::date(会话时区) → 09-01。

    ⚠️ 会话时区用命名区 'Asia/Shanghai' 而非 '+08:00' —— PG 把数字时区串按 POSIX 解析
    (符号反转), SET timezone='+08:00' 实为 UTC-8, 会测出相反语义。
    """
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    cred = uuid.uuid4()

    # run_aggregation 内部自建连接, 用隔离 engine 保证其每条连接会话时区 +08
    # (connect 事件等效于"同一 connection SET 后再跑聚合"), 不污染共享单例 engine。
    def _tz08_engine():
        eng = create_engine(DB_URL)

        @event.listens_for(eng, "connect")
        def _apply_tz08(dbapi_conn, _rec):
            with dbapi_conn.cursor() as cur:
                cur.execute("SET timezone = 'Asia/Shanghai'")

        return eng

    eng = _tz08_engine()
    monkeypatch.setattr(ma, "get_engine", lambda: eng)
    try:
        with eng.begin() as conn:
            conn.execute(text("SET timezone = 'Asia/Shanghai'"))
            conn.execute(text(
                """
                INSERT INTO store_metrics_history
                    (tenant_id, credential_id, store_id, snapshot_at, order_count,
                     sales_amount, commission_amount, product_count, low_stock_count,
                     active_discount_count, profit_rate)
                VALUES (:t, :c, :s, '2026-09-01 12:00:00+08', 1, 100.0, 10.0, 1, 0, 0, 0.20)
                """
            ), {"t": tenant, "c": cred, "s": str(cred)})
            conn.execute(text(
                """
                INSERT INTO ozon_orders_cache (tenant_id, credential_id, posting_number, status,
                    raw_status, products, product_count, total_amount, commission_amount,
                    real_profit, warehouse, delivery_method, cancel_reason, cancellation,
                    order_created_at)
                VALUES (:t, :c, :pn, 'delivered', 'delivered', '[]'::jsonb, 1, 100, 10,
                        50.0, '', '', '', '', '2026-09-01 00:30:00+08')
                """
            ), {"t": tenant, "c": cred, "pn": f"TZ-{uuid.uuid4().hex[:8]}"})
        ma.run_aggregation()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT stat_date, profit_amount FROM store_daily_metrics "
                "WHERE tenant_id=:t AND credential_id=:c"
            ), {"t": tenant, "c": cred}).fetchone()
        assert row is not None, "应有聚合行"
        assert str(row[0]) == "2026-09-01"
        assert float(row[1] or 0) == 50.0
    finally:
        with eng.begin() as conn:
            conn.execute(text("RESET timezone"))
            for t in ("store_metrics_history", "store_daily_metrics", "ozon_orders_cache"):
                conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})
        eng.dispose()

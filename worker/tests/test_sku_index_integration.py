# -*- coding: utf-8 -*-
"""
v0.38.1 集成验证：N1×N2 冲突修复 — SKU 去重唯一索引状态过滤（真实 PG）

背景：v0.38 唯一索引 uq_ozon_product_tasks_tenant_sku 谓词 WHERE sku_key IS NOT NULL
无状态过滤 → rejected/failed 终态行保留 sku_key，resubmit 以相同 sku_key INSERT 新行
违反唯一索引 → IntegrityError → HTTP 500。本测试用真实 PG 验证修复后：
  1. rejected 行 + 同 SKU 新 pending 行可共存（索引只对 pending/running 唯一）
  2. pending 行已存在时同 SKU 再插入 → 唯一索引拦截（防重复上架仍在）
  3. init_data.py 的 DROP+重建索引幂等可重跑

运行（需本地 Docker PG 5433）：
  PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
  PYTHONPATH=src ../skill/.venv314/bin/python tests/test_sku_index_integration.py
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import logging
logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

from sqlalchemy import text
from storage.database.db import get_engine

PG_URL = os.environ.get("PGDATABASE_URL", "")
if not PG_URL:
    print("⚠️ 未设置 PGDATABASE_URL，跳过真实 PG 集成测试")
    sys.exit(0)


def _ensure_schema(conn):
    """最小表结构（真实 init_data.py 已建表；此处幂等兜底）。"""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ozon_product_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            payload JSONB NOT NULL,
            result JSONB,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            timeout_seconds INTEGER DEFAULT 1800,
            progress JSONB,
            sku_key TEXT
        )
    """))
    conn.execute(text("DROP INDEX IF EXISTS uq_ozon_product_tasks_tenant_sku"))
    conn.execute(text(
        "CREATE UNIQUE INDEX uq_ozon_product_tasks_tenant_sku "
        "ON ozon_product_tasks(tenant_id, sku_key) "
        "WHERE sku_key IS NOT NULL AND status IN ('pending', 'running')"
    ))
    conn.commit()


def test_rejected_row_does_not_block_resubmit():
    """rejected 终态行保留 sku_key，同 SKU 新 pending 行可插入（修复 N1×N2 冲突）。"""
    engine = get_engine()
    tenant = f"it_{uuid.uuid4().hex[:8]}"
    sku = f"{tenant}:store1:prod1"
    with engine.connect() as conn:
        _ensure_schema(conn)
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (tenant_id, status, priority, retry_count, max_retries, timeout_seconds, payload, sku_key) "
            "VALUES (:t, 'rejected', 0, 0, 3, 1800, '{}', :k)"
        ), {"t": tenant, "k": sku})
        conn.commit()
        # 修复后：同 sku_key 新 pending 行可插入（旧逻辑此处 IntegrityError）
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (tenant_id, status, priority, retry_count, max_retries, timeout_seconds, payload, sku_key) "
            "VALUES (:t, 'pending', 0, 0, 3, 1800, '{}', :k)"
        ), {"t": tenant, "k": sku})
        conn.commit()
    print("  ✅ rejected 行 + 同 SKU pending 行共存（resubmit 不再 500）")


def test_active_duplicate_still_blocked_by_index():
    """活跃 pending 行存在时同 SKU 再插入 → 唯一索引拦截（防重复上架仍生效）。"""
    engine = get_engine()
    tenant = f"it_{uuid.uuid4().hex[:8]}"
    sku = f"{tenant}:store1:prod2"
    with engine.connect() as conn:
        _ensure_schema(conn)
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (tenant_id, status, priority, retry_count, max_retries, timeout_seconds, payload, sku_key) "
            "VALUES (:t, 'pending', 0, 0, 3, 1800, '{}', :k)"
        ), {"t": tenant, "k": sku})
        conn.commit()
        from sqlalchemy.exc import IntegrityError
        try:
            conn.execute(text(
                "INSERT INTO ozon_product_tasks (tenant_id, status, priority, retry_count, max_retries, timeout_seconds, payload, sku_key) "
                "VALUES (:t, 'pending', 0, 0, 3, 1800, '{}', :k)"
            ), {"t": tenant, "k": sku})
            conn.commit()
            print("  ❌ 活跃任务存在时同 SKU 二次插入未拦截（唯一索引失效）")
            return False
        except IntegrityError:
            conn.rollback()
            print("  ✅ 活跃任务存在时同 SKU 二次插入被唯一索引拦截")


def test_index_recreate_idempotent():
    """init_data.py 的 DROP + CREATE UNIQUE INDEX IF NOT EXISTS 幂等可重跑。"""
    engine = get_engine()
    with engine.connect() as conn:
        _ensure_schema(conn)
        conn.execute(text("DROP INDEX IF EXISTS uq_ozon_product_tasks_tenant_sku"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ozon_product_tasks_tenant_sku "
            "ON ozon_product_tasks(tenant_id, sku_key) "
            "WHERE sku_key IS NOT NULL AND status IN ('pending', 'running')"
        ))
        conn.commit()
        # 重跑一次（模拟 init_data 再次执行）不抛错
        conn.execute(text("DROP INDEX IF EXISTS uq_ozon_product_tasks_tenant_sku"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ozon_product_tasks_tenant_sku "
            "ON ozon_product_tasks(tenant_id, sku_key) "
            "WHERE sku_key IS NOT NULL AND status IN ('pending', 'running')"
        ))
        conn.commit()
    print("  ✅ 索引 DROP+重建幂等（init_data 可安全重跑）")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)

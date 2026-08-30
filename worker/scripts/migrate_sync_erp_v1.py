#!/usr/bin/env python3
"""PRD store-sync-ERP v1 存量数据库迁移(幂等,二次运行 no-op)。

对应 docs/PRD-store-sync-erp-v1.md §7 DDL:
- 新表:store_sync_jobs / store_daily_metrics / ozon_returns_cache /
  ozon_store_analytics_daily / warehouse_cache / product_costs /
  product_cost_history / source_candidates / fx_rates / order_line_costs /
  scheduled_listings / task_progress_events
- 扩列:credentials(sync 配置/rating)、credential_sync_state(水位/游标/域状态)、
  ozon_orders_cache(real_profit)、ozon_products_cache(三价/status/error/archived_at)、
  order_notes(行级化:主键改 (posting_number, product_id))
- 唯一索引:draft_submissions (draft_id, store_client_id) 部分唯一(提交幂等)

用法:
    python scripts/migrate_sync_erp_v1.py [--db-url $PGDATABASE_URL]
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)


_DDL_STATEMENTS = [
    # ── 同步任务 ──
    """
    CREATE TABLE IF NOT EXISTS store_sync_jobs (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        trigger VARCHAR(16) NOT NULL,
        error_code VARCHAR(32),
        orders_synced INT NOT NULL DEFAULT 0,
        products_synced INT NOT NULL DEFAULT 0,
        progress INT NOT NULL DEFAULT 0,
        error TEXT,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_job_one_active
        ON store_sync_jobs (credential_id) WHERE status IN ('pending','running')
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sync_jobs_tenant_cred_created
        ON store_sync_jobs (tenant_id, credential_id, created_at DESC)
    """,
    # ── 凭证扩展 ──
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS sync_interval_minutes INT NOT NULL DEFAULT 15",
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS sync_products_interval_minutes INT NOT NULL DEFAULT 30",
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS rating_total NUMERIC(4,2)",
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS rating_localization_index NUMERIC(6,2)",
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS rating_items JSONB",
    "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS rating_updated_at TIMESTAMPTZ",
    # ── 同步状态扩展 ──
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS consecutive_failures INT NOT NULL DEFAULT 0",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS orders_window_since TIMESTAMPTZ",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS orders_window_to TIMESTAMPTZ",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS orders_sync_cursor TEXT",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS orders_sync_incomplete BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS last_job_id BIGINT",
    "ALTER TABLE credential_sync_state ADD COLUMN IF NOT EXISTS domain_state JSONB NOT NULL DEFAULT '{}'::jsonb",
    # ── 缓存扩展 ──
    "ALTER TABLE ozon_orders_cache ADD COLUMN IF NOT EXISTS real_profit NUMERIC(14,2)",
    "ALTER TABLE ozon_products_cache ADD COLUMN IF NOT EXISTS old_price NUMERIC(14,2)",
    "ALTER TABLE ozon_products_cache ADD COLUMN IF NOT EXISTS min_price NUMERIC(14,2)",
    "ALTER TABLE ozon_products_cache ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT ''",
    "ALTER TABLE ozon_products_cache ADD COLUMN IF NOT EXISTS error JSONB",
    "ALTER TABLE ozon_products_cache ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    # ── 日聚合 ──
    """
    CREATE TABLE IF NOT EXISTS store_daily_metrics (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        store_id VARCHAR(64) NOT NULL,
        stat_date DATE NOT NULL,
        order_count INT NOT NULL DEFAULT 0,
        sales_amount NUMERIC(14,2),
        commission_amount NUMERIC(14,2),
        profit_amount NUMERIC(14,2),
        product_count INT NOT NULL DEFAULT 0,
        low_stock_count INT NOT NULL DEFAULT 0,
        active_discount_count INT NOT NULL DEFAULT 0,
        profit_rate NUMERIC(6,4),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_store_daily UNIQUE (tenant_id, credential_id, stat_date)
    )
    """,
    # ── 退货 ──
    """
    CREATE TABLE IF NOT EXISTS ozon_returns_cache (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        return_id BIGINT NOT NULL,
        posting_number VARCHAR(64) NOT NULL DEFAULT '',
        order_id VARCHAR(32) NOT NULL DEFAULT '',
        return_type VARCHAR(32) NOT NULL DEFAULT '',
        schema VARCHAR(32) NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        compensation_status VARCHAR(32) NOT NULL DEFAULT '',
        product JSONB,
        status VARCHAR(32) NOT NULL DEFAULT '',
        raw JSONB,
        synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_returns_cache UNIQUE (tenant_id, credential_id, return_id)
    )
    """,
    # ── 店铺分析日表 ──
    """
    CREATE TABLE IF NOT EXISTS ozon_store_analytics_daily (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        stat_date DATE NOT NULL,
        metric VARCHAR(64) NOT NULL,
        value NUMERIC(14,2),
        raw JSONB,
        CONSTRAINT uq_store_analytics_daily UNIQUE (tenant_id, credential_id, stat_date, metric)
    )
    """,
    # ── 仓库字典 ──
    """
    CREATE TABLE IF NOT EXISTS warehouse_cache (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        warehouse_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        is_rfbs BOOLEAN NOT NULL DEFAULT FALSE,
        raw JSONB,
        synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_warehouse_cache UNIQUE (tenant_id, credential_id, warehouse_id)
    )
    """,
    # ── 成本主数据 ──
    """
    CREATE TABLE IF NOT EXISTS product_costs (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        product_id VARCHAR(32) NOT NULL,
        offer_id VARCHAR(128) NOT NULL DEFAULT '',
        purchase_url TEXT NOT NULL DEFAULT '',
        purchase_cost NUMERIC(14,2),
        freight_cny NUMERIC(14,2),
        supplier TEXT NOT NULL DEFAULT '',
        weight_g INT,
        length_mm INT, width_mm INT, height_mm INT,
        currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
        cost_source VARCHAR(16) NOT NULL DEFAULT 'manual',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_product_costs UNIQUE (tenant_id, credential_id, product_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS product_cost_history (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        product_id VARCHAR(32) NOT NULL,
        old_cost NUMERIC(14,2),
        new_cost NUMERIC(14,2),
        changed_by VARCHAR(64),
        changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_candidates (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        product_id VARCHAR(32) NOT NULL,
        source_offer_id VARCHAR(64) NOT NULL DEFAULT '',
        source_url TEXT NOT NULL,
        price_cny NUMERIC(14,2),
        match_score NUMERIC(5,2),
        match_method VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'valid',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_source_candidates UNIQUE (tenant_id, credential_id, product_id, source_offer_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fx_rates (
        date DATE PRIMARY KEY,
        cny_to_rub NUMERIC(12,6),
        source VARCHAR(16) NOT NULL DEFAULT 'manual',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # ── 订单行级成本 ──
    """
    CREATE TABLE IF NOT EXISTS order_line_costs (
        posting_number VARCHAR(64) NOT NULL,
        tenant_id VARCHAR(50) NOT NULL,
        credential_id UUID NOT NULL,
        product_id VARCHAR(32) NOT NULL,
        sku VARCHAR(64) NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        source_cost NUMERIC(14,2),
        logistics_cny NUMERIC(14,2),
        fx_rate NUMERIC(12,6),
        revenue_rub NUMERIC(14,2),
        real_profit NUMERIC(14,2),
        cost_version INT NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_order_line_costs UNIQUE (posting_number, product_id)
    )
    """,
    "ALTER TABLE order_line_costs ADD COLUMN IF NOT EXISTS revenue_rub NUMERIC(14,2)",
    "CREATE INDEX IF NOT EXISTS idx_order_line_costs_product ON order_line_costs (tenant_id, credential_id, product_id)",
    # ── order_notes 行级化(主键改 (posting_number, product_id)) ──
    "ALTER TABLE order_notes ADD COLUMN IF NOT EXISTS product_id VARCHAR(32) NOT NULL DEFAULT ''",
    "ALTER TABLE order_notes ADD COLUMN IF NOT EXISTS sku VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE order_notes ADD COLUMN IF NOT EXISTS cost_source VARCHAR(16) NOT NULL DEFAULT 'manual'",
    # ── 采集箱提交幂等:同店只允许一个「进行中」submission(pending/uploading);
    #    failed/rejected 后可重试新行(PRD §16 重试语义)。
    "DROP INDEX IF EXISTS uq_draft_submissions_draft_store",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_submissions_draft_store
        ON draft_submissions (draft_id, store_client_id)
        WHERE draft_id IS NOT NULL AND status IN ('pending','uploading')
    """,
    # 草稿图片镜像状态(M5b):'' = 未启用/未镜像;pending = 异步镜像中;
    # mirrored = 已转存 COS;failed = 镜像失败(保持原外链)
    "ALTER TABLE product_drafts ADD COLUMN IF NOT EXISTS image_mirror_state VARCHAR(16) NOT NULL DEFAULT ''",
    # 用户设置(工作台/系统设置真实化):tenant 级 KV,JSONB 整体读写
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        tenant_id VARCHAR(50) PRIMARY KEY,
        settings JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # ── 定时上架 ──
    """
    CREATE TABLE IF NOT EXISTS scheduled_listings (
        id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        draft_id UUID NOT NULL REFERENCES product_drafts(id) ON DELETE CASCADE,
        credential_id UUID NOT NULL,
        scheduled_at TIMESTAMPTZ NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        task_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_scheduled_listings UNIQUE (draft_id, credential_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scheduled_listings_due ON scheduled_listings (status, scheduled_at)",
    "ALTER TABLE scheduled_listings ADD COLUMN IF NOT EXISTS token_enc BYTEA",
    "ALTER TABLE scheduled_listings ADD COLUMN IF NOT EXISTS error TEXT",
    # ── 任务进度事件 ──
    """
    CREATE TABLE IF NOT EXISTS task_progress_events (
        id BIGSERIAL PRIMARY KEY,
        task_id TEXT NOT NULL,
        seq INT NOT NULL,
        node VARCHAR(64) NOT NULL,
        step VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(16) NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        detail JSONB,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ,
        CONSTRAINT uq_task_progress_seq UNIQUE (task_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_progress_events_task ON task_progress_events (task_id, seq)",
]


def run_migrations(engine) -> None:
    """幂等执行 PRD v1 同步/ERP 迁移;重复调用全部 no-op。"""
    with engine.begin() as conn:
        for sql in _DDL_STATEMENTS:
            conn.execute(text(sql))
        # order_notes 主键:摘旧 PK(默认名 order_notes_pkey)后建 (posting_number, product_id)
        # 旧整单行 product_id='' 保持唯一,新行级行按 (posting, product) 唯一。
        conn.execute(text("ALTER TABLE order_notes DROP CONSTRAINT IF EXISTS order_notes_pkey"))
        conn.execute(text("ALTER TABLE order_notes ADD PRIMARY KEY (posting_number, product_id)"))
    logger.info("✅ sync-erp v1 迁移完成(幂等)")


def main() -> None:
    parser = argparse.ArgumentParser(description="PRD sync-ERP v1 迁移(幂等)")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""), help="PG 连接串")
    args = parser.parse_args()
    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL 环境变量或通过 --db-url 传入")
        sys.exit(1)
    run_migrations(create_engine(args.db_url))


if __name__ == "__main__":
    main()

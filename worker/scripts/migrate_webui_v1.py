#!/usr/bin/env python3
"""WebUI v1 存量数据库迁移（幂等，二次运行 no-op）。

契约 C3（task_generated_images）+ 新表索引补齐：
- ADD COLUMN IF NOT EXISTS version/params/image_parent_task_id（version NOT NULL DEFAULT 1 → 存量行自动回填 1）
- 主键 (task_id, slot) → (task_id, slot, version)
- 新表索引（create_all 只在建表时生效；存量表/半迁移库由这里兜底）

用法:
    python scripts/migrate_webui_v1.py [--db-url $PGDATABASE_URL]
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

# 新表索引：与 model.py __table_args__ 对齐（唯一索引名供服务端引用）
_NEW_TABLE_INDEXES = [
    ("CREATE INDEX IF NOT EXISTS idx_product_drafts_tenant ON product_drafts (tenant_id, updated_at DESC)",),
    ("CREATE INDEX IF NOT EXISTS idx_draft_submissions_draft ON draft_submissions (draft_id)",),
    ("CREATE INDEX IF NOT EXISTS idx_pti_tenant_offer ON product_task_index (tenant_id, offer_id)",),
    ("CREATE UNIQUE INDEX IF NOT EXISTS uq_credentials_tenant_client ON credentials (tenant_id, ozon_client_id)",),
    ("CREATE UNIQUE INDEX IF NOT EXISTS uq_credentials_default ON credentials (tenant_id) WHERE is_default",),
]


def run_migrations(engine) -> None:
    """幂等执行 WebUI v1 迁移。重复调用全部语句为 no-op。"""
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE task_generated_images "
            "ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
        ))
        conn.execute(text(
            "ALTER TABLE task_generated_images ADD COLUMN IF NOT EXISTS params JSONB"
        ))
        conn.execute(text(
            "ALTER TABLE task_generated_images ADD COLUMN IF NOT EXISTS image_parent_task_id TEXT"
        ))
        # 主键迁移：先摘旧 PK（默认名 task_generated_images_pkey），再建 (task_id, slot, version)
        conn.execute(text(
            "ALTER TABLE task_generated_images DROP CONSTRAINT IF EXISTS task_generated_images_pkey"
        ))
        conn.execute(text(
            "ALTER TABLE task_generated_images ADD PRIMARY KEY (task_id, slot, version)"
        ))
        for (sql,) in _NEW_TABLE_INDEXES:
            conn.execute(text(sql))
    logger.info("✅ WebUI v1 迁移完成（幂等）")


def main() -> None:
    parser = argparse.ArgumentParser(description="WebUI v1 存量迁移（幂等）")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""), help="PG 连接串")
    args = parser.parse_args()

    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL 环境变量或通过 --db-url 传入")
        sys.exit(1)

    run_migrations(create_engine(args.db_url))


if __name__ == "__main__":
    main()

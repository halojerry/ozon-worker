#!/usr/bin/env python3
"""T-P3.1 批次契约 存量数据库迁移(幂等,二次运行 no-op)。

对应 docs/CONTRACT-v4.md Part 1b.6 采集箱批次契约:
- product_drafts 加列 source_batch VARCHAR(64)（可选来源批次标识,缺省 NULL）
  - NULL = 无批次(老 skill 不带该字段照常工作)
  - 新装环境由 init_data.py 的 Base.metadata.create_all 直接带列(model.py 已同步),
    本脚本只兜底存量库;init_data.py 末尾同样接线本迁移。

用法:
    python scripts/migrate_drafts_batch_v1.py [--db-url $PGDATABASE_URL]
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)


_DDL_STATEMENTS = [
    # ── 采集箱批次契约(T-P3.1):来源批次标识 ──
    # VARCHAR(64) 与 API 契约 ≤64 字符对齐(DB 层兜底);NULL=无批次,
    # 精确过滤走 ?batch=(service 层 WHERE source_batch = :batch)。
    "ALTER TABLE product_drafts ADD COLUMN IF NOT EXISTS source_batch VARCHAR(64)",
]


def run_migrations(engine) -> None:
    """幂等执行批次契约迁移;重复调用全部 no-op。"""
    with engine.begin() as conn:
        for sql in _DDL_STATEMENTS:
            conn.execute(text(sql))
    logger.info("✅ drafts batch v1 迁移完成(幂等)")


def main() -> None:
    parser = argparse.ArgumentParser(description="T-P3.1 批次契约迁移(幂等)")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""), help="PG 连接串")
    args = parser.parse_args()
    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL 环境变量或通过 --db-url 传入")
        sys.exit(1)
    run_migrations(create_engine(args.db_url))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""v0.75 C2（BL-25 Phase 1-2 漏项）: memory.checkpoints 三表存量孤儿一次性清理。

背景：A9 S9-06 任务表 30 天归档删除（main._periodic_task_cleanup）此前不清理
langgraph checkpoint——thread_id == ozon_product_tasks.id（task_processor.py:970
``configurable={"thread_id": task_id}``），任务行物理删除后 checkpoint 行 join
不上任何任务，只能按「孤儿 thread_id」清。运行期增量清理已由
main._purge_checkpoints 接管（删任务行前同 WHERE 收集 id），本脚本只做
一次性存量清理，部署 v0.75 后跑一次即可。

孤儿判定：thread_id 出现于三表任一、且 NOT IN (SELECT id::text FROM
ozon_product_tasks)。比单看 checkpoints 表取并集（blobs/writes 可能残留
checkpoints 已消失的 thread）。
⚠️ memory.checkpoint_migrations 是 langgraph 自有版本表，绝不清理。

用法：
    python scripts/cleanup_checkpoints.py --dry-run            # 只统计各表将删行数
    python scripts/cleanup_checkpoints.py                      # 实际删除（默认读 $PGDATABASE_URL）
    python scripts/cleanup_checkpoints.py --db-url postgresql://... --batch 5000

幂等可重跑：删完再跑 = 0 行。ctid 批删 LIMIT :batch 防大事务锁表。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_checkpoints")

# 清理序：语义父表先删（与 main._CHECKPOINT_PURGE_TABLES 同序）；migrations 绝不进列
_PURGE_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

# 孤儿集物化为临时表（连接会话级——批删循环中要 commit，勿用 ON COMMIT DROP，
# 那会在首次 commit 后把临时表删掉）：三表 thread_id 并集 - 存活任务 id
# （NOT IN 一次求值，后续批删只碰临时表，不再全表反连接）
_ORPHAN_SQL = """
CREATE TEMP TABLE _orphan_threads AS
SELECT DISTINCT thread_id FROM (
    SELECT thread_id FROM memory.checkpoints
    UNION
    SELECT thread_id FROM memory.checkpoint_blobs
    UNION
    SELECT thread_id FROM memory.checkpoint_writes
) s
WHERE thread_id NOT IN (SELECT id::text FROM ozon_product_tasks)
"""


def _resolve_db_url(args) -> str:
    if args.db_url:
        return args.db_url
    url = os.getenv("PGDATABASE_URL") or ""
    if not url:
        print("❌ 未提供 --db-url 且环境变量 PGDATABASE_URL 为空", file=sys.stderr)
        sys.exit(2)
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description="memory.checkpoints 三表存量孤儿一次性清理")
    parser.add_argument("--db-url", default="", help="PG 连接串（默认读 $PGDATABASE_URL）")
    parser.add_argument("--dry-run", action="store_true", help="只打印各表将删行数，不删除")
    parser.add_argument("--batch", type=int, default=5000, help="ctid 批删每批行数（默认 5000）")
    args = parser.parse_args()

    from sqlalchemy import create_engine

    engine = create_engine(_resolve_db_url(args))
    total_deleted = 0
    with engine.connect() as conn:
        conn.execute(text(_ORPHAN_SQL))
        n_orphans = conn.execute(
            text("SELECT count(*) FROM _orphan_threads")
        ).scalar_one()
        logger.info("孤儿 thread_id 数: %d（三表并集 - 存活任务）", n_orphans)
        if not n_orphans:
            print("✅ 无孤儿 checkpoint，无需清理")
            return 0

        for table in _PURGE_TABLES:
            if args.dry_run:
                n = conn.execute(text(
                    f"SELECT count(*) FROM memory.{table} t "
                    "WHERE thread_id IN (SELECT thread_id FROM _orphan_threads)"
                )).scalar_one()
                print(f"[dry-run] memory.{table} 将删 {int(n)} 行")
                continue
            table_deleted = 0
            while True:
                res = conn.execute(text(
                    f"DELETE FROM memory.{table} WHERE ctid IN ("
                    f"  SELECT ctid FROM memory.{table} t"
                    "   WHERE thread_id IN (SELECT thread_id FROM _orphan_threads)"
                    "   LIMIT :batch"
                    ")"
                ), {"batch": args.batch})
                deleted = int(res.rowcount or 0)
                table_deleted += deleted
                conn.commit()
                if deleted < args.batch:
                    break
            print(f"memory.{table} 删除 {table_deleted} 行")
            total_deleted += table_deleted

    if args.dry_run:
        print("✅ dry-run 完成（未删除任何行）")
    else:
        print(f"✅ 清理完成：三表合计删除 {total_deleted} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())

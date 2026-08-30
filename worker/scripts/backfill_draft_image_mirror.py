#!/usr/bin/env python3
"""PRD M5b: 采集箱存量草稿图片镜像回填(COS)。

扫描 product_drafts 中 payload.draft.images 含非 COS 外链的草稿,逐个下载转存
COS 并按 version 回写(与在线镜像同一守卫,避免覆盖并发编辑)。

用法:
    python scripts/backfill_draft_image_mirror.py [--db-url $PGDATABASE_URL] \\
        [--limit 200] [--dry-run]

--dry-run 只统计不写入;退出码 0 = 成功(含 0 条),1 = 异常。
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

from sqlalchemy import create_engine, text

from services.draft_image_mirror import mirror_draft_images
from utils.cos_uploader import cos_enabled

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_draft_image_mirror")


def main() -> int:
    parser = argparse.ArgumentParser(description="采集箱存量草稿图片镜像回填")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""))
    parser.add_argument("--limit", type=int, default=200, help="本次扫描上限(默认 200)")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL 或 --db-url")
        return 1
    if not cos_enabled():
        logger.warning("COS 未配置(COS_SECRET_ID/KEY/BUCKET),回填跳过")
        return 0

    engine = create_engine(args.db_url)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, tenant_id, version, payload FROM product_drafts "
            "WHERE image_mirror_state <> 'mirrored' "
            "ORDER BY updated_at DESC LIMIT :lim"
        ), {"lim": args.limit}).fetchall()

    total = len(rows)
    mirrored = 0
    failed = 0
    skipped = 0
    for rid, tenant_id, version, payload in rows:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                failed += 1
                continue
        images = ((payload or {}).get("draft") or {}).get("images") or []
        if not isinstance(images, list) or not images:
            skipped += 1
            continue
        if all(
            not isinstance(u, str)
            or (".myqcloud.com" in u.lower() or "cos." in u.lower())
            for u in images
        ):
            skipped += 1
            continue
        if args.dry_run:
            logger.info("[dry-run] 待镜像 draft=%s v=%s 图片=%d 张", rid, version, len(images))
            continue
        new_images, changed = mirror_draft_images(payload)
        if not changed:
            failed += 1
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE product_drafts SET image_mirror_state='failed' "
                    "WHERE id=:id AND tenant_id=:tenant_id AND version=:version"
                ), {"id": rid, "tenant_id": tenant_id, "version": version})
            logger.warning("镜像失败(保持外链) draft=%s v=%s", rid, version)
            continue
        new_payload = json.loads(json.dumps(payload))
        new_payload["draft"]["images"] = new_images
        with engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE product_drafts SET payload=CAST(:payload AS jsonb), "
                "image_mirror_state='mirrored' "
                "WHERE id=:id AND tenant_id=:tenant_id AND version=:version"
            ), {
                "payload": json.dumps(new_payload, ensure_ascii=False),
                "id": rid, "tenant_id": tenant_id, "version": version,
            })
        if result.rowcount == 0:
            logger.warning("回写丢弃(版本已变更) draft=%s", rid)
            failed += 1
        else:
            mirrored += 1

    logger.info("回填完成: 扫描=%d 镜像=%d 失败=%d 跳过=%d (dry-run=%s)",
                total, mirrored, failed, skipped, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

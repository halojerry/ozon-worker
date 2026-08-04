#!/usr/bin/env python3
"""清理 category_mapping 学习缓存中的污染记录。

背景（v0.21）：旧版 learning_record 在 upload_status=="success"（含 imported/pending 假成功）
时把"1688 末级类目 → Ozon dc/tp"写入 category_mapping，导致错误类目被 L0 高置信复用。
v0.21 起只写 source='learned_approved'（仅审核 approved 才写入）。
本脚本把旧的非 learned_approved 记录一键失效。

用法：
  python scripts/clean_category_mapping.py --dry-run   # 只统计/列出
  python scripts/clean_category_mapping.py --apply     # 置 is_active=false
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import update, select, func
from storage.database.db import get_session
from storage.database.shared.model import CategoryMapping


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 category_mapping 学习缓存污染记录")
    parser.add_argument("--dry-run", action="store_true", help="只统计/列出，不修改")
    parser.add_argument("--apply", action="store_true", help="实际执行：置 is_active=false")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("请指定 --dry-run 或 --apply")
        return 1

    session = get_session()
    try:
        # v0.21 起新记录 source='learned_approved'；其余为旧污染/未经验证记录
        polluted = session.execute(
            select(CategoryMapping).where(
                CategoryMapping.is_active.is_(True),
                CategoryMapping.source != "learned_approved",
            )
        ).scalars().all()

        total = session.execute(
            select(func.count(CategoryMapping.id)).where(CategoryMapping.is_active.is_(True))
        ).scalar_one()

        print(f"category_mapping 现有 active 记录: {total} 条，其中非 learned_approved（待清理）: {len(polluted)} 条")
        for r in polluted[:50]:
            print(f"  #{r.id} leaf={r.source_category_leaf} → [{r.description_category_id}/{r.type_id}] "
                  f"conf={r.confidence} succ={r.success_count} fail={r.fail_count} src={r.source}")
        if len(polluted) > 50:
            print(f"  ... 其余 {len(polluted) - 50} 条略")

        if args.apply and polluted:
            session.execute(
                update(CategoryMapping)
                .where(
                    CategoryMapping.is_active.is_(True),
                    CategoryMapping.source != "learned_approved",
                )
                .values(is_active=False)
            )
            session.commit()
            print(f"✅ 已失效 {len(polluted)} 条污染记录（学习将从 approved-only 重新积累）")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""PR-6: 历史 ozon_attribute_mappings source 列回填。

已有行的 source 列默认 'learned_approved'（迁移默认值），但其中混着两类毒数据：
1. fabricated source_value（`[{属性名}]`）— 非真实 1688→Ozon 映射 → default_fallback
2. target_value 是 attr_defaults 硬编码默认值（如 "Нет бренда"/"Унисекс"/"0"）— 默认兜底 → default_fallback

用法（需 PGDATABASE_URL）：
    python scripts/backfill_mapping_source.py            # dry-run 预览
    python scripts/backfill_mapping_source.py --apply    # 实际回填
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage.database.db import get_session
from storage.database.shared.model import OzonAttributeMapping
from sqlalchemy import select, update

# attr_defaults 硬编码默认值（与 worker/src/utils/attr_defaults.py 对齐，回填识别毒数据）
DEFAULT_TARGET_VALUES = {
    "Нет бренда", "Унисекс", "0", "не требуется", "нет",
    "Китай", "730", "1", "365", "40", "сухое место", "полимерные материалы",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="ozon_attribute_mappings source 列回填（PR-6）")
    parser.add_argument("--apply", action="store_true", help="实际回填（默认 dry-run）")
    args = parser.parse_args()

    session = get_session()
    try:
        rows = session.execute(select(OzonAttributeMapping)).scalars().all()
    except Exception as e:
        print(f"❌ 查询失败（需 PGDATABASE_URL）: {e}")
        return 1

    fabricated = 0
    default_val = 0
    already_ok = 0
    to_update = []

    for r in rows:
        sv = str(r.source_value or "")
        tv = str(r.target_value or "")
        cur = str(r.source or "")
        if cur != "learned_approved":
            already_ok += 1
            continue
        if sv.startswith("[{"):
            fabricated += 1
            to_update.append((r.id, "default_fallback"))
        elif tv in DEFAULT_TARGET_VALUES:
            default_val += 1
            to_update.append((r.id, "default_fallback"))

    print(f"📊 扫描 {len(rows)} 行: fabricated={fabricated}, 默认值={default_val}, 已非默认={already_ok}")
    if not args.apply:
        print(f"  [dry-run] 将回填 {len(to_update)} 行 → default_fallback。加 --apply 执行。")
        return 0

    for rid, new_source in to_update:
        session.execute(
            update(OzonAttributeMapping)
            .where(OzonAttributeMapping.id == rid)
            .values(source=new_source)
        )
    session.commit()
    print(f"✅ 已回填 {len(to_update)} 行 → default_fallback")
    return 0


if __name__ == "__main__":
    sys.exit(main())

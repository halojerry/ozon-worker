#!/usr/bin/env python3
"""category_mapping (L0 学习表) 边界清洗（v0.83 类目权威边界批③配套，一次性运维工具）。

背景：`category_mapping` 是「1688 leaf → Ozon (dc,tp)」的学习映射，L0 权威档
（curated / learned success>=2）会**跳过**文本一致性守卫直通类目链。历史污染
（旧写侧假成功、跨域错配、同义词泛化）会让学习表自证回灌毒行。本脚本按三档
判据一次性清洗（`source="curated"` 行恒不动——人工/官方来源）：

  ① `dc/tp` 不在 Ozon 类目树中（`category_tree_nodes` 无该组合）→ `is_active=False`
     （整行下线，下次 L0 读侧自然不再命中）；
  ② `category_path_zh` 空 → **只报告**（无 ZH 路径无从判 overlap，不误伤）；
  ③ `source_category_leaf` 与 `category_path_zh` 零 overlap
     （复用 `graphs.nodes.learning_record_node._leaf_path_overlap`，与写侧守卫同源）
     → `success_count` 压回 1（**降权走 L0 弱档，不整行下线**——弱档不一致时会走
     R2b LLM 仲裁，仍有救回与取证机会）。

判据优先级：① > ② > ③（dc/tp 不在树的行无意义，直接下线）。

用法（需可访问 worker PG）：
  python scripts/audit_category_mapping.py --dry-run    # 只报告动作计划，不动库
  python scripts/audit_category_mapping.py --apply      # 实际写库
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ── 判定动作常量（纯函数返回，测试与日志共用） ──
ACT_OK = "ok"
ACT_SKIP_CURATED = "skip_curated"
ACT_OFFLINE = "offline"
ACT_REPORT_ONLY = "report_only"
ACT_DOWNGRADE = "downgrade"

_ACTION_LABEL = {
    ACT_SKIP_CURATED: "跳过(curated)",
    ACT_OFFLINE: "下线(is_active=False)",
    ACT_REPORT_ONLY: "仅报告(cat_zh 空)",
    ACT_DOWNGRADE: "降权(success_count→1)",
    ACT_OK: "保留",
}


def _get(row, key: str):
    """duck-typed 取值（ORM 行对象或 dict 均可，测试可传 dict）。"""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def classify_mapping_row(row, tree_ids) -> tuple[str, str]:
    """判定单行 category_mapping 的清洗动作（**纯函数**，可单测）。

    Args:
        row: duck-typed 行（`source` / `description_category_id` / `type_id` /
            `category_path_zh` / `source_category_leaf`）。
        tree_ids: Ozon 类目树现有 (dc, tp) 组合集合。

    Returns:
        (action, reason)。action 见 ACT_* 常量。
    """
    src = str(_get(row, "source") or "")
    if src == "curated":
        return ACT_SKIP_CURATED, "curated 行恒不动（人工/官方来源）"
    try:
        dc = int(_get(row, "description_category_id") or 0)
        tp = int(_get(row, "type_id") or 0)
    except (TypeError, ValueError):
        dc, tp = 0, 0
    if (dc, tp) not in tree_ids:
        return ACT_OFFLINE, f"dc/tp [{dc}/{tp}] 不在 Ozon 类目树"
    cat_zh = str(_get(row, "category_path_zh") or "").strip()
    if not cat_zh:
        return ACT_REPORT_ONLY, "category_path_zh 空，无 ZH 路径无从判 overlap"
    leaf = str(_get(row, "source_category_leaf") or "").strip()
    if leaf:
        from graphs.nodes.learning_record_node import _leaf_path_overlap
        if not _leaf_path_overlap(leaf, cat_zh):
            return (ACT_DOWNGRADE,
                    f"leaf「{leaf}」与 ZH 路径「{cat_zh[:40]}」零 overlap")
    return ACT_OK, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="category_mapping L0 学习表边界清洗")
    parser.add_argument("--dry-run", action="store_true", help="只报告动作计划，不修改")
    parser.add_argument("--apply", action="store_true", help="实际写库")
    args = parser.parse_args()
    if not args.dry_run and not args.apply:
        print("请指定 --dry-run 或 --apply")
        return 1

    from sqlalchemy import select

    from storage.database.db import get_session
    from storage.database.shared.model import CategoryMapping, CategoryTreeNode

    session = get_session()
    try:
        tree_rows = session.execute(
            select(CategoryTreeNode.description_category_id, CategoryTreeNode.type_id)
        ).all()
        tree_ids = {(int(a or 0), int(b or 0)) for a, b in tree_rows}
        print(f"Ozon 类目树载入 (dc,tp) 组合: {len(tree_ids)}")

        rows = session.execute(select(CategoryMapping)).scalars().all()
        counts: dict[str, int] = {k: 0 for k in _ACTION_LABEL}
        offline: list = []
        downgrade: list = []
        for r in rows:
            action, reason = classify_mapping_row(r, tree_ids)
            counts[action] = counts.get(action, 0) + 1
            if action == ACT_OK:
                continue
            print(f"  [{_ACTION_LABEL[action]}] #{getattr(r, 'id', '?')} "
                  f"leaf={getattr(r, 'source_category_leaf', '')} "
                  f"→ [{getattr(r, 'description_category_id', '?')}/"
                  f"{getattr(r, 'type_id', '?')}] succ={getattr(r, 'success_count', '?')} "
                  f"src={getattr(r, 'source', '?')} :: {reason}")
            if action == ACT_OFFLINE:
                offline.append(r)
            elif action == ACT_DOWNGRADE:
                downgrade.append(r)

        print("\n=== category_mapping 清洗汇总 ===")
        for k, label in _ACTION_LABEL.items():
            print(f"  {label}: {counts.get(k, 0)}")
        print(f"  （总行数 {len(rows)}）")

        if args.apply:
            for r in offline:
                r.is_active = False
            for r in downgrade:
                try:
                    r.success_count = min(int(r.success_count or 1), 1)
                except (TypeError, ValueError):
                    r.success_count = 1
            session.commit()
            print(f"\n✅ 已应用: 下线 {len(offline)} 行，降权 {len(downgrade)} 行")
        else:
            print(f"\n（--dry-run：未写库。将下线 {len(offline)} 行，"
                  f"降权 {len(downgrade)} 行）")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())

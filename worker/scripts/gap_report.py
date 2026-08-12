"""属性缺口报告 CLI（Phase 0）— 离线量化"应填 vs 已填"真实缺口。

用法：
    python -m src.scripts.gap_report [--limit N] [--products N]

从 PG 读取任务表 + ozon_attribute_mappings，输出：
    - schema 属性总数 / 系统生成 / 应填 / 已填 / attempted_fill_rate
    - 缺口来源分布（from_1688_attr / from_title / from_variant / from_ozon_attrs / no_source）
    - Top 缺口属性（按出现频次）

不需要 Worker 运行，纯离线统计。DB 不可用时打印说明退出（非致命）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(
    os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
))


def main() -> int:
    parser = argparse.ArgumentParser(description="属性缺口量化报告")
    parser.add_argument("--limit", type=int, default=500, help="扫描任务数上限（默认 500）")
    parser.add_argument("--products", type=int, default=50, help="采样产品数用于缺口统计（默认 50）")
    args = parser.parse_args()

    try:
        from storage.database.db import get_session  # type: ignore
        from utils.attr_gap import compute_gap, summarize_gaps  # type: ignore
    except Exception as e:
        print(f"⚠️ 依赖加载失败（需在 worker 环境运行）: {e}")
        return 1

    try:
        session = get_session()
    except Exception as e:
        print(f"⚠️ PG 连接失败: {e}")
        print("本地 Docker: cd deploy && docker compose up -d db （端口 5433）")
        return 1

    print(f"🔍 扫描最近 {args.limit} 条任务，采样 {args.products} 产品统计缺口...")
    # 读最近任务（含信封 payload 的 draft + 结果 final_attributes）
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT payload, result FROM ozon_product_tasks "
        "WHERE payload IS NOT NULL ORDER BY id DESC LIMIT :lim"
    ), {"lim": args.limit}).mappings().all()
    session.close()

    if not rows:
        print("⚠️ 无任务数据（表为空或全部无 payload）")
        return 0

    reports = []
    for row in rows[: args.products]:
        payload = row.get("payload") or {}
        result = row.get("result") or {}
        draft = payload.get("envelope", {}).get("draft", {})
        schema = payload.get("envelope", {}).get("extensions", {}).get(
            "attributes_schema", []) or payload.get("attributes_schema", [])
        if not schema:
            continue
        # 已填属性 ID 集合（从结果 final_attributes 提取）
        filled_ids = []
        for attr in (result.get("final_attributes") or []):
            if isinstance(attr, dict) and attr.get("id"):
                filled_ids.append(int(attr["id"]))
        reports.append(compute_gap(schema, draft, filled_ids))

    if not reports:
        print("⚠️ 采样产品无 schema 数据（payload 缺 attributes_schema）")
        return 0

    agg = summarize_gaps(reports)
    print()
    print("=" * 60)
    print(f"📊 属性缺口汇总（{agg['products']} 产品）")
    print("=" * 60)
    print(f"  平均 schema 属性数 : {agg['avg_schema']}")
    print(f"  平均系统生成(不应填): {agg['system_generated_per_product']}")
    print(f"  平均应填           : {agg['avg_should_fill']}")
    print(f"  平均已填           : {agg['avg_filled']}")
    print(f"  attempted_fill_rate: {agg['attempted_fill_rate'] * 100:.1f}%")
    print()
    print("📍 缺口来源分布（未填属性可能从哪补）:")
    for hint, n in sorted(agg["source_distribution"].items(), key=lambda kv: -kv[1]):
        print(f"    {hint:<16} {n}")
    print()
    print("🏆 Top 缺口属性:")
    for aid, d in list(agg["top_gap_attrs"].items())[:15]:
        req = "必填" if d["required"] else "可选"
        dict_mark = "字典" if d["dictionary_id"] else "自由文本"
        print(f"    [{aid}] {d['name']} ({dict_mark}/{req}) 缺口 {d['count']} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main())

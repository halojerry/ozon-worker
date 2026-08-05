#!/usr/bin/env python3
"""类目透传诊断（v0.26）— 为下一版 Widget→Seller 类目映射表提供数据依据。

背景：skill 从 Ozon 页面抓的面包屑类目 ID 是 Widget 空间，Worker 的 Seller 类目树
直查经常失败 → 降级 pg_trgm 文本猜 → 猜错（wave6 帽子 declined 根因）。

本脚本量化「类目错配」规模：
  1. 读 audit_products.py 的聚合 JSON（含各商品 dc/type + 审核状态 + 错误码）
  2. 对 declined/fail 商品聚合错误码分布（DESCRIPTION_DECLINE 是类目错配直接证据）
  3. 列出「类目错配高风险」商品清单（dc/type + 名称 + 错误），供下一版映射表优先修复

用法：
  python3 audit_products.py --json /tmp/ozon_audit.json   # 先生成审计数据
  python3 scripts/analyze_category_mismatch.py /tmp/ozon_audit.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 scripts/analyze_category_mismatch.py <audit.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"❌ 审计文件不存在: {path}", file=sys.stderr)
        return 2
    agg = json.loads(path.read_text(encoding="utf-8"))

    total_declined = 0
    total_fail = 0
    category_errors = Counter()      # DESCRIPTION_DECLINE 等类目相关错误
    all_errors = Counter()
    declined_examples: list = []

    for store, data in agg.items():
        if not isinstance(data, dict):
            continue
        for p in data.get("problem_products", []):
            status = p.get("moderate_status") or ""
            if status == "declined":
                total_declined += 1
                declined_examples.append({
                    "store": store,
                    "offer_id": p.get("offer_id"),
                    "name": p.get("name", "")[:50],
                    "errors": [e.get("code", "") for e in p.get("errors", [])],
                })
            if (p.get("validation_status") or "") == "fail":
                total_fail += 1
            for e in p.get("errors", []):
                code = e.get("code") or ""
                all_errors[code] += 1
                if code in ("DESCRIPTION_DECLINE", "VALUE_MUST_BE_INTEGER",
                            "VALUE_MUST_BE_DECIMAL", "ATTRIBUTE_VALUE_COUNT_EXCEEDED"):
                    category_errors[code] += 1

    print("=" * 60)
    print("类目/属性错配诊断（v0.26，为下一版映射表提供数据）")
    print("=" * 60)
    print(f"\n📊 全店 declined: {total_declined} | validation fail: {total_fail}")

    print(f"\n🔴 类目错配相关错误（DESCRIPTION_DECLINE 是 Widget→Seller 文本猜错直接证据）:")
    for code, cnt in category_errors.most_common():
        print(f"  [{cnt:4d}] {code}")

    print(f"\n📋 declined 商品示例（前 15 个）:")
    for ex in declined_examples[:15]:
        errs = ",".join(ex["errors"][:3])
        print(f"  {ex['store']} | {ex['offer_id']} | {ex['name']} | errs: {errs}")

    # 类目错配 = declined 且带 DESCRIPTION_DECLINE 的商品，其 dc/type 是下一版要修的目标
    cat_mismatch = [
        ex for ex in declined_examples
        if any("DESCRIPTION_DECLINE" in e for e in [ex["errors"]])
    ]
    print(f"\n🎯 类目错配高风险商品: {len(cat_mismatch)} 个（下一版映射表优先修复对象）")
    print("\n提示：下一版映射表需要 skill 抓取 Widget ID + 面包屑文本 → 在 Seller 类目树")
    print("建立 Widget ID ↔ (description_category_id, type_id) 映射并回写 category_mapping 表。")

    return 0


if __name__ == "__main__":
    sys.exit(main())

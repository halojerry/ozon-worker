#!/usr/bin/env python3
"""实证实验:1688 中文类目名 → Ozon category_tree_nodes(ZH_HANS) 匹配率。

从 batch_results 提取真实 1688 source_category,在 category_tree_nodes 里:
① 末级名精确匹配  ② 末级名相似度匹配(pg_trgm)  ③ 全路径匹配
统计命中率,验证「1688 类目名能否在 Ozon 侧匹配」。
"""
import glob
import json
import os
import re
from collections import Counter

import psycopg2

DSN = os.environ.get("PGDATABASE_URL", "postgresql://postgres:ozon123@host.docker.internal:5433/ozon")


def find_source_category(o):
    """递归提取任意深度的 source_category 字符串。"""
    out = []
    if isinstance(o, dict):
        v = o.get("source_category")
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
        for vv in o.values():
            out.extend(find_source_category(vv))
    elif isinstance(o, list):
        for vv in o:
            out.extend(find_source_category(vv))
    return out


def load_source_categories():
    cats = []
    for path in glob.glob("/app/skill/data/batch_results/*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for src in find_source_category(data):
            if src and not re.search(r"[\ufffd]", src):  # 排除乱码
                cats.append(src)
    return list(dict.fromkeys(cats))


def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("SELECT node_name, full_path FROM category_tree_nodes WHERE language='ZH_HANS'")
    rows = cur.fetchall()
    names = [r[0] for r in rows]
    paths = [r[1] or "" for r in rows]
    cur.close()
    conn.close()
    print(f"category_tree_nodes ZH_HANS: {len(rows)} 节点")

    cats = load_source_categories()
    print(f"真实 1688 类目路径: {len(cats)} 条(去重后)\n")

    exact_hit = partial_hit = path_hit = miss = 0
    examples_exact, examples_miss = [], []
    for cat in cats:
        parts = [p.strip() for p in cat.split(">")]
        leaf = parts[-1]
        # ① 末级名精确匹配(含去除「其他/通用」等泛化词)
        if leaf in names:
            exact_hit += 1
            examples_exact.append(cat)
            continue
        # ② 末级名子串包含匹配(节点名含叶名 或 叶名含节点名)
        sub = [n for n in names if n and (n in leaf or leaf in n)]
        if sub:
            partial_hit += 1
            continue
        # ③ 全路径任一级匹配
        if any(any(p in path for p in parts) for path in paths):
            path_hit += 1
            continue
        miss += 1
        examples_miss.append(cat)

    total = len(cats)
    print(f"① 末级名精确命中: {exact_hit}/{total} ({exact_hit/total*100:.0f}%)")
    print(f"② 子串包含命中:  {partial_hit}/{total} ({partial_hit/total*100:.0f}%)")
    print(f"③ 路径级命中:    {path_hit}/{total} ({path_hit/total*100:.0f}%)")
    print(f"❌ 完全未命中:    {miss}/{total} ({miss/total*100:.0f}%)")
    print("\n== 精确命中示例 ==")
    for e in examples_exact[:5]:
        print("  ", e)
    print("\n== 未命中示例(错配风险) ==")
    for e in examples_miss[:15]:
        print("  ", e)


if __name__ == "__main__":
    main()

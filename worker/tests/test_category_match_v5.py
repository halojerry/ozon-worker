#!/usr/bin/env python3
"""v5 类目匹配测试：jieba + LIKE vs pg_trgm

用法: cd worker && PYTHONPATH=src python3 tests/test_category_match_v5.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ozon_category_query import OzonCategoryQuery

# ── 测试用例（基于实际产品） ──
# (产品名, 1688类目, 期望关键词, 旧结果-预期可能跑偏)
TEST_CASES = [
    # 已知 pg_trgm 跑偏的
    ("打水桶钓鱼装备", "垂钓用品 > 鱼桶", ["鱼", "桶", "钓鱼"],
     "pg_trgm: 鱼钩/鱼缸/鱼干 (共享'鱼'丢失'桶')"),
    ("太阳镜墨镜护目", "眼镜及配件 > 太阳镜", ["太阳镜", "墨镜", "眼镜"],
     "pg_trgm: 太阳能集热器 (共享'太阳'丢失'镜')"),
    ("冰丝防晒冰袖", "运动户外辅助用品 > 户外袖套", ["冰袖", "防晒", "袖套"],
     "pg_trgm: 0结果"),
    ("辣椒帽派对道具", "工艺品 > 塑料工艺品", ["帽子", "派对", "道具"],
     "pg_trgm: 牙盘/自行车配件 (declined!)"),
    ("蝙蝠猫面具", "工艺品 > 塑料工艺品", ["面具", "猫", "宠物"],
     "pg_trgm: 牙盘/自行车配件 (declined!)"),
    ("3D青蛙模型玩具", "工艺品 > 塑料工艺品", ["青蛙", "玩具", "模型"],
     "pg_trgm: L0命中OK但无log"),

    # 新测试
    ("盆景耙子", "园林资材 > 园艺工具", ["盆景", "耙子", "园艺"],
     "pg_trgm: declared 8229"),
    ("修枝剪园艺剪刀", "园林资材 > 园艺工具", ["修枝剪", "园艺", "剪刀"],
     ""),
    ("昆虫捕捉器", "居家日用 > 其他居家日用", ["昆虫", "捕捉", "夹子"],
     "pg_trgm: declined"),
    ("喷水壶浇花", "园林资材 > 园艺灌溉工具", ["喷壶", "浇花", "园艺"],
     "pg_trgm: declined 8229"),
    ("甩脂机抖抖机", "运动户外 > 甩脂机", ["甩脂机", "健身", "减肥"],
     ""),
    ("油炸锅炸鸡", "厨房用品 > 锅具", ["油炸锅", "炸鸡", "锅"],
     ""),
]


def search_wrapper(query, top_k=10, node_type="type", language="ZH_HANS"):
    """Wrapper that catches errors"""
    try:
        q = OzonCategoryQuery()
        return q.search_nodes(query, top_k=top_k, node_type=node_type, language=language)
    except Exception as e:
        return [{"error": str(e)}]


def main():
    print("=" * 80)
    print("🔬 v5 类目匹配测试：jieba + LIKE")
    print("=" * 80)

    ok = 0
    fail = 0

    for title, source_cat, keywords, pg_note in TEST_CASES:
        print(f"\n── {title[:40]} ──")
        print(f"   1688类目: {source_cat}")
        print(f"   期望关键词: {keywords}")
        if pg_note:
            print(f"   pg_trgm旧行为: {pg_note}")

        # 用 source_category 的末级名称搜索
        leaf = source_cat.split(" > ")[-1].strip()
        results = search_wrapper(leaf, top_k=5)

        if not results:
            print(f"   ❌ 无结果（leaf='{leaf}'）")
            # 回退：用 jieba 关键词搜索
            results = search_wrapper(title, top_k=5)
            if not results:
                print(f"   ❌ 关键词搜索也无结果")
                fail += 1
                continue

        top = results[0]
        if "error" in top:
            print(f"   ❌ 错误: {top['error']}")
            fail += 1
            continue

        sim = top.get("similarity", 0)
        matched = top.get("matched_tokens", [])

        # 检查是否有有意义的匹配
        path = top.get("full_path", "")
        name = top.get("node_name", "")

        # 简单验证：关键词中至少一个出现在 full_path 中
        keyword_hit = any(kw in path for kw in keywords)

        status = "✅" if keyword_hit else "⚠️"
        if keyword_hit:
            ok += 1
        else:
            fail += 1

        print(f"   {status} Top-1: [{top['description_category_id']}/{top['type_id']}] {path}")
        print(f"       name={name}, sim={sim:.2f}, tokens={matched}")

        # 显示 top-3
        for i, r in enumerate(results[1:3], 2):
            r_sim = r.get("similarity", 0)
            r_tokens = r.get("matched_tokens", [])
            print(f"       #{i}: [{r['description_category_id']}/{r['type_id']}] {r['full_path']} (sim={r_sim:.2f})")

    print("\n" + "=" * 80)
    print(f"📊 结果: ✅ {ok}/{len(TEST_CASES)} 通过, ❌ {fail}/{len(TEST_CASES)} 失败")
    print("=" * 80)


if __name__ == "__main__":
    main()

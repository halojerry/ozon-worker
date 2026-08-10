"""discover 集成 --blue-ocean-source（C4 step2）单测 — all_queries 蓝海数据反哺。

覆盖：load_blue_ocean_csv 解析/降级、competitor_keyword_density 因子 +
clip、cmd_discover 无 CSV 降级原流程、有效 CSV 注入蓝海评分。
mock 采集与 CDP，不依赖真实网络。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ozon_discovery import (
    ProductCandidate,
    calculate_blue_ocean_score,
    compute_competitor_keyword_density,
    load_blue_ocean_csv,
)


def _mk(product_id="p1", margin=35, sales=100, growth=20, drr=15, comm=8, price=1500,
        sellers=7, has_analytics=True, chain_depth=0, seed_cat="", category=""):
    c = ProductCandidate(
        ozon_product_id=product_id, ozon_title="Автопоилка для кошек", ozon_price=price)
    c.competing_sellers = sellers
    c.profit_margin = margin
    c.monthly_sales = sales
    c.sales_growth = growth
    c.drr = drr
    c.commission_fbp = comm
    c.has_analytics = has_analytics
    c.chain_depth = chain_depth
    c._seed_category_id = seed_cat
    c.category = category
    return c


def _write_csv(path: str, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(r) + "\n")


def test_load_blue_ocean_csv_parses_and_missing_returns_empty():
    """正常解析（值保留字符串）+ 文件不存在返回 []。"""
    path = os.path.join(tempfile.mkdtemp(), "queries_all.csv")
    _write_csv(path, ["query", "count", "ca", "avg_ca_rub", "uniq_sellers",
                      "ordering_amount", "gmv"],
               [["поилка", "9494", "27.14", "1585", "30", "920", "1385552"],
                ["миска", "500", "10", "200", "3", "50", "1000"]])
    rows = load_blue_ocean_csv(path)
    assert len(rows) == 2, f"应解析 2 行, got {len(rows)}"
    assert rows[0]["query"] == "поилка"
    assert rows[0]["uniq_sellers"] == "30"
    assert rows[0]["count"] == "9494"
    assert rows[1]["query"] == "миска"
    # 文件不存在 → []
    assert load_blue_ocean_csv("/tmp/__no_such_blue_ocean__.csv") == []


def test_load_blue_ocean_csv_parse_failure_returns_empty():
    """CSV 解析失败（非法字节）→ [] 不抛异常。"""
    bad = os.path.join(tempfile.mkdtemp(), "bad.csv")
    with open(bad, "wb") as f:
        f.write(b"\xff\xfe\x00garbage")
    assert load_blue_ocean_csv(bad) == []


def test_compute_competitor_keyword_density_matching_and_none():
    """双向子串匹配 + 取 count 最高行 + 无匹配/缺数据 → None。"""
    rows = [
        {"query": "поилка", "count": "9494", "uniq_sellers": "30"},
        {"query": "автопоилка", "count": "100", "uniq_sellers": "5"},
        {"query": "миска", "count": "500", "uniq_sellers": ""},
    ]
    # 命中 поилка（uniq_sellers=30）→ 1 - 30/50 = 0.4
    assert compute_competitor_keyword_density(rows, "Поилка для кошек") == 0.4
    # 无匹配 → None
    assert compute_competitor_keyword_density(rows, "зуммер") is None
    # 空 rows / 空 keyword → None
    assert compute_competitor_keyword_density([], "x") is None
    assert compute_competitor_keyword_density(rows, "") is None
    # 匹配但 uniq_sellers 缺失 → None（不加因子）
    assert compute_competitor_keyword_density(rows, "миска") is None
    # 高卖家数封顶 → 0
    rows_hi = [{"query": "x", "count": "10", "uniq_sellers": "999"}]
    assert compute_competitor_keyword_density(rows_hi, "x") == 0.0
    # 多条匹配取 count 最高者（автопоилка count=9000 → 1 - 10/50 = 0.8）
    rows2 = [
        {"query": "поилка", "count": "5", "uniq_sellers": "45"},
        {"query": "автопоилка", "count": "9000", "uniq_sellers": "10"},
    ]
    assert compute_competitor_keyword_density(rows2, "поилка") == 0.8


def test_score_density_factor_boost_and_clip():
    """density 因子单调加分，总分 clip ≤100（全满分候选 + density=1 不超）。"""
    base = _mk(margin=35, sales=100, growth=20, drr=15, comm=8, price=1500, sellers=7)
    s0 = calculate_blue_ocean_score(base)
    s1 = calculate_blue_ocean_score(base, competitor_keyword_density=0.5)
    s2 = calculate_blue_ocean_score(base, competitor_keyword_density=1.0)
    assert s0 < s1 < s2, f"density 单调加分: {s0} < {s1} < {s2}"
    assert s2 - s0 == 10, f"density=1 加满 10 分, got {s2 - s0}"
    # clip 生效：全满分候选（基础 100）+ density=1 → 仍 100
    best = _mk(margin=50, sales=30, growth=50, drr=5, comm=5, price=2500,
               sellers=3, chain_depth=0, seed_cat="x", category="x")
    assert calculate_blue_ocean_score(best) == 100
    assert calculate_blue_ocean_score(best, competitor_keyword_density=1.0) == 100
    # 默认 None → 行为不变（回归）
    assert calculate_blue_ocean_score(base, competitor_keyword_density=None) == s0


def _discover_args(**overrides):
    import argparse
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="monthly_sales>=1", export="", output="", auto_submit=False,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _mock_cdp_and_collect(candidates, rules_result):
    return mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                      return_value=(True, "ok")), \
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=candidates), \
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   return_value=rules_result), \
        mock.patch("scripts.lib.ozon_discovery.match_selected"), \
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value="")


def test_blue_ocean_source_without_csv_falls_back():
    """--blue-ocean-source 但本地无 CSV → 打印降级提示，走原流程不崩。"""
    from scripts.cli import cmd_discover

    c1 = _mk(product_id="p1")
    c1.status = "ok"
    c2 = _mk(product_id="p2")
    c2.status = "ok"

    args = _discover_args(blue_ocean_source="csv",
                          blue_ocean_csv="/tmp/__no_such_blue_ocean__.csv")
    patches = _mock_cdp_and_collect([c1, c2], [c1, c2])
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cmd_discover(args)
    assert rc == 0
    assert "no blue_ocean data, fallback to original" in out.getvalue()
    assert "蓝海增强" not in out.getvalue()
    # 走原流程：候选 blue_ocean_score 未被预计算注入（保持 0）
    assert c1.blue_ocean_score == 0


def test_blue_ocean_source_with_csv_injects_scores():
    """--blue-ocean-source + 有效 CSV → 候选 blue_ocean_score 在表格前注入。"""
    from scripts.cli import cmd_discover

    path = os.path.join(tempfile.mkdtemp(), "queries_all.csv")
    _write_csv(path, ["query", "count", "ca", "uniq_sellers"],
               [["поилка", "9494", "27.14", "30"]])

    c1 = _mk(product_id="p1")
    c1.status = "ok"
    args = _discover_args(blue_ocean_source="csv", blue_ocean_csv=path)
    patches = _mock_cdp_and_collect([c1], [c1])
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cmd_discover(args)
    assert rc == 0
    assert "蓝海增强: 载入 1 个关键词" in out.getvalue()
    # 标题 "Автопоилка для кошек" 命中 query "поилка"（uniq_sellers=30 → density=0.4）
    assert c1.blue_ocean_score > 0, "候选蓝海评分应被注入（>0）"
    assert c1.blue_ocean_score <= 100


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

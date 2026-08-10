"""export_analysis_report 单测 — match_selected 完成后自动生成 MD + JSON 双份选品分析。

覆盖：双文件生成/返回结构、JSON schema 顶层键、状态分布计数、蓝海 Top-N
排序与截断、MD 标题与汇总、非 ASCII 标题保留（ensure_ascii=False）、空列表
返回 {} 不写文件、md/json 同 ts 配对、summary 利润/蓝海指标计算、MD 详情字段。
纯 mock 构造 ProductCandidate，不依赖真实网络。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ozon_discovery import ProductCandidate, export_analysis_report


def _mk(product_id="p1", title="Автопоилка для кошек", price=1500, margin=0,
        sales=0, growth=0, drr=0, sellers=0, create_days=0, rating=0,
        blue_ocean=0, match_url="", match_price=0, status="pending"):
    c = ProductCandidate(ozon_product_id=product_id, ozon_title=title, ozon_price=price)
    c.profit_margin = margin
    c.monthly_sales = sales
    c.sales_growth = growth
    c.drr = drr
    c.competing_sellers = sellers
    c.create_days = create_days
    c.rating = rating
    c.blue_ocean_score = blue_ocean
    c.match_1688_url = match_url
    c.match_1688_price = match_price
    c.status = status
    return c


def test_generates_md_and_json_files():
    """双份文件生成在 out_dir，返回 dict 含 md/json 路径且文件真实存在。"""
    cands = [_mk(product_id="p1", status="profitable", margin=30, blue_ocean=80),
             _mk(product_id="p2", status="rejected", margin=5, blue_ocean=60)]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out, top_n=5)
    assert set(result.keys()) == {"md", "json"}
    assert result["md"].exists() and result["json"].exists()
    assert result["md"].suffix == ".md"
    assert result["json"].suffix == ".json"


def test_json_schema_top_level_keys():
    """JSON 顶层键齐全：generated_at/summary/candidates/top_blue_ocean。"""
    cands = [_mk(product_id="p1")]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out)
    data = json.loads(result["json"].read_text(encoding="utf-8"))
    assert set(data.keys()) == {"generated_at", "summary", "candidates", "top_blue_ocean"}
    assert set(data["summary"].keys()) == {"total", "status_distribution", "blue_ocean", "profit"}
    assert set(data["summary"]["blue_ocean"].keys()) == {"max", "avg"}
    assert set(data["summary"]["profit"].keys()) == {"max", "median", "profitable_count"}
    assert "T" in data["generated_at"], "generated_at 应为 ISO 时间字符串"


def test_status_distribution_counts():
    """status_distribution 按状态计数正确。"""
    cands = [
        _mk(product_id="p1", status="profitable"),
        _mk(product_id="p1", status="profitable"),
        _mk(product_id="p2", status="rejected"),
        _mk(product_id="p3", status="no_match"),
    ]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out)
    data = json.loads(result["json"].read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 4
    assert data["summary"]["status_distribution"] == {
        "profitable": 2, "rejected": 1, "no_match": 1}


def test_top_blue_ocean_sorted_and_limited():
    """top_blue_ocean 按 blue_ocean_score 降序且不超过 top_n。"""
    cands = [_mk(product_id="p1", blue_ocean=30),
             _mk(product_id="p2", blue_ocean=90),
             _mk(product_id="p3", blue_ocean=60)]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out, top_n=2)
    data = json.loads(result["json"].read_text(encoding="utf-8"))
    tops = [x["ozon_product_id"] for x in data["top_blue_ocean"]]
    assert tops == ["p2", "p3"], f"应按蓝海分降序截断, got {tops}"
    assert len(data["top_blue_ocean"]) == 2
    # candidates 保持原始顺序全量输出
    assert len(data["candidates"]) == 3


def test_md_contains_titles_and_summary_counts():
    """MD 含每个产品标题 + 汇总计数（总数/状态分布）。"""
    cands = [_mk(product_id="p1", title="Автопоилка для кошек", status="profitable"),
             _mk(product_id="p2", title="Миска для собак", status="rejected")]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out)
    md = result["md"].read_text(encoding="utf-8")
    assert "Автопоилка для кошек" in md
    assert "Миска для собак" in md
    assert "候选总数: 2" in md
    assert "状态分布" in md
    assert "profitable=1" in md
    assert "rejected=1" in md


def test_json_keeps_non_ascii_titles():
    """俄文/emoji 标题 preserve（ensure_ascii=False，不转成 \\uXXXX 转义）。"""
    cands = [_mk(product_id="p1", title="Автопоилка для кошек 🐱")]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out)
    raw = result["json"].read_text(encoding="utf-8")
    assert "Автопоилка для кошек 🐱" in raw, "JSON 不应转义非 ASCII 字符"
    data = json.loads(raw)
    assert data["candidates"][0]["ozon_title"] == "Автопоилка для кошек 🐱"


def test_empty_candidates_returns_empty_dict():
    """空列表 → 返回 {}，不写任何文件不崩溃。"""
    out = tempfile.mkdtemp()
    result = export_analysis_report([], out_dir=out)
    assert result == {}
    assert list(Path(out).iterdir()) == [], "空候选不应产生文件"


def test_md_json_share_same_ts_prefix():
    """md/json 同名前缀（analysis_{ts}）配对。"""
    cands = [_mk(product_id="p1")]
    out = tempfile.mkdtemp()
    result = export_analysis_report(cands, out_dir=out)
    md_stem = result["md"].name.rsplit(".", 1)[0]
    json_stem = result["json"].name.rsplit(".", 1)[0]
    assert md_stem == json_stem, f"md/json 应共享同一 ts, got {md_stem} vs {json_stem}"
    assert md_stem.startswith("analysis_")


def test_summary_profit_and_blue_ocean_metrics():
    """summary 利润/蓝海指标：max/median/profitable_count + blue_ocean max/avg。"""
    c1 = _mk(product_id="p1", margin=30, blue_ocean=80, status="profitable")
    c2 = _mk(product_id="p2", margin=0, blue_ocean=40, status="rejected")  # margin=0 不计入可盈利
    c3 = _mk(product_id="p3", margin=20, blue_ocean=60, status="matched")  # margin>0 计入可盈利
    out = tempfile.mkdtemp()
    result = export_analysis_report([c1, c2, c3], out_dir=out)
    data = json.loads(result["json"].read_text(encoding="utf-8"))
    profit = data["summary"]["profit"]
    assert profit["max"] == 30
    assert profit["median"] == 20
    assert profit["profitable_count"] == 2  # c1 status=profitable + c3 margin>0
    bo = data["summary"]["blue_ocean"]
    assert bo["max"] == 80
    assert round(bo["avg"], 2) == 60.0


def test_md_detail_fields_present():
    """MD 产品详情块含全部汇报字段（价格/月销/增长/广告/跟卖/上架天/评分/蓝海/利润/货源/采购价/状态）。"""
    c = _mk(product_id="p1", title="Автопоилка", price=1500, margin=25.5, sales=120,
            growth=33.3, drr=12.5, sellers=7, create_days=90, rating=4.5,
            blue_ocean=88, match_url="https://detail.1688.com/offer/123.html",
            match_price=15.0, status="profitable")
    out = tempfile.mkdtemp()
    result = export_analysis_report([c], out_dir=out)
    md = result["md"].read_text(encoding="utf-8")
    for fragment in ["价格", "月销", "月增长率", "广告占比", "跟卖数", "上架天数",
                     "评分", "蓝海分", "利润率", "1688 货源", "采购价", "审核状态"]:
        assert fragment in md, f"MD 详情块缺少字段: {fragment}"
    assert "https://detail.1688.com/offer/123.html" in md
    assert "25.5" in md
    assert "1500" in md
    assert "profitable" in md


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

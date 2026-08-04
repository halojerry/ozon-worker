"""趋势选品单测（v0.25 S3）— 关键词解析/满3即停/渲染模板。"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trend_selection import parse_keywords_json, search_by_keywords, render_results


def test_parse_keywords_json():
    raw = '[{"keyword": "儿童益智积木", "reason": "竞争少需求增"}]'
    assert parse_keywords_json(raw) == [{"keyword": "儿童益智积木", "reason": "竞争少需求增"}]


def test_parse_keywords_json_fenced():
    raw = '```json\n[{"keyword": "宠物梳子", "reason": "x"}]\n```'
    assert parse_keywords_json(raw)[0]["keyword"] == "宠物梳子"


def test_parse_keywords_json_bad_raises():
    try:
        parse_keywords_json("不是json")
        assert False, "应抛异常"
    except ValueError:
        pass


def test_search_stops_at_three():
    from scripts.lib import ak_1688_client as mod
    calls = []
    def fake_search(keyword, **kw):
        calls.append(keyword)
        return [{"product_id": f"p{len(calls)}", "title": f"商品{len(calls)}",
                 "price": 10, "moq": 1, "sales": 100, "ship_rate_48h": 95, "location": "浙江",
                 "supplier_tags": [], "detail_url": "", "image_url": ""}]
    keywords = [{"keyword": f"k{i}", "reason": "r"} for i in range(5)]
    with mock.patch.object(mod, "search_products", side_effect=fake_search):
        results = search_by_keywords(keywords, max_results=3)
    assert len(results) == 3
    assert len(calls) == 3, "满 3 必须停止，不得继续搜索"


def test_render_contains_all_skus():
    results = [{
        "keyword": "儿童积木", "reason": "潜力大",
        "item": {"title": "积木", "price": 20, "moq": 1, "sales": 500,
                 "ship_rate_48h": 95, "location": "浙江", "supplier": "xx厂",
                 "supplier_tags": ["实力商家"], "detail_url": "https://d/1", "image_url": "",
                 "skus": [{"name": "红", "price": 20, "suggestedPrice": 60, "stock": 10},
                          {"name": "蓝", "price": 22, "suggestedPrice": 66, "stock": 8}]},
    }]
    out = render_results(results)
    assert "### 🔥 细分市场：儿童积木" in out
    assert "红" in out and "蓝" in out
    assert "60" in out and "66" in out
    assert "| SKU名称 | 拿货价(¥) | 建议售价(¥) | 库存 |" in out
    assert "查看" in out


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)

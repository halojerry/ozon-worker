#!/usr/bin/env python3
"""P1c (Task): fetch_bestseller_metrics_map 批量畅销榜指标 map（TDD）。

背景: fetch_ozon_bestsellers 单次 what_to_sell data/v3 调用即返回 top-50 商品
（sku + category1/2/3_id + sold_count/gmv/weight/尺寸等），是批量原语；逐 SKU 的
fetch_sales_analytics（1 调用/SKU @1s）是瓶颈。本 helper 复用批量响应按 sku 建
索引，磁盘缓存 6h（namespace seller_analytics，key 含 lang + company_id 维度），
只缓存成功结果。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_bestseller_metrics_map.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.lib.ozon_seller_analytics as sa


def _rows() -> list[dict]:
    return [
        {
            "sku": "123456",
            "name": "Товар A",
            "category1_id": 17028866,
            "category2_id": 17028929,
            "category3_id": 504866264,
            "sold_count": 100,
            "gmv_sum": 50000.0,
        },
        {
            "sku": "789012",
            "name": "Товар B",
            "category1_id": 17028866,
            "category2_id": 17029050,
            "category3_id": 0,
        },
    ]


def test_bestseller_metrics_map_keyed_by_sku():
    """{sku: row} 索引；单次批量调用 fetch_ozon_bestsellers，且行内含类目 ID。"""
    with mock.patch.object(sa, "fetch_ozon_bestsellers", return_value=_rows()) as m_fetch, \
         mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"):
        result = sa.fetch_bestseller_metrics_map(mock.MagicMock(), company_id="1111")
    m_fetch.assert_called_once_with(mock.ANY, company_id="1111")
    assert set(result.keys()) == {"123456", "789012"}
    assert result["123456"]["category2_id"] == 17028929
    assert result["123456"]["category3_id"] == 504866264
    assert result["789012"]["category2_id"] == 17029050
    assert result["123456"]["sold_count"] == 100


def test_bestseller_metrics_map_skips_empty_sku_rows():
    """无 sku 的行不进 map（空 key 无意义）。"""
    rows = [{"sku": "", "name": "no sku"}, {"sku": "42", "name": "ok"}]
    with mock.patch.object(sa, "fetch_ozon_bestsellers", return_value=rows), \
         mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"):
        result = sa.fetch_bestseller_metrics_map(mock.MagicMock())
    assert set(result.keys()) == {"42"}


def test_bestseller_metrics_map_empty_not_cached():
    """无结果（未登录/失败）→ 空 dict，不写缓存（可重试）。"""
    with mock.patch.object(sa, "fetch_ozon_bestsellers", return_value=[]), \
         mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set") as m_set:
        result = sa.fetch_bestseller_metrics_map(mock.MagicMock())
    assert result == {}
    m_set.assert_not_called()


def test_bestseller_metrics_map_cache_key_has_lang_and_company():
    """cache key 含 lang + company_id 维度；写缓存用 seller_analytics + 6h TTL。"""
    with mock.patch.object(sa, "fetch_ozon_bestsellers", return_value=_rows()), \
         mock.patch("scripts.lib.cache.cache_get", return_value=None) as m_get, \
         mock.patch("scripts.lib.cache.cache_set") as m_set:
        sa.fetch_bestseller_metrics_map(mock.MagicMock(), company_id="2222")
    key = m_get.call_args.args[1]
    assert "2222" in key and "zh-Hans" in key, f"key 应含公司+语言维度: {key}"
    assert m_set.call_args.args[0] == "seller_analytics"
    assert m_set.call_args.kwargs.get("ttl", 0) == 21600


def test_bestseller_metrics_map_cache_hit_skips_fetch():
    """缓存命中 → 不调 fetch_ozon_bestsellers，直接返回缓存。"""
    cached = {"111": {"sku": "111", "category2_id": 1}}
    with mock.patch.object(sa, "fetch_ozon_bestsellers") as m_fetch, \
         mock.patch("scripts.lib.cache.cache_get", return_value=cached):
        result = sa.fetch_bestseller_metrics_map(mock.MagicMock(), company_id="3333")
    assert result == cached
    m_fetch.assert_not_called()


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

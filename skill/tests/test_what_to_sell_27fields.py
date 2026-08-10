"""what_to_sell 27 字段解析扩展 + 昂贵抓取磁盘缓存 + Ozon 价格货币归一化回归。

来源：毛子ERP 插件源码逆向（maozi-plugin-3.2.2，/Users/halo/Downloads），
字段全部来自 what_to_sell + otherOffersFromSellers，与本项目同源。

覆盖：
1. _extract_metrics 解析 27+ 字段（camelCase → snake_case 全量映射）
2. fetch_sales_analytics / fetch_competing_sellers 磁盘缓存（key 含语言维度）
3. normalize_ozon_price CNY→RUB 唯一真源 + RUB/未知原样
4. fetch_product_info widget 货币识别 → 统一 RUB
5. cloud_probe._cached_ozon_scrape 缓存包装
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.lib.ozon_seller_analytics as osa
import scripts.lib.ozon_widget as ozon_widget
import scripts.lib.utils as utils
from scripts.lib.utils import normalize_ozon_price


# ─────────────────────────────────────────────────────────────────────────────
# 毛子ERP what_to_sell 响应 mock（字段名与 maozi-plugin 实测一致）
# ─────────────────────────────────────────────────────────────────────────────
def _what_to_sell_item() -> dict:
    """构造含 27+ 字段的单条 what_to_sell item（camelCase 为主，与 maozi 同源）。"""
    return {
        "sku": "1234567890",
        "brand": "ТестБренд",
        "soldCount": "1234",
        "soldSum": "98765.5",
        "salesDynamics": 12.5,
        "avgOrdersOnAccDays": 41,
        "avgGmvOnAccDays": 3292.2,
        "drr": 7.3,
        "daysInPromo": 5,
        "discount": 15.0,
        "promoRevenueShare": 0.22,
        "daysWithTrafarets": 3,
        "qtyViewPdp": 45678,
        "convToCartPdp": 4.11,
        "sessionCountSearch": 889900,
        "convToCartSearch": 1.33,
        "convViewToOrder": 0.87,
        "custom_click_rate": 3.2,
        "salesSchema": "FBO",
        "nullableRedemptionRate": 88.0,
        "custom_volume": "20x15x5",
        "custom_weight": 450,
        "nullableCreateDate": "2026-01-15",
        "createDays": 120,
        "followInfo": [{"seller_id": "1", "price": 900}],
        "followMinPrice": "900",
        "followMaxPrice": "1200",
        "soldSumCny": 7409.0,
        "soldSumRub": 98765.5,
        "rfbs_rate": {"rfbs_leq_1500": 7.5, "rfbs_leq_5000": 7.5, "rfbs_gt_5000": 7.5},
        "fbp_rate": {"fbp_leq_1500": 6.0, "fbp_leq_5000": 6.0, "fbp_gt_5000": 6.0},
        "category1Id": "17027492",
        "category2Id": "17029017",
        "category3Id": "91400",
        "attributes": [
            {"id": 4497, "value": "450"},
            {"id": 9454, "value": "200"},
            {"id": 9455, "value": "150"},
            {"id": 9456, "value": "50"},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. _extract_metrics 27 字段解析
# ─────────────────────────────────────────────────────────────────────────────
def test_extract_metrics_parses_all_maozi_fields():
    """毛子 27+ 字段全部解析为 snake_case 输出。"""
    m = osa._extract_metrics(_what_to_sell_item())

    # 基础 5 字段（既有行为回归）
    assert m["sold_count"] == 1234
    assert m["sales_dynamics"] == 12.5
    assert m["drr"] == 7.3
    assert m["create_days"] == 120
    assert m["return_rate"] == 12.0  # 100 - nullableRedemptionRate

    # 佣金（分段对象 → 中间段）
    assert m["commission_fbp"] == 6.0
    assert m["commission_rfbs"] == 7.5

    # 身份字段
    assert m["sku"] == "1234567890"
    assert m["brand"] == "ТестБренд"

    # 销售额（soldSum 双通道）
    assert m["sold_sum"] == 98765.5
    assert m["gmv_sum"] == 98765.5  # gmvSum 缺失时 fallback soldSum（既有行为）

    # 新增 maozi 字段（camelCase → snake_case）
    assert m["avg_orders_on_acc_days"] == 41
    assert m["avg_gmv_on_acc_days"] == 3292.2
    assert m["days_in_promo"] == 5
    assert m["discount"] == 15.0
    assert m["promo_revenue_share"] == 0.22
    assert m["days_with_trafarets"] == 3
    assert m["qty_view_pdp"] == 45678
    assert m["conv_to_cart_pdp"] == 4.11
    assert m["session_count_search"] == 889900
    assert m["conv_to_cart_search"] == 1.33
    assert m["conv_view_to_order"] == 0.87
    assert m["custom_click_rate"] == 3.2
    assert m["sales_schema"] == "FBO"
    assert m["nullable_redemption_rate"] == 88.0
    assert m["custom_volume"] == "20x15x5"
    assert m["custom_weight"] == 450
    assert m["nullable_create_date"] == "2026-01-15"
    assert m["follow_info"] == [{"seller_id": "1", "price": 900}]
    assert m["follow_min_price"] == 900.0
    assert m["follow_max_price"] == 1200.0
    assert m["sold_sum_cny"] == 7409.0
    assert m["sold_sum_rub"] == 98765.5

    # 重量/尺寸 attributes 既有行为回归
    assert m["weight_g"] == 450
    assert m["length_mm"] == 200
    assert m["width_mm"] == 150
    assert m["height_mm"] == 50

    # 类目权威 ID 回归
    assert m["category2_id"] == 17029017
    assert m["category3_id"] == 91400

    assert m["has_sales_data"] is True


def test_extract_metrics_missing_fields_safely_default():
    """字段缺失/None → 安全默认（不抛异常、不误报销量）。"""
    m = osa._extract_metrics({"sku": "x"})
    assert m["sold_count"] == 0
    assert m["avg_orders_on_acc_days"] == 0
    assert m["discount"] == 0.0
    assert m["follow_info"] == []
    assert m["follow_min_price"] == 0.0
    assert m["custom_click_rate"] == 0.0
    assert m["brand"] == ""
    assert m["sales_schema"] == ""
    assert m["has_sales_data"] is False


def test_extract_metrics_snake_case_input_also_works():
    """响应已为 snake_case（毛子部分字段）→ 同样解析。"""
    m = osa._extract_metrics({
        "sold_count": "10", "sold_sum": 500.0, "avg_orders_on_acc_days": 2,
        "custom_click_rate": 1.5, "follow_min_price": "100", "follow_max_price": "200",
    })
    assert m["sold_count"] == 10
    assert m["sold_sum"] == 500.0
    assert m["avg_orders_on_acc_days"] == 2
    assert m["custom_click_rate"] == 1.5
    assert m["follow_min_price"] == 100.0
    assert m["follow_max_price"] == 200.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. 昂贵抓取磁盘缓存（key 含语言维度）
# ─────────────────────────────────────────────────────────────────────────────
def test_fetch_sales_analytics_cache_hit_skips_fetch():
    """缓存命中（seller_analytics）→ 直接返回，不建 tab 不 evaluate。"""
    cached = {"111": {"sold_count": 42}}
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached) as cg:
        with mock.patch.object(osa, "_tab_for_seller") as tfs:
            with mock.patch.object(osa, "_close_seller_tab"):
                result = osa.fetch_sales_analytics(conn, ["111"])
    assert result == cached
    tfs.assert_not_called()
    # key 含语言维度
    key = cg.call_args[0][1]
    assert "111" in key and "zh-Hans" in key


def test_fetch_sales_analytics_cache_miss_fetches_and_sets():
    """缓存未命中 → fetch 并写缓存（仅结果非空时）。"""
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    tab.evaluate.return_value = json.dumps({"result": {"items": [_what_to_sell_item()]}})
    conn.find_tab.return_value = tab  # 真实 _tab_for_seller 路径：复用已打开 seller tab
    with mock.patch("scripts.lib.cache.cache_get", return_value=None):
        with mock.patch("scripts.lib.cache.cache_set") as cs:
            with mock.patch("time.sleep"):
                result = osa.fetch_sales_analytics(conn, ["1234567890"])
    assert "1234567890" in result
    cs.assert_called_once()
    args = cs.call_args
    assert args.args[0] == "seller_analytics"
    assert args.args[2] == result
    assert args.kwargs.get("ttl") == 21600


def test_fetch_competing_sellers_cache_hit_skips_fetch():
    """缓存命中（ozon_sellers）→ 直接返回，不 evaluate。"""
    cached = {"count": 2, "min_price": 900, "sellers": [{"sku": "1"}]}
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached) as cg:
        with mock.patch.object(ozon_widget, "_ensure_ozon_tab") as eot:
            result = ozon_widget.fetch_competing_sellers(
                "http://x", "999", cdp=conn)
    assert result == cached
    eot.assert_not_called()
    assert cg.call_args[0][1] == "999:ru", f"key 应含语言维度: {cg.call_args[0][1]}"


def test_fetch_competing_sellers_cache_only_with_results():
    """无结果（sellers 空）→ 不写缓存。"""
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    tab.evaluate.return_value = json.dumps({"count": 0, "min_price": 0, "sellers": []})
    with mock.patch("scripts.lib.cache.cache_get", return_value=None):
        with mock.patch("scripts.lib.cache.cache_set") as cs:
            with mock.patch.object(ozon_widget, "_ensure_ozon_tab", return_value=tab):
                result = ozon_widget.fetch_competing_sellers(
                    "http://x", "999", cdp=conn)
    assert result["sellers"] == []
    cs.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Ozon 价格货币归一化（CNY → RUB 唯一真源）
# ─────────────────────────────────────────────────────────────────────────────
def test_normalize_ozon_price_cny_converts_to_rub():
    """CNY → RUB 按固定汇率转换。"""
    assert normalize_ozon_price("¥119.00", "CNY") == round(119.0 * utils.CNY_TO_RUB, 2)


def test_normalize_ozon_price_rub_and_unknown_unchanged():
    """RUB / 未知货币 → 原样 parse_price（语义与 parse_price 一致）。"""
    assert normalize_ozon_price("1 234 ₽", "RUB") == 1234.0
    assert normalize_ozon_price("1 234 ₽", "") == 1234.0
    assert normalize_ozon_price("1 234 ₽", None) == 1234.0
    assert normalize_ozon_price("", "CNY") == 0.0


def test_normalize_ozon_price_case_insensitive_currency():
    """currency 大小写不敏感（cny/rub）。"""
    assert normalize_ozon_price("¥10", "cny") == round(10.0 * utils.CNY_TO_RUB, 2)
    assert normalize_ozon_price("100 ₽", "rub") == 100.0


def test_parse_price_semantics_unchanged():
    """parse_price 语义不变（1688 CNY 依赖它）— 回归。"""
    assert utils.parse_price("¥119.00") == 119.0
    assert utils.parse_price("327 ₽") == 327.0
    assert utils.parse_price("1 234,56") == 1234.56


def test_fetch_product_info_normalizes_cny_widget_price_to_rub():
    """widget 返回 CNY 价格 → fetch_product_info 归一化为 RUB + currency=RUB。"""
    widget_payload = json.dumps({
        "title": "Тест",
        "price": "¥119.00",
        "cardPrice": "¥109.00",
        "originalPrice": "¥199.00",
        "currency": "CNY",
    })
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    tab.evaluate.return_value = widget_payload
    with mock.patch.object(ozon_widget, "_ensure_ozon_tab", return_value=tab):
        with mock.patch("scripts.lib.cache.cache_get", return_value=None):
            with mock.patch("scripts.lib.cache.cache_set"):
                result = ozon_widget.fetch_product_info("http://x", "999", cdp=conn)
    expected = round(119.0 * utils.CNY_TO_RUB, 2)
    assert float(result["price"]) == expected, result["price"]
    assert float(result["originalPrice"]) == round(199.0 * utils.CNY_TO_RUB, 2)
    assert result["currency"] == "RUB"


def test_fetch_product_info_rub_widget_passthrough():
    """widget 已为 RUB → 价格原样保留，currency=RUB。"""
    widget_payload = json.dumps({
        "title": "Тест",
        "price": "1 234 ₽",
        "cardPrice": "",
        "originalPrice": "1 500 ₽",
        "currency": "RUB",
    })
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    tab.evaluate.return_value = widget_payload
    with mock.patch.object(ozon_widget, "_ensure_ozon_tab", return_value=tab):
        with mock.patch("scripts.lib.cache.cache_get", return_value=None):
            with mock.patch("scripts.lib.cache.cache_set"):
                result = ozon_widget.fetch_product_info("http://x", "999", cdp=conn)
    assert float(result["price"]) == 1234.0
    assert result["currency"] == "RUB"


# ─────────────────────────────────────────────────────────────────────────────
# 4. cloud_probe._cached_ozon_scrape 缓存包装
# ─────────────────────────────────────────────────────────────────────────────
def test_cached_ozon_scrape_cache_hit_skips_scrape():
    """缓存命中 → 不调 scrape_ozon_product_via_cdp。"""
    import scripts.cloud_probe as cp
    cached = {"success": True, "title": "cached", "images": []}
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached):
        with mock.patch("scripts.lib.ozon_scraper.scrape_ozon_product_via_cdp") as sc:
            result = cp._cached_ozon_scrape("https://www.ozon.ru/product/x-123/")
    assert result == cached
    sc.assert_not_called()


def test_cached_ozon_scrape_miss_fetches_and_caches_only_success():
    """未命中 → fetch；成功才写缓存。"""
    import scripts.cloud_probe as cp
    url = "https://www.ozon.ru/product/x-123/"
    with mock.patch("scripts.lib.cache.cache_get", return_value=None):
        with mock.patch("scripts.lib.cache.cache_set") as cs:
            with mock.patch("scripts.lib.ozon_scraper.scrape_ozon_product_via_cdp",
                            return_value={"success": True, "title": "ok"}) as sc:
                result = cp._cached_ozon_scrape(url, cdp_url="http://127.0.0.1:9222", timeout=30)
    assert result["success"] is True
    sc.assert_called_once()
    cs.assert_called_once_with("ozon_cdp", url, {"success": True, "title": "ok"}, ttl=21600)

    # 失败不写缓存
    with mock.patch("scripts.lib.cache.cache_get", return_value=None):
        with mock.patch("scripts.lib.cache.cache_set") as cs:
            with mock.patch("scripts.lib.ozon_scraper.scrape_ozon_product_via_cdp",
                            return_value={"success": False, "error": "x"}):
                cp._cached_ozon_scrape(url)
    cs.assert_not_called()


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

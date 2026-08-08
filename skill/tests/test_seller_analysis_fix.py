"""fetch_seller_analysis 双 bug 修复回归（v0.31 P1）— 签名 + 字段名 + cdp 复用。"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ozon_discovery
import scripts.lib.cdp_client as cdp_client
import scripts.lib.ozon_seller_analytics as ozon_seller_analytics
import scripts.lib.ozon_widget as ozon_widget


def _fake_cdp():
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    conn.new_tab.return_value = tab
    return conn, tab


def test_analysis_passes_cdp_object_as_first_arg():
    """Bug A: fetch_sales_analytics 必须收到 cdp 连接对象（第一个位置参数），而不是 product_ids list。"""
    conn, _ = _fake_cdp()
    with mock.patch.object(ozon_discovery, "fetch_seller_products",
                           return_value=["111", "222"]) as fsp:
        with mock.patch.object(ozon_seller_analytics, "fetch_sales_analytics",
                               return_value={}) as fsa:
            with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
                conn_cls.return_value = conn
                ozon_discovery.fetch_seller_analysis(
                    seller_id="999", max_products=10, max_skus=10)
                call = fsa.call_args
                assert call.args[0] is conn, f"第一个位置参数应为 cdp 连接对象，got {type(call.args[0])}"
                assert call.args[1] == ["111", "222"], f"第二个参数应为 SKU 列表，got {call.args[1]}"
                assert "cdp_url" not in call.kwargs, f"不应有 cdp_url kwarg，got {call.kwargs}"


def test_analysis_reads_snake_case_metric_fields():
    """Bug B: 读取 _extract_metrics 的 snake_case 键（sold_count/gmv_sum/...），非 camelCase。"""
    conn, _ = _fake_cdp()
    metrics = {
        "111": {
            "sold_count": 42,
            "gmv_sum": 1050.5,
            "sales_dynamics": 3.5,
            "drr": 7.0,
            "create_days": 20,
            "category_name": "Товары для животных",
        }
    }
    with mock.patch.object(ozon_discovery, "fetch_seller_products",
                           return_value=["111"]):
        with mock.patch.object(ozon_seller_analytics, "fetch_sales_analytics",
                               return_value=metrics):
            with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
                conn_cls.return_value = conn
                result = ozon_discovery.fetch_seller_analysis(
                    seller_id="999", max_products=10, max_skus=10)
                p = result["products"][0]
                assert p["monthly_sales"] == 42, f"monthly_sales 应为 42（sold_count），got {p['monthly_sales']}"
                assert p["monthly_revenue"] == 1050.5, f"monthly_revenue 应为 1050.5（gmv_sum），got {p['monthly_revenue']}"
                assert p["sales_dynamics"] == 3.5, f"sales_dynamics got {p['sales_dynamics']}"
                assert p["drr"] == 7.0, f"drr got {p['drr']}"
                assert p["create_days"] == 20, f"create_days got {p['create_days']}"
                assert p["category"] == "Товары для животных", f"category got {p['category']}"


def test_analysis_cdp_none_builds_own_connection():
    """cdp=None 时自建 CdpConnection 并 close（现有 cmd_seller 路径不变）。"""
    conn, _ = _fake_cdp()
    with mock.patch.object(ozon_discovery, "fetch_seller_products",
                           return_value=["111"]):
        with mock.patch.object(ozon_seller_analytics, "fetch_sales_analytics",
                               return_value={}):
            with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
                conn_cls.return_value = conn
                ozon_discovery.fetch_seller_analysis(seller_id="999")
                conn_cls.assert_called_once()
                conn.close.assert_called_once()


def test_analysis_cdp_provided_not_closed():
    """外部传入 cdp 连接时不自建也不 close（连接归调用方管理）。"""
    conn, _ = _fake_cdp()
    with mock.patch.object(ozon_discovery, "fetch_seller_products",
                           return_value=["111"]):
        with mock.patch.object(ozon_seller_analytics, "fetch_sales_analytics",
                               return_value={}):
            with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
                ozon_discovery.fetch_seller_analysis(seller_id="999", cdp=conn)
                conn_cls.assert_not_called()
                conn.close.assert_not_called()


def test_analysis_empty_products_returns_early():
    """店铺无产品 → 直接返回空结果，不调 analytics。"""
    conn, _ = _fake_cdp()
    with mock.patch.object(ozon_discovery, "fetch_seller_products",
                           return_value=[]):
        with mock.patch.object(ozon_seller_analytics, "fetch_sales_analytics") as fsa:
            with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
                conn_cls.return_value = conn
                result = ozon_discovery.fetch_seller_analysis(seller_id="999")
                fsa.assert_not_called()
                assert result["product_count"] == 0
                assert result["products"] == []


def test_seller_products_reuses_provided_cdp():
    """fetch_seller_products(cdp=...) 复用 _ensure_ozon_tab，不自建新 CdpConnection、不 new_tab。"""
    conn, tab = _fake_cdp()
    with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
        with mock.patch.object(ozon_discovery, "_lazy_collect_urls",
                               return_value=["111", "222"]) as lcu:
            with mock.patch.object(ozon_widget, "_ensure_ozon_tab",
                                   return_value=tab) as ensure:
                pids = ozon_discovery.fetch_seller_products(
                    seller_id="999", max_products=10, cdp=conn)
            ensure.assert_called_once()
            conn_cls.assert_not_called()
            conn.new_tab.assert_not_called()
            tab.close.assert_not_called()  # 复用已有 tab，不关（跨卖家复用）
            conn.close.assert_not_called()
            assert pids == ["111", "222"]


def test_seller_products_no_cdp_self_manage():
    """fetch_seller_products 无 cdp/tab → 自建连接并 close（现有路径保持）。"""
    conn, tab = _fake_cdp()
    with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
        conn_cls.return_value = conn
        with mock.patch.object(ozon_discovery, "_lazy_collect_urls",
                               return_value=["111"]):
            with mock.patch.object(ozon_widget, "_ensure_ozon_tab",
                                   return_value=tab):
                ozon_discovery.fetch_seller_products(seller_id="999", max_products=10)
            conn_cls.assert_called_once()
            conn.close.assert_called_once()


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

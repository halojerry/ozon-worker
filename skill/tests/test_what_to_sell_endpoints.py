"""what-to-sell SPA 三页端点 fetch 函数回归（v0.33.2 C4 step1）。

覆盖：签名/解析/premium unlock/降级/CSV 导出。mock fetch 与 CDP，不依赖真实网络。
"""
from __future__ import annotations

import io
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))

import scripts.lib.ozon_seller_analytics as osa


def _fake_tab(raw_payload: str):
    tab = mock.MagicMock()
    tab.evaluate.return_value = raw_payload
    return tab


def _mock_ok_response(data: dict) -> str:
    import json
    return json.dumps(data)


def test_signatures_exist_and_params_correct():
    """三个 fetch 函数签名存在且参数名/默认值正确。"""
    import inspect
    sig = inspect.signature(osa.fetch_all_queries)
    params = list(sig.parameters)
    assert params == ["cdp", "keyword", "company_id"], f"all-queries 签名: {params}"
    assert sig.parameters["keyword"].default is None
    assert sig.parameters["company_id"].default is None

    sig = inspect.signature(osa.fetch_ozon_bestsellers)
    params = list(sig.parameters)
    assert params == ["cdp", "sku_or_id", "company_id"], f"ozon-bestsellers 签名: {params}"

    sig = inspect.signature(osa.fetch_market_bestsellers)
    params = list(sig.parameters)
    assert params == ["cdp", "category_id", "price_rub_min", "price_rub_max", "company_id"], \
        f"market-bestsellers 签名: {params}"
    assert sig.parameters["price_rub_min"].default is None
    assert sig.parameters["price_rub_max"].default is None


def test_fetch_all_queries_parses_success():
    """fetch_all_queries 解析成功（data.data[] 结构 → list[dict]）。"""
    resp = _mock_ok_response({"data": {"data": [
        {"query": "поилка", "count": 9494, "ca": 27.14, "avgCaRub": 1585,
         "uniqSellers": 30, "ord": 920, "gmv": 1385552,
         "uniqQueriesWCa": 2577, "searchUsersToOrdUsers": 9.41},
    ]}})
    tab = _fake_tab(resp)
    with mock.patch.object(osa, "_tab_for_seller", return_value=(tab, True)):
        with mock.patch.object(osa, "_read_company_id", return_value="5371047"):
            rows = osa.fetch_all_queries(mock.MagicMock())
    assert len(rows) == 1
    row = rows[0]
    assert row["query"] == "поилка"
    assert row["count"] == 9494
    assert row["ca"] == 27.14
    assert row["avg_ca_rub"] == 1585
    assert row["uniq_sellers"] == 30
    assert row["ordering_amount"] == 920
    assert row["gmv"] == 1385552


def test_fetch_ozon_bestsellers_parses_success():
    """fetch_ozon_bestsellers 解析成功（data.items[] → list[dict]）。"""
    resp = _mock_ok_response({"data": {"items": [
        {"sku": "7969279", "name": "SvetoCopy Paper", "brand": "SvetoCopy",
         "soldCount": "15136", "gmvSum": 6419792.282, "salesDynamics": 18,
         "sessionCountSearch": "773518", "convToCartSearch": 1.33,
         "category1Id": "17027492", "category2Id": "17029017", "category3Id": "91400",
         "attributes": []},
    ]}})
    tab = _fake_tab(resp)
    with mock.patch.object(osa, "_tab_for_seller", return_value=(tab, True)):
        with mock.patch.object(osa, "_read_company_id", return_value="5371047"):
            rows = osa.fetch_ozon_bestsellers(mock.MagicMock())
    assert len(rows) == 1
    row = rows[0]
    assert row["sku"] == "7969279"
    assert row["name"] == "SvetoCopy Paper"
    assert row["sold_count"] == 15136
    assert row["gmv_sum"] == 6419792.282
    assert row["category2_id"] == 17029017


def test_fetch_market_bestsellers_parses_success_with_filters():
    """fetch_market_bestsellers 解析成功，且 category_id/price 拼进 JS。"""
    resp = _mock_ok_response({"data": {"items": [
        {"sku": "2811310229", "name": "Xiaomi Redmi 15", "brand": "Xiaomi",
         "soldCount": "5467", "gmvSum": 82908845.527,
         "category1Id": "15621042", "category2Id": "15621050",
         "attributes": []},
    ]}})
    tab = _fake_tab(resp)
    with mock.patch.object(osa, "_tab_for_seller", return_value=(tab, True)):
        with mock.patch.object(osa, "_read_company_id", return_value="5371047"):
            rows = osa.fetch_market_bestsellers(
                mock.MagicMock(), category_id=286, price_rub_min=500, price_rub_max=2000)
    assert len(rows) == 1
    assert rows[0]["sku"] == "2811310229"
    js = tab.evaluate.call_args[0][0]
    assert '"minPrice":"500"' in js
    assert '"maxPrice":"2000"' in js
    assert '["286"]' in js
    assert 'PLATFORM_ALL' in js


def test_premium_unlock_injected_on_reused_tab():
    """复用 seller tab 时 _install_premium_unlock 被调用（真实 _tab_for_seller 路径）。"""
    resp = _mock_ok_response({"data": {"data": []}})
    tab = _fake_tab(resp)
    conn = mock.MagicMock()
    conn.find_tab.return_value = tab
    with mock.patch.object(osa, "_read_company_id", return_value="5371047"):
        with mock.patch.object(osa, "_install_premium_unlock") as ipu:
            osa.fetch_all_queries(conn)
    ipu.assert_called_once()


def test_tab_fetch_failure_returns_empty():
    """tab 获取失败 → 返回 [] 不抛异常。"""
    with mock.patch.object(osa, "_tab_for_seller", side_effect=Exception("CDP down")):
        with mock.patch.object(osa, "_close_seller_tab"):
            assert osa.fetch_all_queries(mock.MagicMock()) == []
            assert osa.fetch_ozon_bestsellers(mock.MagicMock()) == []
            assert osa.fetch_market_bestsellers(mock.MagicMock()) == []


def test_company_id_missing_degrades_to_empty():
    """company_id 缺失（未登录）→ 返回 []，不调 evaluate。"""
    tab = _fake_tab("")
    with mock.patch.object(osa, "_tab_for_seller", return_value=(tab, True)):
        with mock.patch.object(osa, "_read_company_id", return_value=""):
            assert osa.fetch_all_queries(mock.MagicMock()) == []
    tab.evaluate.assert_not_called()


def test_parse_query_items_empty_and_error_payload():
    """空响应 / error 负载 → 空 list。"""
    assert osa._parse_query_items({}) == []
    assert osa._parse_query_items({"data": {"data": []}}) == []
    assert osa._parse_bestseller_items({"data": {"items": []}}) == []
    assert osa._parse_query_items({"result": {"items": [{"query": "x", "count": "10"}]}})[0]["count"] == 10


def test_csv_export_format():
    """CSV 导出：表头 + 行数正确（utf-8-sig 编码）。"""
    from scripts.cli import cmd_queries
    import argparse

    rows = [{"query": "поилка", "count": 5, "ca": 1.5}, {"query": "миска", "count": 3, "ca": 2.5}]
    out_path = "/tmp/test_queries_export.csv"
    try:
        with mock.patch.object(osa, "check_seller_login", return_value=True):
            with mock.patch.object(osa, "fetch_all_queries", return_value=rows):
                with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
                    conn_cls.return_value = mock.MagicMock()
                    # C5 todo8 接线后 cmd_queries 会上报采集数据——测试里打桩防真实网络请求
                    with mock.patch("scripts.lib.analytics_upload.upload_in_background"):
                        args = argparse.Namespace(
                            type="all-queries", keyword="поилка", sku="", category_id="",
                            price_min=None, price_max=None, export="csv", output=out_path)
                        rc = cmd_queries(args)
        assert rc == 0
        raw = open(out_path, "rb").read()
        assert raw.startswith(b"\xef\xbb\xbf"), "CSV 应带 utf-8-sig BOM"
        text = raw.decode("utf-8-sig")
        lines = text.strip().splitlines()
        assert lines[0] == "query,count,ca"
        assert len(lines) == 3  # 表头 + 2 行
    finally:
        import os as _os
        if _os.path.exists(out_path):
            _os.remove(out_path)


def test_cmd_queries_not_logged_in_prints_and_returns_0():
    """未登录 seller.ozon.ru → 打印提示并返回 0（不崩）。"""
    from scripts.cli import cmd_queries
    import argparse

    with mock.patch.object(osa, "check_seller_login", return_value=False):
        with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
            conn_cls.return_value = mock.MagicMock()
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                args = argparse.Namespace(
                    type="all-queries", keyword="", sku="", category_id="",
                    price_min=None, price_max=None, export="csv", output="")
                rc = cmd_queries(args)
    assert rc == 0
    assert "未登录" in out.getvalue()


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

# skill/tests/test_metrics_pool_giveback.py
"""读-回馈贡献钩子（Task 2.2，goldminer 模式）：消费 what_to_sell 畅销榜数据时
顺手上报数据池（fire-and-forget）。

锁定三件事：
1. `_giveback_metrics` 把 (sku, item) 对塑形成 {"sku", "sales_payload"} 并委托
   metrics_pool_client.report_seller_sync；
2. 钩子吞一切异常（client 内部吞 + 本地双保险），绝不影响富化/查询主流程；
3. 两个消费现场真的挂了钩：`_enrich_with_seller_metrics`（map 物化处，每收获
   恰一次）与 `cli.cmd_queries`（ozon-bestsellers 采集成功后）。
纯 mock，不触网、不启 Chrome。
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib import metrics_pool_client as mpc  # noqa: E402
from scripts.lib import ozon_discovery as od  # noqa: E402


def test_giveback_shapes_items_and_delegates(monkeypatch):
    """(sku, item) 对 → [{"sku", "sales_payload"}] 原样委托 report_seller_sync。"""
    seen = {}

    def fake_report(items, **kwargs):
        seen["items"] = items
        return len(items)

    monkeypatch.setattr(mpc, "report_seller_sync", fake_report)
    od._giveback_metrics([("3171397439", {"monthsales": 140})])
    assert seen["items"] == [
        {"sku": "3171397439", "sales_payload": {"monthsales": 140}},
    ]


def test_giveback_swallows_client_errors(monkeypatch):
    """client 抛错（网络炸/未配置 token）不逃逸出钩子——贡献永不阻断主流程。"""

    def boom(items, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(mpc, "report_seller_sync", boom)
    od._giveback_metrics([("1", {"monthsales": 1})])  # 不抛即通过
    od._giveback_metrics([])  # 空收获同样安全


def test_enrich_path_triggers_giveback(monkeypatch):
    """enrich 拿到非空畅销榜 map（cookie 直调命中）→ 钩子被调恰一次，且富化
    返回值零变化（副作用纪律）。"""
    from scripts.lib import ozon_seller_analytics as osa

    fake_map = {"3171397439": {"monthsales": 140, "gmv_sum": 570000.0}}
    candidate = od.ProductCandidate(
        ozon_product_id="3171397439", ozon_title="测试商品", ozon_price=570.0,
        ozon_url="https://www.ozon.ru/product/3171397439")

    # 直采缝：cookie 直调成功 → 走 map 快路径，永不触 CDP（cdp=None 也安全）
    monkeypatch.setattr(osa, "get_seller_session_cookies",
                        lambda cdp_url: {"sc_company_id": "1"})
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map_direct",
                        lambda cookies, lang="zh-Hans": dict(fake_map))
    monkeypatch.setattr(osa, "apply_analytics_to_candidate", lambda c, m: True)
    calls: list = []
    monkeypatch.setattr(od, "_giveback_metrics",
                        lambda items: calls.append(list(items)))

    enriched = od._enrich_with_seller_metrics(
        [candidate], None, "http://127.0.0.1:9222")

    assert enriched == fake_map  # 行为零变化
    assert len(calls) == 1  # 每收获恰一次
    assert calls[0] == [("3171397439", fake_map["3171397439"])]


def test_queries_command_triggers_giveback():
    """cmd_queries 源码含 _giveback_metrics 调用（CLI 侧钩子在场，防回退）。"""
    from scripts import cli

    src = inspect.getsource(cli.cmd_queries)
    assert "_giveback_metrics" in src

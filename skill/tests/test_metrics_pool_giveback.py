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

    # Task 2.3：enrich 入口先查数据池——本用例测 CDP 路径，池按不可用（None）
    # 处理，让候选原样回落直采（否则 token 已配置的机器会真实打 worker）。
    monkeypatch.setattr(mpc, "query_sku_metrics", lambda skus, **kw: None)
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


# ---------------------------------------------------------------------------
# Task 2.3: discover 富化池优先（池命中免 CDP 直采；未命中行为不变）
#
# 字段勘误（对照真实代码，brief 示例字段名不存在的以实现为准）：
# - 候选 sku 字段是 `ozon_product_id`（ProductCandidate 无 ozon_sku/sku_id），
#   与 CDP 畅销榜 map 的键同词汇（map 以 sku 为键、富化循环按
#   c.ozon_product_id 查）——池查询键即它。
# - 池行 sales_payload 与 CDP map 行同词汇表（_giveback_metrics 上报的正是
#   metrics_map 行 = _extract_metrics 产物），月销量走 apply_analytics_to_candidate
#   的 sold_count → monthly_sales 同一映射函数，非 brief 示例的 monthsales。
# ---------------------------------------------------------------------------


def _pool_metric(sales_payload, **extra):
    """worker GET /analytics/sku-metrics 行 shape（Task 1.2 契约）的测试模具。"""
    metric = {
        "sku": "", "sales_payload": sales_payload, "variant_payload": None,
        "category_dc": None, "category_tp": None, "category_name_zh": None,
        "needs_sales_sync": False, "needs_variant_sync": False,
        "updated_at": None,
    }
    metric.update(extra)
    return metric


def test_enrich_pool_hit_fills_candidate(monkeypatch):
    """池命中：候选用池里 sales_payload 走与 CDP 富化同一映射函数填漏斗字段
    （has_analytics 置位），且全命中整段跳过 CDP 直采（cookie 直调/CDP map
    均不触——这就是本任务的赢面）。"""
    from scripts.lib import ozon_seller_analytics as osa

    payload = {"sku": "3171397439", "sold_count": 140, "gmv_sum": 570000.0,
               "has_sales_data": True}
    pool = {"3171397439": _pool_metric(payload, sku=3171397439)}
    seen_skus: list[list[str]] = []

    def fake_query(skus, **kw):
        seen_skus.append(list(skus))
        return pool

    monkeypatch.setattr(mpc, "query_sku_metrics", fake_query)
    # CDP 缝断言：全命中必须零触——任一被调即 AssertionError（真实映射函数
    # apply_analytics_to_candidate 不 mock，映射正确性一并锁定）。
    monkeypatch.setattr(osa, "get_seller_session_cookies",
                        lambda url: (_ for _ in ()).throw(
                            AssertionError("池全命中不应触 cookie 直调")))
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: (_ for _ in ()).throw(
                            AssertionError("池全命中不应触 CDP map")))

    cand = od.ProductCandidate(ozon_product_id="3171397439",
                               ozon_title="测试商品", ozon_price=570.0)
    enriched = od._enrich_with_seller_metrics(
        [cand], None, "http://127.0.0.1:9222")

    assert seen_skus == [["3171397439"]]      # 用候选 sku 查池
    assert cand.has_analytics is True
    assert cand.monthly_sales == 140          # 漏斗字段 = 池 payload 值
    assert cand.monthly_revenue == 570000.0
    assert enriched == {"3171397439": payload}  # 返回值与 CDP 富化同构


def test_enrich_pool_miss_falls_back_to_cdp(monkeypatch):
    """池零命中（{}）→ 候选原样走既有 CDP 直采路径并照常富化（行为不变）。"""
    from scripts.lib import ozon_seller_analytics as osa

    monkeypatch.setattr(mpc, "query_sku_metrics", lambda skus, **kw: {})
    cdp_map = {"555": {"sku": "555", "sold_count": 7, "gmv_sum": 21000.0,
                       "has_sales_data": True}}
    monkeypatch.setattr(osa, "get_seller_session_cookies", lambda url: {})
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: True)
    monkeypatch.setattr(osa, "wait_for_seller_login", lambda cdp, **kw: True)
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: dict(cdp_map))

    cand = od.ProductCandidate(ozon_product_id="555", ozon_title="t",
                               ozon_price=700.0)
    enriched = od._enrich_with_seller_metrics(
        [cand], object(), "http://127.0.0.1:9222")

    assert cand.has_analytics is True
    assert cand.monthly_sales == 7
    assert enriched == cdp_map


def test_enrich_pool_none_behaves_as_today(monkeypatch):
    """池不可用（None = worker 挂/未配置 token）→ 与今天逐字一致（CDP 直采）。"""
    from scripts.lib import ozon_seller_analytics as osa

    monkeypatch.setattr(mpc, "query_sku_metrics", lambda skus, **kw: None)
    cdp_map = {"666": {"sku": "666", "sold_count": 9, "has_sales_data": True}}
    monkeypatch.setattr(osa, "get_seller_session_cookies", lambda url: {})
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: True)
    monkeypatch.setattr(osa, "wait_for_seller_login", lambda cdp, **kw: True)
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: dict(cdp_map))

    cand = od.ProductCandidate(ozon_product_id="666", ozon_title="t",
                               ozon_price=900.0)
    enriched = od._enrich_with_seller_metrics(
        [cand], object(), "http://127.0.0.1:9222")

    assert cand.has_analytics is True
    assert cand.monthly_sales == 9
    assert enriched == cdp_map


def test_enrich_pool_partial_hit_remaining_goes_cdp(monkeypatch):
    """部分命中：命中者用池数据，未命中者照旧走 CDP map——池数据不被覆盖。"""
    from scripts.lib import ozon_seller_analytics as osa

    pool_payload = {"sku": "111", "sold_count": 50, "has_sales_data": True}
    monkeypatch.setattr(mpc, "query_sku_metrics",
                        lambda skus, **kw: {"111": _pool_metric(pool_payload,
                                                                sku=111)})
    monkeypatch.setattr(osa, "get_seller_session_cookies", lambda url: {})
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: True)
    monkeypatch.setattr(osa, "wait_for_seller_login", lambda cdp, **kw: True)
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: {"222": {"sku": "222", "sold_count": 9,
                                                   "has_sales_data": True}})

    c_pool = od.ProductCandidate(ozon_product_id="111", ozon_title="a",
                                 ozon_price=100.0)
    c_cdp = od.ProductCandidate(ozon_product_id="222", ozon_title="b",
                                ozon_price=200.0)
    enriched = od._enrich_with_seller_metrics(
        [c_pool, c_cdp], object(), "http://127.0.0.1:9222")

    assert c_pool.monthly_sales == 50 and c_pool.has_analytics
    assert c_cdp.monthly_sales == 9 and c_cdp.has_analytics
    assert set(enriched) == {"111", "222"}


def test_pool_hit_wires_category_name_zh(monkeypatch):
    """池行带非 None category_name_zh → 写入候选 category（CSV/Excel「类目」
    展示列；CDP 路径此列是数字 dc，池数据有人话名优先人话名）。"""
    from scripts.lib import ozon_seller_analytics as osa

    payload = {"sku": "333", "sold_count": 20, "has_sales_data": True}
    monkeypatch.setattr(mpc, "query_sku_metrics",
                        lambda skus, **kw: {"333": _pool_metric(
                            payload, sku=333,
                            category_name_zh="美容和卫生 > 洗发水")})
    monkeypatch.setattr(osa, "get_seller_session_cookies",
                        lambda url: (_ for _ in ()).throw(
                            AssertionError("池全命中不应触 cookie 直调")))
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: (_ for _ in ()).throw(
                            AssertionError("池全命中不应触 CDP map")))

    cand = od.ProductCandidate(ozon_product_id="333", ozon_title="t",
                               ozon_price=300.0)
    od._enrich_with_seller_metrics([cand], None, "http://127.0.0.1:9222")

    assert cand.category == "美容和卫生 > 洗发水"


# ---------------------------------------------------------------------------
# 终审修复（2026-09-10）：陈旧池行（needs_sales_sync=True）不得抑制 CDP 刷新。
# 裁定：字段照填（聊胜于无），但不登记 enriched / 不计命中——候选留在
# remaining，CDP 直采照跑。
# ---------------------------------------------------------------------------


def test_enrich_pool_stale_hit_fills_but_routes_cdp(monkeypatch):
    """陈旧池行 + CDP map 有该 sku：池值先填、CDP 刷新值覆盖——enriched 登记
    CDP 行（非池行），且 giveback 照常发生（CDP map 物化未被跳过）。"""
    from scripts.lib import ozon_seller_analytics as osa

    stale_payload = {"sku": "777", "sold_count": 3, "has_sales_data": True}
    monkeypatch.setattr(mpc, "query_sku_metrics",
                        lambda skus, **kw: {"777": _pool_metric(
                            stale_payload, sku=777, needs_sales_sync=True)})
    monkeypatch.setattr(osa, "get_seller_session_cookies", lambda url: {})
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: True)
    monkeypatch.setattr(osa, "wait_for_seller_login", lambda cdp, **kw: True)
    cdp_map = {"777": {"sku": "777", "sold_count": 12, "has_sales_data": True}}
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: dict(cdp_map))
    monkeypatch.setattr(mpc, "report_seller_sync",
                        lambda items, **kw: len(items))  # giveback 不触网

    cand = od.ProductCandidate(ozon_product_id="777", ozon_title="t",
                               ozon_price=500.0)
    enriched = od._enrich_with_seller_metrics(
        [cand], object(), "http://127.0.0.1:9222")

    assert cand.monthly_sales == 12      # CDP 刷新值，不是池的陈旧 3
    assert enriched == cdp_map           # enriched 登记的是 CDP 行


def test_enrich_pool_stale_hit_keeps_fields_when_cdp_misses(monkeypatch):
    """陈旧池行 + CDP 全程无该 sku：池值保留（聊胜于无），但 enriched 不登记
    池行（返回值保持「池未命中」语义）。"""
    from scripts.lib import ozon_seller_analytics as osa

    stale_payload = {"sku": "888", "sold_count": 3, "has_sales_data": True}
    monkeypatch.setattr(mpc, "query_sku_metrics",
                        lambda skus, **kw: {"888": _pool_metric(
                            stale_payload, sku=888, needs_sales_sync=True)})
    monkeypatch.setattr(osa, "get_seller_session_cookies", lambda url: {})
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: True)
    monkeypatch.setattr(osa, "wait_for_seller_login", lambda cdp, **kw: True)
    monkeypatch.setattr(osa, "fetch_bestseller_metrics_map",
                        lambda cdp, **kw: {})            # CDP map 未命中
    monkeypatch.setattr(osa, "fetch_sales_analytics",
                        lambda cdp, pids, **kw: {})      # 逐 SKU 降级也未命中
    monkeypatch.setattr(mpc, "report_seller_sync", lambda items, **kw: 0)

    cand = od.ProductCandidate(ozon_product_id="888", ozon_title="t",
                               ozon_price=500.0)
    enriched = od._enrich_with_seller_metrics(
        [cand], object(), "http://127.0.0.1:9222")

    assert cand.monthly_sales == 3       # 陈旧池值仍在（数据聊胜于无）
    assert cand.has_analytics is True
    assert enriched == {}                # 但池侧不登记——不算命中

"""P2: discover --blue-ocean-source queries + --keyword → 实时 what_to_sell 查询。

- seller 已登录 + fetch_all_queries 返回行 → blue_ocean_rows 来自实时数据
  （stdout "蓝海增强: 载入 N 个关键词（what_to_sell 实时查询）" + 评分注入）
- 未登录 / 异常 / 空结果 → 静默降级本地 CSV，绝不崩
- fetch_all_queries 复用 cmd_queries 同款 seller tab 机制（check_seller_login）
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate


def _mk(pid="p1"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title="Автопоилка для кошек",
                         ozon_price=1500.0)
    c.status = "ok"
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _write_csv(path: str, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(r) + "\n")


class _FakeCdp:
    """CdpConnection 假实现（真实 __enter__，避免 MagicMock 返回新 mock）。"""

    instances: list["_FakeCdp"] = []

    def __init__(self, *a, **k):
        _FakeCdp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _discover_args(**overrides):
    import argparse
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="", export="", output="", auto_submit=False,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="queries", blue_ocean_csv="",
        china=False, local=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_discover(args, candidates, selected, live_patches=()):
    """跑完整 cmd_discover，采集/matching mock，live 查询链路透传。

    返回 (rc, stdout)。
    """
    from scripts import cli
    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=candidates),
        mock.patch("scripts.cli._interactive_select", return_value=selected),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.cdp_client.CdpConnection", side_effect=_FakeCdp),
    ] + list(live_patches)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    return rc, out.getvalue()


def _live_rows():
    return [
        {"query": "поилка", "count": 9494, "ca": 27.14, "avg_ca_rub": 1585.0,
         "uniq_sellers": 30, "ordering_amount": 920, "daily_avg": 130,
         "gmv": 1385552.0, "uniq_queries_w_ca": 12,
         "search_users_to_ord_users": 1.8},
        {"query": "миска", "count": 500, "ca": 10.0, "avg_ca_rub": 200.0,
         "uniq_sellers": 3, "ordering_amount": 50, "daily_avg": 7,
         "gmv": 1000.0, "uniq_queries_w_ca": 2, "search_users_to_ord_users": 0.5},
    ]


def test_live_queries_populate_blue_ocean_rows():
    """seller 登录 + fetch_all_queries 有结果 → blue_ocean_rows 来自实时数据。"""
    from scripts import cli
    c1 = _mk("p1")
    c1.status = "ok"
    args = _discover_args(blue_ocean_source="queries", keyword="поилка")
    c = mock.patch("scripts.lib.ozon_seller_analytics.check_seller_login",
                   return_value=True)
    with c, \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                    return_value=[c1]), \
         mock.patch("scripts.cli._interactive_select", return_value=[c1]), \
         mock.patch("scripts.lib.ozon_discovery.match_selected"), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}), \
         mock.patch("scripts.lib.cdp_client.CdpConnection", side_effect=_FakeCdp), \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_all_queries",
                    return_value=_live_rows()) as f:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    assert rc == 0
    assert f.call_args.kwargs.get("keyword") == "поилка", \
        f"fetch_all_queries 应带 keyword, got {f.call_args}"
    assert "蓝海增强: 载入 2 个关键词（what_to_sell 实时查询）" in out.getvalue(), \
        "实时行应进入 blue_ocean_rows"
    assert "no blue_ocean data" not in out.getvalue()
    # 实时行被消费：候选标题命中 query "поилка" → density 注入 → 评分 > 0
    assert c1.blue_ocean_score > 0, "实时蓝海行应注入候选评分"
    assert c1.blue_ocean_score <= 100


def test_live_queries_falls_back_to_csv_when_not_logged_in():
    """seller 未登录 → 警告 + 降级本地 CSV（有数据仍增强）。"""
    c1 = _mk("p1")
    c1.status = "ok"
    path = os.path.join(tempfile.mkdtemp(), "queries_all.csv")
    _write_csv(path, ["query", "count", "ca", "uniq_sellers"],
               [["поилка", "9494", "27.14", "30"]])
    args = _discover_args(blue_ocean_source="queries", keyword="поилка",
                          blue_ocean_csv=path)
    rc, out = _run_discover(
        args, [c1], [c1],
        live_patches=[
            mock.patch("scripts.lib.ozon_seller_analytics.check_seller_login",
                       return_value=False),
            mock.patch("scripts.lib.ozon_seller_analytics.fetch_all_queries",
                       return_value=[]),
        ],
    )
    assert rc == 0, "未登录绝不能崩"
    assert "蓝海增强: 载入 1 个关键词" in out, "应降级本地 CSV 并继续增强"
    assert "what_to_sell 实时查询" not in out


def test_live_queries_falls_back_to_csv_on_exception():
    """fetch_all_queries 抛异常 → 静默降级本地 CSV（有数据仍增强）。"""
    c1 = _mk("p1")
    c1.status = "ok"
    path = os.path.join(tempfile.mkdtemp(), "queries_all.csv")
    _write_csv(path, ["query", "count", "ca", "uniq_sellers"],
               [["поилка", "9494", "27.14", "30"]])
    args = _discover_args(blue_ocean_source="queries", keyword="поилка",
                          blue_ocean_csv=path)
    rc, out = _run_discover(
        args, [c1], [c1],
        live_patches=[
            mock.patch("scripts.lib.ozon_seller_analytics.check_seller_login",
                       return_value=True),
            mock.patch("scripts.lib.ozon_seller_analytics.fetch_all_queries",
                       side_effect=RuntimeError("seller fetch timeout")),
        ],
    )
    assert rc == 0, "异常绝不能崩"
    assert "蓝海增强: 载入 1 个关键词" in out, "应降级本地 CSV 并继续增强"


def test_live_queries_falls_back_when_empty():
    """fetch_all_queries 返回空 → 降级本地 CSV；CSV 也空 → fallback 原流程提示。"""
    c1 = _mk("p1")
    c1.status = "ok"
    args = _discover_args(blue_ocean_source="queries", keyword="поилка",
                          blue_ocean_csv="/tmp/__no_such_blue_ocean__.csv")
    rc, out = _run_discover(
        args, [c1], [c1],
        live_patches=[
            mock.patch("scripts.lib.ozon_seller_analytics.check_seller_login",
                       return_value=True),
            mock.patch("scripts.lib.ozon_seller_analytics.fetch_all_queries",
                       return_value=[]),
        ],
    )
    assert rc == 0
    assert "no blue_ocean data, fallback to original" in out, \
        "实时空 + CSV 空 → 原流程提示"
    assert c1.blue_ocean_score == 0


def test_live_queries_not_triggered_without_keyword():
    """queries 源但无 --keyword → 直接走本地 CSV（不触发实时查询）。"""
    c1 = _mk("p1")
    c1.status = "ok"
    path = os.path.join(tempfile.mkdtemp(), "queries_all.csv")
    _write_csv(path, ["query", "count", "ca", "uniq_sellers"],
               [["поилка", "9494", "27.14", "30"]])
    args = _discover_args(blue_ocean_source="queries", keyword="",
                          blue_ocean_csv=path)
    rc, out = _run_discover(
        args, [c1], [c1],
        live_patches=[
            mock.patch("scripts.lib.ozon_seller_analytics.check_seller_login",
                       return_value=True),
            mock.patch("scripts.lib.ozon_seller_analytics.fetch_all_queries",
                       return_value=_live_rows()),
        ],
    )
    assert rc == 0
    assert "蓝海增强: 载入 1 个关键词" in out, "无 keyword → 只走 CSV"
    assert "what_to_sell 实时查询" not in out


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

"""P1a: discover --china 标志 — parser 解析 + cmd_discover 透传 collect_and_analyze。

- parser（main() 内联构造）: --china → args.china=True；缺省 → False
- cmd_discover: 把 china=args.china 传给 collect_and_analyze（T7 已实现 china 路由）
"""
from __future__ import annotations

import io
import os
import sys
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


def _discover_args(**overrides):
    import argparse
    defaults = dict(
        url="", keyword="手套", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="", export="", output="", auto_submit=False,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
        china=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _parse_discover(argv: list[str]):
    """跑 main() 到 cmd_discover 分发处（其余全 mock），返回解析后的 args。"""
    from scripts import cli
    with mock.patch("scripts.cli._init_sentry"), \
         mock.patch("scripts.cli._preflight_runtime", return_value=(True, "")), \
         mock.patch("scripts.cli._silent_update_check"), \
         mock.patch("sys.argv", ["cli.py", "discover", *argv]), \
         mock.patch("scripts.cli.cmd_discover") as md:
        cli.main()
    md.assert_called_once()
    return md.call_args.args[0]


def test_parser_china_flag_sets_true():
    """parser: --china → args.china is True（关键词一并解析）。"""
    args = _parse_discover(["--china", "--keyword", "手套"])
    assert args.china is True, "--china 应解析为 True"
    assert args.keyword == "手套"


def test_parser_china_flag_default_false():
    """parser: 不带 --china → args.china is False（零回归）。"""
    args = _parse_discover(["--keyword", "поилка"])
    assert args.china is False, "缺省 --china 应为 False"


def test_cmd_discover_passes_china_true_to_collect_and_analyze():
    """cmd_discover: china=True → collect_and_analyze(china=True) 透传。"""
    from scripts import cli
    c = _mk()
    args = _discover_args(keyword="手套", china=True)
    with mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                    return_value=[c]) as ca, \
         mock.patch("scripts.cli._interactive_select", return_value=[]), \
         mock.patch("scripts.lib.ozon_discovery.match_selected"), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = cli.cmd_discover(args)
    assert rc == 0
    assert ca.call_args.kwargs.get("china") is True, \
        f"collect_and_analyze 应收到 china=True, got {ca.call_args.kwargs}"


def test_cmd_discover_passes_china_false_by_default():
    """cmd_discover: 缺省 → collect_and_analyze(china=False)（行为不变）。"""
    from scripts import cli
    c = _mk()
    args = _discover_args(keyword="поилка")
    with mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                    return_value=[c]) as ca, \
         mock.patch("scripts.cli._interactive_select", return_value=[]), \
         mock.patch("scripts.lib.ozon_discovery.match_selected"), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = cli.cmd_discover(args)
    assert rc == 0
    assert ca.call_args.kwargs.get("china") is False, \
        f"缺省应传 china=False, got {ca.call_args.kwargs}"


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

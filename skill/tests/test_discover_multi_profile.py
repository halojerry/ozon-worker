#!/usr/bin/env python3
"""discover-multi 漏斗 v2 对齐回归——档位后置判定 + sales_mode 过滤补齐。

背景：评审 E 约定「ai 档/自定义区间完整判定由调用方在 ②b 富化后收口」
（collect_and_analyze 阶段尾两段），但 cmd_discover_multi 一直没接——
多关键词路径 ai 档只做行级（seller_count）判定，--base-filter 自定义区间
静默失效，sales_mode 也不标注过滤。本批对齐：_analyze_pids（含 ②b 富化）
之后补 _apply_profile_filter + _apply_sales_mode_filter 两段。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_discover_multi_profile.py -q
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cli  # noqa: E402
from scripts.lib.ozon_discovery import (  # noqa: E402
    ProductCandidate,
    _apply_profile_filter,
)


def _multi_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        keywords="猫玩具,化妆刷", max_each=30, min_margin=15.0,
        fx_rate=0.075, store="", no_analytics=False, min_price=0, max_price=0,
        brand_filter="nobrand", rules="", export="", output="", auto_submit=False,
        to_box=False, notify=False, review=False,
        blue_ocean_source="", blue_ocean_csv="", local=False, china=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _cand(pid: str, **kw) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=kw.pop("price", 300.0))
    c.status = kw.pop("status", "ok")  # _analyze_product 成功路径等价态
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _run_multi(args: argparse.Namespace, candidates: list[ProductCandidate],
               stack: ExitStack) -> tuple[int, mock.Mock]:
    """跑 cmd_discover_multi：滚动/分析 mock 成直接返回 candidates。"""
    stack.enter_context(mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                                   return_value=(True, "ok")))
    stack.enter_context(mock.patch.object(cli, "_collect_keyword_pids",
                                          side_effect=lambda *a, **k: ["p1"]))
    stack.enter_context(mock.patch.object(cli, "_merge_pids",
                                          return_value=["p1"]))
    stack.enter_context(mock.patch.object(cli, "_analyze_pids",
                                          return_value=candidates))
    stack.enter_context(mock.patch("scripts.lib.ozon_discovery._save_discovery_log",
                                   return_value=None))
    m_finish = mock.Mock(return_value=0)
    stack.enter_context(mock.patch.object(cli, "_finish_discover_flow", m_finish))
    with mock.patch("sys.stdout", new_callable=io.StringIO):
        rc = cli.cmd_discover_multi(args)
    return rc, m_finish


def test_ai_profile_post_filter_applied_after_enrichment():
    """ai 档：行级放行（seller_count≤30）但富化后月销阶梯不过 → 收尾前被置 filtered。

    候选无 analytics（has_analytics=False）→ 降级放行是预期；这里给
    has_analytics=True + 月销不达阶梯（300₽ 需 >500），验证后置判定真跑。
    """
    bad = _cand("p1", price=300.0, monthly_sales=100, create_days=100,
                sales_growth=5.0, drr=5.0, seller_count=3, has_analytics=True)
    good = _cand("p2", price=300.0, monthly_sales=900, create_days=100,
                 sales_growth=5.0, drr=5.0, seller_count=3, has_analytics=True)
    with ExitStack() as stack:
        rc, m_finish = _run_multi(_multi_args(filter_profile="ai"),
                                  [bad, good], stack)
    assert rc == 0
    out_candidates = m_finish.call_args.args[1]
    by_pid = {c.ozon_product_id: c for c in out_candidates}
    assert by_pid["p1"].status == "filtered", "月销阶梯不过应在收尾前被过滤"
    assert by_pid["p2"].status == "ok", "达阶梯候选不受影响"


def test_base_filter_rules_reach_post_filter():
    """--base-filter 自定义区间必须进后置判定（此前多关键词路径静默失效）。"""
    c = _cand("p1", monthly_sales=5)
    with ExitStack() as stack:
        m_apply = stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery._apply_profile_filter",
            wraps=_apply_profile_filter))
        rc, _ = _run_multi(_multi_args(base_filter="monthly_sales>=50"),
                           [c], stack)
    assert rc == 0
    m_apply.assert_called_once()
    kwargs = m_apply.call_args.kwargs
    assert kwargs.get("extra_rules") == [("monthly_sales", ">=", 50.0)]
    by_pid = {x.ozon_product_id: x
              for x in m_apply.call_args.args[0]}
    assert by_pid["p1"].status == "filtered", "月销 5 < 50 应被自定义区间过滤"


def test_sales_mode_filter_wired():
    """②c 发货模式过滤补齐：store sales_mode=FBO 时 FBS 候选在收尾前被过滤。"""
    c = _cand("p1")
    c.sales_schema = "FBS"
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.config_store.get_store_profile",
                                       return_value={"sales_mode": "FBO"}))
        rc, m_finish = _run_multi(_multi_args(), [c], stack)
    assert rc == 0
    out_candidates = m_finish.call_args.args[1]
    assert out_candidates[0].status == "filtered", "FBS 候选应被 sales_mode=FBO 过滤"


def test_invalid_base_filter_exits_2():
    """--base-filter 未知字段 → ValueError 被既有 handler 捕获，退出码 2。"""
    args = _multi_args(base_filter="bogus_field>=1")
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                                       return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(cli, "_collect_keyword_pids",
                                              side_effect=lambda *a, **k: ["p1"]))
        stack.enter_context(mock.patch.object(cli, "_merge_pids",
                                              return_value=["p1"]))
        stack.enter_context(mock.patch.object(cli, "_analyze_pids",
                                              return_value=[_cand("p1")]))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover_multi(args)
    assert rc == 2
    assert "粗筛参数错误" in out.getvalue()

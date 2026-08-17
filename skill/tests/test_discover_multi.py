"""D7': discover-multi 多关键词并行 — 拆分/合并 + cmd 调度（纯 mock）。

- _split_keywords: 逗号分隔 → 去空白/去空串/去重（保序）
- _merge_pids: 多批 pid 保序去重
- cmd_discover_multi: N 关键词串行滚动（_collect_keyword_pids 调 N 次）
  + 合并 pid 单次分析（_analyze_pids 调 1 次吃合并列表）→ 复用收尾流程
- 全链路（真实 _finish_discover_flow）：--rules + --auto-submit --to-box
  → 合并后的候选提交入采集箱

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_discover_multi.py -q
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
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


# ── 纯函数：关键词拆分 ──


def test_split_keywords_three_trimmed():
    """_split_keywords("猫玩具,宠物饮水机,化妆刷") → 3 个去空白词。"""
    assert cli._split_keywords("猫玩具,宠物饮水机,化妆刷") == [
        "猫玩具", "宠物饮水机", "化妆刷"]


def test_split_keywords_strips_whitespace():
    """关键词首尾空白去除（含中文逗号分隔符空格）。"""
    assert cli._split_keywords(" 猫玩具 , 宠物饮水机 ") == ["猫玩具", "宠物饮水机"]


def test_split_keywords_drops_blank_and_dedups():
    """空串/连续逗号剔除；重复关键词去重（保序）。"""
    assert cli._split_keywords("猫玩具,,,化妆刷") == ["猫玩具", "化妆刷"]
    assert cli._split_keywords("猫玩具,猫玩具,化妆刷") == ["猫玩具", "化妆刷"]
    assert cli._split_keywords("   ") == []


# ── 纯函数：合并去重 ──


def test_merge_pids_dedup_preserves_order():
    """合并多批 pid：重复 pid 去重且保序。"""
    assert cli._merge_pids([["1", "2"], ["2", "3"], ["4"]]) == ["1", "2", "3", "4"]


def test_merge_pids_handles_empty_and_nonstr():
    """空批/空串忽略；非字符串 pid 归一化为 str。"""
    assert cli._merge_pids([[], ["1"], [None, 2, ""]]) == ["1", "2"]


# ── cmd 调度：滚动 N 次 + 分析 1 次合并 ──


def _multi_args(**overrides):
    defaults = dict(
        keywords="猫玩具,宠物饮水机,化妆刷", max_each=30, min_margin=15.0,
        fx_rate=0.075, store="", no_analytics=False, min_price=0, max_price=0,
        brand_filter="nobrand", rules="", export="", output="", auto_submit=False,
        to_box=False, notify=False, review=False,
        blue_ocean_source="", blue_ocean_csv="", local=False, china=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _candidate(pid, title=None, status="ok"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title or f"T{pid}",
                         ozon_price=1500.0)
    c.status = status
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def test_cmd_multi_serial_collect_then_single_analyze():
    """3 关键词 → _collect_keyword_pids 调 3 次（串行滚动）→ _analyze_pids 调 1 次吃合并 pid。"""
    collected = {
        "猫玩具": ["p1", "p2"],
        "宠物饮水机": ["p2", "p3"],
        "化妆刷": ["p3", "p4"],
    }
    scroll_calls: list[tuple] = []

    def _collect(cdp_url, kw, max_each, china=True):
        scroll_calls.append((kw, max_each))
        return collected[kw]

    merged = [_candidate(p) for p in ["p1", "p2", "p3", "p4"]]
    args = _multi_args(keywords="猫玩具,宠物饮水机,化妆刷")
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                                       return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(cli, "_collect_keyword_pids",
                                              side_effect=_collect))
        m_analyze = mock.Mock(return_value=merged)
        stack.enter_context(mock.patch.object(cli, "_analyze_pids", m_analyze))
        stack.enter_context(mock.patch("scripts.lib.ozon_discovery._save_discovery_log",
                                       return_value=None))
        m_finish = mock.Mock(return_value=0)
        stack.enter_context(mock.patch.object(cli, "_finish_discover_flow", m_finish))
        stack.enter_context(mock.patch("scripts.lib.config_store.get_store_profile",
                                       return_value={}))
        stack.enter_context(mock.patch("scripts.lib.config_store.get_setting",
                                       return_value=None))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = cli.cmd_discover_multi(args)

    assert rc == 0
    assert scroll_calls == [("猫玩具", 30), ("宠物饮水机", 30), ("化妆刷", 30)]
    m_analyze.assert_called_once()
    assert m_analyze.call_args.args[1] == ["p1", "p2", "p3", "p4"], \
        "分析应吃合并去重后的 pid（p2/p3 重复剔除）"
    m_finish.assert_called_once()
    finish_candidates = m_finish.call_args.args[1]
    assert [c.ozon_product_id for c in finish_candidates] == ["p1", "p2", "p3", "p4"]


def test_cmd_multi_invalid_keywords_returns_1():
    """--keywords 全空白 → 报错退出 1，不触发任何滚动/分析。"""
    args = _multi_args(keywords=", ,")
    with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        rc = cli.cmd_discover_multi(args)
    assert rc == 1
    assert "--keywords" in out.getvalue()


# ── 全链路：真实收尾流程 + to-box 提交合并候选 ──


def _profitable(pid="p1", title="Товар один", url="https://detail.1688.com/offer/1001.html"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = url
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _build_envelope(c, store_config, store_id=""):
    return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}


def test_cmd_multi_full_chain_to_box_submits_merged():
    """真实 _finish_discover_flow：--rules + --to-box → 合并候选入采集箱，走 submit_draft。"""
    c1 = _profitable("p1", "Товар один")
    submitted: list[str] = []

    def _submit_draft(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "draft_id": f"D-{envelope['draft']['item_id']}"}

    args = _multi_args(keywords="猫玩具,宠物饮水机", rules="monthly_sales>=1",
                       auto_submit=True, to_box=True)
    m_env = mock.Mock(return_value={"ok": True, "task_id": "T-x"})
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                                       return_value=(True, "ok")))
        stack.enter_context(mock.patch.object(
            cli, "_collect_keyword_pids",
            side_effect=lambda cdp_url, kw, max_each, china=True: ["p1"]))
        stack.enter_context(mock.patch.object(cli, "_analyze_pids",
                                              return_value=[c1]))
        stack.enter_context(mock.patch("scripts.lib.ozon_discovery._save_discovery_log",
                                       return_value=None))
        stack.enter_context(mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                                       return_value=[c1]))
        stack.enter_context(mock.patch("scripts.lib.ozon_discovery.match_selected"))
        stack.enter_context(mock.patch("scripts.lib.config_store.get_mxou_token",
                                       return_value=""))
        stack.enter_context(mock.patch("scripts.lib.config_store.get_store_profile",
                                       return_value={}))
        stack.enter_context(mock.patch("scripts.lib.config_store.get_store",
                                       return_value={}))
        stack.enter_context(mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                                       side_effect=_build_envelope))
        stack.enter_context(mock.patch("scripts.cloud_probe.submit_draft",
                                       side_effect=_submit_draft))
        stack.enter_context(mock.patch("scripts.cloud_probe.submit_envelope", m_env))
        stack.enter_context(mock.patch("builtins.input", return_value="y"))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover_multi(args)

    assert rc == 0
    assert submitted == ["p1"], f"to_box 应提交合并候选, got {submitted}"
    m_env.assert_not_called()
    assert "📥 已入采集箱: Товар один → draft_id=D-p1" in out.getvalue()

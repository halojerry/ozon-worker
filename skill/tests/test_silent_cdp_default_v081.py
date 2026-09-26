#!/usr/bin/env python3
"""v0.81 静默抓取教义：new_tab 默认后台 + force_active 可见化渲染（零前台弹窗）。

动因（2026-09-26 用户反馈）：批量抓取每商品 4-6 次前台激活（widget/seller/
session 探针 + 商品页抓取复用可见 tab）把 Chrome 反复弹前台——「电脑完全
没法做事情了」。

实机实证：后台 tab + Page.setWebLifecycleState('active') +
Emulation.setFocusEmulationEnabled(true) → document.visibilityState 由
hidden 翻 visible、rAF 由冻结恢复 64fps、IntersectionObserver 正常派发——
懒加载渲染（Ozon 商品页全表特征、搜索结果流）不再依赖抢 macOS 前台。

本文件锁定：
- CdpTab.force_active() 行为（两条 CDP 命令 + 失败静默）
- new_tab 默认 background=True（接口级教义）
- 懒加载收集链（discovery 瀑布流 / cli 搜索收集）必须后台 tab + force_active

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_silent_cdp_default_v081.py -q
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import cdp_client  # noqa: E402
from scripts.lib import ozon_discovery as od  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _bare_tab() -> cdp_client.CdpTab:
    """绕过 __init__（不建真 WebSocket）构造裸 CdpTab，注入假 _send/_recv。"""
    tab = object.__new__(cdp_client.CdpTab)
    tab._closed = False
    tab._cdp_url = "http://127.0.0.1:9222"
    return tab


def test_force_active_sends_lifecycle_and_focus():
    """force_active 必须连发 setWebLifecycleState('active') + setFocusEmulationEnabled。"""
    tab = _bare_tab()
    sent: list[tuple[str, dict | None]] = []

    def _send(method, params=None, msg_id=None):
        sent.append((method, params))
        return 42

    tab._send = _send  # type: ignore[method-assign]
    tab._recv_until_id = lambda target_id, timeout=15: {"id": target_id}  # type: ignore[method-assign]
    assert tab.force_active() is True
    assert sent == [
        ("Page.setWebLifecycleState", {"state": "active"}),
        ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
    ]


def test_force_active_graceful_on_legacy_chrome():
    """老 Chrome 无 setWebLifecycleState → 返回 False 且不抛（调用方继续走）。"""
    tab = _bare_tab()

    def _send(method, params=None, msg_id=None):
        raise RuntimeError("unknown command")

    tab._send = _send  # type: ignore[method-assign]
    assert tab.force_active() is False


def test_new_tab_defaults_background():
    """接口教义：new_tab 默认后台（批量链所有裸调用点自动静默化）。"""
    sig = inspect.signature(cdp_client.CdpConnection.new_tab)
    assert sig.parameters["background"].default is True


def test_lazy_collect_chains_use_background_plus_force_active():
    """懒加载收集链锁定：discovery 瀑布流 + cli 搜索收集必须后台 tab + force_active。"""
    src_disc = (_SCRIPTS / "lib" / "ozon_discovery.py").read_text(encoding="utf-8")
    assert "new_tab(url, background=True)" in src_disc, "discovery 瀑布流收集必须后台 tab"
    # collect_product_urls 的 tab 创建后必须紧跟 force_active（同函数体内）
    fn = src_disc.split("def discover_from_url(", 1)[1].split("\ndef ", 1)[0]
    assert "force_active()" in fn, "后台 tab 必须接 force_active 才能渲染瀑布流"

    src_cli = (_SCRIPTS / "cli.py").read_text(encoding="utf-8")
    fn_cli = src_cli.split("def _collect_keyword_pids(", 1)[1].split("\ndef ", 1)[0]
    assert "new_tab(url, background=True)" in fn_cli, "cli 搜索收集必须后台 tab"
    assert "force_active()" in fn_cli, "cli 搜索收集必须 force_active"

    src_sc = (_SCRIPTS / "lib" / "ozon_scraper.py").read_text(encoding="utf-8")
    fn_sc = src_sc.split("def scrape_ozon_product_via_cdp(", 1)[1].split("\ndef ", 1)[0]
    assert "new_tab(background=True)" in fn_sc, "商品页抓取必须后台 tab"
    assert "force_active()" in fn_sc
    # A7 兜底：bring_to_front 只允许在 visibilityState 仍 hidden 的降级分支里
    # 出现一次（不许再无条件抢前台）
    assert fn_sc.count("bring_to_front()") == 1, (
        "bring_to_front 仅允许 A7 兜底降级分支一处"
    )


def test_scraper_no_longer_reuses_visible_tab():
    """v0.10 的 find_tab 复用可见 tab 已废除——cookie 是 profile 级，复用只会
    让用户前台 tab 被反复导航。scrape_product 主路径不得再出现 find_tab。"""
    src_sc = (_SCRIPTS / "lib" / "ozon_scraper.py").read_text(encoding="utf-8")
    fn_sc = src_sc.split("def scrape_ozon_product_via_cdp(", 1)[1].split("\ndef ", 1)[0]
    assert 'find_tab("ozon.ru")' not in fn_sc, "主路径禁止复用用户可见 tab"

"""fetch_seller_products tab 复用回归（v0.31.1 T4）— 复用 _ensure_ozon_tab，不 per-seller new_tab。

背景: fission 裂变 20 卖家时，旧实现每卖家 cdp.new_tab("about:blank") → 20 tab 增殖。
修复: 改为复用 _ensure_ozon_tab（find_tab 命中已有 ozon tab + 存活校验 + release 契约），
跨卖家只用一个 tab；stale tab（刚被关闭残留）由 _ensure_ozon_tab 检测并新建。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))

import ozon_discovery
import scripts.lib.cdp_client as cdp_client
import scripts.lib.ozon_widget as ozon_widget

_SELLER_URL = "https://www.ozon.ru/seller/999/products/"


def _fake_cdp():
    conn = mock.MagicMock()
    tab = mock.MagicMock()
    conn.new_tab.return_value = tab
    return conn, tab


def test_uses_ensure_ozon_tab_not_per_seller_new_tab():
    """fetch_seller_products(cdp=...) 复用 _ensure_ozon_tab，不再直接 per-seller new_tab。"""
    conn, tab = _fake_cdp()
    with mock.patch.object(ozon_widget, "_ensure_ozon_tab", return_value=tab) as ensure:
        with mock.patch.object(ozon_discovery, "_lazy_collect_urls",
                               return_value=["p1", "p2"]) as lcu:
            with mock.patch("time.sleep"):
                pids = ozon_discovery.fetch_seller_products(
                    seller_id="999", max_products=10, cdp=conn)
    ensure.assert_called_once()
    conn.new_tab.assert_not_called()  # ← tab 增殖根因不再触发
    conn.close.assert_not_called()    # 连接归调用方管理
    assert pids == ["p1", "p2"]


def test_multiple_sellers_reuse_same_tab():
    """fission 20 卖家场景: 同一 cdp 多次调用每次走 _ensure_ozon_tab，无 new_tab 增殖。"""
    conn, tab = _fake_cdp()
    with mock.patch.object(ozon_widget, "_ensure_ozon_tab", return_value=tab) as ensure:
        with mock.patch.object(ozon_discovery, "_lazy_collect_urls", return_value=["p1"]):
            with mock.patch("time.sleep"):
                for i in range(5):
                    ozon_discovery.fetch_seller_products(
                        seller_id=str(i), max_products=10, cdp=conn)
    assert ensure.call_count == 5
    conn.new_tab.assert_not_called()
    tab.close.assert_not_called()
    conn.close.assert_not_called()


def test_no_cdp_self_manage_uses_ensure_ozon_tab():
    """无 cdp → 自建 CdpConnection 并走 _ensure_ozon_tab，关闭自建连接。"""
    conn, tab = _fake_cdp()
    with mock.patch.object(cdp_client, "CdpConnection") as conn_cls:
        conn_cls.return_value = conn
        with mock.patch.object(ozon_widget, "_ensure_ozon_tab", return_value=tab) as ensure:
            with mock.patch.object(ozon_discovery, "_lazy_collect_urls",
                                   return_value=["p1"]):
                with mock.patch("time.sleep"):
                    pids = ozon_discovery.fetch_seller_products(
                        seller_id="999", max_products=10)
    conn_cls.assert_called_once()
    ensure.assert_called_once()
    conn.close.assert_called_once()
    assert pids == ["p1"]


def test_stale_tab_detection_falls_back_to_new():
    """stale tab 检测替换: _ensure_ozon_tab 命中已死 tab（evaluate 抛异常）→ 新建。"""
    conn = mock.MagicMock()
    stale = mock.MagicMock()
    stale.evaluate.side_effect = ConnectionError("CDP target gone")
    fresh = mock.MagicMock()
    conn.find_tab.return_value = stale
    conn.new_tab.return_value = fresh
    with mock.patch("scripts.lib.ozon_widget.time.sleep"):
        tab = ozon_widget._ensure_ozon_tab(conn, _SELLER_URL)
    assert tab is fresh, "stale tab 存活校验失败应新建"
    conn.new_tab.assert_called_once()


def test_live_tab_reused_and_released():
    """存活 tab: find_tab 命中 + evaluate 存活 → release 契约 + 复用，不 new_tab。"""
    conn = mock.MagicMock()
    live = mock.MagicMock()
    live.evaluate.return_value = "1"
    conn.find_tab.return_value = live
    conn.new_tab.side_effect = AssertionError("应复用已有 tab，不应新建")
    with mock.patch("scripts.lib.ozon_widget.time.sleep"):
        tab = ozon_widget._ensure_ozon_tab(conn, _SELLER_URL)
    assert tab is live, "存活 tab 应直接复用"
    conn.release.assert_called_once_with(live)  # ← release 契约


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

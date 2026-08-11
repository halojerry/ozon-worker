"""P2 China mode: collect_and_analyze(china=True, keyword=...) → 中国站 highlight + text。

背景: 旧路由只有 url / keyword 搜索页 / 裸 highlight 三条路，没有
「highlight + text 关键词」——用户想要「中国站范围内的关键词搜索」时只能
全部中国站页或全部搜索结果页。china=True + keyword 应构造
CHINA_HIGHLIGHT_URL?text=<quote(keyword)>（China goods highlight 页内搜索）。

v0.38: 默认翻转 — china 缺省即 True（跨境卖家主战场），--local 显式传 False
切主站 /search/。验证: 断言 collect 阶段传给 cdp.new_tab 的 target_url 正确。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import urllib.parse

from scripts.lib import ozon_discovery as od

CHINA = od.CHINA_HIGHLIGHT_URL


class _FakeCdp:
    """MagicMock.__enter__ 返回新 mock 而非 self——用真实 __enter__ 假连接。"""

    instances: list["_FakeCdp"] = []

    def __init__(self, *a, **k):
        self.new_tab = mock.MagicMock(return_value=mock.MagicMock())
        _FakeCdp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _target_url(china=None, keyword="", url=""):
    """跑 collect_and_analyze（串行路径），返回 collect 阶段 new_tab 的目标 URL。

    china=None（默认）→ 不传 china kwarg，验证函数缺省值（默认中国站）。
    """
    _FakeCdp.instances = []
    with mock.patch.object(od, "_discover_workers", return_value=1):
        with mock.patch("scripts.lib.cdp_client.CdpConnection", side_effect=_FakeCdp):
            with mock.patch.object(od, "_lazy_collect_urls", return_value=["p1"]):
                with mock.patch.object(od, "_analyze_product",
                                       return_value=od.ProductCandidate(
                                           ozon_product_id="p1", ozon_title="T",
                                           ozon_price=100.0)):
                    with mock.patch.object(od, "_save_discovery_log"):
                        with mock.patch("time.sleep"):
                            kwargs = dict(url=url, keyword=keyword, use_analytics=False)
                            if china is not None:
                                kwargs["china"] = china
                            od.collect_and_analyze("http://127.0.0.1:9222", **kwargs)
    assert len(_FakeCdp.instances) == 1, "串行路径应只建一个主连接"
    return _FakeCdp.instances[0].new_tab.call_args.args[0]


def test_default_keyword_routes_to_china_highlight():
    """缺省（不传 china）→ 默认中国站：keyword 走 CHINA_HIGHLIGHT_URL?text=（不落主站 /search/）。"""
    target = _target_url(keyword="手套")
    assert target == f"{CHINA}?text={urllib.parse.quote('手套')}", target


def test_china_keyword_builds_highlight_with_text():
    """china=True + keyword="手套" → CHINA_HIGHLIGHT_URL?text=手套（URL 编码）。"""
    target = _target_url(china=True, keyword="手套")
    assert target == f"{CHINA}?text={urllib.parse.quote('手套')}", target


def test_china_no_keyword_falls_back_to_bare_highlight():
    """china=True + 空 keyword → 裸 CHINA_HIGHLIGHT_URL（无 text 参数）。"""
    target = _target_url(china=True, keyword="   ")
    assert target == CHINA, target


def test_non_china_keyword_uses_search_page():
    """china=False + keyword → 原搜索页路由不变（零回归）。"""
    target = _target_url(china=False, keyword="поилка")
    assert target == f"https://www.ozon.ru/search/?text={urllib.parse.quote('поилка')}", target


def test_url_always_wins_over_china_keyword():
    """url 给定 → 原样使用（china 路由不覆盖显式 url）。"""
    target = _target_url(china=True, keyword="手套", url="https://www.ozon.ru/product/123/")
    assert target == "https://www.ozon.ru/product/123/", target


def test_china_keyword_whitespace_stripped():
    """china + keyword 带首尾空白 → strip 后编码。"""
    target = _target_url(china=True, keyword="  собака  ")
    assert target == f"{CHINA}?text={urllib.parse.quote('собака')}", target


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

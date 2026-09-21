#!/usr/bin/env python3
"""批A A3（fix/skill-silent-cdp-v1）：seller 数据获取失败负缓存窗回归。

背景：fetch_sales_analytics / fetch_bestseller_metrics_map 失败/空结果此前
永不缓存 → 每次调用原样重开 seller tab 导航（「seller 页反复打开」伴生根因）。
本文件锁定：失败/空结果写 600s 负缓存窗（实现风格对齐本文件 DataDome 短路窗
先例：进程内全局 + 落盘 600s）；窗内二次调用直接早退返回失败形状——不建 tab、
不导航、零底层调用；成功缓存语义不变。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_seller_negcache_v078.py -q
"""
from __future__ import annotations

import logging
import os
import sys
import time
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402


class _FakeTab:
    """seller 数据 tab 假体：evaluate 恒空（无 sc_company_id → 失败路径）。"""

    def __init__(self):
        self.navigated: list[str] = []
        self.closed = False

    def navigate(self, url, **kw):
        self.navigated.append(url)

    def set_bypass_csp(self):
        pass

    def add_init_script(self, js):
        pass

    def evaluate(self, js, **kw):
        return ""

    def close(self, *a, **k):
        self.closed = True


@pytest.fixture(autouse=True)
def _sealed(monkeypatch):
    """摘除 pytest 守卫启用负缓存窗（生产语义）+ 隔离磁盘缓存 + 清进程内窗。"""
    monkeypatch.setattr(osa, "_under_pytest", lambda: False)
    monkeypatch.setattr(osa, "_SELLER_NEG_SALES_UNTIL", 0.0)
    monkeypatch.setattr(osa, "_SELLER_NEG_MAP_UNTIL", 0.0)
    store: dict = {}

    def _get(ns, key):
        rec = store.get((ns, key))
        if rec is None or time.time() > rec["expires_at"]:
            return None
        return rec["value"]

    def _set(ns, key, data, ttl=86400):
        store[(ns, key)] = {"value": data, "expires_at": time.time() + ttl}

    monkeypatch.setattr("scripts.lib.cache.cache_get", _get)
    monkeypatch.setattr("scripts.lib.cache.cache_set", _set)
    yield


def test_sales_analytics_failure_opens_negcache_window(monkeypatch, caplog):
    """fetch_sales_analytics 失败 → 600s 窗内二次调用不建 tab 不导航。"""
    monkeypatch.setattr("time.sleep", lambda s: None)
    tab = _FakeTab()
    tab_calls: list[int] = []

    def _fake_tab_for_seller(cdp, **kw):
        tab_calls.append(1)
        return tab, False

    monkeypatch.setattr(osa, "_tab_for_seller", _fake_tab_for_seller)
    cdp = mock.Mock()

    with caplog.at_level(logging.INFO, logger="scripts.lib.ozon_seller_analytics"):
        r1 = osa.fetch_sales_analytics(cdp, ["123"])
        assert r1 == {}
        assert tab_calls == [1], "第一次走完整失败路径（建 tab）"
        r2 = osa.fetch_sales_analytics(cdp, ["123"])
        assert r2 == {}, "窗内二次调用返回既有失败形状"
    assert tab_calls == [1], "窗内二次调用不得再建 tab / 导航"
    assert tab.navigated == []
    assert any("负缓存窗" in r.message for r in caplog.records), "须打一行日志"


def test_sales_analytics_exception_also_marks_window(monkeypatch):
    """整体异常也是失败：同样开窗（下次调用直接早退）。"""
    monkeypatch.setattr("time.sleep", lambda s: None)

    def _boom(cdp, **kw):
        raise RuntimeError("cdp gone")

    monkeypatch.setattr(osa, "_tab_for_seller", _boom)
    cdp = mock.Mock()
    assert osa.fetch_sales_analytics(cdp, ["123"]) == {}
    tab_calls: list[int] = []

    def _never(cdp, **kw):
        tab_calls.append(1)
        return _FakeTab(), False

    monkeypatch.setattr(osa, "_tab_for_seller", _never)
    assert osa.fetch_sales_analytics(cdp, ["123"]) == {}
    assert tab_calls == [], "异常开窗后二次调用必须早退"


def test_bestseller_map_failure_opens_negcache_window(monkeypatch, caplog):
    """fetch_bestseller_metrics_map 空/失败 → 窗内二次调用零底层调用。"""
    fetch_calls: list[int] = []

    def _fake_fetch(cdp, **kw):
        fetch_calls.append(1)
        return []

    monkeypatch.setattr(osa, "fetch_ozon_bestsellers", _fake_fetch)
    cdp = mock.Mock()

    with caplog.at_level(logging.INFO, logger="scripts.lib.ozon_seller_analytics"):
        r1 = osa.fetch_bestseller_metrics_map(cdp)
        assert r1 == {}
        assert fetch_calls == [1]
        r2 = osa.fetch_bestseller_metrics_map(cdp)
        assert r2 == {}
    assert fetch_calls == [1], "窗内二次调用零底层调用（不建 tab 不导航）"
    assert any("负缓存窗" in r.message for r in caplog.records)


def test_success_result_still_cached_and_reused(monkeypatch):
    """成功缓存语义不变：有结果照常写 6h 缓存，二次调用缓存命中零底层调用。"""
    fetch_calls: list[int] = []

    def _fake_fetch(cdp, **kw):
        fetch_calls.append(1)
        return [{"sku": "111", "sold_count": 5}]

    monkeypatch.setattr(osa, "fetch_ozon_bestsellers", _fake_fetch)
    cdp = mock.Mock()
    r1 = osa.fetch_bestseller_metrics_map(cdp)
    assert r1 == {"111": {"sku": "111", "sold_count": 5}}
    r2 = osa.fetch_bestseller_metrics_map(cdp)
    assert r2 == r1
    assert fetch_calls == [1]


def test_window_expires_after_600s(monkeypatch):
    """负缓存窗过期后恢复完整失败路径（窗不是永久封禁）。"""
    monkeypatch.setattr("time.sleep", lambda s: None)
    tab = _FakeTab()
    monkeypatch.setattr(osa, "_tab_for_seller", lambda cdp, **kw: (tab, False))
    cdp = mock.Mock()
    assert osa.fetch_sales_analytics(cdp, ["123"]) == {}
    # 窗拨到过去（时间可用窗内全局值直接模拟 600s 流逝）
    monkeypatch.setattr(osa, "_SELLER_NEG_SALES_UNTIL", time.time() - 1)
    assert osa.fetch_sales_analytics(cdp, ["123"]) == {}, "过期后应重新实检"


def test_mark_writes_disk_cache(monkeypatch):
    """落盘对齐 DataDome 短路窗先例：跨命令（新进程）读盘恢复窗。"""
    osa._mark_sales_negcache()
    assert osa._SELLER_NEG_SALES_UNTIL > time.time()
    # _mark 落盘后，进程内全局清零仍应从「磁盘」恢复（本测试的 stub store）
    monkeypatch.setattr(osa, "_SELLER_NEG_SALES_UNTIL", 0.0)
    assert osa._sales_negcache_active() is True, "冷进程应从落盘缓存恢复负缓存窗"

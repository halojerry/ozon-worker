# -*- coding: utf-8 -*-
"""BL-01: fx_rate_service 三函数测试（exchange_rates 死缓存事故根治）。

背景：exchange_rates 表的写侧 set_exchange_rate 全仓零调用（死缓存）——
24h 新鲜度一过 get_exchange_rate 恒 None，pricing 兜底恒 12.0 且无告警。
本模块补三级源：PG 缓存（读侧自带 24h 新鲜度）→ er-api live 拉取并回写 →
12.0 兜底，返回值带 source 标记（pg_cache/live_fetch/fallback_12）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_fx_rate_service.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from utils import fx_rate_service as fx


class _FakeResp:
    """requests.Response 最小桩（fetch_cny_rub_live 只消费 .json()）。"""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeDB:
    """LocalDBManager 桩：可编排 get/set 行为并记录回写调用。"""

    def __init__(self, get_result=None, get_raises=None, set_raises=None):
        self.get_result = get_result
        self.get_raises = get_raises
        self.set_raises = set_raises
        self.set_calls: list = []

    def get_exchange_rate(self, from_currency, to_currency):
        if self.get_raises is not None:
            raise self.get_raises
        return self.get_result

    def set_exchange_rate(self, from_currency, to_currency, rate):
        if self.set_raises is not None:
            raise self.set_raises
        self.set_calls.append((from_currency, to_currency, rate))


@pytest.fixture(autouse=True)
def _reset_throttle():
    """每用例重置 24h 节流游标（模块级全局，防用例间串扰）。"""
    fx._LAST_AT = 0.0
    yield
    fx._LAST_AT = 0.0


# ── 1. fetch_cny_rub_live ──

def test_fetch_live_success(monkeypatch):
    monkeypatch.setattr(
        fx.requests, "get", lambda *a, **k: _FakeResp({"rates": {"RUB": 11.55}}),
    )
    assert fx.fetch_cny_rub_live() == 11.55


def test_fetch_live_missing_rub_key_returns_none(monkeypatch):
    monkeypatch.setattr(
        fx.requests, "get", lambda *a, **k: _FakeResp({"rates": {}}),
    )
    assert fx.fetch_cny_rub_live() is None, "缺键返回 None（绝不 raise）"


def test_fetch_live_non_positive_returns_none(monkeypatch):
    for bad in (0, -3.2):
        monkeypatch.setattr(
            fx.requests, "get", lambda *a, **k: _FakeResp({"rates": {"RUB": bad}}),
        )
        assert fx.fetch_cny_rub_live() is None, f"非正数 {bad} 必须返回 None"


def test_fetch_live_http_error_returns_none(monkeypatch):
    def _boom(*a, **k):
        raise TimeoutError("er-api 超时")

    monkeypatch.setattr(fx.requests, "get", _boom)
    assert fx.fetch_cny_rub_live() is None, "网络异常绝不 raise"


def test_fetch_live_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(
        fx.requests, "get", lambda *a, **k: _FakeResp(ValueError("not json")),
    )
    assert fx.fetch_cny_rub_live() is None


# ── 2. resolve_cny_rub_rate ──

def test_resolve_pg_cache_hit(monkeypatch):
    db = _FakeDB(get_result=11.8)
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager", lambda: db)

    def _no_live():
        raise AssertionError("PG 命中不应走 live 拉取")

    monkeypatch.setattr(fx, "fetch_cny_rub_live", _no_live)
    assert fx.resolve_cny_rub_rate() == (11.8, "pg_cache")
    assert db.set_calls == [], "PG 命中不应回写"


def test_resolve_pg_miss_live_ok_writes_back(monkeypatch):
    db = _FakeDB(get_result=None)
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager", lambda: db)
    monkeypatch.setattr(fx, "fetch_cny_rub_live", lambda: 11.42)
    assert fx.resolve_cny_rub_rate() == (11.42, "live_fetch")
    assert db.set_calls == [("CNY", "RUB", 11.42)], "live 值必须回写死缓存"


def test_resolve_write_failure_still_live(monkeypatch):
    """回写失败仅 warning 不中断——本次仍用 live 值。"""
    db = _FakeDB(get_result=None, set_raises=RuntimeError("pg down"))
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager", lambda: db)
    monkeypatch.setattr(fx, "fetch_cny_rub_live", lambda: 11.42)
    assert fx.resolve_cny_rub_rate() == (11.42, "live_fetch")


def test_resolve_both_fail_fallback_12(monkeypatch):
    db = _FakeDB(get_result=None)
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager", lambda: db)
    monkeypatch.setattr(fx, "fetch_cny_rub_live", lambda: None)
    assert fx.resolve_cny_rub_rate() == (12.0, "fallback_12")
    assert db.set_calls == [], "兜底值不得写库（防污染缓存）"


def test_resolve_pg_read_raises_degrades_to_live(monkeypatch):
    db = _FakeDB(get_raises=RuntimeError("pg down"))
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager", lambda: db)
    monkeypatch.setattr(fx, "fetch_cny_rub_live", lambda: 11.1)
    assert fx.resolve_cny_rub_rate() == (11.1, "live_fetch")


def test_resolve_stale_zero_rate_treated_as_miss(monkeypatch):
    """PG 脏数据（0/负数）按未命中处理，不让垃圾值进定价。"""
    db = _FakeDB(get_result=0.0)
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager", lambda: db)
    monkeypatch.setattr(fx, "fetch_cny_rub_live", lambda: 11.3)
    assert fx.resolve_cny_rub_rate() == (11.3, "live_fetch")


# ── 3. refresh_cny_rub_if_due ──

def test_refresh_calls_resolve_and_throttles(monkeypatch):
    calls: list = []

    def _fake_resolve():
        calls.append(1)
        return (11.5, "live_fetch")

    monkeypatch.setattr(fx, "resolve_cny_rub_rate", _fake_resolve)
    fx.refresh_cny_rub_if_due()
    fx.refresh_cny_rub_if_due()
    assert len(calls) == 1, "24h 节流：周期内第二次调用不得再触发 resolve"


def test_refresh_swallows_exception(monkeypatch):
    def _boom():
        raise RuntimeError("resolve 炸了")

    monkeypatch.setattr(fx, "resolve_cny_rub_rate", _boom)
    fx.refresh_cny_rub_if_due()  # 整体吞错：不 raise 即通过

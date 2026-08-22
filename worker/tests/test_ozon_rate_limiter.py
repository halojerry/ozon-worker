"""ozon_rate_limiter 单测 — 同步令牌桶（按 section 路由，线程安全）。

覆盖: 低量不阻塞 / finance 与 seller 桶隔离 / 容量耗尽阻塞超时 / 超时返回 False / 单例导入。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ozon_rate_limiter import RateLimiter, _rate_limiter


def test_low_volume_no_block():
    limiter = RateLimiter()
    start = time.monotonic()
    for _ in range(10):
        assert limiter.acquire("/v3/product/import") is True
    assert time.monotonic() - start < 1.0


def test_finance_bucket_isolated_from_seller():
    limiter = RateLimiter()
    seller_before = limiter._buckets["seller"].tokens
    finance_before = limiter._buckets["finance"].tokens
    assert limiter.acquire("/v1/finance/transaction/list") is True
    assert limiter._buckets["finance"].tokens < finance_before
    assert limiter._buckets["seller"].tokens == seller_before


def test_third_acquire_blocks_and_times_out():
    limiter = RateLimiter(capacities={"seller": 2, "finance": 2, "premium": 2})
    assert limiter.acquire("/v3/product/import") is True
    assert limiter.acquire("/v3/product/import") is True
    start = time.monotonic()
    result = limiter.acquire("/v3/product/import", timeout=0.1)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed >= 0.05


def test_capacity_one_second_acquire_returns_false():
    limiter = RateLimiter(capacities={"seller": 1, "finance": 1, "premium": 1})
    assert limiter.acquire("/v3/product/import") is True
    assert limiter.acquire("/v3/product/import", timeout=0.01) is False


def test_singleton_import():
    assert isinstance(_rate_limiter, RateLimiter)
    assert set(_rate_limiter._buckets.keys()) == {"seller", "finance", "premium"}

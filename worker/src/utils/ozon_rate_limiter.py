"""Ozon API 速率限制器 — 按 section 的同步令牌桶（线程安全）。

每个 section（seller/finance/premium）独立令牌桶，按每分钟上限连续补充令牌。
acquire(endpoint) 阻塞（time.sleep）直到令牌可用或超时。

限制源: docs/refs/ozon-mcp/knowledge/rate_limits.yaml
    seller  1000/min  Seller API 默认（实证: 20 RPS 内无 429）
    finance   100/min  财务报告节，Ozon 限流更紧
    premium    60/min  Analytics/Premium 方法节

用法:
    from utils.ozon_rate_limiter import _rate_limiter
    if _rate_limiter.acquire("/v1/finance/transaction/list"):
        ...call Ozon...
"""
from __future__ import annotations

import threading
import time


_SECTION_LIMITS = {"seller": 1000, "finance": 100, "premium": 60}


class _Bucket:
    __slots__ = ("capacity", "refill_rate", "tokens", "last_refill")

    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.refill_rate = per_minute / 60.0  # 每分钟上限 → 每秒补充量
        self.tokens = float(per_minute)  # 桶满启动
        self.last_refill = time.monotonic()


class RateLimiter:
    """同步令牌桶速率限制器（按 endpoint section 路由，线程安全）。"""

    def __init__(self, capacities: dict[str, int] | None = None):
        limits = {**_SECTION_LIMITS, **(capacities or {})}
        self._buckets: dict[str, _Bucket] = {sec: _Bucket(pm) for sec, pm in limits.items()}
        self._lock = threading.Lock()

    def _get_section(self, endpoint: str) -> str:
        ep = endpoint.lower()
        if "/v1/finance/" in ep:
            return "finance"
        if "/v1/analytics/" in ep or "/v1/premium/" in ep:
            return "premium"
        return "seller"

    def _refill(self, b: _Bucket, now: float) -> None:
        elapsed = now - b.last_refill
        if elapsed > 0:
            b.tokens = min(b.capacity, b.tokens + elapsed * b.refill_rate)
            b.last_refill = now

    def acquire(self, endpoint: str, timeout: float = 30.0) -> bool:
        """阻塞直到 endpoint 所属 section 有可用令牌，或超时。返回是否获取成功。"""
        section = self._get_section(endpoint)
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                b = self._buckets[section]
                self._refill(b, now)
                if b.tokens >= 1.0:
                    b.tokens -= 1.0
                    return True
                deficit = 1.0 - b.tokens
                wait = deficit / b.refill_rate if b.refill_rate > 0 else float("inf")
            remaining = deadline - now
            if remaining <= 0:
                return False
            time.sleep(min(wait, remaining))


_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter

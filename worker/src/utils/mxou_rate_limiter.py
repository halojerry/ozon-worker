"""MXOU API 速率限制器 — 按 token 滑动窗口控制请求速率。

防止多个并发任务同时打爆 MXOU 500 RPM 限制。
令牌不够时自动等待，429 时指数退避重试。

用法（自动集成，无需手动调用）:
    # call_mxou_chat_api 和 call_mxou_image_api 内部自动调用
    _rate_limiter.acquire(token)
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List

from utils.logger import get_logger

logger = get_logger("mxou.ratelimit")

# 每个 token 的 RPM 上限（留 50 余量，避免踩线）
DEFAULT_RPM = 450


class TokenRateLimiter:
    """按 token 的滑动窗口速率限制器（线程安全）。

    每个 token 独立计数，互不影响。
    超限时自动 sleep 到窗口释放。
    """

    def __init__(self, rpm: int = DEFAULT_RPM):
        self.rpm = rpm
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def acquire(self, token: str):
        """阻塞等待直到该 token 在最近 60 秒内的请求数 < rpm。"""
        if not token:
            return

        while True:
            with self._lock:
                now = time.monotonic()
                window_start = now - 60.0

                # 清理过期记录
                timestamps = self._requests[token]
                self._requests[token] = [t for t in timestamps if t > window_start]
                timestamps = self._requests[token]

                if len(timestamps) < self.rpm:
                    # 窗口未满，放行
                    self._requests[token].append(now)
                    return

                # 窗口满了，计算等待时间
                oldest = timestamps[0]
                wait_time = oldest + 60.0 - now + 0.01

            # 释放锁后等待（不阻塞其他 token）
            if wait_time > 0:
                logger.info(f"⏳ MXOU 限流等待: token={token[:8]}..., {wait_time:.1f}s ({len(timestamps)}/{self.rpm} RPM)")
                time.sleep(wait_time)

    def get_usage(self, token: str) -> tuple[int, int]:
        """获取该 token 当前 RPM 使用情况。返回 (当前请求数, 上限)。"""
        with self._lock:
            now = time.monotonic()
            window_start = now - 60.0
            count = len([t for t in self._requests.get(token, []) if t > window_start])
        return count, self.rpm


# 全局单例
_rate_limiter = TokenRateLimiter()


def get_rate_limiter() -> TokenRateLimiter:
    return _rate_limiter


def mxou_acquire(token: str):
    """MXOU 请求前调用，等待到速率允许。"""
    _rate_limiter.acquire(token)


def mxou_usage(token: str) -> tuple[int, int]:
    return _rate_limiter.get_usage(token)


def handle_mxou_429(token: str, attempt: int, max_retries: int = 3) -> bool:
    """处理 MXOU 429 响应。指数退避后返回是否应重试。

    Returns:
        True = 应重试，False = 放弃
    """
    if attempt >= max_retries:
        logger.error(f"MXOU 429 重试耗尽 ({max_retries} 次)，放弃")
        return False

    wait = min(2 ** (attempt + 1), 30)
    logger.warning(f"MXOU 429 限流，{wait}s 后重试 (第 {attempt + 1}/{max_retries} 次)")
    time.sleep(wait)
    return True

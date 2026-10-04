"""API 限流器：按 token 的滑动窗口限流（W3b 自 main.py 顶层抽出）。

此前 RateLimiter 住在 main 模块顶层，routes/mcp_server/api.deps_tenant 只能
函数内 ``from main import rate_limiter`` 懒导入。本模块是唯一权威；
main.py 保留同名 re-export（测试 monkeypatch main.rate_limiter 的兼容面 +
调用方不变）。

消费约定（改鉴权链前必读）：
- _authenticate_token（api/security.py）在凭证校验**之后**才写限流键——
  未认证洪水不消耗限流字典内存（T21 race-M5）。
- task_status 等高频轮询端点**有意不接** rate_limiter（逐 token 限流会误伤
  正常轮询，见 _task_status_guard docstring）。
"""

import os
import threading
import time
from typing import Dict

# API 限流配置
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))  # 每 token 每分钟最大提交数（与 AGENTS.md/.env.example 一致）

# T21(race-M5): 限流字典有界化阈值——RateLimiter._requests 键数超过该值时，
# check() 先清扫「最后活跃已滑出 60s 窗口」的键，未认证/海量 token 洪水不再无界吃内存。
_RATE_LIMITER_MAX_KEYS = 4096


class RateLimiter:
    """滑动窗口限流器：按 token 限制提交频率"""

    def __init__(self, max_per_minute: int = RATE_LIMIT_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self._requests: Dict[str, list] = {}  # token → [timestamp, ...]
        self._lock = threading.Lock()

    def check(self, token: str) -> tuple[bool, int]:
        """检查是否允许请求。返回 (allowed, remaining)。"""
        now = time.time()
        window_start = now - 60
        with self._lock:
            # T21(race-M5): 字典有界化——_requests 此前永不清扫（每个新 token 一个键），
            # 未认证洪水可无界吃内存。超阈值先清「最后活跃已滑出窗口」的键，活跃键不动。
            if len(self._requests) > _RATE_LIMITER_MAX_KEYS:
                stale = [k for k, ts in self._requests.items() if not ts or ts[-1] <= window_start]
                for k in stale:
                    del self._requests[k]
            timestamps = self._requests.get(token, [])
            # 清理过期记录
            timestamps = [t for t in timestamps if t > window_start]
            if len(timestamps) >= self.max_per_minute:
                self._requests[token] = timestamps
                return False, 0
            timestamps.append(now)
            self._requests[token] = timestamps
            return True, self.max_per_minute - len(timestamps)


rate_limiter = RateLimiter()

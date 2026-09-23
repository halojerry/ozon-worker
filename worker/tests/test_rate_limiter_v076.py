"""v0.76 T21(race-M5): 限流器字典有界——4096 键上限触发过期清扫，未认证洪水不再无界吃内存。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from main import _RATE_LIMITER_MAX_KEYS, RateLimiter


def test_purge_when_over_cap():
    rl = RateLimiter(max_per_minute=10)
    old = time.time() - 3600
    for i in range(5000):
        rl._requests[f"old-{i}"] = [old]
    allowed, _ = rl.check("fresh-1")
    assert allowed
    assert len(rl._requests) <= 4097          # 清扫发生
    assert "old-0" not in rl._requests


def test_active_keys_survive_purge():
    rl = RateLimiter(max_per_minute=10)
    now = time.time()
    for i in range(5000):
        rl._requests[f"act-{i}"] = [now]
    rl.check("fresh-1")
    assert any(k.startswith("act-") for k in list(rl._requests)[:100])  # 活跃键不误清（抽样）


def test_fresh_token_counts_normally_after_purge():
    """卫生用例：清扫发生后，新 token 的计数/超限语义不受影响。"""
    rl = RateLimiter(max_per_minute=3)
    old = time.time() - 3600
    for i in range(5000):
        rl._requests[f"old-{i}"] = [old]
    allowed, remaining = rl.check("fresh-1")  # 触发清扫 + 首次计数
    assert allowed
    assert remaining == 2                     # 3 - 1，清扫不吃掉本次额度
    allowed, remaining = rl.check("fresh-1")
    assert allowed and remaining == 1
    allowed, _ = rl.check("fresh-1")
    assert allowed
    allowed, remaining = rl.check("fresh-1")  # 第 4 次 → 超限
    assert not allowed and remaining == 0
    assert len(rl._requests) <= _RATE_LIMITER_MAX_KEYS + 1

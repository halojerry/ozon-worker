"""余额降级反查修复（v0.62.4）：submit_task 传哈希租户时，
_check_mxou_balance 应能用 key 反查真实 Supabase user_id + unlimited_quota，
避免对「有余额的 key」误判「余额不足」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import os
os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"
os.environ["SKIP_STORE_SYNC"] = "1"

import main as main_mod  # noqa: E402


class _Exec:
    def __init__(self, data):
        self.data = data


class _Chain:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_cols):
        return self

    def eq(self, _c, _v):
        return self

    def is_(self, _c, _v):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return _Exec(self._rows)


class _FakeSupabase:
    def __init__(self, token_rows, user_rows):
        self._tokens = token_rows
        self._users = user_rows

    def table(self, name):
        return _Chain(self._tokens if name == "tokens" else self._users)


def test_balance_fallback_resolves_real_user(monkeypatch):
    """MXOU 不可用(返回 inf→None) + 传入哈希租户 + 无 unlimited_quota 字段。

    修复前：users 表按哈希 id 查不到 → (0.0, False) → 「余额不足」。
    修复后：用 key 反查 tokens → 真实 user_id=113 + unlimited_quota=True →
           users.quota=10000000 → 放行。
    """
    monkeypatch.setattr("utils.mxou_api._check_balance_cached", lambda token, ttl=30.0: float("inf"))
    monkeypatch.setattr(
        main_mod,
        "get_supabase_client",
        lambda: _FakeSupabase(
            token_rows=[{"user_id": 113, "unlimited_quota": True, "status": 1}],
            user_rows=[{"quota": 10000000}],
        ),
    )

    balance, has_quota = main_mod._check_mxou_balance({
        "key": "ODyGgd9EN1D6jn69SMNTurAQspA4OkEnunJH8tBOhuFzDcfl",
        "user_id": "user_deadbeef",  # 复现 submit_task 传哈希租户
    })

    assert has_quota is True
    assert balance > 0


def test_balance_fallback_unlimited_zero_quota(monkeypatch):
    """unlimited_quota=True + users.quota=0：应仍放行（unlimited 优先于 quota）。"""
    monkeypatch.setattr("utils.mxou_api._check_balance_cached", lambda token, ttl=30.0: float("inf"))
    monkeypatch.setattr(
        main_mod,
        "get_supabase_client",
        lambda: _FakeSupabase(
            token_rows=[{"user_id": 113, "unlimited_quota": True, "status": 1}],
            user_rows=[{"quota": 0}],
        ),
    )

    _balance, has_quota = main_mod._check_mxou_balance({
        "key": "ODyGgd9EN1D6jn69SMNTurAQspA4OkEnunJH8tBOhuFzDcfl",
        "user_id": "user_deadbeef",
    })

    assert has_quota is True

"""v0.62.4 get_mxou_balance 解析：billing/subscription 无 balance 字段时
回退会话 /api/user/self 的 quota 或 soft_limit(有额度视为 >0)，避免误判余额不足。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import os
os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")

from utils import mxou_api  # noqa: E402


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload

    def get(self, _url, headers=None, timeout=None):
        return _Resp(200, self._payload)


def test_subscription_no_balance_falls_back_to_soft_limit(monkeypatch):
    """订阅响应无 balance 字段但有 soft_limit_usd(无限哨兵) → 视为有额度。"""
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)
    monkeypatch.setattr(
        mxou_api,
        "_get_session",
        lambda: _FakeSession({
            "soft_limit_usd": 100000000,
            "hard_limit_usd": 100000000,
            "has_payment_method": True,
        }),
    )
    monkeypatch.setattr(mxou_api, "_get_session_balance", lambda token: None)

    bal = mxou_api.get_mxou_balance("sk-ODyGgd9EN1D6jn69SMNTurAQspA4OkEnunJH8tBOhuFzDcfl")
    assert bal is not None
    assert bal > 0


def test_session_balance_quota_used(monkeypatch):
    """会话 /api/user/self 返回 quota=10000000 → /quota_per_unit=500000 → 20 元。"""
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)
    monkeypatch.setattr(
        mxou_api,
        "_get_session",
        lambda: _FakeSession({"object": "billing_subscription", "has_payment_method": True}),
    )
    monkeypatch.setattr(mxou_api, "_get_session_balance", lambda token: 20.0)

    bal = mxou_api.get_mxou_balance("sk-ODyGgd9EN1D6jn69SMNTurAQspA4OkEnunJH8tBOhuFzDcfl")
    assert bal == 20.0

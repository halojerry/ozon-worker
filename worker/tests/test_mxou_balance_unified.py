"""v0.29.3: MXOU 平台真实余额统一 — 欠费不再放行(此前 unlimited_quota 跳过实查)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_get_mxou_balance_prepends_sk_prefix(monkeypatch):
    """无 sk- 前缀 token 自动补前缀调 MXOU(与生产 key 存储一致)。"""
    import utils.mxou_api as mxou_api

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"object": "billing_subscription", "balance": 141629.24}

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            captured["url"] = url
            captured["auth"] = headers.get("Authorization", "")
            return FakeResp()

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    bal = mxou_api.get_mxou_balance("Ccpo3ziBuPH6daniA13XPDPGRem7m9OqsXPGZWvA5xK3eJyL")
    assert bal == 141629.24
    assert captured["auth"] == "Bearer sk-Ccpo3ziBuPH6daniA13XPDPGRem7m9OqsXPGZWvA5xK3eJyL"
    assert "billing/subscription" in captured["url"]


def test_get_mxou_balance_keeps_sk_prefix(monkeypatch):
    """已带 sk- 前缀不再重复补。"""
    import utils.mxou_api as mxou_api

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"balance": 5.0}

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            captured["auth"] = headers.get("Authorization", "")
            return FakeResp()

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    bal = mxou_api.get_mxou_balance("sk-abc123")
    assert bal == 5.0
    assert captured["auth"] == "Bearer sk-abc123"


def test_get_mxou_balance_negative(monkeypatch):
    """欠费 token → 返回负余额(调用方据此拒绝)。"""
    import utils.mxou_api as mxou_api

    class FakeResp:
        status_code = 200

        def json(self):
            return {"balance": -0.068388}

    monkeypatch.setattr(mxou_api, "_get_session", lambda: type("S", (), {"get": lambda self, *a, **k: FakeResp()})())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    bal = mxou_api.get_mxou_balance("sk-broke")
    assert bal == -0.068388
    assert bal <= 0  # 判定: 欠费


def test_get_mxou_balance_failure_returns_none(monkeypatch):
    """查询失败(HTTP 非200) → None(调用方降级)。"""
    import utils.mxou_api as mxou_api

    class FakeResp:
        status_code = 500

    monkeypatch.setattr(mxou_api, "_get_session", lambda: type("S", (), {"get": lambda self, *a, **k: FakeResp()})())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    assert mxou_api.get_mxou_balance("sk-x") is None
    assert mxou_api.get_mxou_balance("") is None

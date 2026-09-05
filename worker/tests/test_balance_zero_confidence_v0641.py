"""v0.64.1 余额误报修复 B1-B4：订阅账号 balance=0 哨兵不再误报 402。

背景：api.mxou.cn(newapi) 近期把订阅响应形态改了——给订阅/无限账号也返回字面
`balance` 字段（常为 0 哨兵，非欠费），并保留 soft/hard_limit=100M 哨兵。
旧 get_mxou_balance 在 balance<=0 时短路返回 0.0，不 consult limit，
经 _check_balance_cached 30s 缓存放大 → main submit_task 误报 402。

覆盖：
- B1 get_mxou_balance：字面 balance<=0 时 consult soft/hard_limit——
  有 >0 limit（订阅/无限 100M 哨兵）→ 返回 None 降级（调用方走兜底放行）；
  真欠费（balance<0 且无 limit）→ 原样返回负数拒绝；
  balance=0 且 limit=0 → 维持 0.0；balance>0 正常返回。
- B2 _check_balance_cached：首查 0.0 → 绕过 30s 缓存二次直查确认，防单次误读
  0.0 写缓存被后续 30s 全量复用放大大面积 402；直查非 0.0 用直查值。
- B3 main._check_mxou_balance：unlimited token + MXOU 实查降级(None) → 放行。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")

import pytest  # noqa: E402


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


@pytest.fixture(autouse=True)
def _reset_balance_cache():
    """每个用例重置模块级余额缓存，避免跨用例污染。"""
    import utils.mxou_api as mxou_api

    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    mxou_api._BALANCE_CACHE["fp"] = None
    yield
    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    mxou_api._BALANCE_CACHE["fp"] = None


def _patch_subscription_session(monkeypatch, payload):
    """mock billing/subscription GET 响应；会话 quota 路径置 None（不干扰字面 balance 分支）。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)
    monkeypatch.setattr(mxou_api, "_get_session", lambda: _FakeSession(payload))
    monkeypatch.setattr(mxou_api, "_get_session_balance", lambda token: None)


# ---------- B1: get_mxou_balance 订阅 0 哨兵处理 ----------


def test_zero_balance_with_soft_limit_treats_as_subscription(monkeypatch):
    """订阅账号 balance=0 + soft_limit=1e8(无限哨兵) → None（降级，让调用方兜底放行）。"""
    from utils import mxou_api

    _patch_subscription_session(
        monkeypatch, {"balance": 0, "soft_limit_usd": 1e8, "hard_limit_usd": 1e8}
    )
    bal = mxou_api.get_mxou_balance("sk-abc")
    assert bal is None, "订阅哨兵 balance=0 不应被当作欠费拒绝，应降级返回 None"


def test_negative_balance_no_limit_still_rejects(monkeypatch):
    """真欠费：balance=-0.5 且无 limit → 原样返回 -0.5（拒绝行为必须保留）。"""
    from utils import mxou_api

    _patch_subscription_session(monkeypatch, {"balance": -0.5})
    bal = mxou_api.get_mxou_balance("sk-abc")
    assert bal == -0.5


def test_zero_balance_zero_limit_rejects(monkeypatch):
    """balance=0 且 soft/hard_limit=0（无订阅哨兵）→ 维持 0.0（拒绝）。"""
    from utils import mxou_api

    _patch_subscription_session(
        monkeypatch, {"balance": 0, "soft_limit_usd": 0, "hard_limit_usd": 0}
    )
    bal = mxou_api.get_mxou_balance("sk-abc")
    assert bal == 0.0


def test_positive_balance_unchanged(monkeypatch):
    """balance>0 → 正常返回数值，不受 B1 影响。"""
    from utils import mxou_api

    _patch_subscription_session(monkeypatch, {"balance": 50})
    bal = mxou_api.get_mxou_balance("sk-abc")
    assert bal == 50.0


# ---------- B2: _check_balance_cached 0.0 特判二次直查 ----------


def test_check_balance_cached_rechecks_zero(monkeypatch):
    """首查 0.0 → 触发二次直查；直查返回正数 → 返回正数且 0.0 不进缓存。"""
    from utils import mxou_api

    calls = {"n": 0}
    results = iter([0.0, 42.0])

    def fake_balance(token):
        calls["n"] += 1
        return next(results)

    monkeypatch.setattr(mxou_api, "get_mxou_balance", fake_balance)

    val = mxou_api._check_balance_cached("tok")
    assert val == 42.0
    assert calls["n"] == 2, "首查得 0.0 时应触发一次二次直查"

    # 0.0 未污染缓存：TTL 内再调命中缓存返回 42.0，不再打余额接口
    assert mxou_api._check_balance_cached("tok") == 42.0
    assert calls["n"] == 2, "二次调用应命中缓存，不再触发直查"
    assert mxou_api._BALANCE_CACHE["value"] == 42.0


# ---------- B3: main 层 unlimited 用户不再被 402 ----------


class _Res:
    def __init__(self, rows):
        self.data = rows


class _Q:
    """supabase 查询链桩：忽略 select/eq/is_/limit，直接返回预设 rows。"""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Res(self._rows)


class _FakeSupabase:
    def __init__(self, tokens, users):
        self._tokens = tokens
        self._users = users

    def table(self, name):
        if name == "tokens":
            return _Q(self._tokens)
        if name == "users":
            return _Q(self._users)
        return _Q([])


def test_check_mxou_balance_unlimited_not_rejected(monkeypatch):
    """unlimited token + MXOU 实查降级(None) → Supabase 兜底放行（修复后不再 402）。

    回归场景：v0.64.1 前 subscription 账号 get_mxou_balance 短路返回 0.0 → main
    判 (0.0, False) → submit_task 402「MXOU 余额不足 (current: 0.0)」。B1 后实查
    返回 None（降级）→ 必须走 unlimited 兜底放行，绝不放行/拒绝错位。
    """
    import main as main_mod
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: None)
    fake = _FakeSupabase(
        tokens=[{"user_id": "u1", "unlimited_quota": True, "status": 1}],
        users=[{"quota": 0}],
    )
    monkeypatch.setattr(main_mod, "get_supabase_client", lambda: fake)

    balance, ok = main_mod._check_mxou_balance({"key": "tok123", "user_id": "u1"})
    assert ok is True, f"unlimited 用户 + MXOU 降级不应被拒，got balance={balance} ok={ok}"

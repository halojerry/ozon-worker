"""R1 (v0.62): 余额治理 — 401 out_of_quota 分类 + 低余额用户通知 + 缓存按 token 隔离。

覆盖：
- 401 响应（chat/image）→ 直接抛 MxouOutOfQuotaError，不重试不降级
- 0 < balance < BALANCE_ALERT_THRESHOLD → 30min 去重通知 TASK_NOTIFY_URL
- balance=None（查询失败）→ fail-open，不误伤、不触发告警
- BALANCE_ALERT_THRESHOLD 可配（环境变量）
- _check_balance_cached 按 token 指纹隔离缓存（多用户不互相污染）
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class _R:
    """fake requests.Response"""

    def __init__(self, code=200, j=None, text=""):
        self.status_code = code
        self._j = j or {}
        self.text = text

    def json(self):
        return self._j


@pytest.fixture(autouse=True)
def _reset_state():
    """每个用例重置余额缓存 + 告警去重表，避免跨用例污染。"""
    import utils.mxou_api as mxou_api

    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    mxou_api._BALANCE_CACHE["fp"] = None
    mxou_api._BALANCE_ALERT_TS.clear()
    yield
    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    mxou_api._BALANCE_CACHE["fp"] = None
    mxou_api._BALANCE_ALERT_TS.clear()


def test_chat_api_401_raises_out_of_quota(monkeypatch):
    """chat API 401（余额耗尽/认证失效）→ 直接抛，不再走普通 4xx 静默 None。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)
    post_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            post_calls.append(url)
            return _R(401, {}, "unauthorized")

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    with pytest.raises(mxou_api.MxouOutOfQuotaError) as exc_info:
        mxou_api.call_mxou_chat_api(token="tok", system_prompt="s", user_prompt="u")

    assert "OUT_OF_QUOTA" in str(exc_info.value)
    assert len(post_calls) == 1, "401 是永久性错误，不应重试"


def test_image_api_401_raises_out_of_quota(monkeypatch):
    """image API 401 → 直接抛，不重试不降级。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)
    post_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            post_calls.append(url)
            return _R(401, {}, "invalid credentials")

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    with pytest.raises(mxou_api.MxouOutOfQuotaError):
        mxou_api.call_mxou_image_api(token="tok", prompt="测试", model="gpt-image-2")

    assert len(post_calls) == 1


def test_low_balance_alert_sends_once_and_dedup(monkeypatch):
    """余额 < 阈值 → 通知 TASK_NOTIFY_URL；30min 内同 token 去重。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setenv("TASK_NOTIFY_URL", "https://sctapi.example/send")
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 10.0)
    notify_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            notify_calls.append((url, kwargs.get("json")))
            return _R(200, {})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    assert mxou_api._check_balance_cached("tok") == 10.0
    assert len(notify_calls) == 1
    body = notify_calls[0][1]
    assert body["type"] == "low_balance"
    assert body["balance"] == 10.0
    assert "请充值" in body["message"]

    # 清余额缓存强制重新查询 → 仍只通知一次（30min 去重）
    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["fp"] = None
    assert mxou_api._check_balance_cached("tok") == 10.0
    assert len(notify_calls) == 1, "30min 内同 token 不应重复通知"


def test_low_balance_alert_threshold_configurable(monkeypatch):
    """BALANCE_ALERT_THRESHOLD 可配：调高阈值后余额 20 不再告警。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setenv("TASK_NOTIFY_URL", "https://sctapi.example/send")
    monkeypatch.setenv("BALANCE_ALERT_THRESHOLD", "10")
    # 重新读环境变量（模块常量在 import 时已固化，此处模拟进程内变更）
    monkeypatch.setattr(mxou_api, "BALANCE_ALERT_THRESHOLD", 10.0)
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 20.0)
    notify_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            notify_calls.append(url)
            return _R(200, {})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    assert mxou_api._check_balance_cached("tok") == 20.0
    assert notify_calls == [], "余额 20 >= 阈值 10，不应告警"


def test_balance_query_failure_no_alert(monkeypatch):
    """余额查询失败（None）→ fail-open inf，不触发告警、不阻断。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setenv("TASK_NOTIFY_URL", "https://sctapi.example/send")
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: None)
    notify_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            notify_calls.append(url)
            return _R(200, {})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    bal = mxou_api._check_balance_cached("tok")
    assert bal == float("inf")
    assert notify_calls == []


def test_balance_cache_isolated_per_token(monkeypatch):
    """缓存按 token 指纹隔离：不同 token 不命中对方缓存。"""
    import utils.mxou_api as mxou_api

    values = {"tok_a": 100.0, "tok_b": 5.0}
    calls = {"n": 0}

    def fake_balance(token):
        calls["n"] += 1
        return values[token]

    monkeypatch.setattr(mxou_api, "get_mxou_balance", fake_balance)
    monkeypatch.setenv("TASK_NOTIFY_URL", "")  # 不触发真实通知
    monkeypatch.setattr(mxou_api, "BALANCE_ALERT_THRESHOLD", 0.0)  # 关闭告警避免噪音

    assert mxou_api._check_balance_cached("tok_a") == 100.0
    # 同 token 命中缓存（fp 一致）
    assert mxou_api._check_balance_cached("tok_a") == 100.0
    assert calls["n"] == 1

    # 不同 token 不命中 tok_a 的缓存（fp 不一致 → 重新查询）
    assert mxou_api._check_balance_cached("tok_b") == 5.0
    assert calls["n"] == 2

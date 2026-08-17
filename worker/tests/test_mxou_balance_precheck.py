"""T3/W12: MXOU 余额事中复查 — 生图前 fast-fail OUT_OF_QUOTA，防止烧帮豆。

覆盖：
- 余额 < MIN_BALANCE_THRESHOLD → call_mxou_image_api 入口抛 OUT_OF_QUOTA（零 POST）
- 余额充足 → 正常生图（mock session，不连真实 MXOU）
- _check_balance_cached 30s TTL：TTL 内二次调用不再打余额接口
- 余额查询失败 → fail-open（不阻断生图）
- 403/OUT_OF_QUOTA 响应不再当普通失败重试，直接抛（task 最终 fail）
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
def _reset_balance_cache():
    """每个用例重置模块级余额缓存，避免跨用例污染。"""
    import utils.mxou_api as mxou_api

    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    yield
    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0


def test_balance_below_threshold_raises_out_of_quota(monkeypatch):
    """余额 < 1.0 → 生图前 fast-fail，零 POST。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 0.5)
    post_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            post_calls.append(url)
            return _R(500, {})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    with pytest.raises(mxou_api.MxouOutOfQuotaError) as exc_info:
        mxou_api.call_mxou_image_api(token="tok", prompt="测试")

    assert "OUT_OF_QUOTA" in str(exc_info.value)
    assert post_calls == [], "余额不足时应生图前直接抛，绝不 POST 到 MXOU"


def test_balance_sufficient_proceeds(monkeypatch):
    """余额充足 → 正常走生图流程并返回图片 URL。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)

    class FakeSession:
        def post(self, url, **kwargs):
            return _R(200, {"status": "succeeded", "results": [{"url": "http://img/1.png"}]})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    url = mxou_api.call_mxou_image_api(token="tok", prompt="测试", model="gpt-image-2")
    assert url == "http://img/1.png"


def test_check_balance_cached_ttl(monkeypatch):
    """_check_balance_cached 30s TTL：TTL 内二次调用不打余额接口，过期后重新查。"""
    import utils.mxou_api as mxou_api

    calls = {"n": 0}

    def fake_balance(token):
        calls["n"] += 1
        return 42.0

    monkeypatch.setattr(mxou_api, "get_mxou_balance", fake_balance)

    assert mxou_api._check_balance_cached("tok") == 42.0
    assert mxou_api._check_balance_cached("tok") == 42.0  # TTL 命中，不重新打余额接口
    assert calls["n"] == 1

    # TTL 过期 → 重新查余额接口
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    assert mxou_api._check_balance_cached("tok") == 42.0
    assert calls["n"] == 2


def test_balance_query_failure_fail_open(monkeypatch):
    """余额查询失败（None）→ fail-open：返回 ≥ 阈值，不阻断生图。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: None)

    bal = mxou_api._check_balance_cached("tok")
    assert bal >= mxou_api.MIN_BALANCE_THRESHOLD


def test_403_out_of_quota_no_retry(monkeypatch):
    """生图 POST 收到 403/OUT_OF_QUOTA → 不再当普通失败重试，直接抛。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)
    post_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            post_calls.append(url)
            return _R(403, {}, "out of quota")

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    with pytest.raises(mxou_api.MxouOutOfQuotaError) as exc_info:
        mxou_api.call_mxou_image_api(token="tok", prompt="测试", model="gpt-image-2")

    assert "OUT_OF_QUOTA" in str(exc_info.value)
    assert len(post_calls) == 1, "403 是永久性错误，不得重试（task 最终 fail）"


def test_chat_balance_below_threshold_raises_out_of_quota(monkeypatch):
    """chat 通道（LLM 调用）余额 < 1.0 → 同样 pre-check fast-fail，零 POST。

    W12 修复：591 次 insufficient_user_quota 全是 chat 调用（deepseek-v4-flash），
    原只修了 image 通道漏了 chat——余额不足必须 task 明确 fail「请充值」，
    不能静默返回 None 级联成属性空/翻译失败。
    """
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 0.5)
    post_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            post_calls.append(url)
            return _R(500, {})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    with pytest.raises(mxou_api.MxouOutOfQuotaError) as exc_info:
        mxou_api.call_mxou_chat_api(token="tok", system_prompt="s", user_prompt="u")

    assert "OUT_OF_QUOTA" in str(exc_info.value)
    assert "LLM" in str(exc_info.value)
    assert post_calls == [], "余额不足时应 chat 调用前直接抛，绝不 POST 到 MXOU"


def test_chat_403_out_of_quota_no_retry(monkeypatch):
    """chat POST 收到 403/insufficient_user_quota → 不再当普通 4xx 静默返回 None，直接抛。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)
    post_calls = []

    class FakeSession:
        def post(self, url, **kwargs):
            post_calls.append(url)
            return _R(403, {}, '{"error":{"message":"预扣费额度失败","code":"insufficient_user_quota"}}')

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    with pytest.raises(mxou_api.MxouOutOfQuotaError) as exc_info:
        mxou_api.call_mxou_chat_api(token="tok", system_prompt="s", user_prompt="u")

    assert "OUT_OF_QUOTA" in str(exc_info.value)
    assert len(post_calls) == 1, "403 是永久性错误，不得重试"


def test_mxou_llm_reexports_out_of_quota_error():
    """mxou_llm 纯 re-export：MxouOutOfQuotaError 可直接从 mxou_llm import 且类型一致。"""
    import utils.mxou_llm as mxou_llm
    import utils.mxou_api as mxou_api

    assert mxou_llm.MxouOutOfQuotaError is mxou_api.MxouOutOfQuotaError
    assert mxou_llm.call_mxou_chat_api is mxou_api.call_mxou_chat_api

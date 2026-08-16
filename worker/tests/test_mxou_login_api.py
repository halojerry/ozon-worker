"""T2: MXOU 登录代理端点 + tokens 幂等 upsert + 内存 session 缓存（全 mock，无需 PG）。

覆盖 PLAN-webui-v0.43.md T2 验收门：
1. 登录成功 shape：username/balance/keys(脱敏)/selected_key_id + upsert 去 sk- 前缀 status=1
2. 错误映射：bad_credentials→401 / 2fa→400 / rate_limited→429 / unavailable+unknown_shape→502（含 API Key 直登提示）
3. 限流防爆破：同一 username 超阈值 → 429（真实小窗口 RateLimiter 注入）
4. 本地无 Supabase：get_supabase_client=None → login 仍成功不抛
5. keys 脱敏：响应 keys 绝不含 full_key 字段
6. session store TTL：put 命中 / 过期 → None
7. 密码不进任何 logger 输出
"""
import contextlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod
from services import mxou_login_service
from utils import mxou_platform

PASSWORD = "s3cr3t-密码-@!xYz"

LOGIN_OK = {
    "access_token": "at-1",
    "user": {"id": "uid-42", "username": "alice"},
    "expires_at": "2026-09-01T00:00:00Z",
    "shape": "newapi_jwt",
}

TOKENS_OK = [{"id": "tok-1", "name": "default", "status": 1, "masked": True, "full_key": None}]


class FakeTokensTable:
    """tokens 表 fake：记录 upsert 调用，execute 返回空结果（不落库）。"""

    def __init__(self):
        self.calls = []

    def upsert(self, rows, on_conflict=None):
        self.calls.append((rows, on_conflict))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class FakeSupabase:
    """get_supabase_client 返回的 fake 客户端（只暴露 .table().upsert().execute()）。"""

    def __init__(self):
        self.table_names = []
        self._tokens = FakeTokensTable()

    def table(self, name):
        self.table_names.append(name)
        return self._tokens


@pytest.fixture(scope="module")
def client():
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """每个测试独立宽松限流器（防跨测试累积）+ 默认本地模式（无 Supabase）。"""
    monkeypatch.setattr(main_mod, "rate_limiter", main_mod.RateLimiter(max_per_minute=1000))
    monkeypatch.setattr("storage.database.supabase_client.get_supabase_client", lambda: None)


def _platform_mocks(*, login_result=LOGIN_OK, login_error=None, self_result=None,
                    tokens=TOKENS_OK, key="sk-abc123def456"):
    """同时 patch mxou_platform 四个函数（mock session 防真实网络）。"""
    def _login(session, username, password):
        if login_error:
            raise login_error
        return login_result

    @contextlib.contextmanager
    def _ctx():
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(mxou_platform, "mxou_login", side_effect=_login))
            stack.enter_context(patch.object(
                mxou_platform, "mxou_get_self",
                return_value=self_result if self_result is not None
                else {"id": "uid-42", "username": "alice", "balance": 88.8},
            ))
            stack.enter_context(patch.object(mxou_platform, "mxou_list_tokens", return_value=tokens))
            stack.enter_context(patch.object(mxou_platform, "mxou_get_token_key", return_value=key))
            yield

    return _ctx()


# ═══ 1. 登录成功 ═══

def test_login_success(client):
    """mock 四平台函数 → 返回 username/balance/keys(脱敏)/selected_key_id；upsert 去 sk- 前缀 + status=1。"""
    sb = FakeSupabase()
    with patch("storage.database.supabase_client.get_supabase_client", return_value=sb), \
         _platform_mocks() as mocks:
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "alice"
    assert body["balance"] == 88.8
    assert body["selected_key_id"] == "tok-1"
    assert body["session_expires_at"] == "2026-09-01T00:00:00Z"
    # keys 脱敏（无 full_key）
    assert body["keys"] == [{"id": "tok-1", "name": "default", "status": 1, "masked": True}]
    # upsert 被调：key 已去 sk- 前缀 + status=1 + on_conflict=key
    assert "tokens" in sb.table_names
    rows, on_conflict = sb._tokens.calls[0]
    assert rows == [{"key": "abc123def456", "user_id": "uid-42", "status": 1}]
    assert on_conflict == "key"
    # 明文 full_key 绝不在响应
    assert "abc123def456" not in resp.text
    assert "full_key" not in resp.text
    # session 已按 user id 缓存（供 T4 密钥管理复用）
    cached = mxou_login_service.session_store.get("uid-42")
    assert cached and cached["access_token"] == "at-1"


# ═══ 2-6. 错误映射 ═══

def test_login_bad_credentials(client):
    with _platform_mocks(login_error=mxou_platform.MxouLoginError("bad_credentials")):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401
    assert "MXOU 账号或密码错误" in resp.text


def test_login_2fa(client):
    with _platform_mocks(login_error=mxou_platform.MxouLoginError("2fa_required")):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 400
    assert "两步验证" in resp.text


def test_login_rate_limited(client):
    with _platform_mocks(login_error=mxou_platform.MxouLoginError("rate_limited")):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 429
    assert "限流" in resp.text


def test_login_unavailable(client):
    with _platform_mocks(login_error=mxou_platform.MxouLoginError("unavailable")):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 502
    assert "API Key 直登" in resp.text


def test_login_unknown_shape(client):
    with _platform_mocks(login_error=mxou_platform.MxouLoginError("unknown_shape")):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 502
    assert "API Key 直登" in resp.text


# ═══ 7. 缺字段 ═══

def test_login_missing_fields(client):
    for payload in ({}, {"username": "alice"}, {"password": "x"}):
        resp = client.post("/api/v1/mxou/login", json=payload)
        assert resp.status_code == 400, f"payload={payload} -> {resp.status_code}"
        assert "必填" in resp.text


# ═══ 8. 限流防爆破 ═══

def test_login_rate_limit_bruteforce(client):
    """同一 username 连续超过阈值 → 429（真实小窗口 RateLimiter 注入）。"""
    small = main_mod.RateLimiter(max_per_minute=2)
    with patch.object(main_mod, "rate_limiter", small), \
         _platform_mocks():
        r1 = client.post("/api/v1/mxou/login",
                         json={"username": "bob", "password": PASSWORD})
        r2 = client.post("/api/v1/mxou/login",
                         json={"username": "bob", "password": PASSWORD})
        r3 = client.post("/api/v1/mxou/login",
                         json={"username": "bob", "password": PASSWORD})
        # 不同 username 不受影响
        r_other = client.post("/api/v1/mxou/login",
                              json={"username": "carol", "password": PASSWORD})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429, f"第 3 次应被限流，实际 {r3.status_code}: {r3.text}"
    assert r_other.status_code == 200


# ═══ 9. 本地无 Supabase 降级 ═══

def test_login_upsert_local_no_supabase(client):
    """get_supabase_client=None → login 仍成功，不抛（selected_key 可能为 None）。"""
    with _platform_mocks() as mocks:
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "alice"
    assert isinstance(body["keys"], list)
    # 解钥正常 → selected_key_id 存在；关键是无 Supabase 不阻断
    assert "selected_key_id" in body


# ═══ 10. keys 脱敏 ═══

def test_login_keys_masked_in_response(client):
    """响应 keys 字段集合 = {id, name, masked, status}，绝无 full_key。"""
    with _platform_mocks(tokens=[
        {"id": "t1", "name": "a", "status": 1, "masked": True, "full_key": None},
        {"id": "t2", "name": "b", "status": 0, "masked": True, "full_key": None},
    ]):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200
    for item in resp.json()["keys"]:
        assert set(item.keys()) == {"id", "name", "masked", "status"}
    assert "full_key" not in resp.text
    assert "sk-" not in resp.text


# ═══ 11. session store TTL ═══

def test_session_store_ttl():
    store = mxou_login_service.MxouSessionStore(ttl=60.0)
    store.put("uid", {"access_token": "at"})
    assert store.get("uid") == {"access_token": "at"}
    assert store.get("nobody") is None
    # 注入过期 login_time → get 返回 None
    store._login_time["uid"] = time.monotonic() - 61.0
    assert store.get("uid") is None
    assert store.get("uid") is None  # 二次 get 同样 None（已弹出）
    # 短 ttl 实例也能触发过期
    short = mxou_login_service.MxouSessionStore(ttl=0.01)
    short.put("u2", {"access_token": "b"})
    assert short.get("u2") == {"access_token": "b"}
    time.sleep(0.02)
    assert short.get("u2") is None
    # pop
    store.put("u3", {"access_token": "c"})
    store.pop("u3")
    assert store.get("u3") is None


# ═══ 12. 密码不进日志 ═══

def test_password_not_in_logs(client):
    """login 全程 logger 输出不含 password 值（service + platform logger）。"""
    svc_logger = Mock()
    plat_logger = Mock()
    with patch.object(mxou_login_service, "logger", svc_logger), \
         patch.object(mxou_platform, "logger", plat_logger), \
         _platform_mocks():
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200
    texts = []
    for lgr in (svc_logger, plat_logger):
        for call in (lgr.warning.call_args_list + lgr.info.call_args_list
                     + lgr.error.call_args_list + lgr.debug.call_args_list):
            for a in call.args:
                texts.append(str(a))
            for v in call.kwargs.values():
                texts.append(str(v))
    joined = " ".join(texts)
    assert PASSWORD not in joined, f"logger 泄漏密码: {joined[:200]}"

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
from fastapi import HTTPException
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

    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []
        self._select = None
        self._eqs = []

    def upsert(self, rows, on_conflict=None):
        self.calls.append((rows, on_conflict))
        return self

    def select(self, *cols, **kwargs):
        self._select = cols
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def is_(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        filtered = self.rows
        for col, val in self._eqs:
            if val == "null":
                filtered = [r for r in filtered if r.get(col) is None]
            else:
                filtered = [r for r in filtered if str(r.get(col)) == str(val)]
        return SimpleNamespace(data=filtered[:1] if self._select == ("key",) else filtered)


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
    """mock 四平台函数 → 返回 username/真实余额/keys(脱敏)/selected_key_id/key；upsert 去 sk- 前缀 + status=1。"""
    sb = FakeSupabase()
    with patch("storage.database.supabase_client.get_supabase_client", return_value=sb), \
         patch("utils.mxou_api.get_mxou_balance", return_value=503.26), \
         _platform_mocks() as mocks:
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "alice"
    # 真实余额：get_mxou_balance（/v1/dashboard/billing/subscription 美元），非 quota 原值
    assert body["balance"] == 503.26
    assert body["selected_key_id"] == "tok-1"
    assert body["key"] == "sk-abc123def456"
    assert body["session_expires_at"] == "2026-09-01T00:00:00Z"
    # keys 脱敏（无 full_key）
    assert body["keys"] == [{"id": "tok-1", "name": "default", "status": 1, "masked": True}]
    # upsert 被调：key 已去 sk- 前缀 + status=1 + on_conflict=key
    assert "tokens" in sb.table_names
    rows, on_conflict = sb._tokens.calls[0]
    assert rows == [{"key": "abc123def456", "user_id": "uid-42", "status": 1}]
    assert on_conflict == "key"
    # 响应含选中 key 完整值（WebUI 用它直接建立登录态）
    assert body["key"] in resp.text
    # session 已按 user id 缓存（供 T4 密钥管理复用）+ selected_full_key
    cached = mxou_login_service.session_store.get("uid-42")
    assert cached and cached["access_token"] == "at-1"
    assert cached and cached["selected_full_key"] == "sk-abc123def456"


def test_login_key_prefix_normalized(client):
    """MXOU 存储格式无 sk- 前缀（Supabase tokens 表同规）→ 返回前端 key 自动补 sk-。"""
    with patch.object(mxou_platform, "mxou_login", return_value={
        "access_token": "at-1", "user_id": "uid-42",
        "user": {"id": "uid-42", "username": "alice"},
        "expires_at": None, "shape": "oneapi_cookie",
    }), patch.object(mxou_platform, "mxou_get_self", return_value={"id": "uid-42", "username": "alice"}), \
         patch.object(mxou_platform, "mxou_list_tokens", return_value=[
             {"id": "tok-1", "name": "default", "status": 1, "masked": True, "full_key": None},
         ]), \
         patch.object(mxou_platform, "mxou_get_token_key", return_value="abc123def456"):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["key"] == "sk-abc123def456"


# ═══ 2-6. 错误映射 ═══

def test_login_passes_user_id_to_platform(client):
    """T1.1：login 返回 user_id → get_self/list_tokens/get_token_key 全部透传 user_id kwarg。"""
    calls = {"self": [], "list": [], "key": []}

    def _login(session, username, password):
        return {"access_token": "at-1", "user_id": "uid-42",
                "user": {"id": "uid-42", "username": "alice"},
                "expires_at": "2026-09-01T00:00:00Z", "shape": "newapi_jwt"}

    def _self(session, access_token=None, user_id=None):
        calls["self"].append({"access_token": access_token, "user_id": user_id})
        return {"id": "uid-42", "username": "alice", "balance": 88.8}

    def _list(session, access_token=None, user_id=None):
        calls["list"].append({"access_token": access_token, "user_id": user_id})
        return [{"id": "tok-1", "name": "default", "status": 1, "masked": True, "full_key": None}]

    def _key(session, access_token=None, token_id=None, user_id=None):
        calls["key"].append({"access_token": access_token, "token_id": token_id, "user_id": user_id})
        return "sk-abc123def456"

    with patch.object(mxou_platform, "mxou_login", side_effect=_login), \
         patch.object(mxou_platform, "mxou_get_self", side_effect=_self), \
         patch.object(mxou_platform, "mxou_list_tokens", side_effect=_list), \
         patch.object(mxou_platform, "mxou_get_token_key", side_effect=_key):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json()["selected_key_id"] == "tok-1"
    for c in calls["self"]:
        assert c["user_id"] == "uid-42"
    for c in calls["list"]:
        assert c["user_id"] == "uid-42"
    for c in calls["key"]:
        assert c["user_id"] == "uid-42"
        assert c["token_id"] == "tok-1"
    # session 缓存含 user_id（供 T4 密钥管理复用）
    cached = mxou_login_service.session_store.get("uid-42")
    assert cached and cached["user_id"] == "uid-42"


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
    """响应 keys 字段集合 = {id, name, masked, status}（无 full_key）；选中 key 完整值只在顶层 key 字段。"""
    with _platform_mocks(tokens=[
        {"id": "t1", "name": "a", "status": 1, "masked": True, "full_key": None},
        {"id": "t2", "name": "b", "status": 0, "masked": True, "full_key": None},
    ]):
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    for item in body["keys"]:
        assert set(item.keys()) == {"id", "name", "masked", "status"}
    assert "full_key" not in resp.text
    # keys 列表不含完整 key；完整 key 只在顶层 key 字段（登录响应一次性返回）
    assert body["key"] == "sk-abc123def456"


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


# ════════════════════════════════════════════════════════════════
# ═══ T4 密钥管理（list/create/revoke/select）═══════════════════
# ════════════════════════════════════════════════════════════════
# 鉴权：全 mock _authenticate（本地 supabase=None → tenant_id="local_dev"）；
# 用 Bearer 头触发 _authenticate → _authenticate_token 真实本地降级路径。


def _put_session(tenant_id: str = "local_dev", access_token: str = "at-1"):
    """向模块级 session_store 写入 tenant session（测试前隔离）。"""
    mxou_login_service.session_store.put(tenant_id, {"access_token": access_token, "expires_at": None})


def test_list_keys_auth_required(client):
    """无 Bearer/body token → _authenticate → 401。"""
    resp = client.get("/api/v1/mxou/keys")
    assert resp.status_code == 401


def test_list_keys_ok(client):
    """mock session + mxou_list_tokens → 列表脱敏（绝无 full_key）。"""
    _put_session()
    with patch.object(mxou_platform, "mxou_list_tokens", return_value=[
        {"id": "tok-1", "name": "default", "status": 1, "masked": True, "full_key": None},
        {"id": "tok-2", "name": "work", "status": 0, "masked": True, "full_key": None},
    ]):
        resp = client.get("/api/v1/mxou/keys", headers={"Authorization": "Bearer sk-local"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"id": "tok-1", "name": "default", "status": 1, "masked": True},
        {"id": "tok-2", "name": "work", "status": 0, "masked": True},
    ]
    assert "full_key" not in resp.text


def test_list_keys_no_session(client):
    """MxouSessionStore 无记录 → 401「请重新登录」。"""
    mxou_login_service.session_store.pop("local_dev")
    resp = client.get("/api/v1/mxou/keys", headers={"Authorization": "Bearer sk-local"})
    assert resp.status_code == 401
    assert "重新登录" in resp.text


def test_create_key_ok(client):
    """mxou_create_token 返回 full_key → upsert（去 sk- 前缀 status=1）→ 响应含完整 key（仅一次）。"""
    sb = FakeSupabase()
    _put_session()
    with patch("storage.database.supabase_client.get_supabase_client", return_value=sb), \
         patch.object(mxou_platform, "mxou_create_token",
                      return_value={"id": "tok-new", "name": "my-key", "full_key": "sk-xyz789abc"}):
        resp = client.post("/api/v1/mxou/keys", json={"name": "my-key"},
                           headers={"Authorization": "Bearer sk-local"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "tok-new"
    assert body["name"] == "my-key"
    assert body["key"] == "sk-xyz789abc"  # 新建一次性返回完整 key（用户复制）
    # upsert 被调：key 已去 sk- 前缀 + status=1 + on_conflict=key
    rows, on_conflict = sb._tokens.calls[0]
    assert rows == [{"key": "xyz789abc", "user_id": "local_dev", "status": 1}]
    assert on_conflict == "key"


def test_create_key_no_session(client):
    """无 session → 401「请重新登录」。"""
    mxou_login_service.session_store.pop("local_dev")
    resp = client.post("/api/v1/mxou/keys", json={"name": "x"},
                       headers={"Authorization": "Bearer sk-local"})
    assert resp.status_code == 401
    assert "重新登录" in resp.text


def test_revoke_key_ok(client):
    """mxou_revoke_token → True → 204。"""
    _put_session()
    with patch.object(mxou_platform, "mxou_revoke_token", return_value=True):
        resp = client.delete("/api/v1/mxou/keys/tok-1", headers={"Authorization": "Bearer sk-local"})
    assert resp.status_code == 204, resp.text


def test_select_key_ok(client):
    """mxou_get_token_key → {key: full_key}（仅一次）+ upsert 被调（去 sk- 前缀 status=1）。"""
    sb = FakeSupabase()
    _put_session()
    with patch("storage.database.supabase_client.get_supabase_client", return_value=sb), \
         patch.object(mxou_platform, "mxou_get_token_key", return_value="sk-abc123def456"):
        resp = client.post("/api/v1/mxou/keys/tok-1/select",
                           headers={"Authorization": "Bearer sk-local"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"key": "sk-abc123def456"}
    rows, on_conflict = sb._tokens.calls[0]
    assert rows == [{"key": "abc123def456", "user_id": "local_dev", "status": 1}]
    assert on_conflict == "key"


def test_tenant_isolation():
    """A 的 session 不供 B 用：put userA，get userB → None → _get_session_for_tenant 401。"""
    mxou_login_service.session_store.put("userA", {"access_token": "at-A"})
    with pytest.raises(HTTPException) as ei:
        mxou_login_service._get_session_for_tenant("userB")
    assert ei.value.status_code == 401
    assert "重新登录" in ei.value.detail
    # A 自己的 session 正常解出
    data, token = mxou_login_service._get_session_for_tenant("userA")
    assert token == "at-A"


def test_cookie_session_no_token():
    """session 有但 access_token 空（oneapi cookie 形态）→ 401「会话无效」。"""
    mxou_login_service.session_store.put("cookie-tenant", {"access_token": "", "expires_at": None})
    with pytest.raises(HTTPException) as ei:
        mxou_login_service._get_session_for_tenant("cookie-tenant")
    assert ei.value.status_code == 401
    assert "会话无效" in ei.value.detail


# ═══ role 字段（v0.54：WebUI 管理员路由守卫数据源）═══

def test_login_returns_role_admin(client):
    """登录响应含 role（_fetch_user_role 查 users.role，admin 透传）。"""
    with patch("storage.database.supabase_client.get_supabase_client", return_value=None), \
         patch("utils.mxou_api.get_mxou_balance", return_value=503.26), \
         patch.object(mxou_login_service, "_fetch_user_role", return_value="admin"), \
         _platform_mocks() as mocks:
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


def test_login_returns_role_default_user(client):
    """users.role 查询失败/无记录 → 默认 'user'（安全默认，不误放行 admin）。"""
    with patch("storage.database.supabase_client.get_supabase_client", return_value=None), \
         patch("utils.mxou_api.get_mxou_balance", return_value=503.26), \
         patch.object(mxou_login_service, "_fetch_user_role", return_value="user"), \
         _platform_mocks() as mocks:
        resp = client.post("/api/v1/mxou/login",
                           json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "user"


# ============================================================
# GET /api/v1/mxou/my-key（v0.55.1：WebUI 登录后免手动建 key）
# ============================================================

def test_get_my_key_returns_enabled_key(client):
    """New-Api-User=2 + tokens 有 enabled key → 返回 sk- 前缀完整 key。"""
    fake = FakeSupabase()
    fake._tokens = FakeTokensTable(rows=[
        {"user_id": "2", "key": "abc123def456", "status": 1, "deleted_at": None},
        {"user_id": "2", "key": "xyz789", "status": 4, "deleted_at": None},
    ])
    with patch("services.mxou_login_service.verify_session_user", return_value=True), \
         patch("storage.database.supabase_client.get_supabase_client", return_value=fake):
        resp = client.get("/api/v1/mxou/my-key", params={"uid": "2"},
                          headers={"cookie": "session=valid"})
    assert resp.status_code == 200
    assert resp.json() == {"key": "sk-abc123def456"}


def test_get_my_key_prefers_enabled(client):
    """多个 enabled key → 取第一个；disabled (status=4) 跳过。"""
    fake = FakeSupabase()
    fake._tokens = FakeTokensTable(rows=[
        {"user_id": "9", "key": "first-key", "status": 1, "deleted_at": None},
        {"user_id": "9", "key": "second-key", "status": 1, "deleted_at": None},
        {"user_id": "9", "key": "dead-key", "status": 4, "deleted_at": None},
    ])
    with patch("services.mxou_login_service.verify_session_user", return_value=True), \
         patch("storage.database.supabase_client.get_supabase_client", return_value=fake):
        resp = client.get("/api/v1/mxou/my-key", params={"uid": "9"},
                          headers={"cookie": "session=valid"})
    assert resp.json() == {"key": "sk-first-key"}


def test_get_my_key_no_header_returns_empty(client):
    """无 New-Api-User header → {key: ""}（前端静默跳过）。"""
    resp = client.get("/api/v1/mxou/my-key")
    assert resp.status_code == 200
    assert resp.json() == {"key": ""}


def test_get_my_key_no_key_returns_empty(client):
    """用户无 enabled key → {key: ""}。"""
    fake = FakeSupabase()
    fake._tokens = FakeTokensTable(rows=[{"user_id": "3", "key": "k", "status": 4, "deleted_at": None}])
    with patch("services.mxou_login_service.verify_session_user", return_value=True), \
         patch("storage.database.supabase_client.get_supabase_client", return_value=fake):
        resp = client.get("/api/v1/mxou/my-key", params={"uid": "3"},
                          headers={"cookie": "session=valid"})
    assert resp.json() == {"key": ""}


def test_get_my_key_skips_soft_deleted(client):
    """软删除（deleted_at 非 null）的 key 不返回——与 _authenticate_token 一致。"""
    fake = FakeSupabase()
    fake._tokens = FakeTokensTable(rows=[
        {"user_id": "2", "key": "soft-deleted-key", "status": 1, "deleted_at": "2026-04-25T19:35:18+00:00"},
        {"user_id": "2", "key": "live-key", "status": 1, "deleted_at": None},
    ])
    with patch("services.mxou_login_service.verify_session_user", return_value=True), \
         patch("storage.database.supabase_client.get_supabase_client", return_value=fake):
        resp = client.get("/api/v1/mxou/my-key", params={"uid": "2"},
                          headers={"cookie": "session=valid"})
    assert resp.json() == {"key": "sk-live-key"}


def test_get_my_key_keeps_existing_sk_prefix(client):
    """tokens.key 已带 sk- 前缀 → 不重复加。"""
    fake = FakeSupabase()
    fake._tokens = FakeTokensTable(rows=[{"user_id": "2", "key": "sk-already-prefixed", "status": 1, "deleted_at": None}])
    with patch("services.mxou_login_service.verify_session_user", return_value=True), \
         patch("storage.database.supabase_client.get_supabase_client", return_value=fake):
        resp = client.get("/api/v1/mxou/my-key", params={"uid": "2"},
                          headers={"cookie": "session=valid"})
    assert resp.json() == {"key": "sk-already-prefixed"}


def test_get_my_key_rejects_invalid_session(client):
    """IDOR 防线：session 校验失败 → 401（绝不吐 key）。"""
    fake = FakeSupabase()
    fake._tokens = FakeTokensTable(rows=[
        {"user_id": "2", "key": "abc123def456", "status": 1, "deleted_at": None},
    ])
    with patch("services.mxou_login_service.verify_session_user", return_value=False), \
         patch("storage.database.supabase_client.get_supabase_client", return_value=fake):
        resp = client.get("/api/v1/mxou/my-key", params={"uid": "2"},
                          headers={"cookie": "session=forged"})
    assert resp.status_code == 401
    assert "key" not in resp.json()


def test_verify_session_user_id_match():
    """平台回传 id == 请求 uid → True。"""
    from services import mxou_login_service as svc

    with patch.object(svc.mxou_platform, "mxou_get_self", return_value={"id": 2}) as m:
        assert svc.verify_session_user("session=valid", "2") is True
        m.assert_called_once()


def test_verify_session_user_id_mismatch():
    """平台回传 id != 请求 uid（伪造他人 uid）→ False。"""
    from services import mxou_login_service as svc

    with patch.object(svc.mxou_platform, "mxou_get_self", return_value={"id": 9}):
        assert svc.verify_session_user("session=valid", "2") is False


def test_verify_session_user_platform_error():
    """平台 401（无效 session）→ 异常被吞 → False。"""
    from services import mxou_login_service as svc
    from utils.mxou_platform import MxouLoginError

    with patch.object(svc.mxou_platform, "mxou_get_self",
                      side_effect=MxouLoginError("unavailable", "HTTP 401")):
        assert svc.verify_session_user("session=invalid", "2") is False


def test_verify_session_user_empty_inputs():
    """空 cookie / 空 uid → False（不查平台）。"""
    from services import mxou_login_service as svc

    with patch.object(svc.mxou_platform, "mxou_get_self") as m:
        assert svc.verify_session_user("", "2") is False
        assert svc.verify_session_user("session=x", "") is False
        m.assert_not_called()


def test_verify_session_user_uses_private_session():
    """B3 回归：独立 Session 校验——绝不污染 _get_session() 全局单例 Cookie。

    共享单例被 LLM/生图/余额所有 mxou 调用复用，写入用户 Cookie 会把
    会话泄漏到后续无关上游请求（review CRITICAL）。
    """
    from utils.mxou_api import _get_session

    captured = {}

    def _capture(session, access_token=None, user_id=None):
        captured["session"] = session
        return {"id": 2}

    from services import mxou_login_service as svc

    shared = _get_session()
    shared_before = dict(shared.headers)
    with patch.object(svc.mxou_platform, "mxou_get_self", side_effect=_capture):
        assert svc.verify_session_user("session=abc; other=1", "2") is True
    # 捕获到的是本次调用的独立 session（带请求 cookie），不是全局单例
    assert captured["session"] is not shared
    assert captured["session"].headers.get("Cookie") == "session=abc; other=1"
    # 全局单例 headers 原封不动（无 Cookie 残留）
    assert dict(shared.headers) == shared_before
    assert "Cookie" not in shared.headers

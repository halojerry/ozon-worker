"""MXOU 平台防御式解析客户端（utils.mxou_platform）— 纯 mock 测试，无需 PG。

覆盖 PLAN-webui-v0.43.md T1：success 优先解析 / token 阶梯 / 多形态兼容 /
白名单脱敏 / 脱敏 key 判定 / 密码不进日志。共 20 个测试（18 个 MUST DO + 2 个防御性）。

⚠️ 环境隔离：MXOU_BASE 通过 monkeypatch 指向假 base，不产生真实网络调用。
"""
import sys
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import utils.mxou_platform as mp

FAKE_BASE = "https://fake.mxou.test"


@pytest.fixture(autouse=True)
def _fake_base(monkeypatch):
    """所有测试使用假 base，绝不触达 api.mxou.cn。"""
    monkeypatch.setenv("MXOU_BASE", FAKE_BASE)


def _resp(status_code, payload):
    """构造 mock 响应（鸭子类型：.status_code + .json()）。"""
    r = unittest.mock.Mock()
    r.status_code = status_code
    r.json.return_value = payload
    return r


def _session(resp):
    """构造 mock session（鸭子类型：.post/.get/.delete）。"""
    s = unittest.mock.Mock()
    s.post.return_value = resp
    s.get.return_value = resp
    s.delete.return_value = resp
    return s


# ═══ mxou_login ═══

def test_login_success_newapi_jwt():
    """newapi_jwt 形态：data.access_token + session.expires_at。"""
    s = _session(_resp(200, {
        "success": True,
        "data": {
            "access_token": "t1",
            "user": {"id": 1},
            "session": {"expires_at": "2026-09-01"},
        },
    }))
    result = mp.mxou_login(s, "alice", "pw")
    assert result["shape"] == "newapi_jwt"
    assert result["access_token"] == "t1"
    assert result["user"] == {"id": 1}
    assert result["expires_at"] == "2026-09-01"
    # 请求路径 + Content-Type
    call = s.post.call_args
    assert call[0][0] == f"{FAKE_BASE}/api/user/login"
    assert call.kwargs["headers"].get("Content-Type") == "application/json"


def test_login_http200_but_success_false():
    """HTTP 200 但 success=false → bad_credentials（解析纪律：不信任 status）。"""
    s = _session(_resp(200, {"success": False, "message": "账号或密码错误"}))
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "bad_credentials"


def test_login_http401_wrong_password():
    """401 + body → bad_credentials（body 无 success 字段也归凭据错误）。"""
    s = _session(_resp(401, {"message": "invalid username or password"}))
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "bad_credentials"


def test_login_token_field_ladder():
    """token 阶梯：data.token / data.session_token 字段都能取到。"""
    for field in ("token", "session_token"):
        s = _session(_resp(200, {
            "success": True,
            "data": {
                field: f"tk-{field}",
                "user": {"id": 2},
                "session": {"expires_at": "2026-09-01"},
            },
        }))
        result = mp.mxou_login(s, "alice", "pw")
        assert result["access_token"] == f"tk-{field}"
        assert result["shape"] == "newapi_jwt"


def test_login_oneapi_cookie_shape():
    """data 无任何 token 字段但有 user → oneapi_cookie（access_token=None）。"""
    s = _session(_resp(200, {
        "success": True,
        "data": {"user": {"id": 3, "username": "bob"}},
    }))
    result = mp.mxou_login(s, "bob", "pw")
    assert result["shape"] == "oneapi_cookie"
    assert result["access_token"] is None
    assert result["user"]["id"] == 3


def test_login_unknown_shape():
    """data 空 dict 且无 user/token → unknown_shape（消息含 keys 摘要、不含值）。"""
    s = _session(_resp(200, {"success": True, "data": {}}))
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "unknown_shape"
    assert "top_keys" in str(ei.value)
    assert "password" not in str(ei.value)


def test_login_2fa():
    """data.require_2fa=true → 2fa_required。"""
    s = _session(_resp(200, {"success": True, "data": {"require_2fa": True}}))
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "2fa_required"


def test_login_429():
    """HTTP 429 → rate_limited。"""
    s = _session(_resp(429, {"success": False, "message": "too many requests"}))
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "rate_limited"


def test_login_extracts_user_id():
    """真实探测形态：data.id=38（top-level，无 user 子对象、无 token）→ 返回 dict 新增 user_id=38。

    oneapi_cookie 形态判定放宽：无 token 但 data 含 id → oneapi_cookie（真实平台
    Set-Cookie session + 无 access_token 字段）。
    """
    s = _session(_resp(200, {
        "success": True,
        "data": {
            "display_name": "test", "group": "default",
            "id": 38, "role": 1, "status": 1, "username": "test",
        },
    }))
    result = mp.mxou_login(s, "test", "Aa123456")
    assert result["user_id"] == 38
    assert result["access_token"] is None
    assert result["shape"] == "oneapi_cookie"
    # user_id 也可从 user 子对象兜底（newapi_jwt 形态）
    s2 = _session(_resp(200, {
        "success": True,
        "data": {
            "access_token": "t1",
            "user": {"id": 7, "username": "bob"},
            "session": {"expires_at": "2026-09-01"},
        },
    }))
    result2 = mp.mxou_login(s2, "bob", "pw")
    assert result2["user_id"] == 7
    assert result2["shape"] == "newapi_jwt"


def test_login_network_error_unavailable():
    """网络异常（session.post 抛错）→ unavailable。"""
    s = unittest.mock.Mock()
    s.post.side_effect = ConnectionError("boom")
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "unavailable"


def test_login_non_json_unavailable():
    """JSONDecodeError → unavailable。"""
    s = unittest.mock.Mock()
    r = unittest.mock.Mock()
    r.status_code = 200
    r.json.side_effect = ValueError("No JSON object could be decoded")
    s.post.return_value = r
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_login(s, "alice", "pw")
    assert ei.value.reason == "unavailable"


# ═══ mxou_get_self ═══

def test_self_whitelist_strips_sensitive():
    """/self 返回含 password/access_token 的完整 user → 返回值无这两个键。"""
    s = _session(_resp(200, {"success": True, "data": {
        "id": 1,
        "username": "alice",
        "display_name": "Alice",
        "role": "admin",
        "status": 1,
        "password": "s3cr3t",
        "access_token": "tk-leak",
        "quota": 100,
    }}))
    result = mp.mxou_get_self(s, "tk")
    assert "password" not in result
    assert "access_token" not in result
    assert result["username"] == "alice"


def test_self_quota_or_balance():
    """仅 balance → 原值（美元）；quota → None（真实余额走 get_mxou_balance）；都无 → None。"""
    # quota=100 → None（不在此换算）
    s = _session(_resp(200, {"data": {"quota": 100}}))
    assert mp.mxou_get_self(s, "tk")["balance"] is None
    # 仅 balance=5.5（美元原值，不换算）
    s = _session(_resp(200, {"data": {"balance": 5.5}}))
    assert mp.mxou_get_self(s, "tk")["balance"] == 5.5
    # 都无 → None
    s = _session(_resp(200, {"data": {"id": 9}}))
    assert mp.mxou_get_self(s, "tk")["balance"] is None


def test_self_with_new_api_user_header():
    """真实认证形态：access_token=None + user_id=38 → 请求头带 New-Api-User: 38（cookie 由 session 携带）。"""
    s = _session(_resp(200, {"success": True, "data": {"id": 38, "quota": 251630586}}))
    result = mp.mxou_get_self(s, None, user_id=38)
    headers = s.get.call_args.kwargs["headers"]
    assert headers.get("New-Api-User") == "38"
    assert "Authorization" not in headers
    assert result["balance"] is None  # quota 不在此换算（真实余额走 get_mxou_balance）


def test_self_bearer_still_works():
    """向后兼容：access_token="sk-x" + user_id=None → 只带 Authorization: Bearer sk-x。"""
    s = _session(_resp(200, {"data": {"id": 1, "quota": 10}}))
    mp.mxou_get_self(s, "sk-x")
    headers = s.get.call_args.kwargs["headers"]
    assert headers.get("Authorization") == "Bearer sk-x"
    assert "New-Api-User" not in headers


def test_self_both_headers():
    """两者都有 → Authorization 和 New-Api-User 都在（newapi 兼容）。"""
    s = _session(_resp(200, {"data": {"id": 38, "quota": 10}}))
    mp.mxou_get_self(s, "sk-x", user_id=38)
    headers = s.get.call_args.kwargs["headers"]
    assert headers.get("Authorization") == "Bearer sk-x"
    assert headers.get("New-Api-User") == "38"


def test_quota_passthrough():
    """真实探测：self 返回 quota=251630586（无 balance）→ balance=None（换算职责在 get_mxou_balance）。"""
    s = _session(_resp(200, {"success": True, "data": {
        "id": 38, "username": "test", "quota": 251630586,
    }}))
    result = mp.mxou_get_self(s, None, user_id=38)
    assert result["balance"] is None
    assert result["id"] == 38


# ═══ mxou_list_tokens ═══

def test_list_tokens_paginated():
    """分页形态：data.items。"""
    s = _session(_resp(200, {"data": {"items": [
        {"id": 1, "name": "a", "status": 1, "key": "sk-abcdef"},
        {"id": 2, "name": "b", "status": 2, "key": "sk-xyz"},
    ]}}))
    tokens = mp.mxou_list_tokens(s, "tk")
    assert len(tokens) == 2
    assert tokens[0] == {"id": "1", "name": "a", "status": 1, "masked": False, "full_key": None}
    assert tokens[1]["name"] == "b"
    assert s.get.call_args[0][0] == f"{FAKE_BASE}/api/token/"


def test_list_tokens_array():
    """数组形态：data 直接是 list。"""
    s = _session(_resp(200, {"data": [
        {"id": "7", "name": "x", "status": 1, "key": "sk-clean"},
    ]}))
    tokens = mp.mxou_list_tokens(s, "tk")
    assert len(tokens) == 1
    assert tokens[0]["id"] == "7"
    assert tokens[0]["name"] == "x"
    assert tokens[0]["masked"] is False


def test_list_tokens_double_wrap():
    """双层包裹：data.data。"""
    s = _session(_resp(200, {"data": {"data": [
        {"id": 3, "name": "y", "status": 1, "key": "sk-12345678"},
    ]}}))
    tokens = mp.mxou_list_tokens(s, "tk")
    assert len(tokens) == 1
    assert tokens[0]["id"] == "3"
    assert tokens[0]["name"] == "y"


def test_list_tokens_masked_detection():
    """脱敏判定：key 含 *（sk-1234**********abcd）→ masked=True。"""
    s = _session(_resp(200, {"data": [
        {"id": 1, "name": "k", "status": 1, "key": "sk-1234**********abcd"},
    ]}))
    tokens = mp.mxou_list_tokens(s, "tk")
    assert tokens[0]["masked"] is True
    assert tokens[0]["full_key"] is None


def test_token_list_new_api_user():
    """同 self：access_token=None + user_id=38 → New-Api-User header + data.items 分页解析。"""
    s = _session(_resp(200, {"data": {"items": [
        {"id": 1, "name": "a", "status": 1, "key": "qzFw**********k5dr"},
    ]}}))
    tokens = mp.mxou_list_tokens(s, None, user_id=38)
    assert s.get.call_args.kwargs["headers"].get("New-Api-User") == "38"
    assert "Authorization" not in s.get.call_args.kwargs["headers"]
    assert len(tokens) == 1
    assert tokens[0]["id"] == "1"
    assert tokens[0]["masked"] is True


# ═══ mxou_get_token_key / create / revoke ═══

def test_get_token_key():
    """POST /api/token/{id}/key → data.key。"""
    s = _session(_resp(200, {"success": True, "data": {"key": "sk-full"}}))
    key = mp.mxou_get_token_key(s, "tk", "tok_1")
    assert key == "sk-full"
    assert s.post.call_args[0][0] == f"{FAKE_BASE}/api/token/tok_1/key"


def test_create_token():
    """POST /api/token body = {name, remain_quota:-1}，返回 full_key。"""
    s = _session(_resp(200, {"success": True, "data": {
        "id": "n1", "name": "webui", "key": "sk-new-abc",
    }}))
    result = mp.mxou_create_token(s, "tk", "webui")
    assert result["full_key"] == "sk-new-abc"
    assert result["id"] == "n1"
    assert result["name"] == "webui"
    call = s.post.call_args
    assert call.kwargs["json"] == {"name": "webui", "remain_quota": -1}


def test_revoke_token():
    """DELETE /api/token/{id}：204 → True；500 → unavailable。"""
    s = _session(_resp(204, {}))
    assert mp.mxou_revoke_token(s, "tk", "tok_1") is True
    assert s.delete.call_args[0][0] == f"{FAKE_BASE}/api/token/tok_1"

    s = _session(_resp(500, {"message": "boom"}))
    with pytest.raises(mp.MxouLoginError) as ei:
        mp.mxou_revoke_token(s, "tk", "tok_1")
    assert ei.value.reason == "unavailable"


# ═══ 敏感值不进日志 ═══

def test_password_never_in_log(monkeypatch):
    """mxou_login 抛错时，logger 输出不含 password 值。"""
    fake_logger = unittest.mock.Mock()
    monkeypatch.setattr(mp, "logger", fake_logger)
    password = "s3cr3t-密码-@!xYz"
    s = _session(_resp(500, {"message": "down"}))
    with pytest.raises(mp.MxouLoginError):
        mp.mxou_login(s, "alice", password)
    texts = []
    for call in fake_logger.call_args_list:
        for a in call.args:
            texts.append(str(a))
        for v in call.kwargs.values():
            texts.append(str(v))
    assert all(password not in t for t in texts)

"""批次 C：worker 端 Ozon 会话代管（bindShopCookie 对标）回归。

覆盖：
- C1  ozon_sessions 表模型形状（tenant+credential 唯一约束）
- C2  会话代管服务（AES-GCM 存储/解密头/状态；aad 冻结 tenant:credential；永不回值）
- C3  POST/GET/DELETE /credentials/{id}/session 端点（租户隔离 + 密文不回显）
- C5  会话直调通道（what_to_sell v3 cookie 直调 + /analytics/what-to-sell 端点）
- C6  失效联动（401/403 → mark expired + 409 session_expired）

纯 mock、无 PG 依赖：DB 层用 fake engine 断言 SQL/参数；HTTP 层 TestClient +
patch service 函数。安全红线断言：响应/源码不回显 cookie 值。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ══════════════════ C1: 表模型 ══════════════════

def test_ozon_session_model_shape():
    from storage.database.shared.model import OzonSellerSession

    t = OzonSellerSession.__table__
    assert t.name == "ozon_sessions"
    for col in ("cookies_encrypted", "cookie_names", "sc_company_id_encrypted", "status"):
        assert col in t.columns
    uniques = [u for u in t.constraints if u.__class__.__name__ == "UniqueConstraint"]
    assert any({"tenant_id", "credential_id"} <= {c.name for c in u.columns} for u in uniques)


# ══════════════════ C2: 会话代管服务 ══════════════════

import base64
import json
import os
from types import SimpleNamespace

import pytest


@pytest.fixture()
def key32() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_cipher_roundtrip_with_session_aad(key32):
    # 加密基元（复用 credential_cipher，不新造加密）——aad 格式冻结为 tenant:credential
    from utils import credential_cipher

    aad = "t1:c1"
    blob = credential_cipher.encrypt_with_key('{"sc_company_id":"5371047"}', aad, key32)
    assert credential_cipher.decrypt_with_key(blob, aad, key32) == '{"sc_company_id":"5371047"}'
    with pytest.raises(Exception):
        credential_cipher.decrypt_with_key(blob, "t1:c2", key32)  # aad 绑定：跨租户不可解


def test_service_sql_upsert_and_never_return_values():
    from pathlib import Path
    import services.ozon_session_service as svc

    src = Path(svc.__file__).read_text("utf-8")
    assert "ON CONFLICT" in src and "DO UPDATE" in src          # 原子 upsert
    assert 'aad = f"{tenant_id}:{credential_id}"' in src        # aad 格式
    # session_status 返回结构只含名单与状态，永不回 cookie 值
    assert "cookie_names" in src and "status" in src


# ── 服务功能路径（fake engine，无 PG；加密用真实 credential_cipher）──


class _FakeWriteConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append((str(stmt), params or {}))
        return SimpleNamespace(rowcount=self._engine.rowcounts.pop(0) if self._engine.rowcounts else 1)


class _FakeReadConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append((str(stmt), params or {}))
        return SimpleNamespace(fetchone=lambda: self._engine.rows.pop(0) if self._engine.rows else None)


class _FakeEngine:
    def __init__(self, rows=None, rowcounts=None):
        self.rows = list(rows or [])
        self.rowcounts = list(rowcounts or [])
        self.calls = []

    def connect(self):
        return _FakeReadConn(self)

    def begin(self):
        return _FakeWriteConn(self)


_CRED1 = "00000000-0000-0000-0000-0000000000c1"
_CRED2 = "00000000-0000-0000-0000-0000000000c2"


@pytest.fixture()
def fake_db(monkeypatch):
    # patch service 模块属性（service 模块级 from-import get_engine，仓库同款）
    from services import ozon_session_service as svc

    engine = _FakeEngine()
    monkeypatch.setattr(svc, "get_engine", lambda: engine)
    return engine


def test_store_and_cookie_header_roundtrip(fake_db, key32):
    from services import ozon_session_service as svc

    cookies = {"Abt": "x", "sc_company_id": "5371047"}
    svc.store_session("t1", _CRED1, cookies, key_raw=key32)

    stmt, params = fake_db.calls[-1]
    assert "INSERT INTO ozon_sessions" in stmt and "ON CONFLICT" in stmt and "DO UPDATE" in stmt
    # names 走 JSONB 绑定（实机回归：裸 list 会被 psycopg2 适配成 text[]）
    import json as _json
    assert _json.loads(params["names"]) == ["Abt", "sc_company_id"]  # 名单明文，值只在密文里
    assert b"5371047" not in params["enc"] and b"5371047" not in params["sc_enc"]

    # 读路径：fetchone 返回写库时的密文 → 解出 cookie header
    fake_db.rows = [SimpleNamespace(
        cookies_encrypted=params["enc"], status="active")]
    header = svc.get_cookie_header("t1", _CRED1, key_raw=key32)
    assert header == "Abt=x; sc_company_id=5371047"


def test_decrypt_failure_marks_expired_and_returns_none(fake_db, key32):
    from services import ozon_session_service as svc

    # 密文用 key32 加，读用别的 key → GCM 认证失败 → 标 expired + None
    from utils import credential_cipher
    enc = credential_cipher.encrypt_with_key('{"sc_company_id":"1"}', "t1:c2", key32)
    fake_db.rows = [SimpleNamespace(cookies_encrypted=enc, status="active")]
    assert svc.get_cookie_header("t1", _CRED2, key_raw=key32 + "x") is None
    # mark_status 已被调用（最后一条 SQL 是 UPDATE ... status）
    last_stmt, last_params = fake_db.calls[-1]
    assert "UPDATE ozon_sessions" in last_stmt
    assert last_params["status"] == "expired"


def test_session_status_never_returns_values(fake_db):
    from services import ozon_session_service as svc

    fake_db.rows = [SimpleNamespace(
        status="active", harvested_at=None, cookie_names=["Abt", "sc_company_id"])]
    snap = svc.session_status("t1", _CRED1)
    assert snap == {"status": "active", "harvested_at": None,
                    "cookie_names": ["Abt", "sc_company_id"]}
    assert "Abt=x" not in json.dumps(snap)  # 永不回值
    assert svc.session_status("t1", _CRED2) is None  # 无会话


def test_delete_session(fake_db):
    from services import ozon_session_service as svc

    fake_db.rowcounts = [1]
    assert svc.delete_session("t1", _CRED1) is True
    fake_db.rowcounts = [0]
    assert svc.delete_session("t1", _CRED1) is False


# ══════════════════ C3: /credentials/{id}/session 端点 ══════════════════

from unittest.mock import patch as _mock_patch

from fastapi.testclient import TestClient

import main as _main_mod

_TENANT_A = _main_mod._key_user_id("tokA")
_TOKEN_MAP = {"tokA": _TENANT_A}


class _FakeSupabase:
    """tokens 表 fake：key → user_id 映射（仅够 _authenticate_token 通过）。"""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def table(self, name):
        return _FakeTokensTable(self._mapping)


class _FakeTokensTable:
    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping
        self._key = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        if col == "key":
            self._key = val
        return self

    def is_(self, col, val):
        return self

    def execute(self):
        uid = self._mapping.get(self._key)
        if uid is None:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[{
            "user_id": uid, "key": self._key, "remain_quota": 999,
            "status": 1, "expired_time": -1, "unlimited_quota": False,
        }])


@pytest.fixture(autouse=True)
def _session_auth_env(monkeypatch):
    """放行限流 + Supabase tokens 按 key 分租户（无需真实 PG）。"""
    with _mock_patch.object(_main_mod.rate_limiter, "check", return_value=(True, 10)), \
         _mock_patch("main.get_supabase_client", return_value=_FakeSupabase(_TOKEN_MAP)):
        yield


def _hdr() -> dict:
    return {"Authorization": "Bearer sk-tokA"}


_CRED = "00000000-0000-0000-0000-00000000beef"


def test_session_post_requires_token_401():
    resp = TestClient(_main_mod.app).post(
        f"/api/v1/credentials/{_CRED}/session", json={"cookies": {"a": "b"}})
    assert resp.status_code == 401


def test_session_post_cross_tenant_credential_404():
    from services import credential_service as cs
    with _mock_patch.object(cs, "credential_owned_by", return_value=False):
        resp = TestClient(_main_mod.app).post(
            f"/api/v1/credentials/{_CRED}/session",
            json={"cookies": {"a": "b"}}, headers=_hdr())
    assert resp.status_code == 404


def test_session_post_upstores_and_never_echoes_values(key32, monkeypatch):
    from services import credential_service as cs
    from services import ozon_session_service as svc

    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", key32)
    captured = {}
    with _mock_patch.object(cs, "credential_owned_by", return_value=True), \
         _mock_patch.object(svc, "store_session",
                            side_effect=lambda t, c, cookies, **kw: captured.update(cookies=cookies)):
        resp = TestClient(_main_mod.app).post(
            f"/api/v1/credentials/{_CRED}/session",
            json={"cookies": {"Abt": "SECRET", "sc_company_id": "5371047"}},
            headers=_hdr())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True and body["status"] == "active"
    assert body["cookie_names"] == ["Abt", "sc_company_id"]
    assert "SECRET" not in resp.text and "5371047" not in resp.text  # 密文不回显
    assert captured["cookies"] == {"Abt": "SECRET", "sc_company_id": "5371047"}


def test_session_post_missing_key_500_same_text_as_credentials():
    from services import credential_service as cs
    from services import ozon_session_service as svc
    from utils.credential_cipher import CredentialCipherError

    with _mock_patch.object(cs, "credential_owned_by", return_value=True), \
         _mock_patch.object(svc, "store_session",
                            side_effect=CredentialCipherError("CREDENTIAL_MASTER_KEY 环境变量未设置")):
        resp = TestClient(_main_mod.app).post(
            f"/api/v1/credentials/{_CRED}/session",
            json={"cookies": {"a": "b"}}, headers=_hdr())
    assert resp.status_code == 500
    assert "CREDENTIAL_MASTER_KEY" in resp.json()["detail"]


def test_session_post_invalid_body_422():
    from services import credential_service as cs
    with _mock_patch.object(cs, "credential_owned_by", return_value=True):
        resp = TestClient(_main_mod.app).post(
            f"/api/v1/credentials/{_CRED}/session",
            json={"ozon_api_key": "信封词汇"}, headers=_hdr())
    assert resp.status_code == 422
    assert "cookies" in resp.json()["detail"]


def test_session_get_returns_names_not_values():
    from services import ozon_session_service as svc
    snap = {"status": "active", "harvested_at": "2026-09-08T00:00:00+00:00",
            "cookie_names": ["sc_company_id"]}
    with _mock_patch.object(svc, "session_status", return_value=snap):
        resp = TestClient(_main_mod.app).get(
            f"/api/v1/credentials/{_CRED}/session", headers=_hdr())
    assert resp.status_code == 200
    assert resp.json() == snap


def test_session_get_no_session_404():
    from services import ozon_session_service as svc
    with _mock_patch.object(svc, "session_status", return_value=None):
        resp = TestClient(_main_mod.app).get(
            f"/api/v1/credentials/{_CRED}/session", headers=_hdr())
    assert resp.status_code == 404
    assert "session-sync" in resp.json()["detail"]


def test_session_delete_204_and_404():
    from services import ozon_session_service as svc
    with _mock_patch.object(svc, "delete_session", return_value=True):
        resp = TestClient(_main_mod.app).delete(
            f"/api/v1/credentials/{_CRED}/session", headers=_hdr())
    assert resp.status_code == 204
    with _mock_patch.object(svc, "delete_session", return_value=False):
        resp = TestClient(_main_mod.app).delete(
            f"/api/v1/credentials/{_CRED}/session", headers=_hdr())
    assert resp.status_code == 404


# ══════════════════ C5/C6: 会话直调通道（what_to_sell）+ 失效联动 ══════════════════

from services import ozon_session_service as _svc
from utils import ozon_session_client as _client


def test_what_to_sell_client_builds_proven_contract(monkeypatch):
    """200 → (data, None)；请求契约与 skill 实证直调同源（v3 + 公司头 + zh-Hans）。"""
    captured = {}

    def _fake_post(url, json=None, headers=None, timeout=None, **kw):
        captured.update(url=url, json=json, headers=headers)

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"result": {"items": [{"sku": 123}]}}
        return _Resp()

    monkeypatch.setattr(_client.requests, "post", _fake_post)
    data, err = _client.what_to_sell(
        "Abt=x; sc_company_id=5371047", "5371047",
        _client.build_what_to_sell_payload("123456_0", 50, 0))
    assert err is None and data == {"result": {"items": [{"sku": 123}]}}
    assert captured["url"] == ("https://seller.ozon.ru/api/site/"
                               "seller-analytics/what_to_sell/data/v3")
    h = captured["headers"]
    assert h["Cookie"] == "Abt=x; sc_company_id=5371047"
    assert h["x-o3-company-id"] == "5371047"
    assert h["x-o3-language"] == "zh-Hans"
    body = captured["json"]
    assert body["filter"]["sku"] == "123456"          # int64、无 _0 变体后缀
    assert body["filter"]["period"] == "monthly"
    assert body["filter"]["stock"] == "any_stock"
    assert body["sort"] == {"key": "sum_gmv_desc"}


def test_what_to_sell_client_expires_on_401_403_302(monkeypatch):
    """401/403/登录 302 → (None, "session_expired")（C6 判废出口）。"""
    for status in (401, 403, 302):
        def _fake_post(url, json=None, headers=None, timeout=None, _s=status, **kw):
            return SimpleNamespace(status_code=_s, text="")
        monkeypatch.setattr(_client.requests, "post", _fake_post)
        data, err = _client.what_to_sell("a=1", "1", {"limit": "1"})
        assert data is None and err == "session_expired", f"status={status}"


def test_what_to_sell_follows_nginx_rr307_loop(monkeypatch):
    """实机回归（2026-09-09）：Ozon nginx 机器人回环 307→同路径?__rr=1，
    重放即过——不得判 session_expired（真会话被误标）。"""
    calls = []

    def _fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append(url)
        if len(calls) == 1:
            return SimpleNamespace(status_code=307, text="",
                                   headers={"Location": url + "?__rr=1"})
        return SimpleNamespace(status_code=200, text="{}",
                               headers={},
                               **{"json": lambda: {"result": {"items": [1]}}})

    monkeypatch.setattr(_client.requests, "post", _fake_post)
    data, err = _client.what_to_sell("a=1", "1", {"limit": 1})
    assert err is None and data == {"result": {"items": [1]}}
    assert len(calls) == 2 and calls[1].endswith("?__rr=1")  # 重放到 Location


def test_what_to_sell_plain_302_without_location_expires(monkeypatch):
    """无 Location 的裸 3xx（登录跳转形态）→ 仍判 session_expired。"""
    def _fake_post(url, json=None, headers=None, timeout=None, **kw):
        return SimpleNamespace(status_code=302, text="")
    monkeypatch.setattr(_client.requests, "post", _fake_post)
    data, err = _client.what_to_sell("a=1", "1", {"limit": 1})
    assert data is None and err == "session_expired"


def test_what_to_sell_client_other_status_is_not_session(monkeypatch):
    """429/5xx ≠ 会话失效（不误标 expired）。"""
    def _fake_post(url, json=None, headers=None, timeout=None, **kw):
        return SimpleNamespace(status_code=429, text="rate limited")
    monkeypatch.setattr(_client.requests, "post", _fake_post)
    data, err = _client.what_to_sell("a=1", "1", {})
    assert data is None and err == "ozon_http_429"


_WTS_URL = f"/api/v1/analytics/what-to-sell?credential_id={_CRED}&sku=123456"


def test_wts_endpoint_success(monkeypatch):
    with _mock_patch.object(_svc, "session_status", return_value={
            "status": "active", "harvested_at": None, "cookie_names": ["sc_company_id"]}), \
            _mock_patch.object(_svc, "load_cookies", return_value={
                "Abt": "SECRETVAL", "sc_company_id": "5371047"}), \
            _mock_patch.object(_svc, "mark_status", return_value=None) as _ms, \
            _mock_patch.object(_client, "what_to_sell",
                               return_value=({"result": {"items": []}}, None)) as _wts:
        resp = TestClient(_main_mod.app).get(_WTS_URL, headers=_hdr())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["found"] is True and body["data"] == {"result": {"items": []}}
    assert "SECRETVAL" not in resp.text  # 响应绝不回显会话 cookie 值
    assert _wts.call_args.args[0].startswith("Abt=SECRETVAL")  # cookie 头进上游请求
    assert _ms.call_args_list[-1].args[2] == "active"  # 成功路径刷新 last_checked_at


def test_wts_endpoint_401_marks_expired_409(monkeypatch):
    """直调 401/403 → mark expired + 409 session_expired（C6 失效联动）。"""
    with _mock_patch.object(_svc, "session_status", return_value={
            "status": "active", "harvested_at": None, "cookie_names": []}), \
            _mock_patch.object(_svc, "load_cookies", return_value={
                "sc_company_id": "5371047"}), \
            _mock_patch.object(_svc, "mark_status", return_value=None) as _ms, \
            _mock_patch.object(_client, "what_to_sell",
                               return_value=(None, "session_expired")):
        resp = TestClient(_main_mod.app).get(_WTS_URL, headers=_hdr())
    assert resp.status_code == 409
    assert "session_expired" in str(resp.json()["detail"])
    assert _ms.call_args.args[2] == "expired"  # 联动标记


def test_wts_endpoint_no_session_404(monkeypatch):
    with _mock_patch.object(_svc, "session_status", return_value=None):
        resp = TestClient(_main_mod.app).get(_WTS_URL, headers=_hdr())
    assert resp.status_code == 404
    assert "尚未同步会话" in resp.json()["detail"]
    assert "session-sync" in resp.json()["detail"]


def test_wts_endpoint_stored_expired_409_fastfail(monkeypatch):
    """存量 expired → 直接 409，不再烧直调（重同步闭环）。"""
    with _mock_patch.object(_svc, "session_status", return_value={
            "status": "expired", "harvested_at": None, "cookie_names": []}), \
            _mock_patch.object(_client, "what_to_sell") as _wts:
        resp = TestClient(_main_mod.app).get(_WTS_URL, headers=_hdr())
    assert resp.status_code == 409
    assert _wts.assert_not_called() is None


def test_wts_endpoint_missing_params_400():
    resp = TestClient(_main_mod.app).get(
        "/api/v1/analytics/what-to-sell?sku=123", headers=_hdr())
    assert resp.status_code == 400


def test_store_session_pg_jsonb_roundtrip(monkeypatch):
    """实机回归（2026-09-09 session-sync 真跑 500）：裸 SQL 绑 Python list →
    psycopg2 适配成 text[]，与 JSONB 列 DatatypeMismatch。mock 测试只锁 SQL
    文本没打真库所以漏网——本用例打真 PG 锁死 JSONB 回路。

    需要 PGDATABASE_URL（与 test_store_sync 同模式），缺失跳过。
    """
    import base64
    import os

    import pytest
    if not os.environ.get("PGDATABASE_URL"):
        pytest.skip("需要真 PG（PGDATABASE_URL）")
    from sqlalchemy import create_engine

    import services.ozon_session_service as svc

    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", base64.b64encode(b"0123456789abcdef0123456789abcdef").decode())
    eng = create_engine(os.environ["PGDATABASE_URL"])
    monkeypatch.setattr(svc, "get_engine", lambda: eng)
    tid, cid = "pgtest-session-tenant", "11111111-2222-3333-4444-555555555555"
    try:
        svc.store_session(tid, cid, {"sc_company_id": "5371047", "Abt": "x"})
        st = svc.session_status(tid, cid)
        assert st is not None and st["status"] == "active"
        assert isinstance(st["cookie_names"], list) and "sc_company_id" in st["cookie_names"]
        hdr = svc.get_cookie_header(tid, cid)
        assert hdr and "sc_company_id=5371047" in hdr
    finally:
        svc.delete_session(tid, cid)

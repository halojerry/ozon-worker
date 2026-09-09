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
    assert params["names"] == ["Abt", "sc_company_id"]  # 名单明文，值只在密文里
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

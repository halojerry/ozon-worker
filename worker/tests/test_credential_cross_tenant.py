"""T5: 店铺跨租户一次绑定拦截（create + store 双入口）测试。

背景（计划 todo 5）：A 用户(tenant1)已绑店 X，B 用户(tenant2)再绑 X → 409。

关键洞（评审 H1）：\\``store_credential``\\`` ON CONFLICT (tenant_id, ozon_client_id) DO UPDATE``
是幂等 upsert——B 用 store_credential 绑 A 的店 X 时，不同 tenant 下无约束冲突会 INSERT 成功，
绕过 uq_credentials_tenant_client（租户内唯一）。因此必须用 \\``_assert_client_not_bound_elsewhere``
前置预检同时盖住 create_credential / store_credential 两个入口。

本测试用纯 mock 的内存凭证表（无真实 PG）：拦截逻辑走真实 SQL 语义（precheck SELECT /
create INSERT / store ON CONFLICT upsert / IntegrityError），验证 HTTPException 409。

不改的表结构 / 不改的同租户行为（create 409 → IntegrityError；store 幂等 409 不触发）由
对应用例锁定。
"""
import datetime
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api.schemas import CredentialCreate
from services import credential_service as svc

# 32 字节 AES-256 主密钥（与 test_credentials_api 同口径）
MASTER_KEY = "0123456789abcdef0123456789abcdef"

TENANT_A = "t-a"
TENANT_B = "t-b"


# ============================================================
# 内存凭证表：实现真实 SQL 语义（precheck / create / store upsert）
# ============================================================

class _FakeRow(SimpleNamespace):
    pass


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
        self.rowcount = len(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _CredStore:
    """内存多租户凭证仓：模拟 credentials 表的 (tenant_id, ozon_client_id) 行为。"""

    def __init__(self):
        self._rows = []  # each: dict(tenant_id, ozon_client_id, ...)

    def _other_tenant(self, client_id, tenant_id):
        return any(
            r["tenant_id"] != tenant_id and r["ozon_client_id"] == client_id
            for r in self._rows
        )

    def _same_tenant(self, tenant_id, client_id):
        return any(
            r["tenant_id"] == tenant_id and r["ozon_client_id"] == client_id
            for r in self._rows
        )

    def upsert(self, tenant_id, client_id, params):
        for r in self._rows:
            if r["tenant_id"] == tenant_id and r["ozon_client_id"] == client_id:
                r["ozon_api_key_enc"] = params["enc"]
                r["api_key_masked"] = params["masked"]
                return r
        return self._add(tenant_id, client_id, params)

    def _add(self, tenant_id, client_id, params):
        row = {
            "tenant_id": tenant_id,
            "ozon_client_id": client_id,
            "id": uuid.uuid4(),
            "api_key_masked": params.get("masked", "****"),
            "shop_name": params.get("shop_name"),
            "currency": params.get("currency", "CNY"),
            "is_default": params.get("is_default", False),
            "credential_type": params.get("credential_type", "api_key"),
            "status": "active",
            "last_validated_at": None,
            "last_rotated_at": params.get("last_rotated_at"),
            "created_at": datetime.datetime.now(datetime.timezone.utc),
            "updated_at": datetime.datetime.now(datetime.timezone.utc),
        }
        self._rows.append(row)
        return row


class _FakeConn:
    def __init__(self, store):
        self._store = store

    def execute(self, sql, params=None):
        s = str(sql)
        params = params or {}

        # 预检：SELECT 1 FROM credentials WHERE ozon_client_id=:client_id AND tenant_id != :tenant_id
        if "WHERE ozon_client_id" in s and "tenant_id !=" in s:
            found = self._store._other_tenant(params["client_id"], params["tenant_id"])
            return _FakeResult([SimpleNamespace(_1=1)] if found else [])

        # create_credential：INSERT ... RETURNING 全列（无 ON CONFLICT）→ 同租户重号抛 IntegrityError
        if s.lstrip().startswith("INSERT INTO credentials") and "ON CONFLICT" not in s:
            tid, cid = params["tenant_id"], params["ozon_client_id"]
            if self._store._same_tenant(tid, cid):
                raise IntegrityError("uq_credentials_tenant_client", params, Exception("dup"))
            self._store._add(tid, cid, params)
            row = self._store._rows[-1]
            return _FakeResult([self._to_select_row(row)])

        # store_credential：INSERT ... ON CONFLICT (tenant_id, ozon_client_id) DO UPDATE
        if s.lstrip().startswith("INSERT INTO credentials") and "ON CONFLICT" in s:
            tid, cid = params["tenant_id"], params["ozon_client_id"]
            row = self._store.upsert(tid, cid, params)
            return _FakeResult([SimpleNamespace(id=row["id"])])

        if "SET is_default=false" in s:
            return _FakeResult([])

        return _FakeResult([])

    def _to_select_row(self, row):
        return _FakeRow(
            id=row["id"],
            ozon_client_id=row["ozon_client_id"],
            api_key_masked=row["api_key_masked"],
            shop_name=row["shop_name"],
            currency=row["currency"],
            is_default=row["is_default"],
            credential_type=row["credential_type"],
            status=row["status"],
            last_validated_at=row["last_validated_at"],
            last_rotated_at=row["last_rotated_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.store = _CredStore()

    def begin(self):
        return _FakeConn(self.store)

    def connect(self):
        return _FakeConn(self.store)


@pytest.fixture()
def engine(monkeypatch):
    eng = _FakeEngine()
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with patch.object(svc, "get_engine", return_value=eng):
        yield eng


# ============================================================
# 用例
# ============================================================

def test_cross_tenant_create_rejected(engine):
    """tenant1 绑 X 后，tenant2 create_credential 绑 X → 409（跨租户拦截）。"""
    svc.create_credential(TENANT_A, CredentialCreate(ozon_client_id="X", api_key="k1"))
    assert engine.store._same_tenant(TENANT_A, "X")

    with pytest.raises(HTTPException) as exc:
        svc.create_credential(TENANT_B, CredentialCreate(ozon_client_id="X", api_key="k2"))
    assert exc.value.status_code == 409
    assert "其他用户绑定" in exc.value.detail
    # B 未插入任何行
    assert not engine.store._same_tenant(TENANT_B, "X")


def test_cross_tenant_store_rejected(engine):
    """tenant1 绑 X 后，tenant2 store_credential 绑 X → 409（堵住 ON CONFLICT 幂等空子）。"""
    svc.store_credential(TENANT_A, "X", "k1")
    assert engine.store._same_tenant(TENANT_A, "X")

    with pytest.raises(HTTPException) as exc:
        svc.store_credential(TENANT_B, "X", "k2")
    assert exc.value.status_code == 409
    assert "其他用户绑定" in exc.value.detail
    # B 未插入任何行
    assert not engine.store._same_tenant(TENANT_B, "X")


def test_same_tenant_store_idempotent(engine):
    """同租户 store_credential 重复绑 X → 仍幂等成功（现状不改）。"""
    id1 = svc.store_credential(TENANT_A, "X", "k1")
    id2 = svc.store_credential(TENANT_A, "X", "k2")
    assert id1 == id2  # upsert 复用同一行

    rows = [r for r in engine.store._rows if r["ozon_client_id"] == "X"]
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == TENANT_A


def test_same_tenant_create_409(engine):
    """同租户 create_credential 重复绑 X → 仍 409（IntegrityError 现状不改）。"""
    svc.create_credential(TENANT_A, CredentialCreate(ozon_client_id="X", api_key="k1"))
    with pytest.raises(HTTPException) as exc:
        svc.create_credential(TENANT_A, CredentialCreate(ozon_client_id="X", api_key="k2"))
    assert exc.value.status_code == 409

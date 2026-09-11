"""v0.75 C8: 跨租户绑店拦截原子化（审计 A8 F3）—— advisory lock 时序 TDD。

背景：``_assert_client_not_bound_elsewhere`` 原为纯预检（无 DB 级锁），两个租户并发绑
同一 ozon_client_id 时都过预检 → 双 INSERT → 一方 409 失守 + 密钥 AAD 不匹配解密必败，
需人工修数。修复定案：凭证事务内首条语句执行
``SELECT pg_advisory_xact_lock(hashtext(:cid))``，把「预检 SELECT + INSERT」包进同一把
按 ozon_client_id 哈希的事务级互斥锁（提交/回滚自动释放，无需显式 unlock）。

纯 mock（无 PG）：patch get_engine，每次 begin() 新开一条录制 conn（= 一个事务），
捕获 SQL 执行序列断言「锁 → 预检 → INSERT」顺序与同事务性。
既有跨租户 409 行为回归主 coverage 在 tests/test_credential_cross_tenant.py
（create/store 双入口 409 + 同租户幂等/IntegrityError 现状锁定），本文件补
拒绝路径下锁仍为事务首条语句的时序断言。
"""
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api.schemas import CredentialCreate
from services import credential_service as svc

# 32 字节 AES-256 主密钥（与 test_credential_cross_tenant 同口径，测试假钥）
MASTER_KEY = "0123456789abcdef0123456789abcdef"

_TENANT_A = "tenant" + "-a1"
_TENANT_B = "tenant" + "-b2"
_CID = "shop" + "-0001"
_KEY = "probe" + "-1234" + "-key"

_LOCK_MARK = "pg_advisory_xact_lock"
_PRECHECK_MARK = "tenant_id !="


def _returning_row() -> SimpleNamespace:
    """INSERT ... RETURNING 全列的假行（_row_to_dict 消费）。"""
    return SimpleNamespace(
        id=uuid.uuid4(),
        ozon_client_id=_CID,
        api_key_masked="ab" + "****" + "cd",
        shop_name="假店铺",
        currency="CNY",
        is_default=False,
        credential_type="api_key",
        status="active",
        last_validated_at=None,
        last_rotated_at=None,
        created_at=None,
        updated_at=None,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
        self.rowcount = len(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _RecordingConn:
    """录制 SQL 执行序列的最小 conn：预检/INSERT 按剧本应答，其余放行空结果。"""

    def __init__(self, precheck_hit: bool = False):
        self.calls = []  # [(sql_text, params)]
        self.precheck_hit = precheck_hit

    def execute(self, sql, params=None):
        s = str(sql)
        self.calls.append((s, params or {}))
        if _LOCK_MARK in s:
            return _FakeResult([])  # advisory lock SELECT，结果无人消费
        if _PRECHECK_MARK in s:  # 跨租户预检 SELECT
            rows = [SimpleNamespace(_1=1)] if self.precheck_hit else []
            return _FakeResult(rows)
        if s.lstrip().startswith("INSERT INTO credentials") and "ON CONFLICT" not in s:
            return _FakeResult([_returning_row()])  # create INSERT RETURNING
        if s.lstrip().startswith("INSERT INTO credentials") and "ON CONFLICT" in s:
            return _FakeResult([SimpleNamespace(id=uuid.uuid4())])  # store upsert RETURNING id
        return _FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    """begin() 每次新开一条 conn（= 一个事务）并登记；connect() 视为违例。

    登记表即事务表：len(transactions)==1 且三条语句都在 transactions[0].calls
    ⟺ lock/预检/INSERT 走同一连接同一事务。
    """

    def __init__(self, precheck_hit: bool = False):
        self.transactions: list[_RecordingConn] = []
        self._precheck_hit = precheck_hit

    def begin(self):
        conn = _RecordingConn(self._precheck_hit)
        self.transactions.append(conn)
        return conn

    def connect(self):  # pragma: no cover
        raise AssertionError("凭证写入必须走 begin() 单事务，不允许额外 connect()")


@contextmanager
def _service_with(engine: _FakeEngine, monkeypatch):
    """真实 encrypt/mask（env 主密钥）+ 关闭绑定后 job 入队 + get_engine 换假引擎。"""
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("STORE_SYNC_JOBS_ENABLED", "0")
    with patch.object(svc, "get_engine", return_value=engine):
        yield engine


def _sqls(conn: _RecordingConn) -> list[str]:
    return [s for s, _ in conn.calls]


def _insert_idx(sqls: list[str], with_conflict: bool) -> int:
    for i, s in enumerate(sqls):
        if s.lstrip().startswith("INSERT INTO credentials") and ("ON CONFLICT" in s) == with_conflict:
            return i
    raise AssertionError(f"未找到 INSERT (ON CONFLICT={with_conflict})")


def test_advisory_lock_precedes_check_and_insert(monkeypatch):
    """创建凭证事务内第一条语句是 pg_advisory_xact_lock 且绑参为 ozon_client_id 字符串，
    先于预检 SELECT 与 INSERT 执行。"""
    with _service_with(_FakeEngine(), monkeypatch) as engine:
        out = svc.create_credential(
            _TENANT_A, CredentialCreate(ozon_client_id=_CID, api_key=_KEY)
        )
    assert out["ozon_client_id"] == _CID

    conn = engine.transactions[0]
    sqls = _sqls(conn)
    assert _LOCK_MARK in sqls[0], f"事务首条语句应为 advisory lock，实为: {sqls[0]}"
    assert conn.calls[0][1] == {"cid": _CID}, "lock 绑定参必须是 ozon_client_id 字符串"
    precheck_idx = next(i for i, s in enumerate(sqls) if _PRECHECK_MARK in s)
    assert 0 < precheck_idx < _insert_idx(sqls, with_conflict=False)


def test_lock_in_same_transaction(monkeypatch):
    """lock/预检/INSERT 三者走同一个 connection（单次 begin = 单事务，无第二连接）。"""
    with _service_with(_FakeEngine(), monkeypatch) as engine:
        svc.create_credential(
            _TENANT_A, CredentialCreate(ozon_client_id=_CID, api_key=_KEY)
        )
    assert len(engine.transactions) == 1, "创建凭证只应开启一个事务"
    sqls = _sqls(engine.transactions[0])
    assert any(_LOCK_MARK in s for s in sqls)
    assert any(_PRECHECK_MARK in s for s in sqls)
    assert any(s.lstrip().startswith("INSERT INTO credentials") for s in sqls)


def test_existing_409_flow_unchanged(monkeypatch):
    """预检命中（他租户已绑）→ 仍 409 且不 INSERT；拒绝路径锁也先取（等锁后必命中）。"""
    with _service_with(_FakeEngine(precheck_hit=True), monkeypatch) as engine:
        with pytest.raises(HTTPException) as exc:
            svc.create_credential(
                _TENANT_B, CredentialCreate(ozon_client_id=_CID, api_key=_KEY)
            )
    assert exc.value.status_code == 409
    assert "其他用户绑定" in exc.value.detail
    conn = engine.transactions[0]
    sqls = _sqls(conn)
    assert _LOCK_MARK in sqls[0]
    assert not any(s.lstrip().startswith("INSERT") for s in sqls), "409 路径不得 INSERT"


def test_store_credential_locks_first_too(monkeypatch):
    """store_credential（POST /drafts 凭证剥离 upsert 入口）同享锁优先：锁 → 预检 → upsert。"""
    with _service_with(_FakeEngine(), monkeypatch) as engine:
        svc.store_credential(_TENANT_A, _CID, _KEY)
    conn = engine.transactions[0]
    sqls = _sqls(conn)
    assert _LOCK_MARK in sqls[0]
    assert conn.calls[0][1] == {"cid": _CID}
    precheck_idx = next(i for i, s in enumerate(sqls) if _PRECHECK_MARK in s)
    assert 0 < precheck_idx < _insert_idx(sqls, with_conflict=True)

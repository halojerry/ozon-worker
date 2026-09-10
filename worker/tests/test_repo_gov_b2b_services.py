"""repo-gov B2-β（BL-08 / BL-10 / BL-16 写侧）：治理面服务测试（纯 mock，无需 PG）。

覆盖：
1. backup_heartbeat_service.mark_backup：插入值（epoch 秒/ok/detail 截 500）+ 整体吞错
   （备份脚本绝不因心跳失败而失败）
2. backup_heartbeat_service.last_backup_at：ORDER BY finished_at DESC 排序语义
   + 无行 None + 查询失败 None
3. mxou_ledger_service.record_call：插入参数（tenant_id 可空/token_fp/endpoint 截 200）
   + fire-and-forget 吞错路径
4. draft_service.submit_draft 的 submission INSERT 带 tenant_id（BL-16 写侧——
   submit/resubmit/batch-submit 三路由唯一的行创建点）

运行（mock 模式，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_repo_gov_b2b_services.py -q
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import backup_heartbeat_service as bh  # noqa: E402
from services import mxou_ledger_service as ml  # noqa: E402


class _FakeResult:
    """fetchone 可编程（last_backup_at 查询用）。"""

    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """记录 executed (sql, params)；可注入 fetchone 结果 / 指定片段抛异常。"""

    def __init__(self, executed, row=None, fail_on=None):
        self.executed = executed
        self._row = row
        self._fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        sql_str = str(sql)
        if self._fail_on and self._fail_on in sql_str:
            raise RuntimeError(f"模拟 DB 失败: {self._fail_on}")
        self.executed.append((sql_str, dict(params or {})))
        return _FakeResult(self._row)

    def commit(self):
        pass


class _FakeEngine:
    """connect()/begin() 共享记录；row/fail_on 按需注入。"""

    def __init__(self, row=None, fail_on=None):
        self.executed = []
        self._row = row
        self._fail_on = fail_on

    def _conn(self):
        return _FakeConn(self.executed, row=self._row, fail_on=self._fail_on)

    def connect(self):
        return self._conn()

    def begin(self):
        return self._conn()


# ============================================================
# 1. mark_backup（BL-08）
# ============================================================

def test_mark_backup_insert_values():
    eng = _FakeEngine()
    with patch.object(bh, "get_engine", return_value=eng):
        bh.mark_backup(True, " nightly ok")
    assert len(eng.executed) == 1
    sql_str, params = eng.executed[0]
    assert "INSERT INTO backup_heartbeat" in sql_str
    assert isinstance(params["ts"], float), "finished_at 必须是 epoch 秒 float"
    assert params["ok"] is True
    assert params["detail"] == " nightly ok"  # 原样透传（只截断不清洗）


def test_mark_backup_detail_truncated():
    eng = _FakeEngine()
    with patch.object(bh, "get_engine", return_value=eng):
        bh.mark_backup(False, "x" * 600)
    _, params = eng.executed[0]
    assert len(params["detail"]) == 500, "detail 必须截 500（对齐列型 String(500)）"


def test_mark_backup_swallow_db_error():
    """心跳写失败绝不向备份脚本抛异常（BL-08 红线）。"""
    eng = _FakeEngine(fail_on="INSERT INTO backup_heartbeat")
    with patch.object(bh, "get_engine", return_value=eng):
        bh.mark_backup(False, "boom")  # 不抛即过
    # get_engine 本身炸也同样吞
    with patch.object(bh, "get_engine", side_effect=RuntimeError("no engine")):
        bh.mark_backup(True, "")


# ============================================================
# 2. last_backup_at（BL-08）
# ============================================================

def test_last_backup_at_orders_desc_and_returns_float():
    eng = _FakeEngine(row=(1770000000.5,))
    with patch.object(bh, "get_engine", return_value=eng):
        assert bh.last_backup_at() == 1770000000.5
    sql_str, _ = eng.executed[0]
    assert "ORDER BY finished_at DESC" in sql_str, "必须取最新一行"
    assert "LIMIT 1" in sql_str


def test_last_backup_at_no_row_returns_none():
    eng = _FakeEngine(row=None)
    with patch.object(bh, "get_engine", return_value=eng):
        assert bh.last_backup_at() is None


def test_last_backup_at_query_failure_returns_none():
    """查询失败 → None（观测面不制造第二套错误语义）。"""
    eng = _FakeEngine(fail_on="backup_heartbeat")
    with patch.object(bh, "get_engine", return_value=eng):
        assert bh.last_backup_at() is None


# ============================================================
# 3. record_call（BL-10）
# ============================================================

def test_record_call_insert_params():
    eng = _FakeEngine()
    with patch.object(ml, "get_engine", return_value=eng):
        ml.record_call(tenant_id="u1", token_fp="a" * 64, endpoint="/v1/chat/completions")
    assert len(eng.executed) == 1
    sql_str, params = eng.executed[0]
    assert "INSERT INTO mxou_call_ledger" in sql_str
    assert params["tenant_id"] == "u1"
    assert params["token_fp"] == "a" * 64
    assert params["endpoint"] == "/v1/chat/completions"
    assert isinstance(params["ts"], float), "called_at 必须是 epoch 秒 float"


def test_record_call_endpoint_truncated_and_tenant_none():
    """endpoint 截 200；tenant_id=None（匿名场景）原样透传。"""
    eng = _FakeEngine()
    with patch.object(ml, "get_engine", return_value=eng):
        ml.record_call(tenant_id=None, token_fp="fp", endpoint="e" * 300)
    _, params = eng.executed[0]
    assert params["tenant_id"] is None
    assert len(params["endpoint"]) == 200


def test_record_call_swallow_db_error():
    """fire-and-forget：台账写失败绝不抛回业务调用方。"""
    eng = _FakeEngine(fail_on="INSERT INTO mxou_call_ledger")
    with patch.object(ml, "get_engine", return_value=eng):
        ml.record_call(tenant_id="u1", token_fp="fp", endpoint="/v1/x")  # 不抛即过
    with patch.object(ml, "get_engine", side_effect=RuntimeError("no engine")):
        ml.record_call(tenant_id="u1", token_fp="fp", endpoint="/v1/x")


# ============================================================
# 4. BL-16 写侧：submit_draft 的 submission INSERT 带 tenant_id
# ============================================================

class _SubmissionRow:
    """RETURNING 行（submit_draft 只读 .id）。"""

    id = "sub-1"


def _run_submit_draft():
    """全 mock 驱动 draft_service.submit_draft 走到 submission INSERT（不含 PG）。"""
    import services.draft_service as ds

    envelope = {
        "draft": {"title": "测试商品", "images": []},
        "source": {},
        "extensions": {},
    }
    eng = _FakeEngine(row=_SubmissionRow())

    async def _fake_submit_task(tenant_id, payload, sku_key=""):
        return "task-new"

    def _identity_template(tenant_id, envelope_, template_id=None,
                           is_update=False, credential_id=None):
        return envelope_

    with patch.object(ds, "get_draft", return_value={"payload": envelope}), \
         patch.object(ds, "_ensure_images_mirrored_for_submit",
                      new=AsyncMock(return_value=envelope)), \
         patch.object(ds.credential_service, "get_decrypted",
                      return_value=("clientA", "keyA")), \
         patch.object(ds, "_cross_store_scan", return_value=([], False)), \
         patch.object(ds, "_apply_listing_template", side_effect=_identity_template), \
         patch.object(ds, "_submit_task", new=_fake_submit_task), \
         patch.object(ds, "get_engine", return_value=eng):
        resp = asyncio.run(ds.submit_draft("tenant-u1", "d-1", "tok", "cred-1"))
    return resp, eng


def test_submit_draft_inserts_tenant_id():
    """提交路径的 submission INSERT 必须带 tenant_id（三路由唯一行创建点）。"""
    resp, eng = _run_submit_draft()
    assert resp["ok"] is True and resp["task_id"] == "task-new"
    inserts = [(s, p) for s, p in eng.executed if "INSERT INTO draft_submissions" in s]
    assert len(inserts) == 1, "必须恰好一条 submission INSERT"
    sql_str, params = inserts[0]
    # 列清单与绑定值都带 tenant_id（不是靠 SQL 默认值碰运气）
    assert "tenant_id" in sql_str.split("VALUES")[0], "INSERT 列清单缺 tenant_id"
    assert ":tenant_id" in sql_str.split("VALUES")[1], "VALUES 缺 :tenant_id 绑定"
    assert params["tenant_id"] == "tenant-u1", "tenant_id 必须取鉴权派生租户"


def test_submit_draft_rest_of_contract_unchanged():
    """补列不破坏既有契约：status='pending' / submitted_task_id / store_client_id 快照。"""
    resp, eng = _run_submit_draft()
    sql_str, params = next(
        (s, p) for s, p in eng.executed if "INSERT INTO draft_submissions" in s)
    assert "'pending'" in sql_str
    assert params["task_id"] == "task-new"
    assert params["store_client_id"] == "clientA"
    assert params["draft_id"] == "d-1"

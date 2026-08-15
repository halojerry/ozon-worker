"""M0.7: 直连路径写 draft_submissions 行 — 契约测试（RED → GREEN）。

验收门（WebUI 运营工作台 v0.42 §A4 / M0.7）：
- A4 决策：所有任务（skill 直连 + 采集箱提交）都有 draft_submissions 行；
  直连任务 draft_id=NULL，采集任务 draft_id 有值。
- http_submit_task（直连）入队成功 → 写 draft_submissions 行：
  draft_id=NULL（无草稿）、credential_id=NULL（凭证在 payload）、
  store_client_id=payload.ozon_client_id、status='pending'（终态 M0.3 写回）、
  submitted_task_id=task_id、extensions=NULL。
- 写行失败 → 任务仍入队成功（非致命，与 M0.2 同纪律）。
- 采集路径不重复：draft_service.submit_draft 不触发额外写行（写行逻辑只在
  main.http_submit_task 端点层，task_processor/draft_service 无写行）。

运行（mock 模式，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_submit_task_direct_writes_submission.py -q
"""
import asyncio
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _fake_supabase() -> MagicMock:
    fake = MagicMock()
    exec_chain = fake.table.return_value.select.return_value.eq.return_value.is_.return_value.execute
    exec_chain.return_value.data = [
        {
            "user_id": "u1",
            "key": "tok123",
            "remain_quota": 999,
            "status": 1,
            "expired_time": -1,
            "unlimited_quota": False,
        }
    ]
    return fake


def _submit_body(ozon_client_id="storeA") -> dict:
    draft = {
        "item_id": "123456",
        "title": "测试商品",
        "currency": "CNY",
        "images": ["https://example.com/1.jpg"],
        "weight": 100,
        "dimensions": {"length": 10, "width": 10, "height": 10},
        "purchase_cost": 5.0,
        "purchase_url": "https://example.com/buy",
    }
    return {
        "token": "sk-tok123",
        "ozon_client_id": ozon_client_id,
        "ozon_api_key": "",  # 空 → 跳过 Ozon 配额检查（网络）
        "envelope": {"draft": draft},
    }


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """记录全部 executed SQL；可选在指定 SQL 片段处抛异常（模拟写行失败）。"""

    def __init__(self, result, fail_on=None):
        self._result = result
        self._fail_on = fail_on
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        sql_str = str(sql)
        if self._fail_on and self._fail_on in sql_str:
            raise RuntimeError(f"模拟 DB 写行失败: {self._fail_on}")
        self.executed.append((sql_str, dict(params or {})))
        return self._result

    def commit(self):
        pass


class _FakeEngine:
    """同时支持 connect()（去重查询）与 begin()（写行）；共享 executed 记录。"""

    def __init__(self, fail_on=None):
        self.conn = None
        self._fail_on = fail_on
        self.executed = []

    def connect(self):
        self.conn = _FakeConn(_FakeResult(None), fail_on=self._fail_on)
        self.conn.executed = self.executed
        return self.conn

    def begin(self):
        conn = _FakeConn(_FakeResult(None), fail_on=self._fail_on)
        conn.executed = self.executed
        return conn


class _FakeProcessor:
    def __init__(self):
        self.calls = []

    async def submit_task(self, **kwargs):
        self.calls.append(kwargs)
        return "task-new"


def _run_submit(fake_engine=None, fake_proc=None):
    import main as main_mod

    fake_engine = fake_engine or _FakeEngine()
    fake_proc = fake_proc or _FakeProcessor()
    with patch("main.get_supabase_client", return_value=_fake_supabase()), patch(
        "main._check_mxou_balance", return_value=(100.0, True)
    ), patch("main.get_engine", return_value=fake_engine), patch.object(
        main_mod, "task_processor", fake_proc
    ):
        resp = asyncio.run(main_mod.http_submit_task(FakeRequest(_submit_body())))
    return resp, fake_engine, fake_proc


def _submission_insert(executed):
    for sql_str, params in executed:
        if "INSERT INTO draft_submissions" in sql_str:
            return sql_str, params
    return None, None


# ============================================================
# 1. 直连提交 → draft_submissions 行（draft_id=NULL / store_client_id / task_id）
# ============================================================

def test_direct_submit_writes_submission_row():
    """直连提交入队成功 → 写 draft_submissions 行（draft_id NULL / submitted_task_id / store_client_id）。"""
    resp, fake_engine, fake_proc = _run_submit()

    assert resp["ok"] is True, resp
    assert resp["task_id"] == "task-new"
    assert len(fake_proc.calls) == 1, "任务必须入队"

    sql_str, params = _submission_insert(fake_engine.executed)
    assert sql_str is not None, "直连提交必须执行 draft_submissions INSERT"
    assert "INSERT INTO draft_submissions" in sql_str
    assert params["store_client_id"] == "storeA", "store_client_id 必须来自 payload.ozon_client_id"
    assert params["task_id"] == "task-new", "submitted_task_id 必须指向新任务"
    assert "NULL" in sql_str, "draft_id/credential_id/extensions 必须为 NULL（直连无草稿、凭证在 payload）"
    assert "'pending'" in sql_str, "status 必须为 'pending'（终态由 M0.3 写回）"
    assert "submitted_task_id" in sql_str


# ============================================================
# 2. 写行失败 → 任务仍入队成功（非致命）
# ============================================================

def test_write_row_failure_does_not_block_task():
    """draft_submissions 写行抛异常 → 任务仍入队成功（HTTP 正常，task_id 返回）。"""
    fake_engine = _FakeEngine(fail_on="INSERT INTO draft_submissions")
    resp, fake_engine, fake_proc = _run_submit(fake_engine=fake_engine)

    assert resp["ok"] is True, resp
    assert resp["task_id"] == "task-new"
    assert len(fake_proc.calls) == 1, "写行失败绝不能阻断任务入队"
    # 写行确实失败过（INSERT 被 fail_on 拦截 → 未进入 executed）
    sql_str, _ = _submission_insert(fake_engine.executed)
    assert sql_str is None, "写行失败时不应有成功的 INSERT 记录"


# ============================================================
# 3. 采集路径不重复：写行逻辑只在 main.http_submit_task 端点层
# ============================================================

def test_collection_path_does_not_double_write():
    """draft_service.submit_draft 不触发额外写行 — 写行只在 main.http_submit_task。

    结构断言（防回归）：
    - task_processor.submit_task 是两条路径共用的入队器 → 内部绝不能写行
      （否则采集路径 = draft_service 已写 + task_processor 又写 = 双写）
    - draft_service 不引用 _write_direct_submission_row
    - main 模块定义 _write_direct_submission_row（端点层辅助）
    """
    import main as main_mod
    from utils.task_processor import SupabaseTaskProcessor

    assert hasattr(main_mod, "_write_direct_submission_row"), "写行辅助必须定义在 main 模块"

    proc_src = inspect.getsource(SupabaseTaskProcessor.submit_task)
    assert "draft_submissions" not in proc_src, "task_processor.submit_task 内不得写行（采集路径会双写）"

    from services import draft_service
    ds_src = inspect.getsource(draft_service.submit_draft)
    assert "_write_direct_submission_row" not in ds_src, "draft_service 不调用直连写行辅助"

    submit_src = inspect.getsource(main_mod.http_submit_task)
    assert "_write_direct_submission_row" in submit_src, "写行辅助必须被 http_submit_task 端点调用"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)

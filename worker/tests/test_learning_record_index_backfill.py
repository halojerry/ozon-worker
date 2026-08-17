"""W6: graph 直连路径回填 product_task_index — credential_id 从 task payload 反查（TDD）。

背景：直连 submit_task 写 draft_submissions 行 credential_id=NULL（凭证在 payload，
不落 submission 行）→ _backfill_product_index 在 L96-98 因 credential_id 缺失 skip
索引回填 → 上架成功商品无法按 product_id 反查 task（重上/商品编辑定位缺失）。
修复：learning_record_node 在 draft_submissions 无 credential_id 时，从任务 payload
（ozon_product_tasks.payload.ozon_client_id）反查 credentials 表兜底，仍无才 skip。

锁定行为：
- 直连任务（draft_submissions credential_id=NULL）→ payload 反查出 credential_id → upsert_index 被调
- draft_submissions 有 credential_id → 直接使用，不触发 payload 兜底
- payload 无 ozon_client_id → 跳过不抛
- credentials 表查无此店铺 → 跳过不抛
- payload 反查 DB 异常 → warning 吞掉不抛

运行（mock 模式，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_learning_record_index_backfill.py -q
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_TASK_ID = "11111111-2222-3333-4444-555555555555"
_DRAFT_ID = "aaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CRED_ID = "ffffffff-1111-2222-3333-444444444444"


class _FakeRow:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, i):
        return self._values[i]


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """按 SQL 片段返回不同行（ozon_product_tasks payload 查询 / credentials 查询）。"""

    def __init__(self, rows_by_sql):
        self._rows_by_sql = rows_by_sql

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = str(sql)
        for frag, row in self._rows_by_sql.items():
            if frag in s:
                return _FakeResult(row)
        return _FakeResult(None)


class _FakeEngine:
    def __init__(self, rows_by_sql):
        self._rows_by_sql = rows_by_sql

    def connect(self):
        return _FakeConn(self._rows_by_sql)


def _payload_engine(client_id="storeA", cred_id=_CRED_ID):
    """payload 查询返回 ozon_client_id；credentials 查询返回 credential_id（可 None）。"""
    rows = {}
    if client_id is not None:
        rows["FROM ozon_product_tasks"] = _FakeRow((client_id,))
    if cred_id is not None:
        rows["FROM credentials"] = _FakeRow((cred_id,))
    return _FakeEngine(rows)


def _make_state(product_id="5476361418", user_id="tenant-1", draft=None, envelope=None):
    return SimpleNamespace(
        product_id=product_id,
        user_id=user_id,
        draft=draft or {"title": "测试", "item_id": "980815374096", "sku_id": "980815374096"},
        envelope=envelope or {"extensions": {}},
    )


def _run_backfill(
    state,
    task_id=_TASK_ID,
    resolve_result=(None, None),
    fake_engine=None,
    upsert_side_effect=None,
):
    from graphs.nodes.learning_record_node import _backfill_product_index

    config = SimpleNamespace(configurable={"thread_id": task_id})
    with patch(
        "graphs.nodes.learning_record_node._resolve_draft_submission",
        return_value=resolve_result,
    ), patch(
        "storage.database.db.get_engine",
        return_value=fake_engine or _payload_engine(),
    ), patch(
        "services.product_index_service.upsert_index",
        side_effect=upsert_side_effect,
    ) as mock_upsert:
        _backfill_product_index(state, config)
    return mock_upsert


def test_direct_task_backfills_index_via_payload():
    """直连任务（draft_submissions credential_id=NULL）→ payload 反查 credential_id → 回填。"""
    state = _make_state()
    mock_upsert = _run_backfill(state, resolve_result=(None, None))

    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["product_id"] == "5476361418"
    assert kwargs["offer_id"] == "980815374096"  # draft.item_id（非跟卖）
    assert kwargs["task_id"] == _TASK_ID
    assert kwargs["credential_id"] == _CRED_ID
    assert kwargs["draft_id"] is None
    assert kwargs["tenant_id"] == "tenant-1"


def test_draft_submission_credential_wins_over_payload():
    """draft_submissions 已有 credential_id → 直接使用，payload 兜底不触发。"""
    state = _make_state()
    mock_upsert = _run_backfill(state, resolve_result=(_DRAFT_ID, _CRED_ID))

    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["credential_id"] == _CRED_ID
    assert kwargs["draft_id"] == _DRAFT_ID


def test_payload_without_client_id_skips():
    """payload 无 ozon_client_id → credential 无法反查 → 跳过不抛。"""
    state = _make_state()
    mock_upsert = _run_backfill(state, fake_engine=_payload_engine(client_id=None))

    mock_upsert.assert_not_called()


def test_credentials_miss_skips():
    """payload 有 client_id 但 credentials 表查无此店铺 → 跳过不抛。"""
    state = _make_state()
    mock_upsert = _run_backfill(
        state, fake_engine=_payload_engine(client_id="storeA", cred_id=None)
    )

    mock_upsert.assert_not_called()


def test_payload_backfill_failure_nonblocking():
    """payload 反查抛 DB 异常 → warning 吞掉，不抛、跳过。"""
    state = _make_state()

    class _BoomConn(_FakeConn):
        def execute(self, sql, params=None):
            raise RuntimeError("DB boom")

    class _BoomEngine:
        def connect(self):
            return _BoomConn({})

    with patch(
        "graphs.nodes.learning_record_node._resolve_draft_submission",
        return_value=(None, None),
    ), patch("storage.database.db.get_engine", return_value=_BoomEngine()), patch(
        "services.product_index_service.upsert_index"
    ) as mock_upsert:
        from graphs.nodes.learning_record_node import _backfill_product_index

        _backfill_product_index(
            state, SimpleNamespace(configurable={"thread_id": _TASK_ID})
        )

    mock_upsert.assert_not_called()

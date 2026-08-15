"""M0.6: product_task_index draft_id 传播 — 单测（mock engine，无需 PG）。

覆盖：
- _resolve_draft_id: submitted_task_id 命中 → draft_id；无行 → None；行内 draft_id 为 NULL → None
- _lookup_index: SELECT 返回 draft_id（含 NULL 情况）
- approved 路径（update_product_images）: 已知草稿 → _upsert_index 写 draft_id；
  直连任务（无 submission 行）→ draft_id 为 None（行为不变）

运行（无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_service_index_draft.py -q
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import image_service

TENANT = "tenant-a"
PRODUCT_ID = "1234567890"
TASK_ID = "task-0000-1111-2222"
CRED_ID = "cred-0000-1111"
DRAFT_ID = "draft-0000-1111"

# product_task_index 行：(product_id, offer_id, task_id, credential_id, draft_id)
INDEX_ROW = (PRODUCT_ID, "sku-123", TASK_ID, CRED_ID, None)


class FakeRow:
    def __init__(self, row=None, rowcount=1):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self._engine.calls.append((sql, params))
        if self._engine.pending_rows:
            return FakeRow(self._engine.pending_rows.pop(0))
        return FakeRow(None)


class FakeEngine:
    """pending_rows 按 SELECT 顺序吐出（connect/begin 共享）；execute 记录 (sql, params)。"""

    def __init__(self, pending_rows=None):
        self.pending_rows = list(pending_rows or [])
        self.calls = []

    def connect(self):
        return FakeConn(self)

    def begin(self):
        return FakeConn(self)


def _make_engine(monkeypatch, pending_rows=None):
    engine = FakeEngine(pending_rows)
    monkeypatch.setattr(image_service, "get_engine", lambda: engine)
    return engine


def _approved_ozon_post(client_id, api_key, endpoint, body, **kwargs):
    if endpoint == "/v3/product/import":
        return {"result": {"task_id": "import-task-42"}}
    if endpoint == "/v3/product/info/list":
        return {"result": {"items": [
            {"id": int(body["product_id"][0]), "statuses": {"moderate_status": "approved"}},
        ]}}
    raise AssertionError(f"unexpected endpoint: {endpoint}")


def _index_insert_calls(engine):
    return [(sql, p) for sql, p in engine.calls if "INSERT INTO product_task_index" in sql]


# ── _resolve_draft_id ──
def test_resolve_draft_id_found(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[(DRAFT_ID,)])
    assert image_service._resolve_draft_id(TASK_ID) == DRAFT_ID
    sql = engine.calls[0][0]
    assert "draft_submissions" in sql and "submitted_task_id" in sql


def test_resolve_draft_id_no_row(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[None])
    assert image_service._resolve_draft_id("task-direct") is None


def test_resolve_draft_id_row_null_draft(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[(None,)])
    assert image_service._resolve_draft_id(TASK_ID) is None


# ── _lookup_index ──
def test_lookup_index_returns_draft_id(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[
        (PRODUCT_ID, "sku-123", TASK_ID, CRED_ID, DRAFT_ID),
    ])
    result = image_service._lookup_index(TENANT, PRODUCT_ID)
    assert result["draft_id"] == DRAFT_ID
    assert result["task_id"] == TASK_ID


def test_lookup_index_draft_id_null(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[INDEX_ROW])
    result = image_service._lookup_index(TENANT, PRODUCT_ID)
    assert result["draft_id"] is None


# ── approved 路径写 draft_id ──
def test_approved_path_writes_draft_id(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[
        INDEX_ROW,          # _lookup_index
        (DRAFT_ID,),        # _resolve_draft_id
    ])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=_approved_ozon_post):
        resp = image_service.update_product_images(
            TENANT, PRODUCT_ID, ["https://img.example.com/a.jpg"]
        )
    assert resp["status"] == "approved"
    inserts = _index_insert_calls(engine)
    assert len(inserts) == 1, "approved 后必须 upsert 索引"
    sql, params = inserts[0]
    assert "draft_id" in sql, "upsert SQL 必须含 draft_id 列"
    assert params["draft_id"] == DRAFT_ID


def test_approved_path_direct_task_draft_none(monkeypatch):
    """直连任务（无 submission 行）→ _upsert_index 的 draft_id 参数为 None。"""
    engine = _make_engine(monkeypatch, pending_rows=[
        INDEX_ROW,   # _lookup_index
        None,        # _resolve_draft_id → 无 submission 行
    ])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=_approved_ozon_post):
        resp = image_service.update_product_images(
            TENANT, PRODUCT_ID, ["https://img.example.com/a.jpg"]
        )
    assert resp["status"] == "approved"
    inserts = _index_insert_calls(engine)
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "draft_id" in sql
    assert params["draft_id"] is None

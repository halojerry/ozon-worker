"""T6: GET /products/{id}/edit 端点 + product_index_service 抽取 — 单测（mock engine，无需 PG）。

覆盖（MUST DO）：
1. test_edit_ok: lookup_index 命中（含 draft_id）+ 草稿 payload 读取 → 完整 ProductEditResponse 结构
2. test_edit_no_index: lookup_index None → 404「商品未找到，可能已归档」
3. test_edit_no_draft: index 有但 draft_id None → 409，message 含「仅改图」
4. test_edit_draft_missing: index 有 draft_id 但草稿读取失败 → 404
5. test_edit_cross_tenant: lookup SQL 按 tenant 过滤（断言查询参数）→ 跨租户返回 None → 404
6. test_edit_requires_auth: 无 token → 401
7. test_index_service_extracted: image_service 不再定义 _lookup_index/_upsert_index/_INDEX_UPSERT_SQL
   （复用 product_index_service 同一函数对象）
8. test_update_images_backcompat_via_shared_module: 抽取后 update_product_images 行为不变
   （与 test_image_service_index_draft 回归同证 back-compat）

运行（无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_product_edit.py -q
"""
import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from fastapi import HTTPException

from services import image_service, product_index_service

TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
PRODUCT_ID = "1234567890"
TASK_ID = "task-0000-1111-2222"
CRED_ID = "cred-0000-1111"
DRAFT_ID = "draft-0000-1111"
OFFER_ID = "sku-123"
ENVELOPE = {"draft": {"item_id": "980815374096", "title": "宠物自动饮水器"}, "extensions": {}}

# product_task_index 行：(product_id, offer_id, task_id, credential_id, draft_id)
INDEX_ROW = (PRODUCT_ID, OFFER_ID, TASK_ID, CRED_ID, None)
INDEX_ROW_WITH_DRAFT = (PRODUCT_ID, OFFER_ID, TASK_ID, CRED_ID, DRAFT_ID)


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
    """同一 FakeEngine 同时注入 product_index_service 与 image_service（抽取后两模块各自持有 get_engine）。"""
    engine = FakeEngine(pending_rows)
    monkeypatch.setattr(product_index_service, "get_engine", lambda: engine)
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


def _mock_draft_get(monkeypatch, payload=None, exc=None):
    """mock 草稿读取：payload 命中 → 返回 dict；exc → raise（模拟草稿不存在/跨租户）。"""
    def _get(tid, did):
        if exc is not None:
            raise exc
        return {"id": did, "tenant_id": tid, "payload": payload, "source": "skill", "version": 1}
    monkeypatch.setattr(image_service.draft_service, "get_draft", _get)


# ── 1. 命中：完整 ProductEditResponse 结构 ──
def test_edit_ok(monkeypatch):
    _make_engine(monkeypatch, pending_rows=[
        INDEX_ROW_WITH_DRAFT,   # lookup_index
        ("approved",),          # moderation_status（ozon_product_tasks result->>'moderation_status'）
    ])
    _mock_draft_get(monkeypatch, payload=ENVELOPE)
    result = image_service.get_product_edit_data(TENANT, PRODUCT_ID)
    assert result["product_id"] == PRODUCT_ID
    assert result["offer_id"] == OFFER_ID
    assert result["credential_id"] == CRED_ID
    assert result["draft_id"] == DRAFT_ID
    assert result["payload"] == ENVELOPE
    assert result["moderation_status"] == "approved"


def test_edit_ok_moderation_null(monkeypatch):
    """任务 result 无 moderation_status → None（不阻断编辑数据返回）。"""
    _make_engine(monkeypatch, pending_rows=[
        INDEX_ROW_WITH_DRAFT,
        None,   # ozon_product_tasks 无行/result 键缺失 → NULL
    ])
    _mock_draft_get(monkeypatch, payload=ENVELOPE)
    result = image_service.get_product_edit_data(TENANT, PRODUCT_ID)
    assert result["moderation_status"] is None
    assert result["payload"] == ENVELOPE


# ── 2. 无索引 → 404 ──
def test_edit_no_index(monkeypatch):
    _make_engine(monkeypatch, pending_rows=[None])
    with pytest.raises(HTTPException) as ei:
        image_service.get_product_edit_data(TENANT, PRODUCT_ID)
    assert ei.value.status_code == 404


# ── 3. index 有但无草稿来源 → 409 ──
def test_edit_no_draft(monkeypatch):
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW])  # draft_id=None
    with pytest.raises(HTTPException) as ei:
        image_service.get_product_edit_data(TENANT, PRODUCT_ID)
    assert ei.value.status_code == 409
    assert "仅改图" in ei.value.detail


# ── 4. index 有 draft_id 但草稿表无记录 → 404 ──
def test_edit_draft_missing(monkeypatch):
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW_WITH_DRAFT])
    _mock_draft_get(monkeypatch, exc=HTTPException(status_code=404, detail="草稿不存在或无权访问"))
    with pytest.raises(HTTPException) as ei:
        image_service.get_product_edit_data(TENANT, PRODUCT_ID)
    assert ei.value.status_code == 404
    assert "草稿不存在或无权访问" in ei.value.detail


# ── 5. 跨租户：lookup 按 tenant 过滤 → None → 404 ──
def test_edit_cross_tenant(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[None])  # 跨租户 → 无行
    with pytest.raises(HTTPException) as ei:
        image_service.get_product_edit_data(OTHER_TENANT, PRODUCT_ID)
    assert ei.value.status_code == 404
    # 断言 lookup SQL 带租户过滤（防越权：product_id + tenant_id 双条件）
    sql, params = engine.calls[0]
    assert "product_task_index" in sql
    assert "tenant_id" in sql
    assert params["tenant_id"] == OTHER_TENANT
    assert params["pid"] == PRODUCT_ID


# ── 6. 无 token → 401（真实路由 _authenticate）──
class FakeRequest:
    headers = {}

    def __init__(self, body: bytes = b""):
        self._body = body

    async def body(self):
        return self._body


def test_edit_requires_auth():
    from routes import products_routes
    with pytest.raises(HTTPException) as ei:
        asyncio.run(products_routes.get_product_edit(PRODUCT_ID, FakeRequest(body=b"")))
    assert ei.value.status_code == 401


# ── 7. 抽取断言：image_service 不再定义，复用共享模块 ──
def test_index_service_extracted():
    src = inspect.getsource(image_service)
    assert "def _lookup_index" not in src, "image_service 不应再定义 _lookup_index"
    assert "def _upsert_index" not in src, "image_service 不应再定义 _upsert_index"
    assert "_INDEX_UPSERT_SQL" not in src, "image_service 不应再定义 _INDEX_UPSERT_SQL"
    # 调用等价：image_service 命名空间里的 lookup/upsert 就是共享模块的同一函数对象
    assert image_service.lookup_index is product_index_service.lookup_index
    assert image_service.upsert_index is product_index_service.upsert_index
    # 共享模块提供 SQL + 租户隔离查询
    assert product_index_service._INDEX_UPSERT_SQL is not None
    assert "tenant_id" in str(product_index_service._INDEX_UPSERT_SQL)


# ── 8. back-compat：抽取后 update_product_images 行为不变 ──
def test_update_images_backcompat_via_shared_module(monkeypatch):
    engine = _make_engine(monkeypatch, pending_rows=[
        INDEX_ROW_WITH_DRAFT,   # lookup_index
        (DRAFT_ID,),            # _resolve_draft_id（submission 行命中草稿）
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
    inserts = [(s, p) for s, p in engine.calls if "INSERT INTO product_task_index" in s]
    assert len(inserts) == 1, "approved 后必须 upsert 索引"
    assert "draft_id" in inserts[0][0]
    assert inserts[0][1]["draft_id"] == DRAFT_ID

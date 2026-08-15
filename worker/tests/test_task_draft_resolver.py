"""M1.1: task → draft 解析（worker 侧）— 失败/被拒任务找回采集箱草稿。

契约（WebUI 运营工作台 M1.1）：
- GET /api/v1/tasks/{task_id}/draft → {"draft_id": uuid | None}
- 解析顺序：draft_submissions.submitted_task_id → product_task_index.task_id → None
- 租户隔离：task 必须先确认属于该 tenant（ozon_product_tasks WHERE id AND tenant_id，无 → 404）

mock engine（FakeEngine 风格，仿 test_tasks_api.py），无需 PG。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_draft_resolver.py -q
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from routes import tasks_routes
from services import task_service

TASK_ID = "11111111-1111-1111-1111-111111111111"
DRAFT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DRAFT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class FakeRow:
    """模拟 SQLAlchemy Row：下标访问（row[0]）返回第 0 列。"""

    def __init__(self, *values):
        self._values = values

    def __getitem__(self, index):
        return self._values[index]

    @property
    def id(self):
        return self._values[0]


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []

    def scalar(self):
        return self._row[0] if self._row is not None else None


class FakeConn:
    """按 SQL 包含的表名分发三个查询的结果。

    - ozon_product_tasks    → 归属查询（task 属于 tenant？）
    - draft_submissions     → submitted_task_id 命中的 draft_id
    - product_task_index    → task_id 命中的 draft_id
    """

    def __init__(self, *, task_owner: bool, submission_draft=None, index_draft=None):
        self.task_owner = task_owner
        self.submission_draft = submission_draft
        self.index_draft = index_draft
        self.calls = []

    def execute(self, sql, params=None):
        sql = str(sql)
        self.calls.append((sql, dict(params or {})))
        if "ozon_product_tasks" in sql:
            return FakeResult(FakeRow(1) if self.task_owner else None)
        if "draft_submissions" in sql:
            return FakeResult(FakeRow(self.submission_draft) if self.submission_draft is not None else None)
        if "product_task_index" in sql:
            return FakeResult(FakeRow(self.index_draft) if self.index_draft is not None else None)
        return FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEngine:
    def __init__(self, conn: FakeConn):
        self._conn = conn
        self.last_conn = None

    def connect(self):
        self.last_conn = self._conn
        return self._conn


def _patch_engine(conn: FakeConn):
    engine = FakeEngine(conn)
    patch_obj = patch.object(task_service, "get_engine", return_value=engine)
    patch_obj.start()
    return engine


# ============================================================
# 1. 采集任务：draft_submissions.submitted_task_id 命中 → 返回 draft_id
# ============================================================

def test_resolves_via_draft_submissions():
    # submission 命中 draft-a；product_task_index 有别的 draft-b（提交行应优先）
    conn = FakeConn(task_owner=True, submission_draft=DRAFT_A, index_draft=DRAFT_B)
    engine = _patch_engine(conn)
    try:
        result = task_service.resolve_draft_by_task("u1", TASK_ID)
    finally:
        patch.stopall()
    assert result == DRAFT_A
    # 租户隔离：归属查询确实带 tenant_id 参数
    assert any("ozon_product_tasks" in sql and params["tenant_id"] == "u1"
               for sql, params in conn.calls)


# ============================================================
# 2. 直连任务：无 submission 行，product_task_index.task_id 命中 → 返回 draft_id
# ============================================================

def test_resolves_via_product_task_index_when_no_submission():
    conn = FakeConn(task_owner=True, submission_draft=None, index_draft=DRAFT_B)
    _patch_engine(conn)
    try:
        result = task_service.resolve_draft_by_task("u1", TASK_ID)
    finally:
        patch.stopall()
    assert result == DRAFT_B


# ============================================================
# 3. 直连任务无 draft：两表都无 draft_id → None
# ============================================================

def test_returns_none_when_no_draft_linked():
    conn = FakeConn(task_owner=True, submission_draft=None, index_draft=None)
    _patch_engine(conn)
    try:
        result = task_service.resolve_draft_by_task("u1", TASK_ID)
    finally:
        patch.stopall()
    assert result is None


# ============================================================
# 4. 跨租户 task → 404（任务属于别的租户）
# ============================================================

def test_cross_tenant_task_404():
    # task 存在但属于 u2：u1 查询归属为 None → 404（即便有 draft 关联也不可见）
    conn = FakeConn(task_owner=False, submission_draft=DRAFT_A)
    _patch_engine(conn)
    try:
        with pytest.raises(HTTPException) as exc:
            task_service.resolve_draft_by_task("u1", TASK_ID)
    finally:
        patch.stopall()
    assert exc.value.status_code == 404
    assert "无权访问" in exc.value.detail


# ============================================================
# 5. 不存在 task → 404
# ============================================================

def test_unknown_task_404():
    conn = FakeConn(task_owner=False, submission_draft=None, index_draft=None)
    _patch_engine(conn)
    try:
        with pytest.raises(HTTPException) as exc:
            task_service.resolve_draft_by_task("u1", TASK_ID)
    finally:
        patch.stopall()
    assert exc.value.status_code == 404


# ============================================================
# 6. 路由薄层：GET /api/v1/tasks/{task_id}/draft 存在 + 鉴权 + 透传
# ============================================================

class FakeRequest:
    def __init__(self, query_params: dict):
        self.query_params = query_params
        self.headers = {}

    async def body(self) -> bytes:
        return b""

    async def json(self):
        return {}


def _draft_route():
    for route in tasks_routes.router.routes:
        if getattr(route, "path", "") == "/tasks/{task_id}/draft":
            return route
    raise AssertionError("tasks_routes 未注册 GET /tasks/{task_id}/draft")


def test_route_registered_and_returns_schema():
    conn = FakeConn(task_owner=True, submission_draft=DRAFT_A)
    engine = _patch_engine(conn)
    import main as main_mod

    def _auth(token: str) -> str:
        return "u1"

    try:
        with patch.object(main_mod, "_authenticate_token", side_effect=_auth):
            resp = asyncio.run(_draft_route().endpoint(TASK_ID, FakeRequest({"token": "sk-tok123"})))
    finally:
        patch.stopall()
    assert resp == {"draft_id": DRAFT_A}


def test_route_404_passthrough():
    conn = FakeConn(task_owner=False, submission_draft=None, index_draft=None)
    _patch_engine(conn)
    import main as main_mod

    try:
        with patch.object(main_mod, "_authenticate_token", side_effect=lambda token: "u1"), \
                pytest.raises(HTTPException) as exc:
            asyncio.run(_draft_route().endpoint(TASK_ID, FakeRequest({"token": "sk-tok123"})))
    finally:
        patch.stopall()
    assert exc.value.status_code == 404

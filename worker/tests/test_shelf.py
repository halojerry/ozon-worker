"""M2.1: 在售商品列表端点（shelf）— 租户隔离 + 分页 + draft_id/moderation_status 字段。

mock Supabase（鉴权）+ mock DB（services.shelf_service.get_engine），不真实请求。
FakeEngine 模拟 LEFT JOIN：从 index 行 + tasks_result（任务 result JSONB）计算 moderation_status。

覆盖（PLAN-webui-v1 M2.1 验收）：
1. 已上架商品（result.moderation_status=approved）出现且带 draft_id
2. 直连任务商品（draft_id NULL）出现且 draft_id=null
3. 跨租户 → 空列表（A 看不到 B 的商品）
4. 分页 limit/offset 生效
5. moderation_status 从 result JSONB 提取成功 / 缺失 → null

运行（无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_shelf.py -q
"""
from contextlib import contextmanager
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod
from routes.shelf_routes import router as shelf_router
from services import shelf_service


# 租户 = key 派生（v0.56 _authenticate_token → _key_user_id）：行键必须用派生租户。
TID_A = main_mod._key_user_id("tok123")
TID_B = main_mod._key_user_id("tok456")
TID_EMPTY = main_mod._key_user_id("tok-empty")


class FakeRequest:
    def __init__(self, query_params: dict):
        self.query_params = query_params
        self.headers = {}

    async def body(self) -> bytes:
        return b""

    async def json(self):
        return {}


class FakeRow:
    """模拟 SQLAlchemy Row（下标访问）：(product_id, offer_id, task_id, draft_id, credential_id, created_at, moderation_status)。"""

    def __init__(self, row):
        self._row = row

    def __getitem__(self, i):
        return self._row[i]

    def __len__(self):
        return len(self._row)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """模拟 engine.connect()：按 tenant_id 过滤 + 模拟 LEFT JOIN ozon_product_tasks 提取
    result->>'moderation_status' + LIMIT/OFFSET 切片，记录 SQL 参数。"""

    def __init__(self, rows_by_tenant, tasks_result):
        self._rows_by_tenant = rows_by_tenant
        self._tasks_result = tasks_result
        self.calls = []

    def execute(self, sql, params=None):
        params = dict(params or {})
        self.calls.append((str(sql), params))
        tenant = params.get("tenant_id")
        rows = self._rows_by_tenant.get(tenant, [])
        if "COUNT(*)" in str(sql):
            return FakeResult([len(rows)])
        limit = params.get("limit", len(rows))
        offset = params.get("offset", 0)
        joined = []
        for r in rows[offset:offset + limit]:
            # LEFT JOIN：任务 result JSONB 尽力提取 moderation_status（缺失/无行 → None）
            result = self._tasks_result.get(str(r[2]))
            mod = result.get("moderation_status") if isinstance(result, dict) else None
            joined.append(FakeRow((r[0], r[1], r[2], r[3], r[4], r[5], mod)))
        return FakeResult(joined)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEngine:
    def __init__(self, rows_by_tenant, tasks_result):
        self._rows_by_tenant = rows_by_tenant
        self._tasks_result = tasks_result
        self.last_conn = None

    def connect(self):
        self.last_conn = FakeConn(self._rows_by_tenant, self._tasks_result)
        return self.last_conn


def _rows_for_tenant():
    """租户 A 三条 index 行（created_at DESC 已排好），租户 B 一条，租户 empty 无行。

    index 行原始列：(product_id, offer_id, task_id, draft_id, credential_id, created_at)
    """
    return {
        TID_A: [
            ("1111111111", "sku-111", "task-1", "draft-1", "cred-1", "2026-08-15T10:00:00"),
            ("2222222222", "sku-222", "task-2", None, "cred-2", "2026-08-15T09:00:00"),
            ("3333333333", "sku-333", "task-3", "draft-3", "cred-3", "2026-08-15T08:00:00"),
        ],
        TID_B: [
            ("9999999999", "sku-999", "task-9", "draft-9", "cred-9", "2026-08-15T07:00:00"),
        ],
        TID_EMPTY: [],
    }


def _tasks_result():
    """task_id → 任务 result JSONB（模拟 ozon_product_tasks.result）。
    task-1 有 approved；task-3 有 declined；task-2 无 moderation_status 键（缺失 → null）。"""
    return {
        "task-1": {"product_summary": [], "moderation_status": "approved"},
        "task-2": {"product_summary": []},
        "task-3": {"moderation_status": "declined"},
    }


def _handler():
    return shelf_router.routes[0].endpoint


def _call(query_params: dict, token: str | None = "sk-tok123"):
    params = dict(query_params)
    if token is not None:
        params["token"] = token
    return asyncio.run(_handler()(FakeRequest(params)))


@contextmanager
def _engine_ctx(engine):
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch.object(shelf_service, "get_engine", return_value=engine):
        yield


# ============================================================
# 1. 已上架商品（approved）出现且带 draft_id
# ============================================================

def test_shelf_approved_product_with_draft_id():
    engine = FakeEngine(_rows_for_tenant(), _tasks_result())
    with _engine_ctx(engine):
        resp = _call({})
    approved = next(i for i in resp["items"] if i["moderation_status"] == "approved")
    assert approved["product_id"] == "1111111111"
    assert approved["offer_id"] == "sku-111"
    assert approved["task_id"] == "task-1"
    assert approved["draft_id"] == "draft-1"
    assert approved["credential_id"] == "cred-1"


# ============================================================
# 2. 直连任务商品（draft_id NULL）出现且 draft_id=null
# ============================================================

def test_shelf_direct_task_draft_null():
    engine = FakeEngine(_rows_for_tenant(), _tasks_result())
    with _engine_ctx(engine):
        resp = _call({})
    direct = next(i for i in resp["items"] if i["product_id"] == "2222222222")
    assert direct["offer_id"] == "sku-222"
    assert direct["draft_id"] is None
    assert direct["credential_id"] == "cred-2"


# ============================================================
# 3. 跨租户 → 空列表
# ============================================================

def test_shelf_tenant_isolation():
    """租户 A 看不到租户 B 的商品；无商品租户 → 空列表 + total 0。"""
    engine = FakeEngine(_rows_for_tenant(), _tasks_result())
    with _engine_ctx(engine):
        resp = _call({})
    ids = [i["product_id"] for i in resp["items"]]
    assert "9999999999" not in ids  # 租户 B 的商品不可见
    assert resp["total"] == 3
    # SQL 确实按 tenant_id 过滤（两条查询都带 tenant_id 参数）
    assert len(engine.last_conn.calls) == 2
    assert all("tenant_id" in params for _, params in engine.last_conn.calls)
    # 租户 B 只见自己的商品（不同 token → 不同派生租户）
    with _engine_ctx(engine):
        resp_u2 = _call({}, token="sk-tok456")
    assert [i["product_id"] for i in resp_u2["items"]] == ["9999999999"]
    # 无商品租户 → 空列表
    with _engine_ctx(engine):
        resp_empty = _call({}, token="sk-tok-empty")
    assert resp_empty["items"] == []
    assert resp_empty["total"] == 0


# ============================================================
# 4. 分页 limit/offset 生效
# ============================================================

def test_shelf_pagination():
    engine = FakeEngine(_rows_for_tenant(), _tasks_result())
    with _engine_ctx(engine):
        page1 = _call({"limit": "2", "offset": "0"})
        page2 = _call({"limit": "2", "offset": "2"})
    assert [i["product_id"] for i in page1["items"]] == ["1111111111", "2222222222"]
    assert [i["product_id"] for i in page2["items"]] == ["3333333333"]
    assert page1["total"] == 3
    assert page2["total"] == 3
    assert page1["limit"] == 2 and page1["offset"] == 0
    assert page2["limit"] == 2 and page2["offset"] == 2


def test_shelf_limit_capped_at_100():
    engine = FakeEngine(_rows_for_tenant(), _tasks_result())
    with _engine_ctx(engine):
        resp = _call({"limit": "500"})
    assert resp["limit"] == 100


# ============================================================
# 5. moderation_status 从 result JSONB 提取成功 / 缺失 → null
# ============================================================

def test_shelf_moderation_status_from_result():
    engine = FakeEngine(_rows_for_tenant(), _tasks_result())
    with _engine_ctx(engine):
        resp = _call({})
    items = {i["product_id"]: i for i in resp["items"]}
    # 提取成功：result JSONB 有 moderation_status
    assert items["1111111111"]["moderation_status"] == "approved"
    assert items["3333333333"]["moderation_status"] == "declined"
    # 缺失：result 无 moderation_status 键 → null
    assert items["2222222222"]["moderation_status"] is None
    # 查询必须走 LEFT JOIN + JSONB 提取（不实时调 Ozon）
    list_sql = engine.last_conn.calls[0][0]
    assert "LEFT JOIN ozon_product_tasks" in list_sql
    assert "result->>'moderation_status'" in list_sql
    assert "product_task_index" in list_sql


# ============================================================
# 6. 鉴权：无 token → 401
# ============================================================

def test_shelf_no_token_401():
    with pytest.raises(HTTPException) as exc:
        _call({}, token=None)
    assert exc.value.status_code == 401

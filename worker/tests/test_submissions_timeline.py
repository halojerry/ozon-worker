"""M2.2 草稿提交时间线 API — 契约测试（RED → GREEN）。

验收门（WebUI 运营工作台 v0.42 M2.2）：
- 多次提交 → 时间倒序返回全部（created_at DESC）
- 直连任务草稿（draft_id=NULL 的 submission 行）不出现（按 draft_id 过滤）
- 草稿不存在 / 跨租户 → 404
- 无提交 → 空列表
- 字段完整：id/store_client_id/status/error_message/extensions/submitted_task_id/created_at

运行（需本地 Docker PG 5433）：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        ../skill/.venv314/bin/python -m pytest tests/test_submissions_timeline.py -q
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

TOKEN_A = "sk-token-tenant-A"
TOKEN_B = "sk-token-tenant-B"
CLIENT_A = "4718259"
CLIENT_B = "8822111"
MASTER_KEY = "0123456789abcdef0123456789abcdef"


def make_envelope(item_id: str = "980815374096") -> dict:
    draft = {
        "item_id": item_id,
        "title": "宠物自动饮水器",
        "images": ["https://cbu01.alicdn.com/x.jpg"],
        "weight": 227,
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "purchase_cost": 5.5,
        "purchase_url": f"https://detail.1688.com/offer/{item_id}.html",
    }
    return {
        "draft": draft,
        "source": {"purchase_url": draft["purchase_url"], "purchase_cost": 5.5},
        "extensions": {"margin_rate": 0.25, "commission_rate": 0.10},
    }


def graph_input(client_id: str, envelope: dict, token: str = TOKEN_A) -> dict:
    return {"token": token, "ozon_client_id": client_id, "ozon_api_key": "sk-test-key", "envelope": envelope}


@pytest.fixture(scope="module")
def pg():
    try:
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过提交时间线契约测试")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(pg):
    with pg.begin() as conn:
        # 对齐 test_drafts_api：清 credentials，防跨文件残留 (tenant, ozon_client_id) 触发跨租户 409。
        conn.execute(text("DELETE FROM draft_submissions"))
        conn.execute(text("DELETE FROM product_drafts"))
        conn.execute(text("DELETE FROM credentials"))
    yield


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch):
    """patch main._authenticate_token → token→tenant 映射（与 test_drafts_api 同款）。"""
    import main as main_mod

    TOKEN_TO_TENANT = {TOKEN_A: "tenant-A", TOKEN_B: "tenant-B"}

    def _auth(token: str) -> str:
        if not token:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Token is required")
        return TOKEN_TO_TENANT.get(token, "tenant-A")

    monkeypatch.setattr(main_mod, "_authenticate_token", _auth)
    monkeypatch.setattr(main_mod, "get_supabase_client", lambda: None)
    yield _auth


def make_app() -> FastAPI:
    from routes.drafts_routes import router as drafts_router
    app = FastAPI()
    app.include_router(drafts_router)
    return app


def create_draft(client: TestClient, token: str, item_id: str = "980815374096",
                 client_id: str = CLIENT_A) -> str:
    resp = client.post("/api/v1/drafts",
                       json=graph_input(client_id, make_envelope(item_id), token=token))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def insert_submission(pg, draft_id, *, store_client_id, status="pending", error_message=None,
                      extensions=None, task_id=None, created_at=None):
    """直接插 draft_submissions 行（时间线测试关注读取侧，不经过 submit 流程）。"""
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    with pg.begin() as conn:
        conn.execute(text(
            "INSERT INTO draft_submissions "
            "(draft_id, credential_id, store_client_id, extensions, status, error_message, submitted_task_id, created_at) "
            "VALUES (:draft_id, NULL, :store_client_id, CAST(:extensions AS jsonb), :status, :error_message, :task_id, :created_at)"
        ), {
            "draft_id": draft_id,
            "store_client_id": store_client_id,
            "extensions": json.dumps(extensions or {}, ensure_ascii=False),
            "status": status,
            "error_message": error_message,
            "task_id": task_id,
            "created_at": created_at,
        })


# ============================================================
# 1. 多次提交 → 时间倒序返回全部
# ============================================================

def test_timeline_returns_all_submissions_desc_order(pg):
    client = TestClient(make_app())
    draft_id = create_draft(client, TOKEN_A)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    insert_submission(pg, draft_id, store_client_id=CLIENT_A, status="published", created_at=t0)
    insert_submission(pg, draft_id, store_client_id=CLIENT_A, status="failed", created_at=t0 + timedelta(minutes=2))
    insert_submission(pg, draft_id, store_client_id=CLIENT_B, status="rejected", created_at=t0 + timedelta(minutes=4))

    resp = client.get(f"/api/v1/drafts/{draft_id}/submissions",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 3
    assert [i["status"] for i in items] == ["rejected", "failed", "published"], "必须按 created_at 倒序"
    assert items[0]["store_client_id"] == CLIENT_B
    assert items[2]["store_client_id"] == CLIENT_A


# ============================================================
# 2. 直连任务草稿（draft_id=NULL 行）不出现
# ============================================================

def test_timeline_excludes_direct_task_rows(pg):
    client = TestClient(make_app())
    draft_id = create_draft(client, TOKEN_A)
    insert_submission(pg, draft_id, store_client_id=CLIENT_A, status="published")
    # 直连任务行（_write_direct_submission_row 同款：draft_id=NULL）
    with pg.begin() as conn:
        conn.execute(text(
            "INSERT INTO draft_submissions (draft_id, credential_id, store_client_id, status, submitted_task_id) "
            "VALUES (NULL, NULL, :c, 'published', :t)"),
            {"c": CLIENT_B, "t": str(uuid.uuid4())})

    resp = client.get(f"/api/v1/drafts/{draft_id}/submissions",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1, "draft_id=NULL 的直连任务行不得出现在时间线"
    assert items[0]["store_client_id"] == CLIENT_A


# ============================================================
# 3. 草稿不存在 / 跨租户 → 404
# ============================================================

def test_timeline_404_for_missing_draft(pg):
    client = TestClient(make_app())
    resp = client.get(f"/api/v1/drafts/{uuid.uuid4()}/submissions",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 404


def test_timeline_404_for_cross_tenant(pg):
    client = TestClient(make_app())
    # tenant-B 绑定独立 client_id（跨租户单店一次绑定拦截：同一 ozon_client_id 不得跨租户复用）
    draft_id = create_draft(client, TOKEN_B, item_id="item-b", client_id="8899221")
    resp = client.get(f"/api/v1/drafts/{draft_id}/submissions",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 404, "跨租户不得看到时间线"


# ============================================================
# 4. 无提交 → 空列表
# ============================================================

def test_timeline_empty_when_no_submissions(pg):
    client = TestClient(make_app())
    draft_id = create_draft(client, TOKEN_A)
    resp = client.get(f"/api/v1/drafts/{draft_id}/submissions",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ============================================================
# 5. 字段完整（status/error_message/extensions/submitted_task_id 都返回）
# ============================================================

def test_timeline_fields_complete(pg):
    client = TestClient(make_app())
    draft_id = create_draft(client, TOKEN_A)
    task_id = str(uuid.uuid4())
    insert_submission(
        pg, draft_id,
        store_client_id=CLIENT_A, status="failed",
        error_message="Ozon 拒绝: DESCRIPTION_DECLINE",
        extensions={"margin_rate": 0.25, "warehouse_id": "wh-666"},
        task_id=task_id,
    )

    resp = client.get(f"/api/v1/drafts/{draft_id}/submissions",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 200, resp.text
    item = resp.json()[0]
    assert item["id"]
    assert item["store_client_id"] == CLIENT_A
    assert item["status"] == "failed"
    assert item["error_message"] == "Ozon 拒绝: DESCRIPTION_DECLINE"
    assert item["extensions"] == {"margin_rate": 0.25, "warehouse_id": "wh-666"}
    assert item["submitted_task_id"] == task_id
    assert item["created_at"] is not None


# ============================================================
# 6. 未认证 → 401（路由薄层鉴权）
# ============================================================

def test_timeline_route_auth_required(pg):
    client = TestClient(make_app())
    resp = client.get(f"/api/v1/drafts/{uuid.uuid4()}/submissions")
    assert resp.status_code == 401

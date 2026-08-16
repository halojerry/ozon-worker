"""T6 采集箱草稿 API — 契约测试（RED → GREEN → SURFACE）。

验收门（PLAN-webui-v1 §5 T6）：
- 创建剥离凭证：stored payload JSON 无 ozon_api_key 字段
- PATCH stale version → 409
- submit → ozon_product_tasks 出现 pending 行 + draft_submissions 行 + submitted_task_id
- per-store 重复 → 409「重复商品」；Ozon API 错误 → fail-open（不阻塞）
- 跨店 → confirm_required:true（不硬拦）；换店铺第二次 submit → 新 submission 行且 draft.id 不变
- 租户隔离：A 看不到 B 的草稿
- warehouse_id/stock 透传进 extensions 快照

运行（需本地 Docker PG 5433 + mock Ozon）：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        CREDENTIAL_MASTER_KEY=0123456789abcdef0123456789abcdef \
        ../skill/.venv314/bin/python -m pytest tests/test_drafts_api.py -q
"""
import json
import os
import sys
import uuid
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
MASTER_KEY = "0123456789abcdef0123456789abcdef"

TOKEN_A = "sk-token-tenant-A"
TOKEN_B = "sk-token-tenant-B"
CLIENT_A = "4718259"
CLIENT_B = "8822111"
API_KEY_A = "sk-api-key-AAAA1111"
API_KEY_B = "sk-api-key-BBBB2222"
ITEM_ID = "980815374096"


def make_envelope(item_id: str = ITEM_ID, *, follow: bool = False,
                  warehouse_id=None, stock=None) -> dict:
    draft = {
        "item_id": item_id,
        "title": "宠物自动饮水器",
        "images": ["https://cbu01.alicdn.com/x.jpg"],
        "weight": 227,
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "purchase_cost": 5.5,
        "purchase_url": f"https://detail.1688.com/offer/{item_id}.html",
    }
    extensions = {"margin_rate": 0.25, "commission_rate": 0.10}
    if follow:
        draft["ozon_product_id"] = "3852000144"
        extensions["follow_sell"] = True
    if warehouse_id is not None:
        extensions["warehouse_id"] = warehouse_id
    if stock is not None:
        extensions["stock"] = stock
    return {
        "draft": draft,
        "source": {"purchase_url": draft["purchase_url"], "purchase_cost": 5.5},
        "extensions": extensions,
    }


def graph_input(client_id: str, api_key: str, envelope: dict, token: str = TOKEN_A) -> dict:
    return {"token": token, "ozon_client_id": client_id, "ozon_api_key": api_key,
            "envelope": envelope}


@pytest.fixture(scope="module")
def pg():
    try:
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过草稿 API 契约测试")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(pg):
    with pg.begin() as conn:
        # FK 顺序：product_task_index(→task/credential) 先删，再删 credentials/tasks
        conn.execute(text("DELETE FROM draft_submissions"))
        conn.execute(text("DELETE FROM product_task_index"))
        conn.execute(text("DELETE FROM product_drafts"))
        conn.execute(text("DELETE FROM credentials"))
        conn.execute(text("DELETE FROM ozon_product_tasks"))
    yield


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


# ──────────────────────────────────────────────
# 路由 + 鉴权：patch main._authenticate_token → token→tenant 映射
# ──────────────────────────────────────────────

TOKEN_TO_TENANT = {TOKEN_A: "tenant-A", TOKEN_B: "tenant-B"}


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch):
    import main as main_mod

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


def patch_ozon(monkeypatch, result=None, error=None):
    from services import draft_service as ds

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls = getattr(_fake, "calls", [])
        calls.append({"client_id": client_id, "endpoint": endpoint, "body": body})
        _fake.calls = calls
        if error is not None:
            raise error
        return result if result is not None else {"items": [], "total": 0}

    _fake.calls = []
    monkeypatch.setattr(ds, "ozon_post", _fake)
    return _fake


def patch_submitter(monkeypatch):
    """mock _submit_task：像真实 submit_task 一样插入 pending 行并返回 UUID。"""
    from services import draft_service as ds

    async def _fake(tenant_id, payload, sku_key=""):
        task_id = str(uuid.uuid4())
        getattr(_fake, "payloads", []).append(payload)
        with create_engine(DB_URL).begin() as conn:
            conn.execute(text(
                "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) "
                "VALUES (:id, :tenant_id, 'pending', CAST(:payload AS jsonb))"),
                {"id": task_id, "tenant_id": tenant_id,
                 "payload": json.dumps(payload, ensure_ascii=False)})
        return task_id

    _fake.payloads = []
    monkeypatch.setattr(ds, "_submit_task", _fake)
    return _fake


def create_store_credential(tenant: str, client_id: str, api_key: str) -> str:
    from services import credential_service
    return credential_service.store_credential(tenant, client_id, api_key)


def insert_task_index_row(pg, product_id: str, tenant: str, offer_id: str,
                          credential_id, draft_id=None) -> str:
    """插入 product_task_index 前置行（含引用的 completed task 行；FK task/credential）。"""
    with pg.begin() as conn:
        task_id = str(uuid.uuid4())
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) "
            "VALUES (:id, :tenant_id, 'completed', '{}'::jsonb)"),
            {"id": task_id, "tenant_id": tenant})
        conn.execute(text(
            "INSERT INTO product_task_index "
            "(product_id, tenant_id, offer_id, task_id, credential_id, draft_id) "
            "VALUES (:pid, :tenant, :offer, :task, :cred, :draft)"),
            {"pid": product_id, "tenant": tenant, "offer": offer_id,
             "task": task_id, "cred": credential_id, "draft": draft_id})
    return task_id


# ============================================================
# 1. 创建剥离凭证：payload 无 api_key 明文，凭证加密入库
# ============================================================

def test_create_strips_credentials(pg):
    client = TestClient(make_app())
    env = make_envelope()
    resp = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, env))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "skill"
    assert body["tenant_id"] == "tenant-A"

    with pg.connect() as conn:
        stored = conn.execute(
            text("SELECT payload, version FROM product_drafts WHERE id = :id"),
            {"id": body["id"]},
        ).fetchone()
    payload = stored[0] if isinstance(stored[0], dict) else json.loads(stored[0])
    assert payload == env
    assert "ozon_api_key" not in json.dumps(payload)
    assert "api_key" not in json.dumps(payload)
    assert stored[1] == 1

    with pg.connect() as conn:
        row = conn.execute(
            text("SELECT ozon_api_key_enc, api_key_masked FROM credentials "
                 "WHERE tenant_id='tenant-A' AND ozon_client_id=:c"),
            {"c": CLIENT_A},
        ).fetchone()
    assert row is not None
    assert b"AAAA1111" not in row[0], "凭证表不得出现明文 api_key"
    assert row[1] == "****1111"


def test_create_rejects_api_key_inside_envelope(pg):
    client = TestClient(make_app())
    env = make_envelope()
    env["draft"]["ozon_api_key"] = "sk-leaked-key"
    resp = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, env))
    assert resp.status_code == 400, resp.text


# ============================================================
# 2. 列表 + 租户隔离（A 看不到 B 的草稿）
# ============================================================

def test_list_tenant_isolation(pg):
    client = TestClient(make_app())
    client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope("item-a")))

    list_a = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert list_a.status_code == 200
    assert len(list_a.json()) == 1
    assert list_a.json()[0]["tenant_id"] == "tenant-A"

    list_b = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_B}"})
    assert list_b.status_code == 200
    assert list_b.json() == []


def test_get_tenant_isolation(pg):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]

    assert client.get(f"/api/v1/drafts/{draft_id}", headers={"Authorization": f"Bearer {TOKEN_A}"}).status_code == 200
    assert client.get(f"/api/v1/drafts/{draft_id}", headers={"Authorization": f"Bearer {TOKEN_B}"}).status_code == 404


# ============================================================
# 3. PATCH 乐观锁：stale version → 409；正确 version → version++
# ============================================================

def test_patch_stale_version_409(pg):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]

    new_env = make_envelope()
    new_env["draft"]["title"] = "新版标题"
    resp = client.patch(f"/api/v1/drafts/{draft_id}", json={
        "token": TOKEN_A, "version": 99, "payload": new_env})
    assert resp.status_code == 409, resp.text

    resp = client.patch(f"/api/v1/drafts/{draft_id}", json={
        "token": TOKEN_A, "version": 1, "payload": new_env})
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2
    assert resp.json()["payload"]["draft"]["title"] == "新版标题"


# ============================================================
# 4. submit → 入队 pending + submission 行 + submitted_task_id
# ============================================================

def _submit(client, draft_id, credential_id, token=TOKEN_A, update_product_id=None):
    body = {"token": token}
    if credential_id is not None:
        body["credential_id"] = str(credential_id)
    if update_product_id is not None:
        body["update_product_id"] = update_product_id
    return client.post(f"/api/v1/drafts/{draft_id}/submit", json=body)


def test_submit_creates_task_and_submission(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    fake_ozon = patch_ozon(monkeypatch, result={"items": [], "total": 0})
    fake_sub = patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "pending"
    task_id = body["task_id"]
    assert task_id

    sent = fake_sub.payloads[-1]
    assert sent["token"] == TOKEN_A
    assert sent["ozon_client_id"] == CLIENT_A
    assert sent["ozon_api_key"] == API_KEY_A
    assert sent["envelope"]["draft"]["item_id"] == ITEM_ID
    assert fake_ozon.calls and fake_ozon.calls[0]["endpoint"] == "/v1/product/info/list"

    with pg.connect() as conn:
        task = conn.execute(
            text("SELECT status FROM ozon_product_tasks WHERE id = :id"),
            {"id": task_id},
        ).fetchone()
        sub = conn.execute(
            text("SELECT status, submitted_task_id, store_client_id, credential_id "
                 "FROM draft_submissions WHERE draft_id = :d"),
            {"d": draft_id},
        ).fetchone()
    assert task is not None and task[0] == "pending"
    assert sub is not None
    assert sub[0] == "pending"
    assert sub[1] == task_id
    assert sub[2] == CLIENT_A
    assert str(sub[3]) == str(cred_id)


def test_submit_warehouse_stock_snapshot(pg, monkeypatch):
    client = TestClient(make_app())
    env = make_envelope(warehouse_id="wh-666", stock=42)
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, env))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text

    with pg.connect() as conn:
        ext = conn.execute(
            text("SELECT extensions FROM draft_submissions WHERE draft_id = :d"),
            {"d": draft_id},
        ).fetchone()[0]
    assert ext["warehouse_id"] == "wh-666"
    assert ext["stock"] == 42


# ============================================================
# 5. per-store 重复 → 409「重复商品」；Ozon 错误 → fail-open
# ============================================================

def test_submit_per_store_duplicate_409(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    fake_ozon = patch_ozon(monkeypatch, result={
        "items": [{"offer_id": ITEM_ID, "product_id": "5476361418"}], "total": 1})
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 409, resp.text
    assert "重复商品" in resp.json()["detail"]
    assert ITEM_ID in fake_ozon.calls[0]["body"]["offer_id"]


def test_submit_ozon_error_fail_open(pg, monkeypatch):
    import requests
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    patch_ozon(monkeypatch, error=requests.HTTPError("Ozon 500"))
    fake_sub = patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["task_id"]
    assert fake_sub.payloads, "fail-open 必须继续入队"


def test_submit_follow_offer_id_prefix(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(
        CLIENT_A, API_KEY_A, make_envelope(follow=True)))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    fake_ozon = patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    assert "follow_3852000144" in fake_ozon.calls[0]["body"]["offer_id"]


# ============================================================
# 6. 跨店 confirm_required + 换店第二次 submit → 新行、draft.id 不变
# ============================================================

def test_submit_cross_store_confirm_and_new_submission(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_a = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    cred_b = create_store_credential("tenant-A", CLIENT_B, API_KEY_B)

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp_a = _submit(client, draft_id, cred_a)
    assert resp_a.status_code == 200
    assert resp_a.json()["confirm_required"] is False
    assert resp_a.json()["existing_stores"] == []

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp_b = _submit(client, draft_id, cred_b)
    assert resp_b.status_code == 200, resp_b.text
    body_b = resp_b.json()
    assert body_b["confirm_required"] is True, "跨店应返回 confirm_required:true"
    assert CLIENT_A in body_b["existing_stores"]
    assert body_b["draft_id"] == draft_id

    with pg.connect() as conn:
        rows = conn.execute(
            text("SELECT store_client_id, submitted_task_id FROM draft_submissions "
                 "WHERE draft_id = :d ORDER BY created_at"),
            {"d": draft_id},
        ).fetchall()
    assert [r[0] for r in rows] == [CLIENT_A, CLIENT_B], "换店铺应产生新 submission 行"
    with pg.connect() as conn:
        still = conn.execute(text("SELECT COUNT(*) FROM product_drafts WHERE id=:d"), {"d": draft_id}).scalar()
    assert still == 1


def test_submit_confirm_flag_does_not_block(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_a = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    cred_b = create_store_credential("tenant-A", CLIENT_B, API_KEY_B)

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    _submit(client, draft_id, cred_a)

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_b)
    assert resp.status_code == 200
    assert resp.json()["task_id"]


def test_submit_default_credential_when_id_missing(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    with pg.begin() as conn:
        conn.execute(text(
            "UPDATE credentials SET is_default=true WHERE tenant_id='tenant-A' AND ozon_client_id=:c"),
            {"c": CLIENT_A})

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    fake_sub = patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, None)
    assert resp.status_code == 200, resp.text
    assert fake_sub.payloads[-1]["ozon_client_id"] == CLIENT_A


# ============================================================
# 7. T7 更新模式（update_product_id 透传 + 索引回填）
# ============================================================

UPDATE_PRODUCT_ID = "5476361418"


def test_submit_update_mode_skips_dup_check(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    fake_ozon = patch_ozon(monkeypatch, result={
        "items": [{"offer_id": ITEM_ID, "product_id": UPDATE_PRODUCT_ID}], "total": 1})
    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id, update_product_id=UPDATE_PRODUCT_ID)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert fake_ozon.calls == [], "更新模式必须跳过 per-store 409 重复校验（不查 Ozon）"


def test_submit_update_mode_injects_marker(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    insert_task_index_row(pg, UPDATE_PRODUCT_ID, "tenant-A", ITEM_ID, cred_id, draft_id)

    fake_sub = patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id, update_product_id=UPDATE_PRODUCT_ID)
    assert resp.status_code == 200, resp.text
    ext = fake_sub.payloads[-1]["envelope"]["extensions"]
    assert ext["update_product_id"] == UPDATE_PRODUCT_ID
    assert ext["update_offer_id"] == ITEM_ID, "offer_id 必须从 product_task_index 复用"


def test_submit_update_mode_draft_unchanged(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    insert_task_index_row(pg, UPDATE_PRODUCT_ID, "tenant-A", ITEM_ID, cred_id, draft_id)

    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id, update_product_id=UPDATE_PRODUCT_ID)
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/v1/drafts/{draft_id}",
                     headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert got.status_code == 200
    payload_ext = (got.json()["payload"] or {}).get("extensions") or {}
    assert "update_product_id" not in payload_ext, "marker 不得持久化到 product_drafts.payload"
    assert "update_offer_id" not in payload_ext

    with pg.connect() as conn:
        sub_ext = conn.execute(
            text("SELECT extensions FROM draft_submissions WHERE draft_id=:d"),
            {"d": draft_id}).fetchone()[0]
    assert "update_product_id" not in sub_ext, "marker 不得持久化到 draft_submissions.extensions"


def test_submit_update_mode_index_upsert(pg, monkeypatch):
    import services.product_index_service as pis
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    insert_task_index_row(pg, UPDATE_PRODUCT_ID, "tenant-A", ITEM_ID, cred_id, draft_id)

    upserts = []
    monkeypatch.setattr(pis, "upsert_index",
                        lambda *a, **kw: upserts.append((a, kw)))
    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id, update_product_id=UPDATE_PRODUCT_ID)
    assert resp.status_code == 200, resp.text
    new_task_id = resp.json()["task_id"]
    assert upserts, "更新模式入队后必须 upsert_index（新 task_id 回填）"
    args, kwargs = upserts[-1]
    assert args[0] == "tenant-A"
    assert args[1] == UPDATE_PRODUCT_ID
    assert args[2] == ITEM_ID
    assert args[3] == new_task_id
    assert str(args[4]) == str(cred_id)
    assert str(kwargs["draft_id"]) == draft_id


def test_submit_update_mode_index_missing_offer_id(pg, monkeypatch):
    import services.product_index_service as pis
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    upserts = []
    monkeypatch.setattr(pis, "upsert_index", lambda *a, **kw: upserts.append(1))
    fake_sub = patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id, update_product_id=UPDATE_PRODUCT_ID)
    assert resp.status_code == 200, resp.text
    assert upserts == [], "index 无行（offer_id 不可解析）→ 跳过 upsert_index"
    ext = fake_sub.payloads[-1]["envelope"]["extensions"]
    assert ext["update_product_id"] == UPDATE_PRODUCT_ID
    assert "update_offer_id" not in ext


def test_submit_update_mode_upsert_failure_nonblocking(pg, monkeypatch):
    import services.product_index_service as pis
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    insert_task_index_row(pg, UPDATE_PRODUCT_ID, "tenant-A", ITEM_ID, cred_id, draft_id)

    monkeypatch.setattr(pis, "upsert_index", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id, update_product_id=UPDATE_PRODUCT_ID)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["task_id"], "索引回填失败不得阻断提交"


def test_submit_normal_mode_regression(pg, monkeypatch):
    import services.product_index_service as pis
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_id = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)

    upserts = []
    monkeypatch.setattr(pis, "upsert_index", lambda *a, **kw: upserts.append(1))

    fake_ozon = patch_ozon(monkeypatch, result={
        "items": [{"offer_id": ITEM_ID, "product_id": UPDATE_PRODUCT_ID}], "total": 1})
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 409, resp.text
    assert "重复商品" in resp.json()["detail"]
    assert fake_ozon.calls, "正常模式 per-store 重复校验必须仍生效"
    assert upserts == []

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    fake_sub = patch_submitter(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    ext = (fake_sub.payloads[-1]["envelope"].get("extensions") or {})
    assert "update_product_id" not in ext
    assert "update_offer_id" not in ext
    assert upserts == [], "正常模式不得调 upsert_index"


def test_submit_update_mode_cross_store_confirm(pg, monkeypatch):
    client = TestClient(make_app())
    created = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    draft_id = created.json()["id"]
    cred_a = create_store_credential("tenant-A", CLIENT_A, API_KEY_A)
    cred_b = create_store_credential("tenant-A", CLIENT_B, API_KEY_B)
    insert_task_index_row(pg, UPDATE_PRODUCT_ID, "tenant-A", ITEM_ID, cred_a, draft_id)

    patch_ozon(monkeypatch, result={"items": [], "total": 0})
    patch_submitter(monkeypatch)
    resp_a = _submit(client, draft_id, cred_a)
    assert resp_a.status_code == 200
    assert resp_a.json()["confirm_required"] is False

    patch_submitter(monkeypatch)
    resp_b = _submit(client, draft_id, cred_b, update_product_id=UPDATE_PRODUCT_ID)
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json()["confirm_required"] is True, "更新到其他店仍保留跨店确认语义"
    assert CLIENT_A in resp_b.json()["existing_stores"]


# ============================================================
# 8. 未认证 → 401（路由薄层鉴权）
# ============================================================

def test_route_auth_required():
    client = TestClient(make_app())
    assert client.post("/api/v1/drafts", json={"envelope": make_envelope()}).status_code == 401
    assert client.get("/api/v1/drafts").status_code == 401


# ============================================================
# 8. main.py 注册 v1 router（架构门：/api/v1/drafts 存在）
# ============================================================

def test_main_registers_drafts_router():
    import main as main_mod
    paths = set(main_mod.app.openapi()["paths"])
    assert "/api/v1/drafts" in paths
    assert "/api/v1/drafts/{draft_id}" in paths
    assert "/api/v1/drafts/{draft_id}/submit" in paths

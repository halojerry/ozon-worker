"""P0-1: submit_draft 集成上架配置模板测试。

验收门（docs/PRD-listing-template-v0.44.md §五）：
1. 带 template_id → 模板注入 graph payload extensions（草稿已有值优先）
2. 无 template_id → 租户默认模板兜底
3. 更新模式（update_product_id）→ 忽略 offer_id_prefix（重上不变式）
4. 模板不存在 → 跳过注入不阻断（fail-open）

需要本地 Docker PG；PG 不可达时 skip。
"""
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
MASTER_KEY = "0123456789abcdef0123456789abcdef"
TOKEN_A = "tokA"
TOKEN_B = "tokB"
CLIENT_A = "111111"
API_KEY_A = "api-key-a"
TOKEN_TO_TENANT = {TOKEN_A: "tenant-A", TOKEN_B: "tenant-B"}


@pytest.fixture(scope="module")
def pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过 submit 模板测试")
    yield create_engine(DB_URL)
    eng = create_engine(DB_URL)
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_tables(pg):
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM draft_submissions"))
        conn.execute(text("DELETE FROM product_task_index"))
        conn.execute(text("DELETE FROM product_drafts"))
        conn.execute(text("DELETE FROM credentials"))
        conn.execute(text("DELETE FROM listing_templates"))
        conn.execute(text("DELETE FROM ozon_product_tasks"))
    yield


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


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


def make_envelope(margin=None):
    env = {
        "draft": {
            "item_id": "16880001",
            "title": "测试商品",
            "images": ["https://example.com/a.jpg"],
            "weight": 350,
            "dimensions": {"length": 10, "width": 10, "height": 10},
            "purchase_cost": 12.5,
            "purchase_url": "https://detail.1688.com/offer/16880001.html",
        },
        "source": {"purchase_url": "https://detail.1688.com/offer/16880001.html",
                   "purchase_cost": 12.5},
        "extensions": {},
    }
    if margin is not None:
        env["extensions"]["margin_rate"] = margin
    return env


def graph_input(client_id, api_key, env):
    return {
        "token": TOKEN_A,
        "ozon_client_id": client_id,
        "ozon_api_key": api_key,
        "envelope": env,
    }


def patch_submitter(monkeypatch):
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


def patch_ozon(monkeypatch):
    from services import draft_service as ds

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        return {"items": [], "total": 0}

    monkeypatch.setattr(ds, "ozon_post", _fake)
    return _fake


def create_credential(tenant: str, client_id: str, api_key: str) -> str:
    from services import credential_service
    return credential_service.store_credential(tenant, client_id, api_key)


def create_template(tenant: str, name: str, config: dict, is_default=False) -> str:
    from services import template_service
    return template_service.create_template(
        tenant, {"name": name, "config": config, "is_default": is_default})["id"]


def create_draft(client, env) -> str:
    resp = client.post("/api/v1/drafts", json=graph_input(CLIENT_A, API_KEY_A, env))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _submit(client, draft_id, cred_id, template_id=None, update_product_id=None):
    body = {"token": TOKEN_A, "credential_id": cred_id}
    if template_id:
        body["template_id"] = template_id
    if update_product_id:
        body["update_product_id"] = update_product_id
    return client.post(f"/api/v1/drafts/{draft_id}/submit", json=body)


# ============================================================
# 1. 显式 template_id → 注入（草稿已有值优先）
# ============================================================

def test_submit_with_template_injects(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    tpl_id = create_template("tenant-A", "高利润", {
        "margin_rate": 0.35, "offer_id_prefix": "W1", "stock": 50})
    draft_id = create_draft(client, make_envelope())  # 草稿无 margin

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id, template_id=tpl_id)
    assert resp.status_code == 200, resp.text

    payload = fake.payloads[0]
    ext = payload["envelope"]["extensions"]
    # 模板注入 margin/prefix/stock
    assert ext["margin_rate"] == 0.35
    assert ext["offer_id_prefix"] == "W1"
    assert ext["stock"] == 50


def test_submit_template_draft_value_wins(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    tpl_id = create_template("tenant-A", "高利润", {"margin_rate": 0.35})
    draft_id = create_draft(client, make_envelope(margin=0.5))  # 草稿显式 margin=0.5

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id, template_id=tpl_id)
    assert resp.status_code == 200, resp.text

    ext = fake.payloads[0]["envelope"]["extensions"]
    assert ext["margin_rate"] == 0.5  # 草稿已有值优先


# ============================================================
# 2. 无 template_id → 默认模板兜底
# ============================================================

def test_submit_uses_default_template(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    create_template("tenant-A", "非默认", {"margin_rate": 0.2})
    create_template("tenant-A", "默认的", {"margin_rate": 0.4}, is_default=True)
    draft_id = create_draft(client, make_envelope())

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text

    ext = fake.payloads[0]["envelope"]["extensions"]
    assert ext["margin_rate"] == 0.4  # 默认模板注入


def test_submit_no_template_no_default_unchanged(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    draft_id = create_draft(client, make_envelope())

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text

    ext = fake.payloads[0]["envelope"]["extensions"]
    assert "margin_rate" not in ext  # 无模板无默认 → 原样


# ============================================================
# 3. 更新模式 → 忽略 offer_id_prefix
# ============================================================

def test_submit_update_mode_ignores_prefix(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    tpl_id = create_template("tenant-A", "带前缀", {
        "margin_rate": 0.3, "offer_id_prefix": "W1"})
    draft_id = create_draft(client, make_envelope())

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id, template_id=tpl_id,
                   update_product_id="5947018373")
    assert resp.status_code == 200, resp.text

    payload = fake.payloads[0]
    ext = payload["envelope"]["extensions"]
    assert "offer_id_prefix" not in ext  # 更新模式忽略前缀（重上不变式）
    assert ext["margin_rate"] == 0.3    # 其余字段仍注入
    assert ext["update_product_id"] == "5947018373"


# ============================================================
# 4. 模板不存在 → fail-open 不阻断
# ============================================================

def test_submit_unknown_template_fail_open(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    draft_id = create_draft(client, make_envelope())

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id, template_id=str(uuid.uuid4()))
    assert resp.status_code == 200, resp.text  # 不阻断

    ext = fake.payloads[0]["envelope"]["extensions"]
    assert "margin_rate" not in ext  # 无注入


# ============================================================
# 5. submission 快照记录注入后值
# ============================================================

def test_submission_snapshot_records_injected(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id = create_credential("tenant-A", CLIENT_A, API_KEY_A)
    tpl_id = create_template("tenant-A", "高利润", {"margin_rate": 0.35, "stock": 9})
    draft_id = create_draft(client, make_envelope())

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id, template_id=tpl_id)
    assert resp.status_code == 200, resp.text

    with pg.connect() as conn:
        row = conn.execute(text(
            "SELECT extensions FROM draft_submissions ORDER BY created_at DESC LIMIT 1"
        )).fetchone()
    snapshot = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    assert snapshot["margin_rate"] == 0.35
    assert snapshot["stock"] == 9

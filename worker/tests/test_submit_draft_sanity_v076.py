"""v0.76 终审 Fix-1: submit_draft 入队前补 validate_draft_sanity 闸。

背景：全仓此前唯一 validate_draft_sanity 调用点在 main.py submit_task 路径，
采集箱 submit/resubmit/batch-submit（draft_service.submit_draft）入队前无任何
sanity 检查 → weight=0 / cost<=0 信封可绕过 T27 purchase_cost 闸进管线。

语义辨析：这是「拒绝」不是「改写」——不符合 AGENTS「采集箱即权威禁自主重配」
红线的约束对象（该红线约束的是改写用户可见内容，如 R4 换类目/标题重写）；
拒单让用户回采集箱修正数据，所见即所得不被破坏。

需要本地 Docker PG；PG 不可达时 skip。
"""
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
CLIENT_A = "111111"
API_KEY_A = "api-key-a"
TOKEN_TO_TENANT = {TOKEN_A: "tenant-A"}


@pytest.fixture(scope="module")
def pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过 submit sanity 测试")
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


def make_envelope(**draft_overrides):
    draft = {
        "item_id": "16880001",
        "title": "测试商品",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
        "weight": 350,
        "dimensions": {"length": 10, "width": 10, "height": 10},
        "purchase_cost": 12.5,
        "purchase_url": "https://detail.1688.com/offer/16880001.html",
    }
    draft.update(draft_overrides)
    return {
        "draft": draft,
        "source": {"purchase_url": "https://detail.1688.com/offer/16880001.html",
                   "purchase_cost": draft["purchase_cost"]},
        "extensions": {},
    }


def graph_input(env):
    return {
        "token": TOKEN_A,
        "ozon_client_id": CLIENT_A,
        "ozon_api_key": API_KEY_A,
        "envelope": env,
    }


def patch_submitter(monkeypatch):
    from services import draft_service as ds

    async def _fake(tenant_id, payload, sku_key=""):
        task_id = str(uuid.uuid4())
        getattr(_fake, "payloads", []).append(payload)
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


def _submit(client, draft_id, cred_id):
    body = {"token": TOKEN_A, "credential_id": cred_id}
    return client.post(f"/api/v1/drafts/{draft_id}/submit", json=body)


def _create_credential_and_draft(client, env) -> str:
    from services import credential_service
    cred_id = credential_service.store_credential("tenant-A", CLIENT_A, API_KEY_A)
    resp = client.post("/api/v1/drafts", json=graph_input(env))
    assert resp.status_code == 200, resp.text
    return cred_id, resp.json()["id"]


# ============================================================
# 1. weight=0 草稿 submit → 400（此前绕过闸直入管线）
# ============================================================

def test_submit_rejects_zero_weight(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id, draft_id = _create_credential_and_draft(client, make_envelope(weight=0))

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 400, resp.text
    # 文案与 submit_task 路径 INVALID_REQUEST「信封数据异常」对齐
    assert "信封数据异常" in str(resp.json().get("detail"))
    # 入队防线：不允许已入队后失败
    assert fake.payloads == []


def test_submit_rejects_nonpositive_cost(pg, monkeypatch):
    """T27 purchase_cost 闸此前被 draft 路径绕过——cost=0 → 400。"""
    client = TestClient(make_app())
    cred_id, draft_id = _create_credential_and_draft(
        client, make_envelope(purchase_cost=0))

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 400, resp.text
    assert "采购成本必须大于 0" in str(resp.json().get("detail"))
    assert fake.payloads == []


# ============================================================
# 2. C2 竞品兜底放行语义保持（weight=0 + competitor_weight_g → 放行）
# ============================================================

def test_submit_competitor_fallback_still_passes(pg, monkeypatch):
    client = TestClient(make_app())
    env = make_envelope(weight=0)
    env["extensions"]["competitor_weight_g"] = 420
    cred_id, draft_id = _create_credential_and_draft(client, env)

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    assert len(fake.payloads) == 1


def test_submit_follow_sell_cost_exempt(pg, monkeypatch):
    """跟卖信封定价跟随竞品（purchase_cost<=0 豁免）语义保持。"""
    client = TestClient(make_app())
    env = make_envelope(purchase_cost=0, ozon_product_id="5947018373")
    env["extensions"]["follow_sell"] = True
    cred_id, draft_id = _create_credential_and_draft(client, env)

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    assert len(fake.payloads) == 1


# ============================================================
# 3. 正常草稿不受影响（回归）
# ============================================================

def test_submit_normal_draft_unaffected(pg, monkeypatch):
    client = TestClient(make_app())
    cred_id, draft_id = _create_credential_and_draft(client, make_envelope())

    fake = patch_submitter(monkeypatch)
    patch_ozon(monkeypatch)
    resp = _submit(client, draft_id, cred_id)
    assert resp.status_code == 200, resp.text
    assert len(fake.payloads) == 1

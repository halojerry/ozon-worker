"""T15: WebUI 全链路 E2E 测试（本地 Docker PG + mock Ozon/MXOU，无云访问）。

验收门（docs/PLAN-webui-v1.md §5 T15 / §9 成功判据 2、7）：
    本地 Docker 全路径：skill --to-box 创建草稿 → webui 认领（credential 创建+校验）
    → 提交 → 任务进度查询 → 单 slot 重新生成 → 更新在线商品。

本测试把 T5/T6/T7a/T8/T14/T14b 的独立契约串成一条端到端链路，并断言
**端点间数据流一致性**（契约测试补强）：
    1. POST /api/v1/drafts（skill --to-box 请求体）→ draft_id；payload 无明文字段
    2. GET /drafts + GET /credentials → 草稿/凭证可见（租户隔离）
    3. POST /credentials/{id}/validate（mock Ozon probe）→ valid:true
    4. POST /drafts/{id}/submit → task_id + draft_submissions 行 + ozon_product_tasks pending 行
    5. GET /api/v1/tasks → 列表含该任务（进度查询；租户隔离）
    6. GET /tasks/{id}/images + POST /tasks/{id}/images/{slot}/regen（mock run_node）→ version++
    7. POST /products/{product_id}/update_images（mock Ozon 重传 + 存活检查）→ pending_moderation

运行（需本地 Docker PG 5433；PG 不可达时 skip）：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        CREDENTIAL_MASTER_KEY=0123456789abcdef0123456789abcdef \
        ../skill/.venv314/bin/python -m pytest tests/test_webui_e2e.py -q
"""
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
MASTER_KEY = "0123456789abcdef0123456789abcdef"

TOKEN_A = "sk-tokA"  # → TENANT_A（key 派生租户）
TOKEN_B = "sk-tokB"  # → TENANT_B（key 派生租户）
TENANT_A = main_mod._key_user_id("tokA")
TENANT_B = main_mod._key_user_id("tokB")
CLIENT_A = "4718259"
CLIENT_B = "8822111"
API_KEY_A = "sk-api-key-AAAA1111"
API_KEY_B = "sk-api-key-BBBB2222"
ITEM_ID = "980815374096"
PRODUCT_ID = "5476361418"

_TENANTS = (TENANT_A, TENANT_B)


class FakeSupabase:
    """tokens 表 fake：key → user_id 映射（tenant-a / tenant-b 租户隔离）。"""

    def __init__(self):
        self._mapping = {"tokA": "tenant-a", "tokB": "tenant-b"}

    def table(self, name):
        return _FakeTokensTable(self._mapping)


class _FakeTokensTable:
    def __init__(self, mapping):
        self._mapping = mapping
        self._key = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        if col == "key":
            self._key = val
        return self

    def is_(self, col, val):
        return self

    def execute(self):
        uid = self._mapping.get(self._key)
        if uid is None:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[{
            "user_id": uid, "key": self._key, "remain_quota": 999,
            "status": 1, "expired_time": -1, "unlimited_quota": False,
        }])


def make_envelope(item_id: str = ITEM_ID) -> dict:
    """skill 信封（与 skill/scripts/cloud_probe.py build_graph_envelope 输出同构）。"""
    draft = {
        "item_id": item_id,
        "title": "宠物自动饮水器 静音循环过滤",
        "description": "静音设计，适合宠物日常饮水，循环过滤保持水质干净",
        "images": ["https://cbu01.alicdn.com/x.jpg"],
        "weight": 227,
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "purchase_cost": 5.5,
        "purchase_url": f"https://detail.1688.com/offer/{item_id}.html",
        "attributes": {"颜色": "白色", "材质": "ABS"},
        "stock": 100,
        "ozon_category": {"description_category_id": "17028929", "type_id": "504866264"},
    }
    return {
        "draft": draft,
        "source": {"purchase_url": draft["purchase_url"], "purchase_cost": 5.5},
        "extensions": {"margin_rate": 0.25, "commission_rate": 0.10},
    }


def graph_input(client_id: str, api_key: str, envelope: dict, token: str = TOKEN_A) -> dict:
    """skill submit_draft 请求体（cloud_probe.submit_draft → POST /api/v1/drafts）。"""
    return {"token": token, "ozon_client_id": client_id, "ozon_api_key": api_key,
            "envelope": envelope}


@pytest.fixture(scope="module")
def client():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过 WebUI E2E 测试")
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """CREDENTIAL_MASTER_KEY + 鉴权 mock（rate_limiter 放行 + Supabase 按 key 分租户）。"""
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=FakeSupabase()):
        yield


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        # FK 顺序：product_task_index(→task/credential) → submissions(→draft) → 其余
        conn.execute(text("DELETE FROM product_task_index WHERE tenant_id IN :t"),
                     {"t": tuple(_TENANTS)})
        conn.execute(text("DELETE FROM draft_submissions WHERE draft_id IN "
                          "(SELECT id FROM product_drafts WHERE tenant_id IN :t)"),
                     {"t": tuple(_TENANTS)})
        conn.execute(text("DELETE FROM task_generated_images WHERE task_id IN "
                          "(SELECT id::text FROM ozon_product_tasks WHERE tenant_id IN :t)"),
                     {"t": tuple(_TENANTS)})
        conn.execute(text("DELETE FROM product_drafts WHERE tenant_id IN :t"),
                     {"t": tuple(_TENANTS)})
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE tenant_id IN :t"),
                     {"t": tuple(_TENANTS)})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id IN :t"),
                     {"t": tuple(_TENANTS)})
    eng.dispose()


def _db(sql: str, params: dict | None = None):
    eng = create_engine(DB_URL)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql), params or {}).fetchall()
    finally:
        eng.dispose()


class FakeTaskProcessor:
    """mock main.task_processor：submit_task 像真实一样插入 pending 行并返回 UUID。

    draft_service._submit_task 懒导入 main.task_processor（lifespan 初始化），
    测试中替换为同签名 fake —— 唯一被替换的内部入队器（真实入队需 worker 进程）。
    """

    def __init__(self):
        self.payloads = []

    async def submit_task(self, tenant_id: str, payload: dict, sku_key: str = "") -> str:
        self.payloads.append(payload)
        task_id = str(uuid.uuid4())
        with create_engine(DB_URL).begin() as conn:
            conn.execute(text(
                "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) "
                "VALUES (:id, :tenant_id, 'pending', CAST(:payload AS jsonb))"),
                {"id": task_id, "tenant_id": tenant_id,
                 "payload": json.dumps(payload, ensure_ascii=False)})
        return task_id


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_draft(client) -> str:
    resp = client.post("/api/v1/drafts",
                       json=graph_input(CLIENT_A, API_KEY_A, make_envelope()))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _setup_credential(client) -> str:
    """skill --to-box 剥离凭证 + 返回 credential id（tenant-a / CLIENT_A）。"""
    _create_draft(client)
    return client.get("/api/v1/credentials", headers=_auth(TOKEN_A)).json()[0]["id"]


def _submit(client, monkeypatch, credential_id=None) -> str:
    """draft submit → task_id；返回 fake_tp 供 payload 断言。"""
    draft_id = _create_draft(client)
    cred_id = credential_id or _setup_credential(client)
    fake_tp = FakeTaskProcessor()
    monkeypatch.setattr(main_mod, "task_processor", fake_tp)
    with patch("services.draft_service.ozon_post",
               return_value={"items": [], "total": 0}):
        resp = client.post(f"/api/v1/drafts/{draft_id}/submit",
                           json={"token": TOKEN_A, "credential_id": cred_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"], fake_tp, draft_id


# ══════════════════════════════════════════════════════════════
# 1. skill --to-box → 创建草稿（凭证剥离）
# ══════════════════════════════════════════════════════════════

def test_step1_skill_to_box_creates_draft(client):
    """skill submit_draft 请求 → 200 + draft_id；payload 只存 envelope；凭证密文入库。"""
    draft_id = _create_draft(client)
    body = client.get(f"/api/v1/drafts/{draft_id}", headers=_auth(TOKEN_A)).json()
    assert body["source"] == "skill"
    assert body["tenant_id"] == TENANT_A
    assert body["version"] == 1
    assert body["payload"]["draft"]["title"] == "宠物自动饮水器 静音循环过滤"

    rows = _db(
        "SELECT payload, version FROM product_drafts WHERE id=:id",
        {"id": draft_id},
    )
    payload = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])
    assert "ozon_api_key" not in json.dumps(payload), "payload 不得含凭证明文"

    cred = _db(
        "SELECT ozon_api_key_enc, api_key_masked FROM credentials "
        "WHERE tenant_id=:tid AND ozon_client_id=:c",
        {"c": CLIENT_A, "tid": TENANT_A},
    )
    assert cred, "凭证应已加密入库（凭证剥离）"
    assert b"AAAA1111" not in bytes(cred[0][0]), "DB 不得存明文 api_key"
    assert cred[0][1] == "****1111"


# ══════════════════════════════════════════════════════════════
# 2. webui 认领：草稿列表 + 凭证列表（租户隔离）
# ══════════════════════════════════════════════════════════════

def test_step2_webui_claim_lists(client):
    draft_id = _create_draft(client)

    drafts = client.get("/api/v1/drafts", headers=_auth(TOKEN_A))
    assert drafts.status_code == 200
    assert [d["id"] for d in drafts.json()] == [draft_id]

    creds = client.get("/api/v1/credentials", headers=_auth(TOKEN_A))
    assert creds.status_code == 200
    assert any(c["ozon_client_id"] == CLIENT_A and c["api_key_masked"] == "****1111"
               for c in creds.json())

    # 租户隔离：B 看不到 A 的草稿与凭证
    drafts_b = client.get("/api/v1/drafts", headers=_auth(TOKEN_B))
    creds_b = client.get("/api/v1/credentials", headers=_auth(TOKEN_B))
    assert drafts_b.json() == []
    assert creds_b.json() == []


# ══════════════════════════════════════════════════════════════
# 3. credential validate（mock Ozon probe）
# ══════════════════════════════════════════════════════════════

def test_step3_credential_validate(client):
    cred_id = _setup_credential(client)
    with patch("services.credential_service.ozon_post",
               return_value={"result": {"items": []}}) as m:
        resp = client.post(f"/api/v1/credentials/{cred_id}/validate",
                           headers=_auth(TOKEN_A))
    assert resp.status_code == 200
    assert resp.json()["valid"] is True
    assert resp.json()["reason"] == "ok"
    # probe 使用服务端解密后的明文（仅服务端内部，不进响应）
    assert m.call_args.args[:2] == (CLIENT_A, API_KEY_A)


# ══════════════════════════════════════════════════════════════
# 4. 提交：draft submit → task_id + submission 行（per-store 校验 mock）
# ══════════════════════════════════════════════════════════════

def test_step4_submit_enqueues_task(client, monkeypatch):
    cred_id = _setup_credential(client)
    task_id, fake_tp, draft_id = _submit(client, monkeypatch, cred_id)

    # 数据流一致性：submission 行 submitted_task_id == 返回 task_id；任务行 pending
    sub = _db(
        "SELECT status, submitted_task_id, store_client_id, credential_id "
        "FROM draft_submissions WHERE draft_id=:d",
        {"d": draft_id},
    )[0]
    assert sub[0] == "pending" and str(sub[1]) == task_id
    assert sub[2] == CLIENT_A and str(sub[3]) == cred_id
    task = _db("SELECT status FROM ozon_product_tasks WHERE id=:id", {"id": task_id})
    assert task[0][0] == "pending"

    # 提交 payload 完整性：凭证注入（解密后）+ envelope 原样
    sent = fake_tp.payloads[-1]
    assert sent["token"] == TOKEN_A
    assert sent["ozon_client_id"] == CLIENT_A
    assert sent["ozon_api_key"] == API_KEY_A
    assert sent["envelope"]["draft"]["item_id"] == ITEM_ID
    assert sent["user_id"] == TENANT_A


# ══════════════════════════════════════════════════════════════
# 5. 任务进度查询（/api/v1/tasks 列表 + 租户隔离）
# ══════════════════════════════════════════════════════════════

def test_step5_task_progress_query(client, monkeypatch):
    task_id, _, _ = _submit(client, monkeypatch)

    resp = client.get("/api/v1/tasks", params={"token": TOKEN_A})
    assert resp.status_code == 200
    body = resp.json()
    assert any(t["id"] == task_id and t["status"] == "pending" for t in body["items"])
    # payload 安全提取的表格列（T12）：标题/货号/账号可见，无 token
    item = next(t for t in body["items"] if t["id"] == task_id)
    assert item["title"] == "宠物自动饮水器 静音循环过滤"
    assert item["item_id"] == ITEM_ID
    assert item["ozon_client_id"] == CLIENT_A
    assert item["follow_sell"] is False
    raw = json.dumps(body)
    assert API_KEY_A not in raw and "ozon_api_key" not in raw, "任务列表不得泄漏凭证"

    # 租户隔离：B 的任务列表不含 A 的任务
    resp_b = client.get("/api/v1/tasks", params={"token": TOKEN_B})
    assert all(t["id"] != task_id for t in resp_b.json()["items"])


# ══════════════════════════════════════════════════════════════
# 6. 单 slot 重新生成（version++ 新 URL；不静默烧额度由 T7a 单测覆盖）
# ══════════════════════════════════════════════════════════════

def test_step6_single_slot_regen(client, monkeypatch):
    task_id, _, _ = _submit(client, monkeypatch)

    # 前置：模拟 worker 已生成 white_bg v1（task_generated_images 缓存行）
    from utils import task_image_cache as tic
    tic.save_image(task_id, "white_bg", "https://img/white_v1.jpg", version=1,
                   params={"draft": {"title": "x"}, "token": "t"})

    # GET /tasks/{id}/images → 缓存行可见
    resp = client.get(f"/api/v1/tasks/{task_id}/images", headers=_auth(TOKEN_A))
    assert resp.status_code == 200
    items = resp.json()["images"]
    assert any(i["slot"] == "white_bg" and i["version"] == 1
               and i["url"] == "https://img/white_v1.jpg" for i in items)

    # POST regen：mock 生图节点执行（写 version=2 新行）
    async def fake_run_node(node_id, payload, ctx=None, extra_config=None):
        cfg = extra_config or {}
        tic.save_image(cfg["thread_id"], "white_bg", "https://img/white_v2.jpg",
                       version=cfg["regen_version"], params=payload)
        return {"white_bg_image": "https://img/white_v2.jpg"}

    with patch.object(main_mod.service, "run_node", side_effect=fake_run_node):
        resp = client.post(f"/api/v1/tasks/{task_id}/images/white_bg/regen",
                           headers=_auth(TOKEN_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot"] == "white_bg" and body["version"] == 2
    assert body["url"] == "https://img/white_v2.jpg"

    # 数据流一致性：列表现在 2 行（v1 保留 + v2 新行）；latest 为 v2
    items = client.get(f"/api/v1/tasks/{task_id}/images", headers=_auth(TOKEN_A)).json()["images"]
    versions = {i["version"] for i in items if i["slot"] == "white_bg"}
    assert versions == {1, 2}
    assert tic.get_image(task_id, "white_bg") == "https://img/white_v2.jpg"

    # 跨租户 regen → 404（image_service._ensure_task_tenant 隔离）
    resp = client.post(f"/api/v1/tasks/{task_id}/images/white_bg/regen",
                       headers=_auth(TOKEN_B))
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════
# 7. 更新在线商品（product_task_index 定位 → 存活检查 → 全量重传 → 重新审核中）
# ══════════════════════════════════════════════════════════════

def test_step7_update_online_product(client, monkeypatch):
    task_id, _, _ = _submit(client, monkeypatch)
    cred_id = _setup_credential(client)

    # 前置：product_task_index 行（T14 ozon_status approved 挂钩回填的索引）
    with create_engine(DB_URL).begin() as conn:
        conn.execute(text(
            "INSERT INTO product_task_index (product_id, tenant_id, offer_id, task_id, credential_id) "
            "VALUES (:pid, :t, :offer, :task, :cred)"),
            {"pid": PRODUCT_ID, "t": TENANT_A, "offer": ITEM_ID,
             "task": task_id, "cred": cred_id})

    captured: dict = {}

    def fake_ozon(client_id, api_key, endpoint, body, **kwargs):
        if endpoint == "/v3/product/import":
            captured["import_body"] = body
            return {"result": {"task_id": "import-task-77"}}
        if endpoint == "/v3/product/info/list":
            return {"result": {"items": [
                {"id": int(body["product_id"][0]),
                 "statuses": {"moderate_status": "pending"}},
            ]}}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def checker(url: str) -> bool:
        return url == "https://img1.example.com/ok.jpg"

    with patch("services.image_service.ozon_post", side_effect=fake_ozon), \
         patch("utils.image_quality_evaluator.check_url_alive", side_effect=checker):
        resp = client.post(f"/api/v1/products/{PRODUCT_ID}/update_images",
                           json={"images": [
                               "https://img1.example.com/ok.jpg",
                               "https://img1.example.com/dead.jpg",
                           ]}, headers=_auth(TOKEN_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending_moderation" and body["re_under_review"] is True
    assert body["import_task_id"] == "import-task-77"
    assert body["images"] == ["https://img1.example.com/ok.jpg"]
    assert body["images_filtered"] == ["https://img1.example.com/dead.jpg"]

    # 数据流一致性：重传 payload 含 product_id/offer_id/新 images；原任务状态迁移
    item = captured["import_body"]["items"][0]
    assert item["product_id"] == int(PRODUCT_ID)
    assert item["offer_id"] == ITEM_ID
    assert item["images"] == ["https://img1.example.com/ok.jpg"]
    rows = _db("SELECT status FROM ozon_product_tasks WHERE id=:id", {"id": task_id})
    assert rows[0][0] == "pending_moderation", "改图触发重新审核应迁移任务状态"

    # 未认证 → 401；跨租户 → 404
    assert client.post(f"/api/v1/products/{PRODUCT_ID}/update_images",
                       json={"images": ["https://img1.example.com/a.jpg"]}).status_code == 401
    assert client.post(f"/api/v1/products/{PRODUCT_ID}/update_images",
                       json={"images": ["https://img1.example.com/a.jpg"]},
                       headers=_auth(TOKEN_B)).status_code == 404

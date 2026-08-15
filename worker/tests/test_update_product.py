"""T14: 在线商品改图全量重传端点测试（真实 PG + mock Ozon API）。

验收门（docs/PLAN-webui-v1.md §5 T14）：
1. 重传 payload 含 product_id / offer_id / 新 images（断言捕获的 import body）
2. 死 URL 过滤（mock check_url_alive → 不可达 URL 不进 payload）
3. status 迁移 pending_moderation（ozon_product_tasks 行 status 断言）
4. 索引行写入（mock ozon_status approved → product_task_index 有行，C1b）
5. 无 product_task_index → 404「商品未找到，可能已归档」
6. 未认证 → 401
7. 跨租户访问 → 404（product_task_index 按 tenant_id 过滤）

需要本地 Docker PG（product_task_index 表已由 T1 建好）；PG 不可达时 skip。
"""
import os
import sys
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

# 32 字节 AES-256 主密钥（测试用，与 credential_cipher 单测一致）
MASTER_KEY = "0123456789abcdef0123456789abcdef"

TENANTS = ("tenant-a", "tenant-b")
TOKEN_MAP = {"tokA": "tenant-a", "tokB": "tenant-b"}

PRODUCT_ID = "1234567890"
OFFER_ID = "sku-123"


class FakeSupabase:
    """tokens 表 fake：key → user_id 映射（租户隔离测试用）。"""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def table(self, name):
        return _FakeTokensTable(self._mapping)


class _FakeTokensTable:
    def __init__(self, mapping: dict[str, str]):
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


@pytest.fixture(scope="module")
def client():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过在线商品更新测试")
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """CREDENTIAL_MASTER_KEY + 鉴权 mock（rate_limiter 放行 + Supabase 按 key 分租户）。"""
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=FakeSupabase(TOKEN_MAP)):
        yield


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        # 先删 product_task_index（FK 引用 task + credential），再删 task / credential
        conn.execute(text(
            "DELETE FROM product_task_index WHERE tenant_id IN ('tenant-a','tenant-b')"
        ))
        conn.execute(text(
            "DELETE FROM ozon_product_tasks WHERE tenant_id IN ('tenant-a','tenant-b')"
        ))
        conn.execute(text(
            "DELETE FROM credentials WHERE tenant_id IN ('tenant-a','tenant-b')"
        ))
    eng.dispose()


def _auth_headers(tenant: str) -> dict:
    token = "tokA" if tenant == "tenant-a" else "tokB"
    return {"Authorization": f"Bearer sk-{token}"}


def _db_rows(sql: str, params: dict | None = None) -> list:
    eng = create_engine(DB_URL)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql), params or {}).fetchall()
    finally:
        eng.dispose()


def _setup_product(client, product_id: str = PRODUCT_ID, tenant: str = "tenant-a") -> tuple[dict, str]:
    """建 credential（API 加密存储）+ completed task + product_task_index 行。

    Returns: (credential dict, task_id str)
    """
    cred = client.post(
        "/api/v1/credentials",
        json={"ozon_client_id": "4718259", "api_key": "update-key-4718259"},
        headers=_auth_headers(tenant),
    ).json()
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        task_id = conn.execute(text(
            "INSERT INTO ozon_product_tasks (tenant_id, status, payload) "
            "VALUES (:t, 'completed', '{}'::jsonb) RETURNING id"
        ), {"t": tenant}).scalar()
        conn.execute(text(
            "INSERT INTO product_task_index (product_id, tenant_id, offer_id, task_id, credential_id) "
            "VALUES (:pid, :t, :offer, :task, :cred)"
        ), {"pid": product_id, "t": tenant, "offer": OFFER_ID,
            "task": task_id, "cred": cred["id"]})
    eng.dispose()
    return cred, str(task_id)


def _fake_ozon_post(moderation_status: str = "pending"):
    """mock ozon_post：/v3/product/import 返回 task_id；/v3/product/info/list 返回审核状态。

    Returns: (fake_fn, captured_dict) — captured["import_body"] 记录重传 payload。
    """
    captured: dict = {}

    def fake(client_id, api_key, endpoint, body, **kwargs):
        if endpoint == "/v3/product/import":
            captured["import_body"] = body
            return {"result": {"task_id": "import-task-42"}}
        if endpoint == "/v3/product/info/list":
            pid = int(body["product_id"][0])
            return {"result": {"items": [
                {"id": pid, "statuses": {"moderate_status": moderation_status}},
            ]}}
        raise AssertionError(f"unexpected ozon_post endpoint: {endpoint}")

    return fake, captured


def _post_update(client, images, product_id=PRODUCT_ID, tenant="tenant-a"):
    return client.post(
        f"/api/v1/products/{product_id}/update_images",
        json={"images": images},
        headers=_auth_headers(tenant),
    )


# ============================================================
# 1. 重传 payload 含 product_id / offer_id / 新 images
# ============================================================

def test_payload_contains_product_id_offer_id_images(client):
    _setup_product(client)
    fake, captured = _fake_ozon_post(moderation_status="pending")
    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", return_value=True):
        resp = _post_update(client, [
            "https://img1.example.com/a.jpg",
            "https://img1.example.com/b.jpg",
        ])
    assert resp.status_code == 200, resp.text
    items = captured["import_body"]["items"]
    assert len(items) == 1
    assert items[0]["product_id"] == int(PRODUCT_ID)
    assert items[0]["offer_id"] == OFFER_ID
    assert items[0]["images"] == [
        "https://img1.example.com/a.jpg",
        "https://img1.example.com/b.jpg",
    ]


# ============================================================
# 2. 死 URL 过滤
# ============================================================

def test_dead_url_filtered(client):
    _setup_product(client)
    fake, captured = _fake_ozon_post(moderation_status="pending")

    def checker(url: str) -> bool:
        return url == "https://img1.example.com/alive.jpg"

    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", side_effect=checker):
        resp = _post_update(client, [
            "https://img1.example.com/alive.jpg",
            "https://img1.example.com/dead.jpg",
        ])
    assert resp.status_code == 200, resp.text
    item = captured["import_body"]["items"][0]
    assert "https://img1.example.com/dead.jpg" not in item["images"]
    assert item["images"] == ["https://img1.example.com/alive.jpg"]
    body = resp.json()
    assert body["images_filtered"] == ["https://img1.example.com/dead.jpg"]
    assert body["images"] == ["https://img1.example.com/alive.jpg"]


def test_all_dead_422(client):
    _setup_product(client)
    fake, _ = _fake_ozon_post(moderation_status="pending")
    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", return_value=False):
        resp = _post_update(client, ["https://img1.example.com/dead.jpg"])
    assert resp.status_code == 422


# ============================================================
# 3. status 迁移 pending_moderation
# ============================================================

def test_status_migrates_to_pending_moderation(client):
    _, task_id = _setup_product(client)
    fake, _ = _fake_ozon_post(moderation_status="pending")
    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", return_value=True):
        resp = _post_update(client, ["https://img1.example.com/a.jpg"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending_moderation"
    assert resp.json()["re_under_review"] is True
    rows = _db_rows(
        "SELECT status FROM ozon_product_tasks WHERE id::text=:id", {"id": task_id}
    )
    assert rows[0][0] == "pending_moderation"


# ============================================================
# 4. 索引行写入（mock ozon_status approved → product_task_index 有行）
# ============================================================

def test_index_row_written_when_approved(client):
    cred, task_id = _setup_product(client)
    fake, _ = _fake_ozon_post(moderation_status="approved")
    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", return_value=True):
        resp = _post_update(client, ["https://img1.example.com/a.jpg"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["re_under_review"] is False
    assert resp.json()["status"] == "approved"
    rows = _db_rows(
        "SELECT product_id, offer_id, task_id, credential_id FROM product_task_index "
        "WHERE product_id=:pid AND tenant_id='tenant-a'",
        {"pid": PRODUCT_ID},
    )
    assert len(rows) == 1, "approved 后 product_task_index 应有行"
    assert rows[0][1] == OFFER_ID
    assert str(rows[0][2]) == task_id
    assert str(rows[0][3]) == cred["id"]


# ============================================================
# 5. 无 product_task_index → 404
# ============================================================

def test_no_index_404(client):
    resp = _post_update(client, ["https://img1.example.com/a.jpg"],
                        product_id="9999999999")
    assert resp.status_code == 404
    assert "已归档" in resp.json()["detail"]


# ============================================================
# 6. 未认证 → 401
# ============================================================

def test_auth_required_401(client):
    resp = client.post(
        f"/api/v1/products/{PRODUCT_ID}/update_images", json={"images": []}
    )
    assert resp.status_code == 401


# ============================================================
# 7. 跨租户访问 → 404（product_task_index 按 tenant_id 过滤）
# ============================================================

def test_cross_tenant_404(client):
    _setup_product(client, tenant="tenant-a")
    fake, _ = _fake_ozon_post(moderation_status="pending")
    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", return_value=True):
        resp = _post_update(client, ["https://img1.example.com/a.jpg"], tenant="tenant-b")
    assert resp.status_code == 404


# ============================================================
# SURFACE：TestClient 全链路演示（import 返回 task_id + 死 URL 过滤 + 重新审核中）
# ============================================================

def test_surface_full_flow_demo(client):
    """演示路径：索引定位 → 死 URL 过滤 → 重传 → 任务标记 pending_moderation。"""
    _, task_id = _setup_product(client)

    def checker(url: str) -> bool:
        return url == "https://img1.example.com/ok.jpg"

    fake, captured = _fake_ozon_post(moderation_status="pending")
    with patch("services.image_service.ozon_post", side_effect=fake), \
         patch("utils.image_quality_evaluator.check_url_alive", side_effect=checker):
        resp = _post_update(client, [
            "https://img1.example.com/ok.jpg",
            "https://img1.example.com/broken.jpg",
            "https://img1.example.com/gone.jpg",
        ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 存活检查后只有 1 张进 payload
    assert captured["import_body"]["items"][0]["images"] == [
        "https://img1.example.com/ok.jpg"
    ]
    # 死 URL 全被过滤并回显
    assert sorted(body["images_filtered"]) == sorted([
        "https://img1.example.com/broken.jpg",
        "https://img1.example.com/gone.jpg",
    ])
    # 重传返回 import task_id + 「重新审核中」标记
    assert body["import_task_id"] == "import-task-42"
    assert body["re_under_review"] is True
    assert body["status"] == "pending_moderation"
    # DB 任务状态同步迁移
    rows = _db_rows(
        "SELECT status FROM ozon_product_tasks WHERE id::text=:id", {"id": task_id}
    )
    assert rows[0][0] == "pending_moderation"

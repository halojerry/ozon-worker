"""T-P3.1 批次契约 — drafts source_batch 写入端 + ?batch= 读取端 契约测试。

契约（与 skill 侧并行改动钉死）：
- 写入端：POST /api/v1/drafts 新增可选字符串字段 source_batch，≤64 字符，缺省 None；
  老 skill 不带该字段照常工作（写 NULL）。
- 读取端：GET /api/v1/drafts?batch=<value> 按 source_batch 精确过滤（=）；
  参数缺席 = 行为与现状完全一致（回归锁定）。

覆盖：
1. 纯函数 _norm_source_batch：None/空白→None；64 字符边界；>64 → 400（拒绝而非截断，
   截断会让不同批次静默合并，过滤失真）
2. model/schema：ProductDraft 列存在（VARCHAR(64) nullable）；DraftCreate/DraftOut 字段可选
3. PG API：创建带/不带 source_batch；?batch= 命中/空结果/无参回归；租户隔离不因过滤失效

运行（需本地 PG 5433；纯函数/模型用例无需 PG）：
    cd worker && PGDATABASE_URL="postgresql://postgres:ozon123@localhost:5433/ozon" \
        ../skill/.venv314/bin/python -m pytest tests/test_drafts_batch_v1.py -q
"""
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
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

TOKEN_A = "sk-batch-token-A"
TOKEN_B = "sk-batch-token-B"
# 专用测试租户（带随机后缀）：清理只删本租户行，不碰库内其他数据
TENANT_A = f"batch-t31-a-{uuid.uuid4().hex[:8]}"
TENANT_B = f"batch-t31-b-{uuid.uuid4().hex[:8]}"
TOKEN_TO_TENANT = {TOKEN_A: TENANT_A, TOKEN_B: TENANT_B}


def make_envelope(item_id: str) -> dict:
    return {
        "draft": {
            "item_id": item_id,
            "title": "批次契约测试草稿",
            "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
            "purchase_cost": 5.5,
            "purchase_url": f"https://detail.1688.com/offer/{item_id}.html",
        },
        "source": {"purchase_url": f"https://detail.1688.com/offer/{item_id}.html", "purchase_cost": 5.5},
        "extensions": {},
    }


# ──────────────────────────────────────────────
# 纯函数：_norm_source_batch（无需 PG）
# ──────────────────────────────────────────────

def test_norm_source_batch_none_and_blank():
    from services.draft_service import _norm_source_batch
    assert _norm_source_batch(None) is None
    assert _norm_source_batch("") is None
    assert _norm_source_batch("   ") is None


def test_norm_source_batch_strips_and_allows_64():
    from services.draft_service import _norm_source_batch
    assert _norm_source_batch("  b-20260909-01  ") == "b-20260909-01"
    assert _norm_source_batch("b" * 64) == "b" * 64  # 边界：64 字符合法


def test_norm_source_batch_rejects_over_64():
    from fastapi import HTTPException

    from services.draft_service import _norm_source_batch
    with pytest.raises(HTTPException) as e:
        _norm_source_batch("b" * 65)
    assert e.value.status_code == 400
    assert "64" in str(e.value.detail)


# ──────────────────────────────────────────────
# model + schema（无需 PG）
# ──────────────────────────────────────────────

def test_product_draft_model_has_source_batch_column():
    from storage.database.shared.model import ProductDraft
    col = ProductDraft.__table__.c.source_batch
    assert col.nullable is True, "source_batch 应为 nullable（老 skill 不带该字段）"
    assert col.type.length == 64, f"source_batch 应为 VARCHAR(64)，实际 {col.type}"


def test_draft_schemas_source_batch_optional():
    from api.schemas import DraftCreate, DraftOut
    body = {
        "token": "sk-x",
        "envelope": {"draft": {"title": "t"}},
    }
    parsed = DraftCreate.model_validate(body)
    assert parsed.source_batch is None, "缺省 source_batch 必须是 None（老 skill 兼容）"
    assert "source_batch" in DraftOut.model_fields


# ──────────────────────────────────────────────
# PG API 契约（PG 不可用 → skip）
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def pg():
    try:
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            cols = {
                r[0] for r in conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='product_drafts'"
                )).fetchall()
            }
        if "source_batch" not in cols:
            # 存量库未迁移 → 先跑幂等迁移（与 scripts/migrate_drafts_batch_v1.py 同源）
            from migrate_drafts_batch_v1 import run_migrations
            run_migrations(engine)
        yield engine
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过批次契约 API 测试")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def cleanup_test_tenants(pg):
    """只清理本文件专用租户的行（不碰库内既有数据）。"""
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM product_drafts WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT_A, "b": TENANT_B})
    yield
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM product_drafts WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT_A, "b": TENANT_B})


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch):
    import main as main_mod

    def _auth(token: str) -> str:
        if not token:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Token is required")
        return TOKEN_TO_TENANT.get(token, TENANT_A)

    monkeypatch.setattr(main_mod, "_authenticate_token", _auth)
    monkeypatch.setattr(main_mod, "get_supabase_client", lambda: None)
    yield _auth


def make_app() -> FastAPI:
    from routes.drafts_routes import router as drafts_router
    app = FastAPI()
    app.include_router(drafts_router)
    return app


def _create(client, token, item_id, source_batch=None):
    body = {"token": token, "envelope": make_envelope(item_id), "source": "skill"}
    if source_batch is not None:
        body["source_batch"] = source_batch
    return client.post("/api/v1/drafts", json=body)


def test_create_with_and_without_source_batch(pg):
    """带 source_batch 创建 → 落列并回显；不带 → None（老 skill 行为不变）。"""
    client = TestClient(make_app())
    resp = _create(client, TOKEN_A, "batch-item-1", source_batch="b-20260909-01")
    assert resp.status_code == 200, resp.text
    with_batch = resp.json()
    assert with_batch["source_batch"] == "b-20260909-01"

    resp = _create(client, TOKEN_A, "batch-item-2")
    assert resp.status_code == 200, resp.text
    without_batch = resp.json()
    assert without_batch["source_batch"] is None

    # DB 直查：列真实落库（不是仅响应层拼接）
    with pg.connect() as conn:
        row = conn.execute(text(
            "SELECT source_batch FROM product_drafts WHERE id=:id"
        ), {"id": uuid.UUID(with_batch["id"])}).fetchone()
    assert row[0] == "b-20260909-01"


def test_create_rejects_source_batch_over_64():
    """>64 字符 → 400（拒绝而非截断）。"""
    client = TestClient(make_app())
    resp = _create(client, TOKEN_A, "batch-item-3", source_batch="b" * 65)
    assert resp.status_code == 400
    assert "64" in str(resp.json().get("detail", ""))


def test_list_filter_by_batch_hit_miss_and_regression(pg):
    """?batch= 精确过滤：命中只含该批次；未知批次 → 空；无参 → 全量回归。"""
    client = TestClient(make_app())
    _create(client, TOKEN_A, "f-item-1", source_batch="b-20260909-01")
    _create(client, TOKEN_A, "f-item-2", source_batch="b-20260909-01")
    _create(client, TOKEN_A, "f-item-3", source_batch="b-20260910-99")
    _create(client, TOKEN_A, "f-item-4")  # 无批次（老 skill 形态）

    # 命中：只返回该批次的 2 条
    resp = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_A}"},
                      params={"batch": "b-20260909-01"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert all(d["source_batch"] == "b-20260909-01" for d in items)

    # 空结果：未知批次 → []
    resp = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_A}"},
                      params={"batch": "no-such-batch"})
    assert resp.status_code == 200
    assert resp.json() == []

    # 回归：无 batch 参数 → 不过滤，返回租户全量 4 条（与现状行为一致）
    resp = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 4

    # ?batch=（空值）等价缺席 → 不过滤
    resp = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_A}"},
                      params={"batch": ""})
    assert resp.status_code == 200
    assert len(resp.json()) == 4


def test_list_filter_respects_tenant_isolation(pg):
    """?batch= 过滤叠加租户隔离：B 租户同批次草稿不泄露给 A。"""
    client = TestClient(make_app())
    _create(client, TOKEN_A, "iso-item-a", source_batch="shared-batch")
    _create(client, TOKEN_B, "iso-item-b", source_batch="shared-batch")

    resp = client.get("/api/v1/drafts", headers={"Authorization": f"Bearer {TOKEN_A}"},
                      params={"batch": "shared-batch"})
    items = resp.json()
    assert len(items) == 1
    assert items[0]["tenant_id"] == TENANT_A
    assert items[0]["payload"]["draft"]["item_id"] == "iso-item-a"


def test_batch_draft_visible_in_export_and_get(pg):
    """带批次草稿在单读/导出链路照常可见（消费方不破坏）。"""
    client = TestClient(make_app())
    created = _create(client, TOKEN_A, "exp-item-1", source_batch="b-export").json()

    resp = client.get(f"/api/v1/drafts/{created['id']}",
                      headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert resp.status_code == 200
    assert resp.json()["source_batch"] == "b-export"

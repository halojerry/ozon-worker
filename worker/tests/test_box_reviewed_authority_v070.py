#!/usr/bin/env python3
"""采集箱即权威（box_reviewed）硬语义回归（v0.70）。

用户在采集箱编辑/预组装/采纳类目后的草稿 = 人工审核过的成品卡：
- submit_draft 提交时信封注入 extensions.box_reviewed=True；
- Step 6.5 一致性重配豁免（assemble 不再用俄语标题反猜类目）；
- R4 整卡换类目禁用（拒审如实 failed + moderation_texts 留原文，人工 resubmit）；
- error_repair_llm 的标题/描述重写丢弃（属性值合规修复照常）。
非 box_reviewed（skill 直连管线）旧行为逐字不变。

测试分层：纯函数/State 级（无 PG）+ submit_draft 注入（PG 集成，不可达 skip）。

运行：
    cd worker && PGDATABASE_URL=... PYTHONPATH=src ../skill/.venv314/bin/python \
        -m pytest tests/test_box_reviewed_authority_v070.py -q
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.state import ValidationRetryWrapperInput  # noqa: E402
from graphs.validation_retry_loop import (  # noqa: E402
    ValidationRetryLoopInput,
    ValidationRetryLoopState,
    _box_reviewed,
    _try_recategorize_card,
)


# ============================================================
# 1. 透传链：Input model 声明 extensions（langgraph 过滤纪律防回归）
# ============================================================

def test_extensions_declared_in_input_models():
    """Wrapper/Loop 两级 Input + 子图 State 必须声明 extensions，否则静默拿不到。"""
    assert "extensions" in ValidationRetryWrapperInput.model_fields
    assert "extensions" in ValidationRetryLoopInput.model_fields
    assert "extensions" in ValidationRetryLoopState.model_fields


def test_box_reviewed_helper():
    """_box_reviewed：extensions.box_reviewed=True 判定；None/缺失安全。"""
    s = ValidationRetryLoopState(extensions={"box_reviewed": True})
    assert _box_reviewed(s) is True
    s2 = ValidationRetryLoopState(extensions={})
    assert _box_reviewed(s2) is False
    s3 = ValidationRetryLoopState()
    assert _box_reviewed(s3) is False


# ============================================================
# 2. Step 6.5 豁免（纯函数）
# ============================================================

def test_step65_exempt_with_box_reviewed():
    from graphs.nodes.assemble_ozon_product_node import _step65_consistency_exempt

    assert _step65_consistency_exempt("L1", False, False, box_reviewed=True) is True
    # 无 box_reviewed 的普通 L1 不豁免（A2 型救回语义保持）
    assert _step65_consistency_exempt("L1", False, False, box_reviewed=False) is False
    # 既有豁免类不回归
    assert _step65_consistency_exempt("Skill", False, False) is True
    assert _step65_consistency_exempt("L1", True, False) is True


# ============================================================
# 3. R4 换类目对 box_reviewed 禁用
# ============================================================

def test_r4_disabled_for_box_reviewed():
    """box_reviewed 草稿 R4 恒 False——类目错配拒审由人工改后 resubmit。"""
    state = ValidationRetryLoopState(
        draft={"title": "宠物饮水器", "attributes": {"材质": "ABS"}},
        extensions={"box_reviewed": True},
        error_message="Категория товара не соответствует",
    )
    assert _try_recategorize_card(state) is False


def test_r4_legacy_behavior_unchanged_without_flag():
    """无 box_reviewed 的旧路径不回归：缺源词仍走「无解 False」分支（非 authority 拦截）。"""
    state = ValidationRetryLoopState()  # 空 draft + 无 extensions
    assert _box_reviewed(state) is False
    assert _try_recategorize_card(state) is False  # 无 1688 源词 → 无解 False


# ============================================================
# 4. submit_draft 注入 box_reviewed（PG 集成，不可达 skip）
# ============================================================

DB_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon")
MASTER_KEY = "0123456789abcdef0123456789abcdef"
TOKEN_A = "tokA"


def _pg_available() -> bool:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="PG 不可用，跳过 submit 注入集成测试")
def test_submit_draft_injects_box_reviewed(monkeypatch):
    """采集箱提交的信封 extensions.box_reviewed=True（含 update 模式）。"""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)

    import main as main_mod

    monkeypatch.setattr(main_mod, "_authenticate_token",
                        lambda token: "tenant-A" if token else (_ for _ in ()).throw(
                            __import__("fastapi").HTTPException(status_code=401)))
    monkeypatch.setattr(main_mod, "get_supabase_client", lambda: None)

    from routes.drafts_routes import router as drafts_router
    app = FastAPI()
    app.include_router(drafts_router)
    client = TestClient(app)

    from services import draft_service as ds

    captured: list = []

    async def _fake_submit(tenant_id, payload, sku_key=""):
        captured.append(payload)
        return str(uuid.uuid4())

    monkeypatch.setattr(ds, "_submit_task", _fake_submit)
    monkeypatch.setattr(ds, "ozon_post",
                        lambda *a, **k: {"items": [], "total": 0})

    # 建凭证 + 草稿
    from services import credential_service
    cred_id = credential_service.store_credential("tenant-A", "111111", "api-key-a")
    envelope = {
        "draft": {
            "item_id": "16880042",
            "title": "测试商品",
            "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
            "weight": 350,
            "dimensions": {"length": 10, "width": 10, "height": 10},
            "purchase_cost": 12.5,
            "purchase_url": "https://detail.1688.com/offer/16880042.html",
        },
        "source": {"purchase_url": "https://detail.1688.com/offer/16880042.html",
                   "purchase_cost": 12.5},
        "extensions": {},
    }
    r = client.post("/api/v1/drafts", json={"token": TOKEN_A, "envelope": envelope})
    assert r.status_code in (200, 201), r.text
    draft_id = r.json()["id"]

    r = client.post(f"/api/v1/drafts/{draft_id}/submit",
                    json={"token": TOKEN_A, "credential_id": str(cred_id)})
    assert r.status_code == 200, r.text
    assert captured, "submit 应入队"
    ext = (captured[0].get("envelope") or {}).get("extensions") or {}
    assert ext.get("box_reviewed") is True, "采集箱提交必须注入 box_reviewed 权威标记"

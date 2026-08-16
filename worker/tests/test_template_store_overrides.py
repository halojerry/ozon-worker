"""P1b: 多店铺差异化配置测试（模板 store_overrides 覆盖注入）。

验收门（docs/PRD-listing-template-v0.44.md §二 + P1b 扩展）：
1. create/update 支持 store_overrides 校验（非法 config 拒绝）
2. apply_template_to_envelope：credential_id 有覆盖 → 覆盖值优先于全局 config
3. 无覆盖的店铺 → 用全局 config
4. submit_draft 传 credential_id → 覆盖生效
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import template_service

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
TENANT = "tenant-A"


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过 store_overrides 测试")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM listing_templates WHERE tenant_id=:t"), {"t": TENANT})
    eng.dispose()


def _create(overrides=None, config=None):
    return template_service.create_template(TENANT, {
        "name": "店铺差异化",
        "config": config or {"margin_rate": 0.25},
        "store_overrides": overrides or {},
    })


# ============================================================
# 1. create/update 校验
# ============================================================

def test_create_with_store_overrides(_pg):
    tpl = _create(overrides={"cred-1": {"margin_rate": 0.4, "stock": 50}})
    assert tpl["store_overrides"]["cred-1"]["margin_rate"] == 0.4
    got = template_service.get_template(TENANT, tpl["id"])
    assert got["store_overrides"]["cred-1"]["stock"] == 50


def test_store_overrides_bad_config_422(_pg):
    with pytest.raises(HTTPException) as ei:
        _create(overrides={"cred-1": {"hack": 1}})
    assert ei.value.status_code == 422
    assert "非法字段" in ei.value.detail


def test_update_store_overrides(_pg):
    tpl = _create()
    updated = template_service.update_template(TENANT, tpl["id"], {
        "store_overrides": {"cred-2": {"margin_rate": 0.35}},
    })
    assert updated["store_overrides"]["cred-2"]["margin_rate"] == 0.35


# ============================================================
# 2. apply 注入语义
# ============================================================

def test_apply_store_override_wins():
    tpl = {
        "config": {"margin_rate": 0.25, "fx_buffer": 0.05},
        "store_overrides": {"cred-1": {"margin_rate": 0.4}},
    }
    env = {"draft": {}, "extensions": {}}
    # cred-1 有覆盖 → margin 用 0.4；fx 用全局
    out = template_service.apply_template_to_envelope(env, tpl, credential_id="cred-1")
    assert out["extensions"]["margin_rate"] == 0.4
    assert out["extensions"]["fx_buffer"] == 0.05
    # cred-2 无覆盖 → 全局 0.25
    out2 = template_service.apply_template_to_envelope(env, tpl, credential_id="cred-2")
    assert out2["extensions"]["margin_rate"] == 0.25


def test_apply_no_credential_id_uses_global():
    tpl = {
        "config": {"margin_rate": 0.25},
        "store_overrides": {"cred-1": {"margin_rate": 0.4}},
    }
    env = {"draft": {}, "extensions": {}}
    out = template_service.apply_template_to_envelope(env, tpl, credential_id=None)
    assert out["extensions"]["margin_rate"] == 0.25


def test_apply_store_override_draft_still_wins():
    """草稿显式值优先——即使店铺有覆盖也不覆盖草稿已有值。"""
    tpl = {
        "config": {"margin_rate": 0.25},
        "store_overrides": {"cred-1": {"margin_rate": 0.4}},
    }
    env = {"draft": {}, "extensions": {"margin_rate": 0.5}}
    out = template_service.apply_template_to_envelope(env, tpl, credential_id="cred-1")
    assert out["extensions"]["margin_rate"] == 0.5


# ============================================================
# 3. 更新模式忽略 prefix（含 store override 的 prefix）
# ============================================================

def test_apply_store_override_prefix_ignored_on_update():
    tpl = {
        "config": {"margin_rate": 0.25},
        "store_overrides": {"cred-1": {"offer_id_prefix": "W1"}},
    }
    env = {"draft": {}, "extensions": {}}
    out = template_service.apply_template_to_envelope(env, tpl, credential_id="cred-1", is_update=True)
    assert "offer_id_prefix" not in out["extensions"]
    out2 = template_service.apply_template_to_envelope(env, tpl, credential_id="cred-1", is_update=False)
    assert out2["extensions"]["offer_id_prefix"] == "W1"

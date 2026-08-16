"""P0-1: 上架配置模板 service 层测试（listing_templates 表 CRUD + 注入语义）。

验收门（docs/PRD-listing-template-v0.44.md §五）：
1. CRUD + 租户隔离（A 看不到 B）
2. 设默认清旧默认（每租户最多一个 is_default）
3. config 白名单拒绝非法 key + 数值边界校验
4. apply_template_to_envelope 注入语义：草稿已有值优先 / 模板补缺省
5. offer_id_prefix 仅新建模式生效（is_update 忽略）

需要本地 Docker PG；PG 不可达时 skip。
"""
import json
import os
import sys
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

TENANTS = ("tenant-a", "tenant-b")


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过模板 service 测试")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM listing_templates WHERE tenant_id IN ('tenant-a','tenant-b')"
        ))
    eng.dispose()


def _create(tenant: str, name: str = "默认高利润", **kw) -> dict:
    """创建模板：顶层 kw（name/description/is_default）原样传；其余并入 config。"""
    config = dict(kw.pop("config", {}) or {})
    for k in list(kw):
        if k in template_service.CONFIG_KEYS:
            config[k] = kw.pop(k)
    payload = {"name": name, "config": config}
    payload.update(kw)
    return template_service.create_template(tenant, payload)


# ============================================================
# 1. CRUD + 租户隔离
# ============================================================

def test_create_and_get(_pg):
    tpl = _create("tenant-a", margin_rate=0.35, offer_id_prefix="W1")
    assert tpl["name"] == "默认高利润"
    assert tpl["config"]["margin_rate"] == 0.35
    assert tpl["config"]["offer_id_prefix"] == "W1"
    assert tpl["is_default"] is False

    got = template_service.get_template("tenant-a", tpl["id"])
    assert got["id"] == tpl["id"]


def test_tenant_isolation(_pg):
    tpl_a = _create("tenant-a", name="A的模板")
    _create("tenant-b", name="B的模板")
    list_a = template_service.list_templates("tenant-a")
    assert len(list_a) == 1
    assert list_a[0]["id"] == tpl_a["id"]
    # B 读 A 的模板 → 404
    with pytest.raises(HTTPException) as ei:
        template_service.get_template("tenant-b", tpl_a["id"])
    assert ei.value.status_code == 404


def test_update_partial(_pg):
    tpl = _create("tenant-a", margin_rate=0.25)
    updated = template_service.update_template(
        "tenant-a", tpl["id"], {"name": "改名", "config": {"margin_rate": 0.4}})
    assert updated["name"] == "改名"
    assert updated["config"]["margin_rate"] == 0.4


def test_delete(_pg):
    tpl = _create("tenant-a")
    template_service.delete_template("tenant-a", tpl["id"])
    with pytest.raises(HTTPException) as ei:
        template_service.get_template("tenant-a", tpl["id"])
    assert ei.value.status_code == 404
    # 跨租户删 → 404
    with pytest.raises(HTTPException):
        template_service.delete_template("tenant-b", tpl["id"])


# ============================================================
# 2. 默认模板：设默认清旧默认
# ============================================================

def test_set_default_clears_old(_pg):
    t1 = _create("tenant-a", name="t1")
    t2 = _create("tenant-a", name="t2")
    template_service.set_default("tenant-a", t1["id"])
    got = template_service.get_default_template("tenant-a")
    assert got["id"] == t1["id"]
    template_service.set_default("tenant-a", t2["id"])
    got = template_service.get_default_template("tenant-a")
    assert got["id"] == t2["id"]
    assert template_service.get_template("tenant-a", t1["id"])["is_default"] is False


def test_create_with_default_clears_old(_pg):
    t1 = _create("tenant-a", name="old")
    template_service.set_default("tenant-a", t1["id"])
    t2 = _create("tenant-a", name="new", is_default=True)
    assert template_service.get_template("tenant-a", t1["id"])["is_default"] is False
    assert template_service.get_template("tenant-a", t2["id"])["is_default"] is True


def test_default_tenant_scoped(_pg):
    template_service.set_default("tenant-a", _create("tenant-a", name="A")["id"])
    _create("tenant-b", name="B", is_default=True)
    got_b = template_service.get_default_template("tenant-b")
    assert got_b["name"] == "B"


# ============================================================
# 3. config 白名单 + 数值校验
# ============================================================

def test_config_unknown_key_rejected(_pg):
    with pytest.raises(HTTPException) as ei:
        _create("tenant-a", config={"hack_field": 1})
    assert ei.value.status_code == 422
    assert "非法字段" in ei.value.detail


def test_config_numeric_bounds(_pg):
    for key, bad in (("margin_rate", 1.5), ("commission_rate", -0.1), ("fx_buffer", 0.9)):
        with pytest.raises(HTTPException) as ei:
            _create("tenant-a", config={key: bad})
        assert ei.value.status_code == 422


def test_config_stock_and_follow_type(_pg):
    tpl = _create("tenant-a", config={
        "stock": 100, "follow_type": "hand", "warehouse_id": "wh-1"})
    assert tpl["config"]["stock"] == 100
    assert tpl["config"]["follow_type"] == "hand"
    with pytest.raises(HTTPException):
        _create("tenant-a", config={"follow_type": "evil"})
    with pytest.raises(HTTPException):
        _create("tenant-a", config={"stock": -5})


def test_empty_name_rejected(_pg):
    with pytest.raises(HTTPException) as ei:
        _create("tenant-a", name="   ")
    assert ei.value.status_code == 422


# ============================================================
# 4. apply_template_to_envelope 注入语义
# ============================================================

def test_apply_injects_missing_fields():
    tpl = {"config": {"margin_rate": 0.35, "fx_buffer": 0.1, "stock": 50}}
    env = {"draft": {}, "extensions": {"commission_rate": 0.12}}
    out = template_service.apply_template_to_envelope(env, tpl)
    # 模板补缺省：margin/fx/stock 注入；commission 草稿已有 → 不覆盖
    assert out["extensions"]["margin_rate"] == 0.35
    assert out["extensions"]["fx_buffer"] == 0.1
    assert out["extensions"]["stock"] == 50
    assert out["extensions"]["commission_rate"] == 0.12
    # 入参不被修改
    assert "margin_rate" not in env["extensions"]


def test_apply_keeps_draft_values():
    tpl = {"config": {"margin_rate": 0.35, "stock": 50}}
    env = {"draft": {}, "extensions": {"margin_rate": 0.5, "stock": 999}}
    out = template_service.apply_template_to_envelope(env, tpl)
    assert out["extensions"]["margin_rate"] == 0.5
    assert out["extensions"]["stock"] == 999


def test_apply_prefix_only_when_not_update():
    tpl = {"config": {"offer_id_prefix": "W1"}}
    env = {"draft": {}, "extensions": {}}
    # 新建 → 注入 prefix
    out = template_service.apply_template_to_envelope(env, tpl, is_update=False)
    assert out["extensions"]["offer_id_prefix"] == "W1"
    # 更新 → 忽略 prefix（重上不变式）
    out2 = template_service.apply_template_to_envelope(env, tpl, is_update=True)
    assert "offer_id_prefix" not in out2["extensions"]


def test_apply_empty_config_returns_copy():
    tpl = {"config": {}}
    env = {"draft": {}, "extensions": {"stock": 1}}
    out = template_service.apply_template_to_envelope(env, tpl)
    assert out == env
    assert out is not env


def test_apply_no_extensions_key():
    tpl = {"config": {"stock": 10}}
    env = {"draft": {"title": "x"}}
    out = template_service.apply_template_to_envelope(env, tpl)
    assert out["extensions"]["stock"] == 10

"""采集箱草稿 create 字段弱校验 + submit 真防线守卫 — 契约测试。

问题 ①（worker create 无校验）：
    draft_service.create_draft 只校验 envelope.draft 非空 + 禁 api_key，无字段级必填。
    修复：新增 _validate_draft_fields 弱校验——title 缺失 → 400（严重、无法修复）；
    weight/dimensions 全零且无竞品兜底 → logger.warning（不阻断，真防线在 submit）。
    合法残缺草稿（weight=0+竞品兜底 / dimensions 全 0+竞品兜底）不得被 create 卡死。

问题 ②（skill 降级兜底 dimensions=0 入箱后会被 submit 拒）：
    validate_draft_sanity 对全 0 尺寸且无 competitor_dimensions_mm → 拒；
    有竞品兜底 → 放行。本测试锁定 sanity 守卫不变（真防线仍在 submit）。

运行（需本地 Docker PG 5433，纯函数用例无需 PG）：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        ../skill/.venv314/bin/python -m pytest tests/test_drafts_field_validation.py -q
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from fastapi import HTTPException


# ──────────────────────────────────────────────
# 纯函数：_validate_draft_fields（无需 PG）
# ──────────────────────────────────────────────

def test_validate_fields_accepts_normal_draft():
    """正常草稿（title+weight+dimensions 齐全）→ 不抛错。"""
    from services.draft_service import _validate_draft_fields
    _validate_draft_fields({
        "draft": {
            "title": "宠物自动饮水器",
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        "extensions": {},
    })  # 不抛 → PASS


def test_validate_fields_rejects_missing_title():
    """无 title 的 draft → 400（严重残缺，尽早暴露）。"""
    from services.draft_service import _validate_draft_fields
    with pytest.raises(HTTPException) as e:
        _validate_draft_fields({
            "draft": {
                "weight": 500,
                "dimensions": {"length": 100, "width": 50, "height": 30},
            },
            "extensions": {},
        })
    assert e.value.status_code == 400
    assert "标题" in e.value.detail or "title" in e.value.detail


def test_validate_fields_allows_zero_dims_with_competitor():
    """dimensions 全 0 + 竞品兜底 → 不抛错（合法残缺放行，submit 真防线用竞品兜底）。"""
    from services.draft_service import _validate_draft_fields
    _validate_draft_fields({
        "draft": {
            "title": "宠物自动饮水器",
            "weight": 500,
            "dimensions": {"length": 0, "width": 0, "height": 0},
        },
        "extensions": {
            "competitor_dimensions_mm": {"length": 120, "width": 80, "height": 60},
        },
    })  # 不抛 → PASS（不破坏 weight=0+竞品数据放行语义）


def test_validate_fields_allows_zero_weight_with_competitor():
    """weight=0 + 竞品重量 → 不抛错（合法残缺放行）。"""
    from services.draft_service import _validate_draft_fields
    _validate_draft_fields({
        "draft": {
            "title": "宠物自动饮水器",
            "weight": 0,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        "extensions": {"competitor_weight_g": 300},
    })  # 不抛 → PASS


def test_validate_fields_allows_zero_dims_no_competitor():
    """dimensions 全 0 无竞品兜底 → 不抛错（弱校验只 warning，真防线在 submit）。"""
    from services.draft_service import _validate_draft_fields
    _validate_draft_fields({
        "draft": {
            "title": "宠物自动饮水器",
            "weight": 300,
            "dimensions": {"length": 0, "width": 0, "height": 0},
        },
        "extensions": {},
    })  # 不抛 → PASS（create 不硬拒）


def test_validate_fields_rejects_non_dict_draft():
    """draft 不是对象 → 400。"""
    from services.draft_service import _validate_draft_fields
    with pytest.raises(HTTPException) as e:
        _validate_draft_fields({"draft": "not-a-dict", "extensions": {}})
    assert e.value.status_code == 400


# ──────────────────────────────────────────────
# 真防线：validate_draft_sanity 守卫不变（submit 层）
# ──────────────────────────────────────────────

def test_sanity_still_guards_submit():
    """submit 真防线：全 0 尺寸无竞品兜底 → 拒；有竞品兜底 → 放行。"""
    from utils.draft_sanity import validate_draft_sanity

    # 全 0 尺寸 + 无竞品兜底 → 拒
    err = validate_draft_sanity(
        {"weight": 500, "dimensions": {"length": 0, "width": 0, "height": 0}},
        {},
    )
    assert err is not None
    assert "dimensions" in err

    # 全 0 尺寸 + 竞品尺寸兜底 → 放行（None）
    err = validate_draft_sanity(
        {"weight": 500, "dimensions": {"length": 0, "width": 0, "height": 0}},
        {"competitor_dimensions_mm": {"length": 120, "width": 80, "height": 60}},
    )
    assert err is None

    # weight=0 + 竞品重量兜底 → 放行（不破坏既有语义）
    err = validate_draft_sanity(
        {"weight": 0, "dimensions": {"length": 100, "width": 50, "height": 30}},
        {"competitor_weight_g": 300},
    )
    assert err is None


# ──────────────────────────────────────────────
# create_draft 端到端（需本地 PG，无 PG 跳过）
# ──────────────────────────────────────────────

def _make_envelope(*, title="宠物自动饮水器", weight=227, dims=None,
                   competitor_dims=None, competitor_weight=None) -> dict:
    draft = {
        "item_id": "980815374096",
        "title": title,
        "images": ["https://cbu01.alicdn.com/x.jpg"],
        "weight": weight,
        "dimensions": dims if dims is not None else {"length": 120, "width": 80, "height": 60},
        "purchase_cost": 5.5,
        "purchase_url": "https://detail.1688.com/offer/980815374096.html",
    }
    extensions = {"margin_rate": 0.25, "commission_rate": 0.10}
    if competitor_dims is not None:
        extensions["competitor_dimensions_mm"] = competitor_dims
    if competitor_weight is not None:
        extensions["competitor_weight_g"] = competitor_weight
    return {"draft": draft, "source": {}, "extensions": extensions}


def _valid_body(envelope: dict) -> dict:
    return {
        "token": "sk-oldest",
        "ozon_client_id": "4718259",
        "ozon_api_key": "sk-api-key-AAA",
        "envelope": envelope,
    }


@pytest.fixture(scope="module")
def pg():
    engine = None
    try:
        from sqlalchemy import create_engine, text
        db_url = os.environ.get(
            "PGDATABASE_URL",
            "postgresql://postgres:localdev123@localhost:5433/ozon",
        )
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过 create_draft 端到端用例")
    yield engine
    if engine is not None:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_drafts(pg):
    if pg is None:
        yield
        return
    from sqlalchemy import text
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM draft_submissions"))
        conn.execute(text("DELETE FROM product_drafts"))
        conn.execute(text("DELETE FROM credentials"))
    yield


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("PGDATABASE_URL",
        os.environ.get("PGDATABASE_URL",
            "postgresql://postgres:localdev123@localhost:5433/ozon"))
    yield


def test_create_rejects_missing_title(pg):
    """无 title 的 envelope.draft → create 400。"""
    from services import draft_service
    env = _make_envelope(title="")
    with pytest.raises(HTTPException) as e:
        draft_service.create_draft("tenant-A", _valid_body(env))
    assert e.value.status_code == 400


def test_create_allows_zero_dims(pg):
    """dimension 全 0 无竞品兜底 → create 不硬拒（真防线在 submit）。"""
    from services import draft_service
    body = _valid_body(_make_envelope(dims={"length": 0, "width": 0, "height": 0}))
    row = draft_service.create_draft("tenant-A", body)
    payload = row["payload"]
    assert payload["draft"]["dimensions"] == {"length": 0, "width": 0, "height": 0}
    assert row["version"] == 1


def test_create_allows_zero_dims_with_competitor(pg):
    """dimension 全 0 + 竞品兜底 → create 成功且竞品数据保留（submit 真防线用兜底）。"""
    from services import draft_service
    env = _make_envelope(
        dims={"length": 0, "width": 0, "height": 0},
        competitor_dims={"length": 120, "width": 80, "height": 60},
    )
    row = draft_service.create_draft("tenant-A", _valid_body(env))
    payload = row["payload"]  # payload 即 envelope 本体
    assert payload["draft"]["dimensions"] == {"length": 0, "width": 0, "height": 0}
    assert payload["extensions"]["competitor_dimensions_mm"] == {
        "length": 120, "width": 80, "height": 60,
    }

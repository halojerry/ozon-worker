#!/usr/bin/env python3
"""F-D01/F-D02 草稿乐观锁守卫单测（P0 波修 2，2026-09-09 审计发现）。

审计发现（findings.md，高/中）：
  F-D01 patch_draft 的 version 检查（SELECT）与 UPDATE 分离且 UPDATE 无 version
  谓词 → 并发双 PATCH 双双成功、后写覆盖先写，409 形同虚设；
  F-D02 assemble_draft 的 LLM 长耗时窗口内用户 PATCH → assemble 整包回写覆盖
  该窗口内的全部编辑。

修复契约（本文件锁定）：
  1. patch_draft UPDATE 带 `AND version=:expected`，rowcount=0 → 409；
  2. assemble_draft 写回带 `AND version=:base_version`（读时快照），rowcount=0
     → 409「草稿在预组装期间被修改」，用户 PATCH 内容不被覆盖；
  3. 并发同 version 双 PATCH 恰好一胜一 409（确定性，非概率）。

运行（需本地 PG 5433，不可达自动 skip）：
    cd worker && PGDATABASE_URL=... PYTHONPATH=src ../skill/.venv314/bin/python \
        -m pytest tests/test_draft_optimistic_lock.py -q
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)

_TENANT = "f-d01-test"


def _pg_available() -> bool:
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="本地 PG 不可达")

_MADE: list[str] = []


@pytest.fixture()
def engine():
    os.environ.setdefault("CREDENTIAL_MASTER_KEY", "test-key-f-d01")
    eng = create_engine(DB_URL)
    _MADE.clear()
    yield eng
    with eng.connect() as conn:
        for did in _MADE:
            conn.execute(text("DELETE FROM product_drafts WHERE id = :did"),
                         {"did": did})
        conn.commit()


def _mk_draft(eng, payload: dict | None = None) -> str:
    did = str(uuid.uuid4())
    _MADE.append(did)
    with eng.connect() as conn:
        conn.execute(text("""
            INSERT INTO product_drafts (id, tenant_id, payload, source, version)
            VALUES (:did, :tenant, CAST(:payload AS jsonb), 'webui', 1)
        """), {"did": did, "tenant": _TENANT,
               "payload": json.dumps(payload or {"draft": {"title": "base"}})})
        conn.commit()
    return did


def _get_row(eng, did: str):
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT version, payload FROM product_drafts WHERE id = :did"
        ), {"did": did}).fetchone()


def test_concurrent_patch_same_version_exactly_one_wins(engine):
    """核心回归：并发同 version 双 PATCH 恰好一胜（200）一 409——
    旧实现 UPDATE 无 version 谓词，两窗口双双成功后写覆盖先写。"""
    from services.draft_service import patch_draft
    from api.schemas import DraftPatch

    for round_no in range(6):
        did = _mk_draft(engine)
        results: list[str] = []
        barrier = threading.Barrier(2)

        def _do(tag: str) -> None:
            barrier.wait(timeout=5)
            try:
                patch_draft(_TENANT, did, DraftPatch(
                    version=1, payload={"draft": {"title": f"winner-{tag}"}}))
                results.append(tag)
            except HTTPException as exc:
                assert exc.status_code == 409, f"round{round_no}: {exc.status_code}"
                results.append("409")

        t1 = threading.Thread(target=_do, args=("a",))
        t2 = threading.Thread(target=_do, args=("b",))
        t1.start(); t2.start(); t1.join(); t2.join()

        assert sorted(results) == ["409", "a"] or sorted(results) == ["409", "b"], \
            f"round{round_no}: 并发同 version 应恰一胜一 409，got {results}"
        row = _get_row(engine, did)
        assert row[0] == 2, f"round{round_no}: 胜者后 version 应为 2，got {row[0]}"


def test_patch_stale_version_rejected_without_side_effect(engine):
    did = _mk_draft(engine)
    from services.draft_service import patch_draft
    from api.schemas import DraftPatch

    with pytest.raises(HTTPException) as exc:
        patch_draft(_TENANT, did, DraftPatch(version=99, payload={"draft": {}}))
    assert exc.value.status_code == 409
    row = _get_row(engine, did)
    assert row[0] == 1, "stale 409 不得推进 version"
    assert row[1]["draft"]["title"] == "base"


def test_assemble_writeback_guarded_against_concurrent_patch(engine, monkeypatch):
    """F-D02：assemble 的 LLM 窗口内发生 PATCH → 写回必须 409 拒绝，
    不得用旧 base payload 整包覆盖用户的编辑。"""
    import services.draft_service as ds

    did = _mk_draft(engine)

    def fake_assemble(payload, token):
        # LLM 窗口内：用户并发 PATCH（version 1→2，改 title）
        from api.schemas import DraftPatch
        ds.patch_draft(_TENANT, did, DraftPatch(
            version=1, payload={"draft": {"title": "user-edit-during-llm"}}))
        payload["draft"]["title"] = "assembled"
        payload["suggested_category"] = None
        return payload

    monkeypatch.setattr(
        "services.ai_field_service.assemble_draft", fake_assemble)

    with pytest.raises(HTTPException) as exc:
        ds.assemble_draft(_TENANT, did, "sk-test-token")
    assert exc.value.status_code == 409

    row = _get_row(engine, did)
    assert row[0] == 2
    assert row[1]["draft"]["title"] == "user-edit-during-llm", \
        "assemble 回写不得覆盖窗口内的用户 PATCH"


def test_assemble_writeback_succeeds_when_untouched(engine, monkeypatch):
    """无并发时 assemble 正常写回 version++（正向路径不回归）。"""
    import services.draft_service as ds

    did = _mk_draft(engine)

    def fake_assemble(payload, token):
        # ai_field_service.assemble_draft 契约：就地修改传入 payload 并返回
        payload["draft"]["title"] = "assembled-title"
        return payload

    monkeypatch.setattr(
        "services.ai_field_service.assemble_draft", fake_assemble)

    out = ds.assemble_draft(_TENANT, did, "sk-test-token")
    assert out["version"] == 2
    row = _get_row(engine, did)
    assert row[1]["draft"]["title"] == "assembled-title"

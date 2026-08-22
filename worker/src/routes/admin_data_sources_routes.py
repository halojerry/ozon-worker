"""Batch4 T4.2: /admin/data-sources CRUD + CSV import."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Optional
from sqlalchemy import text

from storage.database.db import get_engine
from services import admin_service

router = APIRouter(prefix="/api/v1/admin/data-sources", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


class DataSourceCreateIn(BaseModel):
    name: str
    type: str
    config: Optional[Any] = None
    enabled: Optional[bool] = True


class DataSourcePatchIn(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    config: Optional[Any] = None
    enabled: Optional[bool] = None


def _row_to_dict(r):
    return {"id": int(r[0]), "name": r[1], "type": r[2], "config": r[3],
            "enabled": bool(r[4]),
            "created_at": r[5].isoformat() if r[5] else None,
            "updated_at": r[6].isoformat() if r[6] else None}


_SELECT_COLS = "id, name, type, config, enabled, created_at, updated_at"


@router.get("")
@router.get("/")
async def list_sources(request: Request, limit: int = 50, offset: int = 0):
    await _authenticate_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM data_sources ORDER BY id LIMIT :limit OFFSET :offset"
        ), {"limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text("SELECT COUNT(*) FROM data_sources")).scalar() or 0
    return {"items": [_row_to_dict(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_source(request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = DataSourceCreateIn.model_validate(body)
    if not data.name.strip() or not data.type.strip():
        raise HTTPException(status_code=400, detail="name and type are required")
    cfg = json.dumps(data.config, ensure_ascii=False) if data.config is not None else None
    eng = get_engine()
    with eng.connect() as conn:
        dup = conn.execute(text("SELECT id FROM data_sources WHERE name=:name"),
                           {"name": data.name}).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail="data source name already exists")
        row = conn.execute(text(
            f"INSERT INTO data_sources (name, type, config, enabled) "
            f"VALUES (:name, :type, CAST(:cfg AS jsonb), :enabled) "
            f"RETURNING {_SELECT_COLS}"
        ), {"name": data.name, "type": data.type, "cfg": cfg,
            "enabled": bool(data.enabled)}).fetchone()
        conn.commit()
    return _row_to_dict(row)


@router.post("/import/csv", status_code=201)
async def import_csv(request: Request):
    await _authenticate_admin(request)
    csv_text = None
    ctype = request.headers.get("content-type", "")
    if "multipart" in ctype:
        form = await request.form()
        file = form.get("file")
        if file is not None and hasattr(file, "read"):
            raw = await file.read()
            csv_text = raw.decode("utf-8", errors="ignore")
        else:
            csv_text = str(form.get("csv_text") or form.get("csv") or "")
    else:
        try:
            body = await request.json()
            csv_text = body.get("csv_text") or body.get("csv") or ""
        except Exception:
            raise HTTPException(status_code=400, detail="csv_text is required")
    if not csv_text or not csv_text.strip():
        raise HTTPException(status_code=400, detail="csv_text is required")
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None or "title" not in reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV must contain 'title' column")
    rows = list(reader)
    eng = get_engine()
    inserted = 0
    with eng.begin() as conn:
        for row in rows:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            dtype = (row.get("type") or "csv").strip() or "csv"
            cfg = {k: v for k, v in row.items() if k != "title"}
            try:
                conn.execute(text(
                    "INSERT INTO data_sources (name, type, config, enabled) "
                    "VALUES (:name, :type, CAST(:cfg AS jsonb), true)"
                ), {"name": title, "type": dtype, "cfg": json.dumps(cfg, ensure_ascii=False)})
                inserted += 1
            except Exception:
                continue
    return {"imported": inserted}


@router.get("/{ds_id}")
async def get_source(ds_id: int, request: Request):
    await _authenticate_admin(request)
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM data_sources WHERE id=:id"
        ), {"id": ds_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="data source not found")
    return _row_to_dict(row)


@router.patch("/{ds_id}")
async def update_source(ds_id: int, request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = DataSourcePatchIn.model_validate(body)
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM data_sources WHERE id=:id"
        ), {"id": ds_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="data source not found")
        name = data.name if data.name is not None else row[1]
        dtype = data.type if data.type is not None else row[2]
        cfg = data.config if data.config is not None else row[3]
        enabled = data.enabled if data.enabled is not None else bool(row[4])
        cfg_json = json.dumps(cfg, ensure_ascii=False) if cfg is not None else None
        if data.name is not None:
            dup = conn.execute(text(
                "SELECT id FROM data_sources WHERE name=:name AND id<>:id"
            ), {"name": name, "id": ds_id}).fetchone()
            if dup:
                raise HTTPException(status_code=409, detail="data source name already exists")
        upd = conn.execute(text(
            f"UPDATE data_sources SET name=:name, type=:type, config=CAST(:cfg AS jsonb), "
            f"enabled=:enabled, updated_at=NOW() WHERE id=:id RETURNING {_SELECT_COLS}"
        ), {"name": name, "type": dtype, "cfg": cfg_json, "enabled": enabled, "id": ds_id}).fetchone()
        conn.commit()
    return _row_to_dict(upd)


@router.delete("/{ds_id}", status_code=204)
async def delete_source(ds_id: int, request: Request):
    await _authenticate_admin(request)
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text("SELECT id FROM data_sources WHERE id=:id"), {"id": ds_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="data source not found")
        conn.execute(text("DELETE FROM data_sources WHERE id=:id"), {"id": ds_id})
        conn.commit()

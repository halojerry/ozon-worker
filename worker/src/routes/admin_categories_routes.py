"""Batch4 T4.1: /admin/categories CRUD — nested tree + admin check."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text

from storage.database.db import get_engine
from services import admin_service

router = APIRouter(prefix="/api/v1/admin/categories", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


class CategoryCreateIn(BaseModel):
    description_category_id: int
    type_id: Optional[int] = None
    name: str
    parent_id: Optional[int] = None
    language: str = "ZH_HANS"


class CategoryPatchIn(BaseModel):
    name: str


def _build_tree(rows):
    # rows: list of tuples from DB
    nodes = {}
    for r in rows:
        nid, dc, tid, name, parent_dc, full_path, top, depth = r
        nodes[dc] = {
            "id": nid,
            "description_category_id": dc,
            "type_id": tid,
            "name": name,
            "parent_id": parent_dc,
            "full_path": full_path,
            "top_level_category_name": top,
            "depth": depth,
            "children": [],
        }
    # Also map by dc for children linking
    roots = []
    for dc, node in nodes.items():
        pid = node["parent_id"]
        if pid is not None and pid in nodes:
            nodes[pid]["children"].append(node)
        else:
            roots.append(node)
    return roots


@router.get("")
@router.get("/")
async def list_categories(request: Request):
    await _authenticate_admin(request)
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, description_category_id, type_id, node_name, "
            "parent_description_category_id, full_path, top_level_category_name, depth "
            "FROM category_tree_nodes ORDER BY depth, description_category_id"
        )).fetchall()
    return _build_tree(rows)


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_category(request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = CategoryCreateIn.model_validate(body)
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    eng = get_engine()
    with eng.connect() as conn:
        # duplicate check: same description_category_id + type_id + language
        existing = conn.execute(text(
            "SELECT id FROM category_tree_nodes WHERE description_category_id=:dc "
            "AND COALESCE(type_id,-1)=COALESCE(:tid,-1) AND language=:lang"
        ), {"dc": data.description_category_id, "tid": data.type_id,
            "lang": data.language}).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="category already exists")
        # resolve parent
        parent_path = None
        parent_depth = -1
        parent_top = None
        if data.parent_id is not None:
            prow = conn.execute(text(
                "SELECT full_path, depth, top_level_category_name FROM category_tree_nodes "
                "WHERE description_category_id=:pid LIMIT 1"
            ), {"pid": data.parent_id}).fetchone()
            if prow:
                parent_path, parent_depth, parent_top = prow
        full_path = f"{parent_path} > {data.name}" if parent_path else data.name
        top = parent_top if parent_top else data.name
        depth = parent_depth + 1
        node_type = "type" if data.type_id is not None else "category"
        row = conn.execute(text(
            "INSERT INTO category_tree_nodes "
            "(description_category_id, type_id, node_name, node_type, "
            "parent_description_category_id, full_path, top_level_category_name, depth, language, disabled) "
            "VALUES (:dc, :tid, :name, :ntype, :pid, :fp, :top, :depth, :lang, false) "
            "RETURNING id"
        ), {"dc": data.description_category_id, "tid": data.type_id, "name": data.name,
            "ntype": node_type, "pid": data.parent_id, "fp": full_path, "top": top, "depth": depth,
            "lang": data.language}).fetchone()
        conn.commit()
        nid = int(row[0]) if row else 0
    return {"id": nid, "description_category_id": data.description_category_id,
            "type_id": data.type_id, "name": data.name, "parent_id": data.parent_id}


@router.patch("/{cat_id}")
async def rename_category(cat_id: int, request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = CategoryPatchIn.model_validate(body)
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text("SELECT id FROM category_tree_nodes WHERE id=:id"), {"id": cat_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="category not found")
        conn.execute(text("UPDATE category_tree_nodes SET node_name=:name WHERE id=:id"),
                     {"name": data.name, "id": cat_id})
        conn.commit()
        updated = conn.execute(text(
            "SELECT id, description_category_id, type_id, node_name, parent_description_category_id "
            "FROM category_tree_nodes WHERE id=:id"), {"id": cat_id}).fetchone()
    return {"id": int(updated[0]), "description_category_id": int(updated[1]),
            "type_id": updated[2], "name": updated[3], "parent_id": updated[4]}


@router.delete("/{cat_id}", status_code=204)
async def delete_category(cat_id: int, request: Request):
    await _authenticate_admin(request)
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT description_category_id FROM category_tree_nodes WHERE id=:id"), {"id": cat_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="category not found")
        dc = int(row[0])
        child = conn.execute(text(
            "SELECT id FROM category_tree_nodes WHERE parent_description_category_id=:dc LIMIT 1"),
            {"dc": dc}).fetchone()
        if child:
            raise HTTPException(status_code=409, detail="category has children, cannot delete")
        conn.execute(text("DELETE FROM category_tree_nodes WHERE id=:id"), {"id": cat_id})
        conn.commit()

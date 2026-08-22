"""Batch4 T4.3: /admin/audit-logs — paginated + create + helper."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional, Any
from sqlalchemy import text

from storage.database.db import get_engine
from services import admin_service

router = APIRouter(prefix="/api/v1/admin/audit-logs", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


class AuditCreateIn(BaseModel):
    action: str
    resource: Optional[str] = None
    detail: Optional[Any] = None


def create_audit_log(db_or_engine, user_id: str, action: str, resource: Optional[str], detail: Optional[Any]):
    """Helper for other endpoints to log. Non-fatal: exceptions are swallowed."""
    try:
        import json as _json
        detail_json = _json.dumps(detail, ensure_ascii=False) if detail is not None else None
        eng = get_engine()
        # db_or_engine may be engine or connection; just use get_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_logs (user_id, action, resource, detail) "
                "VALUES (:uid, :action, :resource, CAST(:detail AS jsonb))"
            ), {"uid": user_id, "action": action, "resource": resource, "detail": detail_json})
    except Exception:
        pass


def _row_to_dict(r):
    return {"id": int(r[0]), "user_id": r[1], "action": r[2], "resource": r[3],
            "detail": r[4], "created_at": r[5].isoformat() if r[5] else None}


@router.get("")
@router.get("/")
async def list_logs(request: Request, limit: int = 50, offset: int = 0):
    user_id = await _authenticate_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, user_id, action, resource, detail, created_at FROM audit_logs "
            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        ), {"limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar() or 0
    items = [_row_to_dict(r) for r in rows]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_log(request: Request):
    user_id = await _authenticate_admin(request)
    body = await request.json()
    data = AuditCreateIn.model_validate(body)
    import json as _json
    detail_json = _json.dumps(data.detail, ensure_ascii=False) if data.detail is not None else None
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "INSERT INTO audit_logs (user_id, action, resource, detail) "
            "VALUES (:uid, :action, :resource, CAST(:detail AS jsonb)) "
            "RETURNING id, user_id, action, resource, detail, created_at"
        ), {"uid": user_id, "action": data.action, "resource": data.resource, "detail": detail_json}).fetchone()
        conn.commit()
    return _row_to_dict(row)

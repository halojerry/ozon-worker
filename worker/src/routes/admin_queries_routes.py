"""C3: 蓝海关键词库管理路由 — 管理员导入/浏览/删除 blue_ocean_queries。

端点（挂载在 /api/v1 下，main.py v1.include_router 注册）：
    GET    /admin/queries/        关键词库浏览（limit/offset/search）
    POST   /admin/queries/import  导入（body 二选一：csv 文本 / items 数组）
    DELETE /admin/queries/{id}    删除关键词行

鉴权：_authenticate_admin 与 admin_routes 同款（Bearer token → require_admin）。
Pydantic 模型模块内私有，不污染共享 schemas.py。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services import admin_service, queries_service

router = APIRouter(prefix="/admin/queries", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


# ──────────────────────────────────────────────
# 模块内 Pydantic（不入共享 schemas.py）
# ──────────────────────────────────────────────


class QueryRow(BaseModel):
    """关键词行（库浏览返回项）。"""

    id: int
    query: str
    count: int = 0
    ca: Optional[float] = None
    avg_ca_rub: Optional[float] = None
    avg_count_items: Optional[float] = None
    items_views: Optional[float] = None
    uniq_queries_wca: Optional[int] = None
    uniq_sellers: Optional[float] = None
    source: str = "fetched"
    created_at: Optional[str] = None


class QueryListOut(BaseModel):
    """库浏览响应。"""

    total: int = 0
    items: list[QueryRow] = Field(default_factory=list)


class QueryImportIn(BaseModel):
    """导入请求体：csv 文本与 items 数组二选一。"""

    items: Optional[list[dict[str, Any]]] = None
    csv: Optional[str] = None


class QueryImportResult(BaseModel):
    """导入结果：新增/更新计数 + 逐行错误。"""

    imported: int = 0
    updated: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)


class QueryDeleteOut(BaseModel):
    """删除结果。"""

    ok: bool = True
    deleted: bool = True


# ──────────────────────────────────────────────
# 端点
# ──────────────────────────────────────────────


@router.get("", response_model=QueryListOut)
@router.get("/", response_model=QueryListOut)
async def list_queries(request: Request, limit: int = 50, offset: int = 0, search: str = ""):
    await _authenticate_admin(request)
    return queries_service.list_queries(limit=limit, offset=offset, search=search)


@router.post("/import", response_model=QueryImportResult)
async def import_queries(body: QueryImportIn, request: Request):
    await _authenticate_admin(request)
    if body.csv is not None and body.csv.strip():
        return queries_service.import_queries_csv(body.csv)
    if body.items:
        return queries_service.import_queries(body.items)
    raise HTTPException(status_code=400, detail="请求体需提供 csv 或 items 之一")


@router.delete("/{query_id}", response_model=QueryDeleteOut)
async def delete_query(query_id: int, request: Request):
    await _authenticate_admin(request)
    if not queries_service.delete_query(query_id):
        raise HTTPException(status_code=404, detail=f"关键词 {query_id} 不存在")
    return QueryDeleteOut(ok=True, deleted=True)

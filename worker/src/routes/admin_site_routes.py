"""v0.55: 站点运营路由（系统设置-站点运营）— 鉴权（管理员）+ 调 site_service，无业务逻辑。

端点（挂载在 /api/v1 下，main.py v1.include_router）：
    GET    /admin/site/banners              Banner 列表（管理端全量）
    POST   /admin/site/banners              创建 Banner（201）
    PUT    /admin/site/banners/{id}         更新 Banner（404 缺失）
    DELETE /admin/site/banners/{id}         删除 Banner（204）
    GET    /admin/site/announcements        通告列表（管理端全量）
    POST   /admin/site/announcements        创建通告（201，announcement_type 非法 400）
    PUT    /admin/site/announcements/{id}   更新通告（404 缺失）
    DELETE /admin/site/announcements/{id}   删除通告（204）

鉴权：_authenticate_token 拿 user_id → require_admin（非管理员 403；本地 local_dev 放行）。
Pydantic 模型模块内定义（不入 schemas.py，站点运营字段独立演进）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import admin_service, site_service

router = APIRouter(prefix="/admin/site", tags=["admin"])


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


# ──────────────────────────────────────────────
# 模块内 Pydantic 模型
# ──────────────────────────────────────────────


class SiteBannerIn(BaseModel):
    image_url: str
    link_url: Optional[str] = None
    title: str = ""
    sort_order: int = 0
    enabled: bool = True


class SiteBannerOut(SiteBannerIn):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SiteAnnouncementIn(BaseModel):
    title: str = ""
    content: str
    announcement_type: str = "banner"
    enabled: bool = True


class SiteAnnouncementOut(SiteAnnouncementIn):
    id: int
    created_at: Optional[datetime] = None


# ──────────────────────────────────────────────
# Banner 端点
# ──────────────────────────────────────────────


@router.get("/banners", response_model=list[SiteBannerOut])
async def admin_site_banners(request: Request):
    await _authenticate_admin(request)
    return site_service.list_banners()


@router.post("/banners", response_model=SiteBannerOut, status_code=201)
async def admin_site_create_banner(request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = SiteBannerIn.model_validate(body)
    return site_service.create_banner(data.model_dump())


@router.put("/banners/{banner_id}", response_model=SiteBannerOut)
async def admin_site_update_banner(banner_id: int, request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = SiteBannerIn.model_validate(body)
    updated = site_service.update_banner(banner_id, data.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Banner 不存在")
    return updated


@router.delete("/banners/{banner_id}", status_code=204)
async def admin_site_delete_banner(banner_id: int, request: Request):
    await _authenticate_admin(request)
    if not site_service.delete_banner(banner_id):
        raise HTTPException(status_code=404, detail="Banner 不存在")


# ──────────────────────────────────────────────
# 通告端点
# ──────────────────────────────────────────────


@router.get("/announcements", response_model=list[SiteAnnouncementOut])
async def admin_site_announcements(request: Request):
    await _authenticate_admin(request)
    return site_service.list_announcements()


@router.post("/announcements", response_model=SiteAnnouncementOut, status_code=201)
async def admin_site_create_announcement(request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = SiteAnnouncementIn.model_validate(body)
    try:
        return site_service.create_announcement(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/announcements/{announcement_id}", response_model=SiteAnnouncementOut)
async def admin_site_update_announcement(announcement_id: int, request: Request):
    await _authenticate_admin(request)
    body = await request.json()
    data = SiteAnnouncementIn.model_validate(body)
    try:
        updated = site_service.update_announcement(announcement_id, data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="通告不存在")
    return updated


@router.delete("/announcements/{announcement_id}", status_code=204)
async def admin_site_delete_announcement(announcement_id: int, request: Request):
    await _authenticate_admin(request)
    if not site_service.delete_announcement(announcement_id):
        raise HTTPException(status_code=404, detail="通告不存在")

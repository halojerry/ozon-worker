"""v0.55: 站点公开只读端点 — Banner/通告 enabled-only，无需鉴权。

端点（挂载在 /api/v1 下）：
    GET /site/banners         公开 Banner（enabled=true，sort_order 升序）
    GET /site/announcements   公开通告（enabled=true）
"""
from fastapi import APIRouter

from services import site_service

router = APIRouter(prefix="/site", tags=["site"])


@router.get("/banners")
async def site_public_banners():
    return site_service.list_public_banners()


@router.get("/announcements")
async def site_public_announcements():
    return site_service.list_public_announcements()

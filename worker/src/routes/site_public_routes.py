"""v0.55: 站点公开只读端点 — Banner/通告 enabled-only，无需鉴权。

端点（挂载在 /api/v1 下）：
    GET /site/banners         公开 Banner（enabled=true，sort_order 升序）
    GET /site/announcements   公开通告（enabled=true）
"""
from fastapi import APIRouter

from services import site_service

router = APIRouter(prefix="/site", tags=["site"])

# 响应示例（openapi_extra 路由级补；结构 = site_service._to_banner_dict /
# _to_announcement_dict，公开面只回 enabled=true 行）
_BANNERS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": [{
    "id": 1,
    "image_url": "https://worker.mxou.cn/static/banner-autumn.png",
    "link_url": "https://worker.mxou.cn/app/discover",
    "title": "秋季选品季", "sort_order": 1, "enabled": True,
    "created_at": "2026-09-01T00:00:00", "updated_at": "2026-09-01T00:00:00",
}]}}}}}
_ANNOUNCEMENTS_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": [{
    "id": 1, "title": "v0.75 上架成功率优化",
    "content": "错配拦截与类目桥接上线，建议升级 skill 后重试失败草稿。",
    "announcement_type": "banner", "enabled": True,
    "created_at": "2026-09-01T00:00:00",
}]}}}}}


@router.get("/banners", openapi_extra=_BANNERS_OK_EXTRA)
async def site_public_banners():
    return site_service.list_public_banners()


@router.get("/announcements", openapi_extra=_ANNOUNCEMENTS_OK_EXTRA)
async def site_public_announcements():
    return site_service.list_public_announcements()

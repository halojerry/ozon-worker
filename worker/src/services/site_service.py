"""站点运营服务（v0.55，系统设置-站点运营）：站点 Banner/通告 CRUD + 公开只读。

职责：管理端增删改查（仅管理员调用，鉴权在路由层）+ 公开只读列表。
用 SQLAlchemy 2.0 ORM Session（get_engine()）操作 storage.database.shared.model 的
SiteBanner/SiteAnnouncement；announcement_type 非法抛 ValueError（路由层转 400）。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.database.db import get_engine
from storage.database.shared.model import SiteAnnouncement, SiteBanner

logger = logging.getLogger(__name__)

ANNOUNCEMENT_TYPES = ("banner", "popup")


# ──────────────────────────────────────────────
# Banner
# ──────────────────────────────────────────────


def _to_banner_dict(row) -> dict:
    return {
        "id": int(row.id),
        "image_url": row.image_url,
        "link_url": row.link_url,
        "title": row.title,
        "sort_order": row.sort_order,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_banners() -> list[dict]:
    """全部 Banner（管理端）：sort_order 升序，同序按 id 升序。"""
    with Session(get_engine()) as session:
        rows = session.scalars(
            select(SiteBanner).order_by(SiteBanner.sort_order.asc(), SiteBanner.id.asc())
        ).all()
        return [_to_banner_dict(r) for r in rows]


def create_banner(data: dict) -> dict:
    """创建 Banner。"""
    with Session(get_engine()) as session:
        row = SiteBanner(
            image_url=str(data.get("image_url") or ""),
            link_url=data.get("link_url"),
            title=str(data.get("title") or ""),
            sort_order=int(data.get("sort_order") or 0),
            enabled=bool(data.get("enabled", True)),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_banner_dict(row)


def update_banner(banner_id: int, data: dict) -> dict | None:
    """更新 Banner；不存在 → None（路由层转 404）。"""
    with Session(get_engine()) as session:
        row = session.get(SiteBanner, banner_id)
        if row is None:
            return None
        if "image_url" in data:
            row.image_url = str(data["image_url"] or "")
        if "link_url" in data:
            row.link_url = data.get("link_url")
        if "title" in data:
            row.title = str(data["title"] or "")
        if "sort_order" in data:
            row.sort_order = int(data["sort_order"] or 0)
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        session.commit()
        session.refresh(row)
        return _to_banner_dict(row)


def delete_banner(banner_id: int) -> bool:
    """删除 Banner；不存在 → False。"""
    with Session(get_engine()) as session:
        row = session.get(SiteBanner, banner_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def list_public_banners() -> list[dict]:
    """公开 Banner：仅 enabled=true，sort_order 升序。"""
    with Session(get_engine()) as session:
        rows = session.scalars(
            select(SiteBanner)
            .where(SiteBanner.enabled.is_(True))
            .order_by(SiteBanner.sort_order.asc(), SiteBanner.id.asc())
        ).all()
        return [_to_banner_dict(r) for r in rows]


# ──────────────────────────────────────────────
# 通告
# ──────────────────────────────────────────────


def _validate_announcement_type(announcement_type: str) -> str:
    t = str(announcement_type or "banner")
    if t not in ANNOUNCEMENT_TYPES:
        raise ValueError(
            f"announcement_type 必须是 {'/'.join(ANNOUNCEMENT_TYPES)} 之一，got {t!r}"
        )
    return t


def _to_announcement_dict(row) -> dict:
    return {
        "id": int(row.id),
        "title": row.title,
        "content": row.content,
        "announcement_type": row.announcement_type,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_announcements() -> list[dict]:
    """全部通告（管理端）：id 升序。"""
    with Session(get_engine()) as session:
        rows = session.scalars(select(SiteAnnouncement).order_by(SiteAnnouncement.id.asc())).all()
        return [_to_announcement_dict(r) for r in rows]


def create_announcement(data: dict) -> dict:
    """创建通告；announcement_type 非法抛 ValueError。"""
    announcement_type = _validate_announcement_type(data.get("announcement_type"))
    with Session(get_engine()) as session:
        row = SiteAnnouncement(
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            announcement_type=announcement_type,
            enabled=bool(data.get("enabled", True)),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_announcement_dict(row)


def update_announcement(announcement_id: int, data: dict) -> dict | None:
    """更新通告；不存在 → None；announcement_type 非法抛 ValueError。"""
    if "announcement_type" in data:
        _validate_announcement_type(data["announcement_type"])
    with Session(get_engine()) as session:
        row = session.get(SiteAnnouncement, announcement_id)
        if row is None:
            return None
        if "title" in data:
            row.title = str(data["title"] or "")
        if "content" in data:
            row.content = str(data["content"] or "")
        if "announcement_type" in data:
            row.announcement_type = str(data["announcement_type"])
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        session.commit()
        session.refresh(row)
        return _to_announcement_dict(row)


def delete_announcement(announcement_id: int) -> bool:
    """删除通告；不存在 → False。"""
    with Session(get_engine()) as session:
        row = session.get(SiteAnnouncement, announcement_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def list_public_announcements() -> list[dict]:
    """公开通告：仅 enabled=true。"""
    with Session(get_engine()) as session:
        rows = session.scalars(
            select(SiteAnnouncement)
            .where(SiteAnnouncement.enabled.is_(True))
            .order_by(SiteAnnouncement.id.asc())
        ).all()
        return [_to_announcement_dict(r) for r in rows]

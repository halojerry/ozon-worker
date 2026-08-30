"""PRD M5b: 草稿图片镜像(COS,异步)。

背景:采集箱草稿的图片可能是 1688 alicdn / Ozon 竞品图等外链,提交后 worker
生图/转存链路对部分 CDN 访问不稳定,且外链随时可能失效(参考 v0.28.5 E1 教训)。
方案:草稿保存(POST/PATCH)时若 COS 已配置 → 异步下载 draft.images(≤5 张,
10s 超时/失败跳过)转存 COS → 替换 payload 图片 URL;未配置 COS / 全部失败 →
保持原外链(不阻断、不告警风暴)。

竞态(R8):回写按 payload version 校验——版本已变(用户又编辑过) → 丢弃镜像
结果并告警,绝不用旧镜像覆盖新编辑。状态列 image_mirror_state:
''(未启用) / pending(镜像中) / mirrored(已转存) / failed(失败保持外链)。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from sqlalchemy import text

from storage.database.db import get_engine
from utils.cos_uploader import cos_enabled, cos_upload_bytes

logger = logging.getLogger(__name__)

MAX_IMAGES = 5
DOWNLOAD_TIMEOUT = 10


def _is_cos_url(url: str) -> bool:
    """判断是否已是 COS 公网 URL(幂等,避免重复转存)。"""
    if not isinstance(url, str):
        return True
    lowered = url.strip().lower()
    if not lowered:
        return True
    return ".myqcloud.com" in lowered or "cos." in lowered


def _mirror_one(url: str, prefix: str = "draft-images") -> Optional[str]:
    """下载单张草稿图 → 转存 COS → 返回公网 URL;失败/非 http → None。"""
    import hashlib

    try:
        import requests
    except Exception:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    try:
        resp = requests.get(url.strip(), timeout=DOWNLOAD_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200 or not resp.content:
            logger.warning("草稿图下载失败(HTTP %s): %s", resp.status_code, url[:120])
            return None
        digest = hashlib.md5(url.strip().encode("utf-8")).hexdigest()[:16]
        key = f"{prefix}/{digest}.jpg"
        return cos_upload_bytes(resp.content, key, content_type="image/jpeg")
    except Exception as exc:
        logger.warning("草稿图转存失败(%s): %s", url[:120], exc)
        return None


def mirror_draft_images(payload: dict, max_n: int = MAX_IMAGES) -> tuple[list[str], bool]:
    """同步镜像 envelope.draft.images(≤max_n 张) → 返回 (新图片列表, 是否变更)。"""
    draft = payload.get("draft") or {}
    images = draft.get("images") or []
    if not isinstance(images, list) or not images:
        return [], False
    if not cos_enabled():
        return [str(u) for u in images], False
    new_images: list[str] = []
    changed = False
    for url in images[:max_n]:
        if _is_cos_url(url):
            new_images.append(str(url))
            continue
        mirrored = _mirror_one(str(url))
        if mirrored:
            new_images.append(mirrored)
            changed = True
        else:
            new_images.append(str(url))
    return new_images, changed


def _update_payload_guarded(tenant_id: str, draft_id: str, version: int,
                            payload: dict, state: str) -> None:
    """按 version 回写 payload + image_mirror_state;版本不匹配 → 丢弃并告警(R8)。"""
    import uuid as _uuid

    try:
        uid = _uuid.UUID(str(draft_id))
    except (ValueError, TypeError):
        return
    try:
        with get_engine().begin() as conn:
            result = conn.execute(text(
                "UPDATE product_drafts SET payload=CAST(:payload AS jsonb), "
                "image_mirror_state=:state "
                "WHERE id=:id AND tenant_id=:tenant_id AND version=:version"
            ), {
                "payload": json.dumps(payload, ensure_ascii=False),
                "state": state,
                "id": uid,
                "tenant_id": tenant_id,
                "version": version,
            })
        if result.rowcount == 0:
            logger.warning(
                "草稿图片镜像回写丢弃(版本已变更,避免覆盖新编辑): draft=%s v=%s",
                draft_id, version)
    except Exception as exc:
        logger.warning("草稿图片镜像回写失败: draft=%s: %s", draft_id, exc)


def spawn_image_mirror(tenant_id: str, draft_id: str, version: int, payload: dict) -> None:
    """异步镜像草稿图(COS 未配置 → 直接返回;线程内全失败 → failed 状态,保持外链)。"""
    if not cos_enabled():
        return
    images = ((payload.get("draft") or {}).get("images")) or []
    if not isinstance(images, list) or not images:
        return

    def _run() -> None:
        try:
            new_images, changed = mirror_draft_images(payload)
            if not changed:
                _update_payload_guarded(tenant_id, draft_id, version, payload, "failed")
                return
            new_payload = json.loads(json.dumps(payload))  # deep copy
            (new_payload.setdefault("draft", {}))["images"] = new_images
            _update_payload_guarded(tenant_id, draft_id, version, new_payload, "mirrored")
            logger.info("✅ 草稿图片镜像完成: draft=%s 图片 %d 张", draft_id, len(new_images))
        except Exception as exc:
            logger.warning("草稿图片镜像异常: draft=%s: %s", draft_id, exc)
            try:
                _update_payload_guarded(tenant_id, draft_id, version, payload, "failed")
            except Exception:
                pass

    threading.Thread(target=_run, name=f"draft-mirror-{draft_id[:8]}", daemon=True).start()

"""任务生图缓存（v0.26）— 重跑/重启不重烧生图额度。

问题背景：任务处理器无 checkpointer，队列重试/超时/重启后整个管线（含全部 9+N 张生图）
从零重跑，每次重烧全部图片额度（Sentry 超时×100/failed×120 实证）。

方案：生图节点生成成功后把 URL 写入 PG 表 task_generated_images(task_id, slot)；
同一任务再次执行时节点先查缓存，命中直接复用，不重新调用生图 API。

task_id 取 LangGraph config.configurable.thread_id（= PG 任务 ID，task_processor 注入）。
注意：不要用 state.task_id —— 那是 ingest 节点随机生成的 UUID，与队列任务 ID 不一致。

用法：
    from utils.task_image_cache import get_image, save_image
    url = get_image(task_id, "main")
    if not url:
        url = call_mxou_image_api(...)
        save_image(task_id, "main", url)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

SLOTS = (
    "main", "white_bg", "multi_angle", "detail", "social_proof",
    "comparison", "scene_1", "scene_2", "scene_3",
)


def _task_id_from_config(config) -> str:
    """从 LangGraph RunnableConfig 提取任务 ID（thread_id = PG 任务 ID）。"""
    try:
        if config is None:
            return ""
        conf = config.get("configurable", {}) if isinstance(config, dict) else {}
        return str(conf.get("thread_id", ""))
    except Exception:
        return ""


def get_image(task_id: str, slot: str) -> Optional[str]:
    """查任务生图缓存。无 task_id / 表不可用 → None（走正常生图，不阻断）。"""
    if not task_id or task_id in ("unknown", "None"):
        return None
    try:
        from storage.database.db import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT url FROM task_generated_images WHERE task_id = :tid AND slot = :s"),
                {"tid": task_id, "s": slot},
            ).fetchone()
        if row and row[0]:
            return str(row[0])
        return None
    except Exception as e:
        logger.debug("task_image_cache.get_image 失败(task=%s slot=%s): %s", task_id, slot, e)
        return None


def save_image(task_id: str, slot: str, url: str) -> None:
    """写入任务生图缓存（幂等 upsert）。失败仅告警，不阻断生图流程。"""
    if not task_id or task_id in ("unknown", "None") or not url:
        return
    try:
        from storage.database.db import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO task_generated_images (task_id, slot, url, created_at) "
                    "VALUES (:tid, :s, :u, NOW()) "
                    "ON CONFLICT (task_id, slot) DO UPDATE SET url = EXCLUDED.url, created_at = NOW()"
                ),
                {"tid": task_id, "s": slot, "u": url},
            )
            conn.commit()
    except Exception as e:
        logger.warning("task_image_cache.save_image 失败(task=%s slot=%s): %s", task_id, slot, e)


def cleanup_old(older_than_days: int = 7) -> int:
    """清理超过 N 天的缓存行（由 main._periodic_task_cleanup 定期调用）。"""
    try:
        from storage.database.db import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            result = conn.execute(
                text("DELETE FROM task_generated_images WHERE created_at < NOW() - INTERVAL ':d days'"),
                {"d": older_than_days},
            )
            conn.commit()
            return result.rowcount
    except Exception as e:
        logger.debug("task_image_cache.cleanup_old 失败: %s", e)
        return 0


__all__ = ["get_image", "save_image", "cleanup_old", "SLOTS"]

"""任务生图缓存（v0.26 → v0.41 T7a）— 版本化 + params 快照 + image_parent_task_id 回溯。

问题背景：任务处理器无 checkpointer，队列重试/超时/重启后整个管线（含全部 9+N 张生图）
从零重跑，每次重烧全部图片额度（Sentry 超时×100/failed×120 实证）。

v0.41（WebUI T1, 契约 C3）改造：
- 主键扩为 (task_id, slot, version)：
  - version: 重生成（webui force_regen）→ version++ 写新行，正常管线读最新版本（恒 1）；
  - params: 完整节点 Input schema JSONB 原样快照（重生成时重建节点入参用）；
  - image_parent_task_id: resubmit 图片血缘（原 task_id），缓存 miss 时回溯父行复用，
    ⚠️ 区别于任务级 payload.parent_task_id（任务血缘，main.py resubmit_task 注入）。
- get_image 默认最新版本；force_regen=True → 绕过缓存读（返回 None，节点重新生图）；
  自身 (task_id,slot) miss → 查任务级 payload.parent_task_id → 用父 task_id 查 → 命中复用
  （并复制一行到当前 task 带 image_parent_task_id=父id）；一层回溯。

task_id 取 LangGraph config.configurable.thread_id（= PG 任务 ID，task_processor 注入）。
注意：不要用 state.task_id —— 那是 ingest 节点随机生成的 UUID，与队列任务 ID 不一致。

用法：
    from utils.task_image_cache import get_image, save_image, _force_regen_from_config, _regen_version_from_config
    # 正常管线（最新版本命中缓存）
    url = get_image(task_id, "main")
    if not url:
        url = call_mxou_image_api(...)
        save_image(task_id, "main", url, params=input_state.model_dump())
    # webui regen（节点读到 config.force_regen → 绕过缓存读 + 显式 version）
    url = call_mxou_image_api(...)
    save_image(task_id, "main", url,
               version=_regen_version_from_config(config), params=input_state.model_dump())
"""
from __future__ import annotations

import json
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


def _force_regen_from_config(config) -> bool:
    """config.configurable.force_regen（regen 端点注入）→ 节点绕过缓存读。"""
    try:
        if config is None:
            return False
        conf = config.get("configurable", {}) if isinstance(config, dict) else {}
        return bool(conf.get("force_regen", False))
    except Exception:
        return False


def _regen_version_from_config(config) -> Optional[int]:
    """config.configurable.regen_version（regen 端点注入，save 时显式 version=prev+1）。"""
    try:
        if config is None:
            return None
        conf = config.get("configurable", {}) if isinstance(config, dict) else {}
        v = conf.get("regen_version")
        return int(v) if v is not None else None
    except Exception:
        return None


# ──────────────────────────────────────────────
# DB 读写辅助
# ──────────────────────────────────────────────


def _fetch_image_row(conn, task_id: str, slot: str, version: Optional[int]):
    """查图片行：version=None → 最新版本；否则指定版本。返回 (url, params, image_parent_task_id, version)。"""
    from sqlalchemy import text
    if version is not None:
        return conn.execute(
            text(
                "SELECT url, params, image_parent_task_id, version FROM task_generated_images "
                "WHERE task_id = :tid AND slot = :s AND version = :v"
            ),
            {"tid": task_id, "s": slot, "v": version},
        ).fetchone()
    return conn.execute(
        text(
            "SELECT url, params, image_parent_task_id, version FROM task_generated_images "
            "WHERE task_id = :tid AND slot = :s ORDER BY version DESC LIMIT 1"
        ),
        {"tid": task_id, "s": slot},
    ).fetchone()


def _next_version(conn, task_id: str, slot: str) -> int:
    """该 (task_id, slot) 的下一版本号（max+1，无行 → 1）。"""
    from sqlalchemy import text
    row = conn.execute(
        text(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM task_generated_images "
            "WHERE task_id = :tid AND slot = :s"
        ),
        {"tid": task_id, "s": slot},
    ).fetchone()
    return int(row[0]) if row else 1


def _get_task_parent_id(conn, task_id: str) -> Optional[str]:
    """任务级血缘：ozon_product_tasks.payload.parent_task_id（resubmit 注入）。"""
    from sqlalchemy import text
    row = conn.execute(
        text("SELECT payload ->> 'parent_task_id' FROM ozon_product_tasks WHERE id = :tid"),
        {"tid": task_id},
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return None


def _insert_row(conn, task_id: str, slot: str, version: int, url: str,
                params: Optional[dict], image_parent_task_id: Optional[str]) -> None:
    """写图片行（幂等 upsert，PK=(task_id, slot, version)）。"""
    from sqlalchemy import text
    params_json = json.dumps(params, ensure_ascii=False) if params is not None else None
    conn.execute(
        text(
            "INSERT INTO task_generated_images "
            "(task_id, slot, version, url, params, image_parent_task_id, created_at) "
            "VALUES (:tid, :s, :v, :u, CAST(:p AS JSONB), :ip, NOW()) "
            "ON CONFLICT (task_id, slot, version) DO UPDATE SET "
            "url = EXCLUDED.url, params = EXCLUDED.params, "
            "image_parent_task_id = EXCLUDED.image_parent_task_id, created_at = NOW()"
        ),
        {"tid": task_id, "s": slot, "v": version, "u": url,
         "p": params_json, "ip": image_parent_task_id},
    )


def _row_to_dict(task_id: str, slot: str, row) -> dict:
    return {
        "task_id": task_id,
        "slot": slot,
        "version": int(row[3]),
        "url": str(row[0]),
        "params": row[1],
        "image_parent_task_id": row[2],
    }


# ──────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────


def get_image(task_id: str, slot: str, version: Optional[int] = None,
              force_regen: bool = False) -> Optional[str]:
    """查任务生图缓存。

    - version=None → 最新版本；version=N → 指定版本
    - force_regen=True → 返回 None（节点绕过缓存读，webui 重生成）
    - 自身 (task_id,slot) miss → 查任务级 payload.parent_task_id → 用父 task_id 查 →
      命中复用（并复制一行到当前 task 带 image_parent_task_id=父id）；一层回溯
    - 无 task_id / 表不可用 → None（走正常生图，不阻断）
    """
    if force_regen or not task_id or task_id in ("unknown", "None"):
        return None
    try:
        from storage.database.db import get_engine
        with get_engine().connect() as conn:
            row = _fetch_image_row(conn, task_id, slot, version)
            if row and row[0]:
                return str(row[0])
            # 一层 parent 回溯（任务级血缘，main.py resubmit_task 注入 payload.parent_task_id）
            parent_id = _get_task_parent_id(conn, task_id)
            if parent_id and parent_id != task_id:
                prow = _fetch_image_row(conn, parent_id, slot, version)
                if prow and prow[0]:
                    _insert_row(conn, task_id, slot,
                                _next_version(conn, task_id, slot),
                                str(prow[0]), prow[1], parent_id)
                    conn.commit()
                    return str(prow[0])
        return None
    except Exception as e:
        logger.debug("task_image_cache.get_image 失败(task=%s slot=%s): %s", task_id, slot, e)
        return None


def get_image_info(task_id: str, slot: str, version: Optional[int] = None) -> Optional[dict]:
    """完整行信息（version/url/params/image_parent_task_id）——regen/list 端点用。"""
    if not task_id or task_id in ("unknown", "None"):
        return None
    try:
        from storage.database.db import get_engine
        with get_engine().connect() as conn:
            row = _fetch_image_row(conn, task_id, slot, version)
            if not row:
                return None
            return _row_to_dict(task_id, slot, row)
    except Exception as e:
        logger.debug("task_image_cache.get_image_info 失败(task=%s slot=%s): %s", task_id, slot, e)
        return None


def get_latest_version(task_id: str, slot: str) -> int:
    """该 (task_id, slot) 最新版本号（无行 → 0）。"""
    info = get_image_info(task_id, slot)
    return int(info["version"]) if info else 0


def list_images(task_id: str) -> list[dict]:
    """该任务全部图片行（slot/version/url/params/image_parent_task_id）。"""
    if not task_id or task_id in ("unknown", "None"):
        return []
    try:
        from storage.database.db import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT slot, version, url, params, image_parent_task_id, created_at "
                    "FROM task_generated_images WHERE task_id = :tid ORDER BY slot, version"
                ),
                {"tid": task_id},
            ).fetchall()
        return [
            {
                "slot": r[0], "version": int(r[1]), "url": str(r[2]),
                "params": r[3], "image_parent_task_id": r[4], "created_at": r[5],
            }
            for r in rows
        ]
    except Exception as e:
        logger.debug("task_image_cache.list_images 失败(task=%s): %s", task_id, e)
        return []


def save_image(task_id: str, slot: str, url: str, version: Optional[int] = None,
               params: Optional[dict] = None,
               image_parent_task_id: Optional[str] = None) -> Optional[int]:
    """写入任务生图缓存。

    - version=None → 自动取该 (task_id,slot) 最大 version+1（正常管线恒 1）；
      version 显式传入（regen 端点 prev+1）→ 写指定版本新行。
    - params: 节点 Input model_dump() 原样 JSONB 快照。
    - 失败仅告警，不阻断生图流程。返回写入的 version（失败 None）。
    """
    if not task_id or task_id in ("unknown", "None") or not url:
        return None
    try:
        from storage.database.db import get_engine
        with get_engine().connect() as conn:
            v = version if version is not None else _next_version(conn, task_id, slot)
            _insert_row(conn, task_id, slot, v, url, params, image_parent_task_id)
            conn.commit()
            return v
    except Exception as e:
        logger.warning("task_image_cache.save_image 失败(task=%s slot=%s): %s", task_id, slot, e)
        return None


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


__all__ = [
    "get_image", "get_image_info", "get_latest_version", "list_images",
    "save_image", "cleanup_old", "SLOTS",
    "_task_id_from_config", "_force_regen_from_config", "_regen_version_from_config",
]

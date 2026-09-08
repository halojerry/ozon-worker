"""任务取证一站式只读聚合（v0.70）——替代「换库 Supabase」的本地/云端配合取证通道。

动机：生产取证此前只能 SSH 进容器 psql 直查留存/审计表（无读 API）。
本服务把单个任务的四路事实聚成一个只读视图：

- task：任务行快照（status/error/时间线/product_id，租户校验在此层做——
  跨租户/不存在 → None → 端点 404）；
- listing_result：listing_result_log 一行（1688/Ozon 双侧事实 + 结果归因，
  v0.67 起唯一长期留存——completed 任务 7 天物理删）；
- category_match_log：类目匹配审计（按 task_id=thread_id，v0.67 前历史行
  为 ingest 随机 uuid 无法关联——已知数据断层，非本服务缺陷）；
- attr_match_log：属性匹配审计。

只读；JSONB/数组列原样返回；时间统一 ISO 字符串。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

_MATCH_LOG_CAP = 20   # category_match_log 行数上限（每次匹配一行，重试任务会多条）
_ATTR_LOG_CAP = 100   # attr_match_log 行数上限（每属性一行）


def get_task_forensics(tenant_id: str, task_id: str) -> Optional[dict]:
    """按任务 uuid 聚合四路取证事实。任务行不存在或不属本租户 → None（404）。"""
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    try:
        with get_engine().connect() as conn:
            trow = conn.execute(text(
                "SELECT id::text, status, error_message, created_at, completed_at,"
                " result->>'product_id' AS product_id,"
                " payload->'envelope'->'draft'->>'title' AS title,"
                " payload->>'source' IS NOT NULL AS has_envelope"
                " FROM ozon_product_tasks WHERE id::text = :i AND tenant_id = :t"
            ), {"i": task_id, "t": tenant_id}).mappings().first()
            if not trow:
                return None
            lrow = conn.execute(text(
                "SELECT * FROM listing_result_log"
                " WHERE task_db_id = :i AND tenant_id = :t LIMIT 1"
            ), {"i": task_id, "t": tenant_id}).mappings().first()
            mrows = conn.execute(text(
                "SELECT id, task_id, source_title, source_url, source_category,"
                " matched_description_category_id, matched_type_id, matched_path_zh,"
                " match_layer, confidence, upload_success, upload_error_code, created_at"
                " FROM category_match_log WHERE task_id = :i"
                " ORDER BY created_at ASC LIMIT :cap"
            ), {"i": task_id, "cap": _MATCH_LOG_CAP}).mappings().all()
            arows = conn.execute(text(
                "SELECT id, task_id, attr_id, attr_name, source_value, candidate_count,"
                " status, match_layer, confidence, dictionary_value_id, source,"
                " should_fill, created_at"
                " FROM attr_match_log WHERE task_id = :i"
                " ORDER BY created_at ASC, attr_id ASC LIMIT :cap"
            ), {"i": task_id, "cap": _ATTR_LOG_CAP}).mappings().all()
    except Exception as exc:
        logger.warning("forensics 查询失败: %s", exc)
        raise

    def _iso(v):
        return v.isoformat() if v is not None else None

    listing = None
    if lrow:
        listing = dict(lrow)
        for k in ("created_at", "completed_at"):
            listing[k] = _iso(listing.get(k))
    return {
        "task": {
            "task_id": trow["id"],
            "status": trow["status"],
            "error_message": (trow["error_message"] or "")[:2000],
            "created_at": _iso(trow["created_at"]),
            "completed_at": _iso(trow["completed_at"]),
            "product_id": trow["product_id"] or "",
            "title": (trow["title"] or "")[:200],
        },
        "listing_result": listing,
        "category_match_log": [
            {
                "id": r["id"],
                "source_title": (r["source_title"] or "")[:120],
                "source_url": r["source_url"],
                "matched_dc": r["matched_description_category_id"],
                "matched_tp": r["matched_type_id"],
                "matched_path_zh": (r["matched_path_zh"] or "")[:160],
                "match_layer": r["match_layer"],
                "confidence": r["confidence"],
                "upload_success": r["upload_success"],
                "upload_error_code": r["upload_error_code"],
                "created_at": _iso(r["created_at"]),
            }
            for r in mrows
        ],
        "attr_match_log": [
            {
                "id": r["id"],
                "attr_id": r["attr_id"],
                "attr_name": (r["attr_name"] or "")[:80],
                "source_value": (r["source_value"] or "")[:120],
                "status": r["status"],
                "match_layer": r["match_layer"],
                "confidence": r["confidence"],
                "dictionary_value_id": r["dictionary_value_id"],
                "should_fill": r["should_fill"],
                "created_at": _iso(r["created_at"]),
            }
            for r in arows
        ],
    }

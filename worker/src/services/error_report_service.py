"""用户问题反馈错误报告（v0.69）——模板化上报 + 任务上下文自动 enrichment。

动机：用户问题反馈此前散落在对话里（实例：2026-09-07 泡脚包三连失败 + 17 条
官方清单，靠人工粘贴对齐）。模板化落库后：agent 按模板填写（复现方式/证据）
→ POST /api/v1/error_reports → worker 自动按 evidence.task_ids 附加任务快照
（租户内查询，跨租户/不存在静默跳过）→ 开发侧可按 status 流转做修复闭环。

表 error_reports（shared/model.ErrorReport）append-only；status 人工流转。
模板契约文档：docs/ERROR-REPORT-TEMPLATE.md。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

SEVERITIES = {"high", "medium", "low"}
CATEGORIES = {
    "upload_failed", "category_wrong", "attribute_error", "image_error",
    "pricing", "cli_bug", "other",
}
STATUSES = {"new", "triaging", "fixed", "wontfix"}


def _task_snapshots(tenant_id: str, task_ids: list) -> list[dict]:
    """按 evidence.task_ids 附加任务快照（租户隔离；查不到/不属于本租户 → 跳过）。

    取证实证（泡脚包 3170fd33 假成功）：单看用户描述难定位——快照带
    status/error_message/时间线/product_id，报告自带复现所需最小事实。
    """
    ids = [str(t).strip() for t in (task_ids or []) if str(t).strip()][:10]
    if not ids:
        return []
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id::text, status, error_message, created_at, completed_at, "
                "result->>'product_id' AS product_id "
                "FROM ozon_product_tasks WHERE id::text = ANY(:ids) AND tenant_id = :t "
                "ORDER BY created_at DESC LIMIT 10"
            ), {"ids": ids, "t": tenant_id}).mappings().all()
        return [
            {
                "task_id": r["id"],
                "status": r["status"],
                "error_message": (r["error_message"] or "")[:500],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                "product_id": r["product_id"] or "",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("error_report 任务快照获取失败(非致命): %s", exc)
        return []


def create_error_report(
    tenant_id: str,
    body: dict,
    worker_version: str = "",
) -> dict:
    """落一行报告 + 自动 enrichment。返回 {report_id, created_at, tasks_attached}。"""
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    severity = str(body.get("severity") or "medium").strip().lower()
    if severity not in SEVERITIES:
        severity = "medium"
    category = str(body.get("category") or "other").strip().lower()
    if category not in CATEGORIES:
        category = "other"

    reproduction = body.get("reproduction") if isinstance(body.get("reproduction"), dict) else None
    evidence = body.get("evidence") if isinstance(body.get("evidence"), dict) else None
    # 兼容扁平传参：task_ids/item_id 直接放顶层时归入 evidence（agent 传参省一层）
    if evidence is None:
        flat: dict[str, Any] = {}
        for k in ("task_ids", "draft_ids", "item_id", "offer_id", "ozon_product_id", "error_codes"):
            if body.get(k):
                flat[k] = body[k]
        evidence = flat or None

    snapshots = _task_snapshots(tenant_id, (evidence or {}).get("task_ids") or [])
    auto_context: Optional[dict] = None
    if snapshots:
        auto_context = {"tasks": snapshots}
    elif evidence and evidence.get("task_ids"):
        auto_context = {"tasks": [], "note": "task_ids 均不存在或不属于当前租户"}

    with get_engine().begin() as conn:
        row = conn.execute(text(
            "INSERT INTO error_reports (tenant_id, title, severity, category, description,"
            " reproduction, evidence, auto_context, status, worker_version, skill_version, platform)"
            " VALUES (:t, :title, :sev, :cat, :desc, CAST(:rep AS jsonb), CAST(:evi AS jsonb),"
            " CAST(:ac AS jsonb), 'new', :wv, :sv, :plat)"
            " RETURNING id::text, created_at"
        ), {
            "t": tenant_id,
            "title": title[:500],
            "sev": severity,
            "cat": category,
            "desc": str(body.get("description") or "") or None,
            "rep": _json(reproduction),
            "evi": _json(evidence),
            "ac": _json(auto_context),
            "wv": str(worker_version or "")[:20] or None,
            "sv": str((evidence or {}).get("skill_version") or "")[:20] or None,
            "plat": str((evidence or {}).get("platform") or "")[:40] or None,
        }).fetchone()
    return {
        "report_id": row[0],
        "created_at": row[1].isoformat() if row[1] else None,
        "tasks_attached": len(snapshots),
    }


def list_error_reports(
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
) -> dict:
    """本租户报告列表（新→旧）；status 可筛。"""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    cond = "WHERE tenant_id = :t"
    params: dict = {"t": tenant_id, "limit": limit, "offset": offset}
    if status:
        cond += " AND status = :st"
        params["st"] = status
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT id::text, title, severity, category, status, created_at,"
            f" auto_context IS NOT NULL AS enriched"
            f" FROM error_reports {cond} ORDER BY created_at DESC"
            f" LIMIT :limit OFFSET :offset"
        ), params).mappings().all()
        total = conn.execute(text(
            f"SELECT COUNT(*) FROM error_reports {cond}"
        ), params).scalar()
    return {
        "total": int(total or 0),
        "reports": [
            {
                "report_id": r["id"],
                "title": r["title"],
                "severity": r["severity"],
                "category": r["category"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "enriched": bool(r["enriched"]),
            }
            for r in rows
        ],
    }


def get_error_report(tenant_id: str, report_id: str) -> Optional[dict]:
    """单条详情（本租户；跨租户 → None 等价 404）。"""
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id::text, title, severity, category, description, reproduction,"
            " evidence, auto_context, status, worker_version, skill_version, platform,"
            " created_at, updated_at"
            " FROM error_reports WHERE id::text = :i AND tenant_id = :t"
        ), {"i": str(report_id), "t": tenant_id}).mappings().first()
    if not row:
        return None
    d = dict(row)
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def _json(obj: Any) -> Optional[str]:
    import json as _json_mod
    return _json_mod.dumps(obj, ensure_ascii=False) if obj is not None else None

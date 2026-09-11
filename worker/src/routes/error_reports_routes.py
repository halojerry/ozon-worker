"""error_reports + forensics 路由 — 租户 guard 一期试点（BL-17 / A9 S12-01）。

端点（与 main.py 内联版逐字等价迁移，鉴权/限流/租户四段内联替换为
``Depends(get_tenant)``，依赖实现见 api/deps_tenant.py）：
    POST /api/v1/error_reports            用户问题反馈错误报告（v0.69）
    GET  /api/v1/error_reports            本租户报告列表 / ?report_id= 详情
    GET  /api/v1/forensics/task/{id}      任务取证一站式只读聚合（v0.70）
    GET  /forensics/task/{id}             旧路径兼容（挂 app 级 root_router）

⏳ main.py 接线步骤（一期未执行——main.py 属另一会话占用区禁碰，待解禁后
由协调者执行，本文件此前不生效、行为零变化）：
    1. 删除 main.py 三内联端点：``v1_create_error_report`` /
       ``v1_list_error_reports`` / ``v1_task_forensics``（约 2684-2783 行）。
    2. 注册（与既有 include_router 区同位）::
           from routes.error_reports_routes import (
               router as error_reports_router,
               root_router as error_reports_root_router,
           )
           v1.include_router(error_reports_router)        # → /api/v1/*
           app.include_router(error_reports_root_router)  # → /forensics/*（旧路径）

行为差异说明（唯一一处，dependency 模式固有语义）：POST /error_reports 现状
顺序为 verify→限流→body 解析(422)→resolve_tenant；dependency 版
resolve_tenant 前移到 body 解析之前——「body 坏 + 租户解析失败」并发时错误码
由 422 变 401/503（鉴权语义优先）。成功路径与鉴权/限流/租户语义逐字等价。

设计依据：docs/audit/2026-09-11-repo-gov/design-b2b-tenant-guard.md §3
Phase 1（试点接线）；main.py 18 处全量收敛是 Phase 2。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps_tenant import get_tenant, get_tenant_no_rate_limit

# 挂 v1（prefix="/api/v1"）下：POST/GET /error_reports + GET /forensics/task/{id}
router = APIRouter(tags=["error-reports"])

# 挂 app 级（无前缀）：GET /forensics/task/{id} 旧路径兼容（对齐 main.py 双装饰器）
root_router = APIRouter(tags=["error-reports"])


@router.post("/error_reports", tags=["error-reports"])
async def v1_create_error_report(
    request: Request, tenant_id: str = Depends(get_tenant)
):
    """用户问题反馈错误报告（v0.69）：agent 按模板填写（含复现方式/证据）→ 落库。

    worker 按 evidence.task_ids 自动附加本租户任务快照（假成功取证实证：
    快照自带 status/error/product_id/时间线，报告自足可复现）。
    模板契约：docs/ERROR-REPORT-TEMPLATE.md。鉴权/限流与 analytics 同源
    （Bearer 提取 + _verify_analytics_token + 限流已由 get_tenant 承担）。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")

    from services.error_report_service import create_error_report

    try:
        out = create_error_report(
            tenant_id, body,
            worker_version=(os.environ.get("APP_VERSION", "") or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "ok", **out}


@router.get("/error_reports", tags=["error-reports"])
async def v1_list_error_reports(
    request: Request, tenant_id: str = Depends(get_tenant_no_rate_limit)
):
    """本租户错误报告列表（新→旧，status 可筛，limit≤200）。详情：?report_id=。

    现状内联序列无限流检查 → 用 get_tenant_no_rate_limit 逐字对齐。
    """
    q = request.query_params
    rid = (q.get("report_id") or "").strip()
    from services.error_report_service import get_error_report, list_error_reports

    if rid:
        detail = get_error_report(tenant_id, rid)
        if detail is None:
            raise HTTPException(status_code=404, detail="report not found")
        return detail
    try:
        limit = max(1, min(int(q.get("limit", 50)), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(0, int(q.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    return list_error_reports(tenant_id, limit=limit, offset=offset,
                              status=(q.get("status") or "").strip() or None)


def _task_forensics(task_id: str, tenant_id: str) -> dict:
    """forensics 主体（v1/旧路径两装饰器共用，与 main.py 版逐字等价）。"""
    from services.forensics_service import get_task_forensics

    out = get_task_forensics(tenant_id, task_id)
    if out is None:
        raise HTTPException(status_code=404, detail="task not found")
    return out


@router.get("/forensics/task/{task_id}", tags=["error-reports"])
async def v1_task_forensics(
    task_id: str, tenant_id: str = Depends(get_tenant)
):
    """任务取证一站式只读聚合（v0.70）：任务快照 + listing_result_log +
    category_match_log + attr_match_log 四路事实。

    替代「换库 Supabase」的本地/云端配合取证通道——agent/MCP 凭 Bearer 直接查
    生产任务的留存与审计（此前只能 SSH psql）。租户校验：任务行不属本租户 →
    404（等价不存在）。v0.67 前的 category_match_log 历史行为 ingest 随机 uuid，
    无法与任务行关联（已知数据断层）。
    """
    return _task_forensics(task_id, tenant_id)


@root_router.get("/forensics/task/{task_id}", tags=["error-reports"])
async def task_forensics_legacy_path(
    task_id: str, tenant_id: str = Depends(get_tenant)
):
    """旧路径兼容（main.py 版 @app.get("/forensics/task/{task_id}") 双装饰器的一半）。"""
    return _task_forensics(task_id, tenant_id)

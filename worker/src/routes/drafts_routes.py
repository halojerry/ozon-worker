"""T6/T14b: 草稿路由（薄层：鉴权 → 读取 → 调 service → 错误码映射）。

端点：
    POST   ""                         创建（凭证剥离，payload 只存 envelope）
    GET    ""                         列表（租户隔离 + 最新 submission 状态）
    GET    /{draft_id}                读取（租户隔离）
    PATCH  /{draft_id}               编辑（version 乐观锁，stale → 409）
    DELETE /{draft_id}               删除（draft_submissions 级联删，T10 采集箱）
    POST   /{draft_id}/submit        提交（per-store 重复 409 + 跨店确认）
    POST   /{draft_id}/ai/{field}    单字段 AI 重新生成（T14b，只读）

业务逻辑在 services/draft_service.py + services/ai_field_service.py。
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from api.schemas import DraftAiResponse, DraftOut, DraftPatch, SubmissionTimelineItem, SubmitResponse
from services import draft_service
from services.ai_field_service import AI_FIELDS, extract_current_value, regenerate_field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])


async def _authenticate(request: Request) -> str:
    """token 来源：Authorization: Bearer 优先，body token 兜底（C6「token body 或 Bearer」）。"""
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    try:
        raw = await request.body()
        if raw:
            data = json.loads(raw.decode("utf-8"))
            token = str(data.get("token", "") or "")
            if token:
                return _authenticate_token(token)
    except Exception:
        pass
    return _authenticate_token("")


@router.post("", response_model=DraftOut)
async def create_draft(request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return draft_service.create_draft(tenant_id, body)


@router.get("", response_model=list[DraftOut])
async def list_drafts(request: Request):
    tenant_id = await _authenticate(request)
    return draft_service.list_drafts(tenant_id)


@router.get("/export")
async def export_drafts(request: Request):
    """PRD M5(P2): 采集箱导出 CSV(租户隔离,UTF-8 BOM 兼容 Excel)。"""
    import datetime as _dt

    tenant_id = await _authenticate(request)
    csv_text = draft_service.export_drafts_csv(tenant_id)
    filename = f"drafts-{_dt.date.today().isoformat()}.csv"
    return Response(
        content="\ufeff" + csv_text,  # BOM 让 Excel 正确识别 UTF-8 中文
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{draft_id}", response_model=DraftOut)
async def get_draft(draft_id: str, request: Request):
    tenant_id = await _authenticate(request)
    return draft_service.get_draft(tenant_id, draft_id)


@router.get("/{draft_id}/submissions", response_model=list[SubmissionTimelineItem])
async def list_submissions(draft_id: str, request: Request):
    """M2.2 提交时间线：草稿被提交过几次、到过哪些店、结果如何（created_at 倒序）。

    先校验草稿归属（不存在/跨租户 → 404），再返回全部 submission 行。
    """
    tenant_id = await _authenticate(request)
    return draft_service.list_submissions(tenant_id, draft_id)


@router.patch("/{draft_id}", response_model=DraftOut)
async def patch_draft(draft_id: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    return draft_service.patch_draft(tenant_id, draft_id, DraftPatch.model_validate(body))


@router.delete("/{draft_id}", status_code=204)
async def delete_draft(draft_id: str, request: Request):
    """T10 采集箱删除：租户隔离；draft_submissions 由 FK CASCADE 级联删（验收：清空/删除级联删 submissions）。"""
    tenant_id = await _authenticate(request)
    draft_service.delete_draft(tenant_id, draft_id)


@router.post("/{draft_id}/submit", response_model=SubmitResponse)
async def submit_draft(draft_id: str, request: Request):
    tenant_id = await _authenticate(request)
    body = await request.json()
    token = str(body.get("token", "") or "")
    credential_id = body.get("credential_id")
    update_product_id = str(body.get("update_product_id", "") or "") or None
    template_id = body.get("template_id")
    scheduled_at = str(body.get("scheduled_at", "") or "").strip()
    if scheduled_at:
        # 定时上架:校验凭证归属后落 scheduled_listings(token 加密)
        from services.credential_service import get_decrypted
        get_decrypted(tenant_id, credential_id)
        scheduled = await draft_service.schedule_listing(
            tenant_id, draft_id, credential_id, token, scheduled_at)
        return JSONResponse(status_code=202, content={"scheduled": True, **scheduled})
    return await draft_service.submit_draft(
        tenant_id, draft_id, token, credential_id, update_product_id, template_id)


@router.post("/{draft_id}/resubmit", response_model=SubmitResponse)
async def resubmit_draft(draft_id: str, request: Request):
    """失败/被拒草稿重新提交(进行中 → 409)。"""
    tenant_id = await _authenticate(request)
    body = await request.json()
    token = str(body.get("token", "") or "")
    credential_id = body.get("credential_id")
    if draft_service.has_active_submission(tenant_id, draft_id, credential_id):
        raise HTTPException(status_code=409, detail="该草稿已在上架中,请勿重复提交")
    return await draft_service.submit_draft(
        tenant_id, draft_id, token, credential_id,
        str(body.get("update_product_id", "") or "") or None,
        body.get("template_id"))


@router.post("/batch-submit")
async def batch_submit_drafts(request: Request):
    """批量提交草稿(≤50):逐条进行中守卫;返回 submitted/skipped/failed 明细。"""
    tenant_id = await _authenticate(request)
    body = await request.json()
    ids = [str(x) for x in (body.get("ids") or [])][:50]
    token = str(body.get("token", "") or "")
    credential_id = body.get("credential_id")
    submitted: list[str] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    for did in ids:
        if draft_service.has_active_submission(tenant_id, did, credential_id):
            skipped.append({"draft_id": did, "reason": "已在上架中"})
            continue
        try:
            result = await draft_service.submit_draft(tenant_id, did, token, credential_id)
            submitted.append(str(result.get("task_id") or did))
        except HTTPException as exc:
            failed.append({"draft_id": did, "reason": str(exc.detail)[:200]})
        except Exception as exc:
            failed.append({"draft_id": did, "reason": str(exc)[:200]})
    return {"submitted": submitted, "skipped": skipped, "failed": failed}


@router.post("/import")
async def import_drafts_csv(request: Request):
    """PRD M5b(P2): CSV/JSON 批量导入采集箱(竞品对标)。

    Content-Type: text/csv → 原始 CSV(表头 title,item_id,images,purchase_cost,
    purchase_url,price,stock,supplier,weight,length,width,height;images 用
    | 或 ; 分隔);application/json → {"rows": [...]}。逐行复用 create_draft
    (凭证剥离/字段校验/图片镜像),失败行返回 error 不阻断其余行。
    """
    import csv
    import io

    tenant_id = await _authenticate(request)
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="body 不能为空")
    content_type = request.headers.get("content-type", "").lower()
    if "text/csv" in content_type:
        text_body = raw.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text_body))
        rows = [dict(r) for r in reader]
        if not rows:
            raise HTTPException(status_code=400, detail="CSV 无数据行")
        return draft_service.import_drafts_csv(tenant_id, rows)
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="body 必须是 JSON 或 text/csv")
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows 不能为空（[{title, item_id, images, ...}]）")
    if len(rows) > 500:
        raise HTTPException(status_code=400, detail="单次最多导入 500 行")
    return draft_service.import_drafts_csv(tenant_id, rows)


def _load_draft_payload(draft_id: str, tenant_id: str) -> Optional[dict]:
    """按 id + tenant 读取草稿 payload（租户隔离；未找到/跨租户 → None）。"""
    from storage.database.db import get_engine
    from storage.database.shared.model import ProductDraft

    try:
        draft_uuid = uuid.UUID(draft_id)
    except (ValueError, TypeError, AttributeError):
        return None
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                select(ProductDraft.payload).where(
                    ProductDraft.id == draft_uuid,
                    ProductDraft.tenant_id == tenant_id,
                )
            ).first()
    except Exception as exc:  # DB 不可用 → 视为未找到（fail-open 语义由 T6 定，这里 404）
        logger.warning("读取草稿失败 draft_id=%s: %s", draft_id, exc)
        return None
    if row is None:
        return None
    return row[0]


@router.post("/{draft_id}/ai/{field}", response_model=DraftAiResponse)
async def draft_ai_field(draft_id: str, field: str, request: Request):
    """单字段 AI 重新生成（T14b）：只读，返回 RU 值，不写回草稿（前端 PATCH 保存）。

    错误映射：无/无效 token → 401（_authenticate_token）；未知 field → 400；
    草稿不存在/跨租户 → 404；草稿字段为空或 LLM 失败/含中文拉丁残留 → 422。
    """
    from main import _authenticate_token, _extract_token_from_body  # 局部 import 防循环

    raw_body = (await request.body()).decode("utf-8", errors="replace")
    token = _extract_token_from_body(raw_body)
    tenant_id = _authenticate_token(token)  # 401/403/429（在 DB 读取之前）

    if field not in AI_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"未知字段: {field}（支持 {sorted(AI_FIELDS)}；brand 强制 Нет бренда 不做 AI 生成）",
        )

    payload = _load_draft_payload(draft_id, tenant_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="草稿不存在或无权访问")

    current_value = extract_current_value(field, payload)
    if not current_value:
        raise HTTPException(status_code=422, detail=f"草稿 {field} 为空，无可重新生成内容")

    mxou_token = token[3:] if token.startswith("sk-") else token
    # v0.59: 标题公式流量词从 envelope extensions 读取（只做提示词增强，服务层 parse 过滤）
    _extensions = payload.get("extensions") if isinstance(payload, dict) else None
    _traffic_keywords = (
        _extensions.get("traffic_keywords") if isinstance(_extensions, dict) else None
    )
    value = regenerate_field(field, current_value, mxou_token, traffic_keywords=_traffic_keywords)
    if value is None:
        raise HTTPException(
            status_code=422,
            detail=f"AI 重新生成失败或结果含中文/拉丁残留（field={field}），请重试",
        )

    logger.info("draft_ai 重新生成成功: draft_id=%s field=%s 长度=%d", draft_id, field, len(value))
    return {"field": field, "value": value}

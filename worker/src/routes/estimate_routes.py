"""M1.2: 草稿预估售价端点（薄层：鉴权 → 读取 → 调 service）。

    POST /api/v1/drafts/{draft_id}/estimate

业务逻辑在 services/estimate_service.py；定价公式在 utils/pricing_estimate.py
（单处定义，与 pricing_node 同源）。纯读派生数据，不落库。

body: {token, currency_code?, exchange_rate?, margin_rate?, commission_rate?, fx_buffer?}
- exchange_rate 缺省 → 按 CNY 处理（端点不接汇率实时源，与 skill 估算一致）

错误映射：无/无效 token → 401（_authenticate_token）；草稿不存在/跨租户 → 404。
"""
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from services import estimate_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])

# 允许请求体覆盖的配置参数（None → service 用 extensions 默认）
_OVERRIDE_KEYS = ("currency_code", "exchange_rate", "margin_rate", "commission_rate", "fx_buffer")


def _load_draft_payload(draft_id: str, tenant_id: str) -> Optional[dict]:
    """按 id + tenant 读取草稿 envelope（租户隔离；未找到/跨租户 → None）。"""
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
    except Exception as exc:  # DB 不可用 → 视为未找到（与 T6 语义一致，这里 404）
        logger.warning("读取草稿失败 draft_id=%s: %s", draft_id, exc)
        return None
    if row is None:
        return None
    return row[0]


def _authenticate_token(token: str) -> str:
    """薄封装：复用 main._authenticate_token（延迟导入防循环）。"""
    from main import _authenticate_token as _auth

    return _auth(token)


def _parse_overrides(raw_body: str) -> dict:
    """从 body 解析可选覆盖参数（非 dict/异常 → 空覆盖由 service 走默认）。"""
    try:
        data = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in _OVERRIDE_KEYS if k in data and data[k] is not None}


@router.post("/{draft_id}/estimate")
async def estimate_draft(draft_id: str, request: Request):
    """预估售价/利润/物流费（纯读：不落库、不调 Ozon 上架）。

    前端采集箱「预估售价/利润/利润率」决策列走此端点；公式与 pricing_node 同源
    （utils/pricing_estimate.compute_price，v0.40 统一纪律：前端/skill 不写公式）。
    """
    from main import _extract_token_from_body  # 局部 import 防循环

    raw_body = (await request.body()).decode("utf-8", errors="replace")
    tenant_id = _authenticate_token(_extract_token_from_body(raw_body))  # 401/403/429

    payload = _load_draft_payload(draft_id, tenant_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="草稿不存在或无权访问")

    return estimate_service.estimate_from_envelope(payload, **_parse_overrides(raw_body))


# ── P2a 独立定价器（无 draft_id）：单独 router，路径 /api/v1/estimate ──
router_estimate = APIRouter(prefix="/api/v1/estimate", tags=["estimate"])


@router_estimate.post("")
async def estimate_envelope_standalone(request: Request):
    """P2a 独立定价器：直接传 envelope（无 draft_id）→ 同源公式预估。

    body: {envelope: {draft:{purchase_cost, weight, dimensions}, extensions:{}},
           margin_rate?, commission_rate?, fx_buffer?}
    与 /api/v1/drafts/{id}/estimate 同公式（estimate_from_envelope）；
    前端/skill 不写公式铁律不变。
    """
    from main import _extract_token_from_body  # 局部 import 防循环

    raw_body = (await request.body()).decode("utf-8", errors="replace")
    tenant_id = _authenticate_token(_extract_token_from_body(raw_body))  # 401/403/429

    body = json.loads(raw_body) if raw_body else {}
    envelope = body.get("envelope")
    if not isinstance(envelope, dict) or not envelope.get("draft"):
        raise HTTPException(status_code=422, detail="缺少 envelope.draft（定价输入）")

    return estimate_service.estimate_from_envelope(envelope, **_parse_overrides(raw_body))

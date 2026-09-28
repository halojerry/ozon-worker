"""M1.2: 草稿预估售价端点（薄层：鉴权 → 读取 → 调 service）。

    POST /api/v1/drafts/{draft_id}/estimate

业务逻辑在 services/estimate_service.py；定价公式在 utils/pricing_estimate.py
（单处定义，与 pricing_node 同源）。纯读派生数据，不落库。

body: {token, currency_code?, exchange_rate?, margin_rate?, commission_rate?, fx_buffer?,
       margin_anchor?, margin_floor?, variable_cost_rate?, promo_variable_cost_rate?}
- exchange_rate 缺省 → 按 CNY 处理（端点不接汇率实时源，与 skill 估算一致）
- v0.60：margin_floor（或请求 margin_rate）在场 → 三档（price/old_price/promo_price）

错误映射：无/无效 token → 401（_authenticate_token）；草稿不存在/跨租户 → 404。
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select

from api.schemas import EstimateBatchIn, EstimateBatchItem
from services import estimate_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])

# 允许请求体覆盖的配置参数（None → service 用 extensions 默认）
# v0.60：margin_anchor/margin_floor/variable_cost_rate/promo_variable_cost_rate 启用三档
_OVERRIDE_KEYS = (
    "currency_code",
    "exchange_rate",
    "margin_rate",
    "commission_rate",
    "fx_buffer",
    "margin_anchor",
    "margin_floor",
    "variable_cost_rate",
    "promo_variable_cost_rate",
)


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


# 响应示例（openapi_extra 路由级补；字段 = estimate_service.estimate_from_envelope 返回值，
# 三档默认激活时含 promo_price/margin_anchor/margin_floor/variable_cost_rate）
_ESTIMATE_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "price": 215.0, "old_price": 258.0, "promo_price": 129.0,
    "profit_cny": 32.6, "profit_rate": 0.152,
    "logistics_cost_cny": 8.0, "currency": "RUB",
    "commission_rate": 0.15, "commission_source": "cache:leq_5000",
    "margin_anchor": 2.0, "margin_floor": 0.6, "variable_cost_rate": 0.155,
}}}}}}


@router.post("/{draft_id}/estimate", openapi_extra=_ESTIMATE_OK_EXTRA)
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

    return estimate_service.estimate_from_envelope(payload, tenant_id=tenant_id, **_parse_overrides(raw_body))


# ── P2a 独立定价器（无 draft_id）：单独 router，路径 /api/v1/estimate ──
router_estimate = APIRouter(prefix="/api/v1/estimate", tags=["estimate"])


@router_estimate.post("", openapi_extra=_ESTIMATE_OK_EXTRA)
async def estimate_envelope_standalone(request: Request):
    """P2a 独立定价器：直接传 envelope（无 draft_id）→ 同源公式预估。

    body: {envelope: {draft:{purchase_cost, weight, dimensions},
           extensions:{credential_id?, ozon_client_id?, ...}},
           margin_rate?, commission_rate?, fx_buffer?, margin_anchor?, margin_floor?,
           variable_cost_rate?, promo_variable_cost_rate?}
    与 /api/v1/drafts/{id}/estimate 同公式（estimate_from_envelope）；
    前端/skill 不写公式铁律不变。

    v0.83 gate 批①：``envelope.extensions`` 可带 ``credential_id``（worker 内部 UUID）
    或 ``ozon_client_id``（skill 侧店铺 client_id，worker 按 (tenant, client) 反查凭证）
    → 物流费走店铺真实 3PL（``logistics_source=store``），与 pricing_node 同源；缺省
    回落 default_rets。
    """
    from main import _extract_token_from_body  # 局部 import 防循环

    raw_body = (await request.body()).decode("utf-8", errors="replace")
    tenant_id = _authenticate_token(_extract_token_from_body(raw_body))  # 401/403/429

    body = json.loads(raw_body) if raw_body else {}
    envelope = body.get("envelope")
    if not isinstance(envelope, dict) or not envelope.get("draft"):
        raise HTTPException(status_code=422, detail="缺少 envelope.draft（定价输入）")

    return estimate_service.estimate_from_envelope(envelope, tenant_id=tenant_id, **_parse_overrides(raw_body))


# ── v0.83 批①：批量预估 · POST /api/v1/estimate/batch ──
# 唯一算价出口（worker compute_price 链）批量形态——skill discover 匹配期批量回填
# 「预估售价/利润」用，退役 skill 侧内联定价公式。
# 鉴权 Bearer（main._require_bearer 唯一入口）+ 独立限流桶 estimate_batch:{token}。
# 部分失败不整体 4xx（逐项 ok/failed，先例 drafts_routes）：单条异常只落 failed。
ESTIMATE_BATCH_MAX_ITEMS = 50

# 请求体声明：**自包含**（EstimateBatchItem 是扁平模型，本无 $defs/$ref）——若直接
# 用 EstimateBatchIn.model_json_schema()，items 元素会带 `$ref: #/$defs/...`，在
# 手拆路由的 openapi_extra 语境下无对应 $defs → openapi-typescript/redoc 解析失败
# （generated.d.ts 生成报 "Can't resolve $ref ... items/items"）。此处展开为内联 schema。
_ESTIMATE_BATCH_ITEM_SCHEMA: Dict[str, Any] = EstimateBatchItem.model_json_schema()
_ESTIMATE_BATCH_REQ_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "maxItems": ESTIMATE_BATCH_MAX_ITEMS,
            "items": _ESTIMATE_BATCH_ITEM_SCHEMA,
        },
        "credential_id": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "店铺凭证 ID；在场则解密探测店铺 3PL（否则 default_rets）",
        },
    },
    "examples": [{
        "items": [(_ESTIMATE_BATCH_ITEM_SCHEMA.get("examples") or [{}])[0]],
        "credential_id": "3c9d2f4e-1111-4222-8333-444455556666",
    }],
}

_ESTIMATE_BATCH_EXTRA: Dict[str, Any] = {
    "requestBody": {"required": True, "content": {
        "application/json": {"schema": _ESTIMATE_BATCH_REQ_SCHEMA}}},
    "responses": {"200": {"content": {"application/json": {"example": {
        "items": [{
            "index": 0, "ok": True,
            "price": 215, "old_price": 258, "promo_price": 129,
            "profit_cny": 32.6, "profit_rate": 0.152, "logistics_cost_cny": 8.0,
            "commission_rate": 0.15, "commission_source": "segments:leq_5000",
            "logistics_source": "default_rets", "estimate_source": "worker",
            "marks": {"weight_suspect": "", "exchange_rate_source": "", "commission_source": "segments:leq_5000"},
        }],
        "failed": [{"index": 1, "reason": "ValueError: 非数字采购成本"}],
    }}}}},
}


def _map_dims_mm(raw: Any) -> Dict[str, float]:
    """尺寸键归一：接受 {length,width,height} 或 {d,w,h}（毫米）；非法值丢弃。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for src, dst in (("length", "length"), ("width", "width"), ("height", "height"),
                     ("d", "length"), ("w", "width"), ("h", "height")):
        if dst in out:
            continue
        v = raw.get(src)
        if v is None:
            continue
        try:
            out[dst] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _run_estimate_batch(body: EstimateBatchIn, tenant_id: str) -> Dict[str, Any]:
    """逐项预估（同步阻塞，供 asyncio.to_thread 调用）：部分失败不整体 4xx。"""
    items_out: list = []
    failed: list = []
    for idx, item in enumerate(body.items):
        try:
            draft: Dict[str, Any] = {
                "purchase_cost": item.purchase_cost,
                "weight": item.weight_g,
                "dimensions": _map_dims_mm(item.dims_mm),
                "attributes": item.attributes or {},
            }
            # ✅ v0.83.1: dc 缺席时经 1688 cid（scid）反查学习映射表——estimate 佣金
            # 冷启动死锁解锁（discover 关键词候选无 Ozon dc 时佣金恒 fallback →
            # v0.83 佣金闸恒拦）。lookup_mapping 自带 succ/conf 门槛，命中才用。
            dc_val = str(item.dc) if item.dc not in (None, "") else ""
            if not dc_val and getattr(item, "scid", None) not in (None, ""):
                try:
                    _cid = int(str(item.scid))
                except (TypeError, ValueError):
                    _cid = None
                if _cid:
                    try:
                        from utils.category_mapping_learn import lookup_mapping
                        _hit = lookup_mapping(source_category_id=_cid)
                        if _hit:
                            dc_val = str(_hit["dc"])
                            logger.info(
                                "estimate/batch scid=%s 反查映射命中 dc=%s", _cid, dc_val)
                    except Exception as exc:  # 反查失败不阻断预估（降级无 dc 旧口径）
                        logger.warning("estimate/batch scid 反查失败: %s", str(exc)[:120])
            if dc_val:
                draft["ozon_category"] = {"description_category_id": dc_val}
            extensions: Dict[str, Any] = {}
            if item.commission_segments:
                extensions["commission_segments"] = item.commission_segments
            envelope = {"draft": draft, "extensions": extensions}
            est = estimate_service.estimate_from_envelope(
                envelope,
                currency_code=item.currency_code,
                credential_id=body.credential_id,
                tenant_id=tenant_id,
            )
            items_out.append({
                "index": idx,
                "ok": True,
                "price": est.get("price"),
                "old_price": est.get("old_price"),
                "promo_price": est.get("promo_price"),
                "profit_cny": est.get("profit_cny"),
                "profit_rate": est.get("profit_rate"),
                "commission_rate": est.get("commission_rate"),
                "commission_source": est.get("commission_source"),
                "logistics_source": est.get("logistics_source"),
                "estimate_source": "worker",
                "marks": {
                    "weight_suspect": est.get("weight_suspect", ""),
                    "exchange_rate_source": est.get("exchange_rate_source", ""),
                    "commission_source": est.get("commission_source"),
                },
            })
        except Exception as exc:  # 单条失败不阻断其余（drafts_routes 先例）
            logger.warning("estimate/batch 第 %s 项失败: %s", idx, str(exc)[:200])
            failed.append({"index": idx, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
    return {"items": items_out, "failed": failed}


@router_estimate.post("/batch", openapi_extra=_ESTIMATE_BATCH_EXTRA)
async def estimate_batch(request: Request):
    """POST /api/v1/estimate/batch —— 批量预估（≤50/批，唯一算价出口批量形态）。

    - 鉴权：``_require_bearer``（只认 Authorization Bearer）；独立限流桶
      ``estimate_batch:{token}``（不与提交额度互挤），超限 429。
    - 租户：恒自身租户（``resolve_tenant``）；credential_id 在场经 credential_service
      解密探测店铺 3PL（跨租户 → 404 由其内部保证）。
    - 不支持 variants（schema ``extra="forbid"`` 定死，收到即 422）。
    - 部分失败不整体 4xx：逐项 ``items[{ok,index,...}]`` + ``failed[{index,reason}]``。
    """
    from main import _require_bearer, rate_limiter  # 局部 import 防循环

    clean_token = _require_bearer(request)
    allowed, _ = rate_limiter.check(f"estimate_batch:{clean_token}")
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limited")

    try:
        body = EstimateBatchIn.model_validate(await request.json())
    except ValidationError as exc:
        # 字段缺失/类型错/未知键（含 variants）→ 422 可读 detail（不裸 500）
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError:
        # 畸形 JSON → 422（原文不回显）
        raise HTTPException(status_code=422, detail="malformed JSON body")

    if not body.items:
        raise HTTPException(status_code=422, detail="items 不能为空")
    if len(body.items) > ESTIMATE_BATCH_MAX_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=f"items 超过单批上限 {ESTIMATE_BATCH_MAX_ITEMS} 条",
        )

    from services.tenant_service import resolve_tenant
    tenant_id = resolve_tenant(clean_token)

    return await asyncio.to_thread(_run_estimate_batch, body, tenant_id)

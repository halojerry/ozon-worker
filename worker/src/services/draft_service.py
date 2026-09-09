"""T6: 采集箱草稿服务 — C1 草稿/提交两表 + 凭证剥离 + C5 重复校验 + warehouse/stock 透传。

分层（1.4 约束）：services 是唯一业务实现，routes/未来 BFF/MCP 都只是门面。
- create: POST /drafts 收 GraphInput → 剥离凭证（加密存 credentials 表）→ payload 只存 envelope
- list/get/patch: 租户隔离 CRUD；patch 带 version 乐观锁（stale → 409）
- submit: 凭证注入 → per-store 重复校验（409，fail-open）→ 跨店提醒（不硬拦）
  → task_processor.submit_task 入队 → draft_submissions 行（extensions 快照）
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text

from api.schemas import DraftPatch
from services import credential_service, product_index_service
from storage.database.db import get_engine
from utils.ozon_client import ozon_post

logger = logging.getLogger(__name__)

DUP_MESSAGE = "重复商品：目标店铺已存在相同商品"
_SECRET_KEYS = ("api_key", "apikey")


def _norm_notes(val) -> str:
    """备注归一：None→空串、strip、cap 2000。create/import/PATCH 三口共用。"""
    if val is None:
        return ""
    return str(val).strip()[:2000]


def has_active_submission(tenant_id: str, draft_id: str,
                          credential_id: Optional[str] = None) -> bool:
    """草稿在该店是否存在进行中 submission(pending/uploading)→ 防重复提交/重试。"""
    try:
        uid = uuid.UUID(draft_id)
    except (ValueError, TypeError, AttributeError):
        return False
    sql = "SELECT 1 FROM draft_submissions WHERE draft_id=:d AND status IN ('pending','uploading')"
    params: dict = {"d": uid}
    if credential_id:
        sql += " AND credential_id::text=:c"
        params["c"] = str(credential_id)
    with get_engine().connect() as conn:
        row = conn.execute(text(sql + " LIMIT 1"), params).fetchone()
    return row is not None


def schedule_listing(tenant_id: str, draft_id: str, credential_id: str,
                     token: str, scheduled_at: str) -> dict:
    """定时上架:落 scheduled_listings(token 加密存 token_enc,aad=tenant)。"""
    import datetime as _dt
    from utils.credential_cipher import encrypt
    try:
        scheduled_dt = _dt.datetime.fromisoformat(scheduled_at)
        if scheduled_dt.tzinfo is None:
            scheduled_dt = scheduled_dt.replace(tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="scheduled_at 格式非法(ISO8601)")
    if scheduled_dt <= _dt.datetime.now(_dt.timezone.utc):
        raise HTTPException(status_code=422, detail="scheduled_at 必须晚于当前时间")
    token_enc = encrypt(token, f"{tenant_id}:scheduled")
    with get_engine().begin() as conn:
        row = conn.execute(text(
            """
            INSERT INTO scheduled_listings
                (tenant_id, draft_id, credential_id, scheduled_at, status, token_enc)
            VALUES (:t, :d, :c, :at, 'pending', :enc)
            ON CONFLICT (draft_id, credential_id) DO UPDATE SET
                scheduled_at = EXCLUDED.scheduled_at,
                status = 'pending',
                token_enc = EXCLUDED.token_enc,
                created_at = NOW()
            RETURNING id, scheduled_at, status
            """
        ), {"t": tenant_id, "d": uuid.UUID(draft_id), "c": str(credential_id),
            "at": scheduled_dt, "enc": token_enc}).fetchone()
    return {"scheduled_listing_id": int(row[0]),
            "scheduled_at": row[1].isoformat(), "status": row[2]}


def _update_scheduled(listing_id: int, status: str, task_id: Optional[str],
                      error: str = "") -> None:
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE scheduled_listings SET status=:s, task_id=:tid, error=:e, created_at=NOW() "
            "WHERE id=:id"
        ), {"s": status, "tid": task_id, "e": error[:500], "id": listing_id})


async def process_scheduled_listings(limit: int = 20) -> dict:
    """到点定时上架:提交成功 → submitted+task_id;重复 → skipped;失败 → failed+error。"""
    import datetime as _dt
    from utils.credential_cipher import decrypt
    now = _dt.datetime.now(_dt.timezone.utc)
    with get_engine().begin() as conn:
        rows = conn.execute(text(
            "SELECT id, tenant_id, draft_id::text, credential_id::text, token_enc "
            "FROM scheduled_listings WHERE status='pending' AND scheduled_at <= :now "
            "ORDER BY scheduled_at LIMIT :lim FOR UPDATE SKIP LOCKED"
        ), {"now": now, "lim": limit}).fetchall()
    results = {"submitted": 0, "skipped": 0, "failed": 0}
    for rid, tenant, draft_id, cred, enc in rows:
        try:
            token = decrypt(bytes(enc), f"{tenant}:scheduled")
            if has_active_submission(tenant, draft_id, cred):
                _update_scheduled(rid, "skipped", None, "已在上架中")
                results["skipped"] += 1
                continue
            result = await submit_draft(tenant, draft_id, token, credential_id=cred)
            _update_scheduled(rid, "submitted", str(result.get("task_id") or ""))
            results["submitted"] += 1
        except HTTPException as exc:
            _update_scheduled(rid, "failed", None, str(exc.detail)[:200])
            results["failed"] += 1
        except Exception as exc:
            _update_scheduled(rid, "failed", None, str(exc)[:200])
            results["failed"] += 1
    return results


def _assert_no_api_key(payload: Any) -> None:
    """递归断言 envelope 不含任何凭证明文（raw api_key 嵌草稿 → 400）。"""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if any(secret in key.lower() for secret in _SECRET_KEYS):
                    raise HTTPException(
                        status_code=400,
                        detail=f"envelope 内禁止携带凭证字段: {key}（凭证请放 GraphInput 顶层）",
                    )
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def _validate_draft_fields(envelope: dict) -> None:
    """create 阶段字段弱校验：只拦严重残缺，不替代 submit 真防线。

    真防线在 submit 的 validate_draft_sanity（draft_sanity.py）——weight<=0 且无
    competitor_weight_g → 拒；dimensions 三边全<=0 且无 competitor_dimensions_mm → 拒。
    create 只做两件事：
    - title 缺失（严重、无法修复）→ 400，把残缺尽早暴露给用户；
    - weight/dimensions 全零且无竞品兜底 → logger.warning（不阻断，攒进采集箱再修）。
    """
    draft = envelope.get("draft") or {}
    if not isinstance(draft, dict):
        raise HTTPException(status_code=400, detail="envelope.draft 必须是对象")

    title = str(draft.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="草稿缺少标题（draft.title）：无法上架，请先补全产品标题")

    extensions = envelope.get("extensions") or {}
    if not isinstance(extensions, dict):
        extensions = {}
    try:
        weight = float(draft.get("weight") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    dims = draft.get("dimensions") or {}
    if not isinstance(dims, dict):
        dims = {}
    dim_positive = any(
        isinstance(dims.get(k), (int, float)) and dims.get(k) > 0
        for k in ("length", "width", "height")
    )
    has_competitor_weight = bool(extensions.get("competitor_weight_g", 0) > 0)
    comp_dims = extensions.get("competitor_dimensions_mm") or {}
    if not isinstance(comp_dims, dict):
        comp_dims = {}
    has_competitor_dims = any(
        isinstance(comp_dims.get(k), (int, float)) and comp_dims.get(k) > 0
        for k in ("length", "width", "height")
    )

    problems = []
    if weight <= 0 and not has_competitor_weight:
        problems.append("weight")
    if not dim_positive and not has_competitor_dims:
        problems.append("dimensions")
    if problems:
        logger.warning(
            "草稿字段缺失（不阻断 create，submit 时将被拦截）: 缺失=%s item=%s",
            ",".join(problems),
            str(draft.get("item_id") or draft.get("sku_id") or "")[:40],
        )


def _resolve_offer_id(envelope: dict) -> str:
    """确定性 offer_id：跟卖 follow_{竞品id}，否则 draft.item_id / sku_id。"""
    draft = envelope.get("draft") or {}
    extensions = envelope.get("extensions") or {}
    follow_sell = bool(extensions.get("follow_sell")) or bool(extensions.get("follow_type"))
    if follow_sell:
        competitor = str(draft.get("ozon_product_id") or "").strip()
        if competitor:
            return f"follow_{competitor}"
    return str(draft.get("item_id") or draft.get("sku_id") or "").strip()


def _draft_row_to_dict(row) -> dict:
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    out = {
        "id": str(row.id),
        "tenant_id": row.tenant_id,
        "payload": payload,
        "source": row.source,
        "version": row.version,
        "image_mirror_state": getattr(row, "image_mirror_state", "") or "",
        "notes": getattr(row, "notes", None),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    status = getattr(row, "submission_status", None)
    if status is not None:
        out["submission_status"] = status
    return out


def _parse_draft_uuid(draft_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(draft_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="草稿不存在")


# ──────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────


def create_draft(tenant_id: str, body: dict) -> dict:
    """POST /drafts：凭证剥离 + 只存 envelope。"""
    envelope = body.get("envelope")
    if not isinstance(envelope, dict) or not envelope.get("draft"):
        raise HTTPException(status_code=400, detail="envelope 不能为空，必须包含 draft 字段")
    _assert_no_api_key(envelope)
    _validate_draft_fields(envelope)

    client_id = str(body.get("ozon_client_id", "") or "").strip()
    api_key = str(body.get("ozon_api_key", "") or "").strip()
    if client_id and api_key:
        credential_service.store_credential(tenant_id, client_id, api_key)

    source = str(body.get("source") or "skill")
    notes = _norm_notes(body.get("notes"))
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "INSERT INTO product_drafts (tenant_id, payload, source, notes) "
            "VALUES (:tenant_id, CAST(:payload AS jsonb), :source, :notes) "
            "RETURNING id, tenant_id, payload, source, version, image_mirror_state, "
            "notes, created_at, updated_at"
        ), {
            "tenant_id": tenant_id,
            "payload": json.dumps(envelope, ensure_ascii=False),
            "source": source,
            "notes": notes,
        }).fetchone()
    from services.draft_image_mirror import spawn_image_mirror
    spawn_image_mirror(tenant_id, str(row.id), row.version, envelope)
    return _draft_row_to_dict(row)


_LATEST_SUBMISSION_SQL = (
    "LEFT JOIN LATERAL ("
    "  SELECT status AS submission_status FROM draft_submissions"
    "  WHERE draft_id = d.id ORDER BY created_at DESC LIMIT 1"
    ") s ON true"
)


def list_drafts(tenant_id: str) -> list[dict]:
    """GET /drafts：租户隔离列表（updated_at 倒序），携带最新 submission 状态（T10 上架状态列）。"""
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT d.id, d.tenant_id, d.payload, d.source, d.version, "
            "d.image_mirror_state, d.notes, d.created_at, d.updated_at, "
            "s.submission_status "
            f"FROM product_drafts d {_LATEST_SUBMISSION_SQL} "
            "WHERE d.tenant_id=:tenant_id ORDER BY d.updated_at DESC"
        ), {"tenant_id": tenant_id}).fetchall()
    return [_draft_row_to_dict(r) for r in rows]


# v0.70 扩列：discovery_meta 透传列（skill 侧生产端 _assemble_discovery_meta 的键，
# 旧草稿无对应键 → 空串；列序 = skill export_to_csv 扩列段，双导出口径一致）。
_DRAFT_META_CSV_KEYS = [
    "ozon_url", "ozon_price", "ozon_image",
    "match_1688_title", "match_1688_category_name",
    "min_competing_price", "competing_sellers", "sales_schema",
    "monthly_revenue", "sales_growth", "drr", "create_days",
    "rating", "review_count",
    "estimated_logistics_cny", "estimated_commission", "estimated_profit_cny",
    "session_count", "conv_to_cart_pdp", "conv_to_cart_search",
    "days_in_promo", "discount", "days_with_trafarets",
    "promo_revenue_share", "nullable_redemption_rate", "return_cancel_rate",
    "discovered_at",
    # B 批次冻结契约 4 键（跟卖利润/划线价/1688 运费；生产端在 skill
    # _assemble_discovery_meta，本表先行占位列——缺失键照常空串）
    "follow_profit_cny", "follow_margin", "ozon_old_price", "match_1688_freight_cny",
]


def _fmt_commission_segments(band: dict) -> str:
    """单通道佣金分段 {leq_1500,leq_5000,gt_5000} → '≤1500:8% 1501~5000:10% >5000:12%'
    紧凑文本（Excel 可读，对标上品帮佣金三段列；列名已含通道，不再加前缀）。"""
    if not isinstance(band, dict) or not band:
        return ""
    parts = []
    for key, label in (("leq_1500", "≤1500"), ("leq_5000", "1501~5000"), ("gt_5000", ">5000")):
        if band.get(key) is not None:
            parts.append(f"{label}:{band[key]}%")
    return " ".join(parts)


def export_drafts_csv(tenant_id: str) -> str:
    """PRD M5(P2): 采集箱导出 CSV(租户隔离,与列表同源)。

    v0.70 扩列：discovery_meta 全集 + 佣金分段（extensions.commission_segments），
    展示层只读 payload——生产端在 skill（skill 落盘 → 上传 → webui 展示链路）。
    """
    import csv
    import io

    drafts = list_drafts(tenant_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "title", "item_id", "images", "purchase_cost", "purchase_url",
        "price", "stock", "supplier", "weight", "source", "notes",
        "submission_status", "created_at", "updated_at",
        "blue_ocean_score", "monthly_sales", "profit_margin", "match_confidence",
        *_DRAFT_META_CSV_KEYS,
        "commission_rfbs", "commission_fbo",
    ])
    for d in drafts:
        payload = d.get("payload") or {}
        draft = payload.get("draft") or {}
        source = payload.get("source") or {}
        extensions = payload.get("extensions") or {}
        # discover 选品元数据（skill 注入 extensions.discovery_meta，缺失键省略）
        meta = extensions.get("discovery_meta") or {}
        segments = extensions.get("commission_segments") or {}
        row = [
            d["id"],
            str(draft.get("title") or ""),
            str(draft.get("item_id") or ""),
            "|".join(str(u) for u in (draft.get("images") or [])),
            draft.get("purchase_cost") if draft.get("purchase_cost") is not None else "",
            str(source.get("purchase_url") or draft.get("purchase_url") or ""),
            draft.get("price") if draft.get("price") is not None else "",
            draft.get("stock") if draft.get("stock") is not None else "",
            str(draft.get("supplier") or ""),
            draft.get("weight") if draft.get("weight") is not None else "",
            d.get("source") or "",
            str(d.get("notes") or ""),
            d.get("submission_status") or "",
            d.get("created_at") or "",
            d.get("updated_at") or "",
            meta.get("blue_ocean_score") if meta.get("blue_ocean_score") is not None else "",
            meta.get("monthly_sales") if meta.get("monthly_sales") is not None else "",
            meta.get("profit_margin") if meta.get("profit_margin") is not None else "",
            meta.get("match_confidence") if meta.get("match_confidence") is not None else "",
        ]
        for key in _DRAFT_META_CSV_KEYS:
            val = meta.get(key)
            row.append("" if val is None else val)
        row.append(_fmt_commission_segments(segments.get("fbs")))
        row.append(_fmt_commission_segments(segments.get("fbo")))
        writer.writerow(row)
    return buf.getvalue()


def delete_draft(tenant_id: str, draft_id: str) -> None:
    """DELETE /drafts/{id}：租户隔离删除草稿（draft_submissions 由 FK ON DELETE CASCADE 级联删）。

    不存在/跨租户 → 404；存在进行中的上架任务（pending/running）→ 409；
    无返回值（204 语义）。
    """
    uid = _parse_draft_uuid(draft_id)
    with get_engine().begin() as conn:
        # 租户隔离：先按 tenant_id 查归属（不存在/跨租户 → 404，先于守卫）
        owner = conn.execute(text(
            "SELECT 1 FROM product_drafts WHERE id=:id AND tenant_id=:tenant_id LIMIT 1"
        ), {"id": uid, "tenant_id": tenant_id}).fetchone()
        if owner is None:
            raise HTTPException(status_code=404, detail="草稿不存在或无权访问")
        # 守卫：存在进行中的上架任务（pending/running）→ 409，禁止删除
        active = conn.execute(text(
            "SELECT 1 FROM draft_submissions ds "
            "JOIN ozon_product_tasks t ON t.id::text = ds.submitted_task_id "
            "WHERE ds.draft_id=:id AND t.status IN ('pending','running') LIMIT 1"
        ), {"id": uid}).fetchone()
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail="草稿存在进行中的上架任务，请先取消任务再删除",
            )
        result = conn.execute(text(
            "DELETE FROM product_drafts WHERE id=:id AND tenant_id=:tenant_id"
        ), {"id": uid, "tenant_id": tenant_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="草稿不存在或无权访问")


def get_draft(tenant_id: str, draft_id: str) -> dict:
    """GET /drafts/{id}：租户隔离读取；不存在/跨租户 → 404。"""
    uid = _parse_draft_uuid(draft_id)
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id, tenant_id, payload, source, version, image_mirror_state, "
            "notes, created_at, updated_at "
            "FROM product_drafts WHERE id=:id AND tenant_id=:tenant_id"
        ), {"id": uid, "tenant_id": tenant_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="草稿不存在或无权访问")
    return _draft_row_to_dict(row)


def _submission_row_to_dict(row) -> dict:
    extensions = row.extensions
    return {
        "id": str(row.id),
        "store_client_id": row.store_client_id,
        "status": row.status,
        "error_message": row.error_message,
        "extensions": extensions if isinstance(extensions, dict) else json.loads(extensions or "{}"),
        "submitted_task_id": row.submitted_task_id,
        "created_at": row.created_at,
    }


def list_submissions(tenant_id: str, draft_id: str) -> list[dict]:
    """GET /drafts/{id}/submissions：草稿提交时间线（M2.2）。

    先校验草稿归属（get_draft 租户隔离，不存在/跨租户 → 404），再按 draft_id
    查全部 submission 行，created_at 倒序。直连任务草稿（draft_id=NULL 的
    submission 行）天然被 WHERE draft_id 过滤掉，不出现。
    """
    get_draft(tenant_id, draft_id)
    uid = _parse_draft_uuid(draft_id)
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, store_client_id, status, error_message, extensions, "
            "submitted_task_id, created_at "
            "FROM draft_submissions WHERE draft_id=:id ORDER BY created_at DESC"
        ), {"id": uid}).fetchall()
    return [_submission_row_to_dict(r) for r in rows]


def import_drafts_csv(tenant_id: str, rows: list[dict]) -> dict:
    """PRD M5b(P2): CSV 导入采集箱(竞品对标)。

    每行最小字段 title;images 支持列表或 "|"/";" 分隔字符串;purchase_cost/
    price/stock/weight/length/width/height 可空。逐行调 create_draft(同一套
    凭证剥离/字段校验/图片镜像),失败行记 error 不阻断其余行。
    返回 {created, failed, errors: [{row, error}]}。
    """
    created = 0
    failed = 0
    errors: list[dict] = []

    def _row_to_envelope(row: dict) -> dict:
        title = str(row.get("title") or "").strip()
        item_id = str(row.get("item_id") or "").strip()
        images_raw = row.get("images") or ""
        if isinstance(images_raw, list):
            images = [str(u).strip() for u in images_raw if str(u).strip()]
        else:
            images = [
                u.strip() for u in re.split(r"[|\n;]", str(images_raw))
                if u.strip()
            ]

        def _f(key: str):
            try:
                val = row.get(key)
                if val in (None, ""):
                    return None
                return float(val)
            except (TypeError, ValueError):
                return None

        def _i(key: str):
            try:
                val = row.get(key)
                if val in (None, ""):
                    return None
                return int(float(val))
            except (TypeError, ValueError):
                return None

        draft: dict[str, Any] = {
            "title": title,
            "item_id": item_id or f"csv_{uuid.uuid4().hex[:12]}",
            "images": images,
            "purchase_cost": _f("purchase_cost"),
            "price": _f("price"),
            "stock": _i("stock"),
            "supplier": str(row.get("supplier") or "").strip(),
            "weight": _i("weight"),
        }
        dims = {k: _i(k) for k in ("length", "width", "height")}
        if any(v for v in dims.values()):
            draft["dimensions"] = {k: v for k, v in dims.items() if v is not None}
        source = {
            "purchase_url": str(row.get("purchase_url") or "").strip(),
            "purchase_cost": _f("purchase_cost"),
        }
        return {
            "draft": draft,
            "source": source,
            "extensions": {},
        }

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            failed += 1
            errors.append({"row": idx, "error": "行不是对象"})
            continue
        try:
            envelope = _row_to_envelope(row)
            # notes 列可选（CSV 竞品备注列），归一与 create 同口（_norm_notes）
            create_draft(tenant_id, {
                "envelope": envelope,
                "source": "csv",
                "notes": _norm_notes(row.get("notes")),
            })
            created += 1
        except HTTPException as exc:
            failed += 1
            errors.append({
                "row": idx,
                "error": str(getattr(exc, "detail", "") or exc)[:200],
            })
        except Exception as exc:  # 单行失败不阻断
            failed += 1
            errors.append({"row": idx, "error": str(exc)[:200]})
    return {"created": created, "failed": failed, "errors": errors}


def patch_draft(tenant_id: str, draft_id: str, data: DraftPatch) -> dict:
    """PATCH /drafts/{id}：version 乐观锁（stale → 409），成功后 version++。"""
    uid = _parse_draft_uuid(draft_id)
    _assert_no_api_key(data.payload)
    with get_engine().begin() as conn:
        current = conn.execute(text(
            "SELECT version, source FROM product_drafts "
            "WHERE id=:id AND tenant_id=:tenant_id"
        ), {"id": uid, "tenant_id": tenant_id}).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="草稿不存在或无权访问")
        if data.version != current.version:
            raise HTTPException(
                status_code=409,
                detail=f"版本冲突（stale）：当前 version={current.version}，请求 version={data.version}",
            )
        new_source = data.source if data.source is not None else current.source
        updated = conn.execute(text(
            "UPDATE product_drafts SET payload=CAST(:payload AS jsonb), source=:source, "
            "notes=COALESCE(:notes, notes), "
            "version=version+1, updated_at=NOW() "
            "WHERE id=:id AND tenant_id=:tenant_id "
            "RETURNING id, tenant_id, payload, source, version, image_mirror_state, "
            "notes, created_at, updated_at"
        ), {
            "payload": json.dumps(data.payload, ensure_ascii=False),
            "source": new_source,
            "notes": data.notes if data.notes is None else data.notes.strip()[:2000],
            "id": uid,
            "tenant_id": tenant_id,
        }).fetchone()
    from services.draft_image_mirror import spawn_image_mirror
    spawn_image_mirror(tenant_id, str(uid), updated.version, data.payload)
    return _draft_row_to_dict(updated)


def assemble_draft(tenant_id: str, draft_id: str, token: str) -> dict:
    """POST /drafts/{id}/assemble（v0.70 一键预组装）：读草稿 → LLM 整卡生成 → 写回 payload。

    - 读行走 get_draft（404 语义一致：不存在/跨租户 → 404）；
    - token 即 mxou key（LLM 调用必需），路由从 body/Bearer 提取（与 /ai/{field}
      端点同源），sk- 前缀在此剥离；
    - 写回 version++（服务端操作 last-write-wins，无客户端乐观锁——区别于 PATCH
      的 stale 409 场景）；
    - 只写透传字段+展示字段，绝不写 draft.ozon_category / draft.price（见
      ai_field_service.assemble_draft docstring 的透传语义说明）。
    返回 assemble 结果 dict + 新 version。
    """
    from services.ai_field_service import assemble_draft as _assemble_payload

    draft = get_draft(tenant_id, draft_id)
    payload = draft["payload"]
    mxou_token = token[3:] if token.startswith("sk-") else token
    result = _assemble_payload(payload, mxou_token)

    uid = _parse_draft_uuid(draft_id)
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "UPDATE product_drafts SET payload=CAST(:payload AS jsonb), "
            "version=version+1, updated_at=NOW() "
            "WHERE id=:id AND tenant_id=:tenant_id "
            "RETURNING version"
        ), {
            "payload": json.dumps(payload, ensure_ascii=False),
            "id": uid,
            "tenant_id": tenant_id,
        }).fetchone()
    return {**result, "version": int(row[0])}


# ──────────────────────────────────────────────
# submit（C5 两层：per-store 409 + 跨店提醒）
# ──────────────────────────────────────────────


def _apply_listing_template(
    tenant_id: str,
    envelope: dict,
    template_id: Optional[str],
    *,
    is_update: bool,
    credential_id: Optional[str] = None,
) -> dict:
    """P0-1: 把上架配置模板注入 envelope.extensions（返回副本）。

    显式 template_id → 校验归属后注入；未指定 → 租户默认模板兜底
    （无默认模板 → 原样返回）。模板补缺省，草稿 extensions 已有值优先；
    is_update（更新上架）→ 忽略 offer_id_prefix（重上不变式）。
    P1b 多店铺差异化：credential_id 在模板 store_overrides 有覆盖 →
    覆盖值优先于全局 config。
    """
    from services.template_service import apply_template_to_envelope, get_default_template, get_template

    try:
        if template_id:
            template = get_template(tenant_id, str(template_id))
        else:
            template = get_default_template(tenant_id)
    except Exception as exc:
        logger.warning("上架配置模板解析失败（跳过注入不阻断）template=%s: %s", template_id, str(exc)[:200])
        return envelope
    if not template:
        return envelope
    return apply_template_to_envelope(envelope, template, is_update=is_update, credential_id=credential_id)


def _cross_store_scan(tenant_id: str, draft_id: str, current_client_id: str) -> tuple[list[str], bool]:
    """该草稿已提交到其他店铺（exclude 当前目标店）；返回 (existing_stores, confirm_required)。"""
    uid = _parse_draft_uuid(draft_id)
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT store_client_id FROM draft_submissions "
            "WHERE draft_id=:id AND store_client_id IS NOT NULL AND store_client_id<>:cur"
        ), {"id": uid, "cur": current_client_id}).fetchall()
    stores = [str(r[0]) for r in rows]
    return stores, bool(stores)


async def _submit_task(tenant_id: str, graph_payload: dict, sku_key: str) -> str:
    """入队（延迟 import main 防循环；main.task_processor 由 lifespan 初始化）。"""
    from main import task_processor
    if task_processor is None:
        raise HTTPException(status_code=503, detail="Task processor not initialized")
    return await task_processor.submit_task(
        tenant_id=tenant_id,
        payload=graph_payload,
        sku_key=sku_key,
    )


async def _ensure_images_mirrored_for_submit(
    tenant_id: str, draft_id: str, draft: dict, envelope: dict,
) -> dict:
    """提交前外链镜像闸（v0.69 declined IMAGE_ERROR 根因①修复）。

    实证链路：镜像失败保持外链（draft_image_mirror）+ submit 原样透传 + 本地
    可达性探测防不住「现在可达、Ozon 异步抓图时已失效/防盗链」时间窗 →
    卡片 images=0 必拒单。此处同步兜底镜像一次；仍有外链 → 422 拒绝提交
    （外链直传=已知必拒，不放行不烧 Ozon 配额与审核）。
    COS 未配置的部署无镜像能力，降级为 warning 按旧行为放行（诚实告警）。
    """
    from services.draft_image_mirror import _update_payload_guarded, mirror_draft_images
    from utils.cos_uploader import cos_enabled, is_cos_url

    images = ((envelope.get("draft") or {}).get("images")) or []
    if not isinstance(images, list) or not images:
        return envelope
    if all(is_cos_url(u) for u in images):
        return envelope
    if not cos_enabled():
        logger.warning(
            "提交镜像闸降级：COS 未配置，%d 张外链按旧行为直传（Ozon 拒单风险）draft=%s",
            sum(1 for u in images if not is_cos_url(u)), draft_id,
        )
        return envelope

    # 入箱异步镜像可能已失败/未跑完 → 提交时同步兜底一次（不限 5 张上限）
    new_images, _changed = await asyncio.to_thread(
        mirror_draft_images, envelope, max(5, len(images)),
    )
    still_external = [u for u in new_images if not is_cos_url(u)]
    if still_external:
        raise HTTPException(
            status_code=422,
            detail=(
                f"源图镜像失败：{len(still_external)}/{len(new_images)} 张仍为外链，"
                "Ozon 下载外链失败是已知必拒项（IMAGE_ERROR，卡片将 0 图被拒）。"
                "请更换货源图片或稍后重试。"
            ),
        )
    envelope.setdefault("draft", {})["images"] = new_images
    try:
        _update_payload_guarded(
            tenant_id, draft_id, int(draft.get("version") or 0), envelope, "mirrored",
        )
    except Exception as exc:
        logger.warning("提交镜像闸回写草稿失败（不阻断提交）draft=%s: %s", draft_id, exc)
    logger.info("✅ 提交镜像闸：同步镜像 %d 张外链成功 draft=%s", len(new_images), draft_id)
    return envelope


async def submit_draft(
    tenant_id: str,
    draft_id: str,
    token: str,
    credential_id: Optional[str] = None,
    update_product_id: Optional[str] = None,
    template_id: Optional[str] = None,
) -> dict:
    """POST /drafts/{id}/submit：凭证注入 → 模板注入 → 重复校验 → 入队 → submission 行。

    update_product_id（T7 更新模式）：商品已存在 → 跳过 per-store 409 重复校验；
    offer_id 优先从 product_task_index 复用（重上不变式）；graph_payload 注入
    extensions.update_product_id/update_offer_id（仅副本，绝不持久化到草稿）；
    入队后 upsert_index 回填新 task_id（失败仅 warning 不阻断）。

    template_id（P0-1 上架配置模板）：显式指定 → 校验归属后注入；
    未指定 → 租户默认模板兜底。注入语义：模板补缺省，草稿 extensions 已有值优先；
    update_product_id（更新模式）→ 忽略 offer_id_prefix（重上不变式）。
    """
    draft = get_draft(tenant_id, draft_id)
    envelope = draft["payload"]
    # v0.69 镜像闸：外链必须镜像成功才能提交（详见 helper 注释）
    envelope = await _ensure_images_mirrored_for_submit(tenant_id, draft_id, draft, envelope)

    if credential_id:
        client_id, api_key = credential_service.get_decrypted(tenant_id, str(credential_id))
        cred_id = str(credential_id)
    else:
        default = credential_service.get_default_credential(tenant_id)
        if default is None:
            raise HTTPException(
                status_code=400,
                detail="未配置默认店铺凭证：请传 credential_id 或先在店铺管理设置默认店铺",
            )
        client_id, api_key = default["ozon_client_id"], default["api_key"]
        cred_id = default["id"]

    offer_id = _resolve_offer_id(envelope)
    update_offer_id: Optional[str] = None
    if update_product_id:
        index_row = product_index_service.lookup_index(tenant_id, update_product_id)
        if index_row:
            offer_id = index_row["offer_id"]
            update_offer_id = index_row["offer_id"]
    elif offer_id:
        try:
            info = ozon_post(
                client_id, api_key, "/v1/product/info/list",
                {"offer_id": [offer_id], "product_id": []},
                timeout=15,
            )
            if (info.get("items") or []):
                raise HTTPException(status_code=409, detail=DUP_MESSAGE)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(
                "per-store 重复校验失败（fail-open 不阻塞）draft=%s client=%s: %s",
                draft_id, client_id, str(exc)[:200],
            )

    existing_stores, confirm_required = _cross_store_scan(tenant_id, draft_id, client_id)

    payload_envelope = envelope
    # v0.70 采集箱即权威：采集箱提交 = 人工审核过的成品卡。管线对其只做合规
    # 修复（中文属性翻译/数值 sanitize/R1·树校验闸）+ 补全缺失，禁用自主重配
    # （R4 换类目/标题描述重写）——用户在采集箱看到什么，上架就是什么。
    payload_ext = payload_envelope.setdefault("extensions", {})
    payload_ext["box_reviewed"] = True
    if update_product_id:
        payload_envelope = copy.deepcopy(envelope)
        payload_ext = payload_envelope.setdefault("extensions", {})
        payload_ext["box_reviewed"] = True
        payload_ext["update_product_id"] = update_product_id
        if update_offer_id:
            payload_ext["update_offer_id"] = update_offer_id

    # P0-1 上架配置模板注入（模板补缺省，草稿已有值优先；P1b 按店铺覆盖）
    payload_envelope = _apply_listing_template(
        tenant_id, payload_envelope, template_id, is_update=bool(update_product_id),
        credential_id=client_id,
    )

    graph_payload = {
        "token": token,
        "ozon_client_id": client_id,
        "ozon_api_key": api_key,
        "envelope": payload_envelope,
        "user_id": tenant_id,
    }
    sku_key = f"{tenant_id}:{client_id}:{offer_id}" if offer_id else ""
    task_id = await _submit_task(tenant_id, graph_payload, sku_key)

    if update_product_id and update_offer_id:
        try:
            product_index_service.upsert_index(
                tenant_id, update_product_id, update_offer_id, task_id, cred_id,
                draft_id=draft_id,
            )
        except Exception as exc:
            logger.warning(
                "索引回填失败（不阻断提交）product_id=%s: %s", update_product_id, str(exc)[:200],
            )

    # 快照 = 模板注入后的 extensions，但排除 update marker（T7 契约：
    # update_product_id/update_offer_id 只进 graph payload 副本，不持久化）
    extensions_snapshot = copy.deepcopy(payload_envelope.get("extensions") or {})
    for _mk in ("update_product_id", "update_offer_id"):
        extensions_snapshot.pop(_mk, None)
    with get_engine().begin() as conn:
        sub = conn.execute(text(
            "INSERT INTO draft_submissions "
            "(draft_id, credential_id, store_client_id, extensions, status, submitted_task_id) "
            "VALUES (:draft_id, :credential_id, :store_client_id, CAST(:extensions AS jsonb), 'pending', :task_id) "
            "RETURNING id, draft_id, credential_id, store_client_id, extensions, status, submitted_task_id, created_at"
        ), {
            "draft_id": draft_id,
            "credential_id": cred_id,
            "store_client_id": client_id,
            "extensions": json.dumps(extensions_snapshot, ensure_ascii=False),
            "task_id": task_id,
        }).fetchone()

    return {
        "ok": True,
        "draft_id": draft_id,
        "submission_id": str(sub.id),
        "task_id": task_id,
        "status": "pending",
        "confirm_required": confirm_required,
        "existing_stores": existing_stores,
    }

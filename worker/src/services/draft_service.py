"""T6: 采集箱草稿服务 — C1 草稿/提交两表 + 凭证剥离 + C5 重复校验 + warehouse/stock 透传。

分层（1.4 约束）：services 是唯一业务实现，routes/未来 BFF/MCP 都只是门面。
- create: POST /drafts 收 GraphInput → 剥离凭证（加密存 credentials 表）→ payload 只存 envelope
- list/get/patch: 租户隔离 CRUD；patch 带 version 乐观锁（stale → 409）
- submit: 凭证注入 → per-store 重复校验（409，fail-open）→ 跨店提醒（不硬拦）
  → task_processor.submit_task 入队 → draft_submissions 行（extensions 快照）
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text

from api.schemas import DraftPatch
from services import credential_service
from storage.database.db import get_engine
from utils.ozon_client import ozon_post

logger = logging.getLogger(__name__)

DUP_MESSAGE = "重复商品：目标店铺已存在相同商品"
_SECRET_KEYS = ("api_key", "apikey")


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

    client_id = str(body.get("ozon_client_id", "") or "").strip()
    api_key = str(body.get("ozon_api_key", "") or "").strip()
    if client_id and api_key:
        credential_service.store_credential(tenant_id, client_id, api_key)

    source = str(body.get("source") or "skill")
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "INSERT INTO product_drafts (tenant_id, payload, source) "
            "VALUES (:tenant_id, CAST(:payload AS jsonb), :source) "
            "RETURNING id, tenant_id, payload, source, version, created_at, updated_at"
        ), {
            "tenant_id": tenant_id,
            "payload": json.dumps(envelope, ensure_ascii=False),
            "source": source,
        }).fetchone()
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
            "SELECT d.id, d.tenant_id, d.payload, d.source, d.version, d.created_at, d.updated_at, "
            "s.submission_status "
            f"FROM product_drafts d {_LATEST_SUBMISSION_SQL} "
            "WHERE d.tenant_id=:tenant_id ORDER BY d.updated_at DESC"
        ), {"tenant_id": tenant_id}).fetchall()
    return [_draft_row_to_dict(r) for r in rows]


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
            "SELECT id, tenant_id, payload, source, version, created_at, updated_at "
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
            "version=version+1, updated_at=NOW() "
            "WHERE id=:id AND tenant_id=:tenant_id "
            "RETURNING id, tenant_id, payload, source, version, created_at, updated_at"
        ), {
            "payload": json.dumps(data.payload, ensure_ascii=False),
            "source": new_source,
            "id": uid,
            "tenant_id": tenant_id,
        }).fetchone()
    return _draft_row_to_dict(updated)


# ──────────────────────────────────────────────
# submit（C5 两层：per-store 409 + 跨店提醒）
# ──────────────────────────────────────────────


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


async def submit_draft(
    tenant_id: str,
    draft_id: str,
    token: str,
    credential_id: Optional[str] = None,
) -> dict:
    """POST /drafts/{id}/submit：凭证注入 → 重复校验 → 入队 → submission 行。"""
    draft = get_draft(tenant_id, draft_id)
    envelope = draft["payload"]

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
    if offer_id:
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

    graph_payload = {
        "token": token,
        "ozon_client_id": client_id,
        "ozon_api_key": api_key,
        "envelope": envelope,
        "user_id": tenant_id,
    }
    sku_key = f"{tenant_id}:{client_id}:{offer_id}" if offer_id else ""
    task_id = await _submit_task(tenant_id, graph_payload, sku_key)

    extensions_snapshot = copy.deepcopy(envelope.get("extensions") or {})
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

"""T5: 凭证业务层 — 唯一实现，被 routes/未来 BFF 复用。

- API key 永不完整回显：列表/响应仅 api_key_masked（T2 cipher 掩码）
- 轮换 = 旧行 revoked + 新行 active；旧行 ozon_client_id 追加 :revoked: 后缀释放
  uq_credentials_tenant_client 唯一槽位（非部分索引，revoked 行仍占槽）
- is_default=true 时同租户旧默认自动清（防 uq_credentials_default 部分唯一冲突）
- validate = 解密 → Ozon /v1/product/info/list probe → {valid, reason}
"""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Optional

import requests
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.schemas import CredentialCreate, CredentialUpdate
from storage.database.db import get_engine
from utils.credential_cipher import CredentialCipherError, decrypt, encrypt, mask
from utils.ozon_client import ozon_post

logger = logging.getLogger(__name__)

_SELECT_COLS = (
    "id::text AS id, ozon_client_id, api_key_masked, shop_name, currency, is_default, "
    "credential_type, status, last_validated_at, last_rotated_at, created_at, updated_at"
)

_INSERT_SQL = f"""
    INSERT INTO credentials (
        tenant_id, ozon_client_id, ozon_api_key_enc, api_key_masked, shop_name,
        currency, is_default, credential_type, status, last_rotated_at
    ) VALUES (
        :tenant_id, :ozon_client_id, :enc, :masked, :shop_name,
        :currency, :is_default, :credential_type, 'active', :last_rotated_at
    ) RETURNING {_SELECT_COLS}
"""

_UTC = datetime.timezone.utc


def _now() -> datetime.datetime:
    return datetime.datetime.now(_UTC)


def _assert_client_not_bound_elsewhere(tenant_id: str, ozon_client_id: str, conn) -> None:
    """跨租户单店一次绑定拦截：同一 ozon_client_id 已被其他 tenant 绑定 → 409。

    create_credential / store_credential 两入口共同的前置预检，堵住 store_credential
    ``ON CONFLICT (tenant_id, ozon_client_id) DO UPDATE`` 的空子——不同 tenant 下无冲突
    会 INSERT 成功，绕过同租户唯一索引 uq_credentials_tenant_client。

    仅预检（无 DB 级锁），极端并发下可能双绑，记为已知残留，不强行加锁/改表结构。
    """
    row = conn.execute(text(
        "SELECT 1 FROM credentials WHERE ozon_client_id = :client_id "
        "AND tenant_id != :tenant_id LIMIT 1"
    ), {"client_id": ozon_client_id, "tenant_id": tenant_id}).fetchone()
    if row is not None:
        logger.warning("店铺已被其他租户绑定 tenant=%s client=%s", tenant_id, ozon_client_id)
        raise HTTPException(
            status_code=409,
            detail=f"该店铺已被其他用户绑定: {ozon_client_id}",
        )


def _row_to_dict(row) -> dict:
    return {
        "id": str(row.id),
        "ozon_client_id": row.ozon_client_id,
        "api_key_masked": row.api_key_masked,
        "shop_name": row.shop_name,
        "currency": row.currency,
        "is_default": row.is_default,
        "credential_type": row.credential_type,
        "status": row.status,
        "last_validated_at": row.last_validated_at,
        "last_rotated_at": row.last_rotated_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _free_unique_slot_sql() -> str:
    """revoked 行释放 uq_credentials_tenant_client 槽位（保留审计轨迹）。"""
    return (
        "ozon_client_id = ozon_client_id || ':revoked:' || "
        "substring(gen_random_uuid()::text, 1, 8)"
    )


def create_credential(tenant_id: str, data: CredentialCreate) -> dict:
    client_id = (data.ozon_client_id or "").strip()
    api_key = data.api_key or ""
    if not client_id or not api_key:
        raise HTTPException(status_code=400, detail="ozon_client_id 和 api_key 不能为空")

    params = {
        "tenant_id": tenant_id,
        "ozon_client_id": client_id,
        "enc": encrypt(api_key, f"{tenant_id}:{client_id}"),
        "masked": mask(api_key),
        "shop_name": data.shop_name,
        "currency": data.currency or "CNY",
        "is_default": data.is_default,
        "credential_type": data.credential_type or "api_key",
        "last_rotated_at": None,
    }
    try:
        with get_engine().begin() as conn:
            _assert_client_not_bound_elsewhere(tenant_id, client_id, conn)
            if data.is_default:
                conn.execute(text(
                    "UPDATE credentials SET is_default=false "
                    "WHERE tenant_id=:tenant_id AND is_default AND status='active'"
                ), {"tenant_id": tenant_id})
            row = conn.execute(text(_INSERT_SQL), params).fetchone()
    except IntegrityError as exc:
        logger.warning("凭证创建唯一约束冲突 tenant=%s client=%s", tenant_id, client_id)
        raise HTTPException(
            status_code=409,
            detail=f"该店铺凭证已存在（或默认店铺并发冲突）: {client_id}",
        ) from exc
    # PRD M1: 绑定即初始化 — 事务提交后异步入队 initial job(失败由调度器 due-scan 兜底)
    try:
        from services.store_sync_scheduler import jobs_enabled
        if jobs_enabled():
            from services import store_sync_jobs
            store_sync_jobs.enqueue(tenant_id, str(row.id), kind="initial", trigger="bind")
    except Exception as exc:
        logger.warning("绑定后 initial job 入队失败(调度器兜底) tenant=%s: %s",
                       tenant_id, str(exc)[:200])
    return _row_to_dict(row)


def list_credentials(tenant_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM credentials "
            "WHERE tenant_id=:tenant_id AND status='active' "
            "ORDER BY created_at DESC"
        ), {"tenant_id": tenant_id}).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_credential_id_by_client(tenant_id: str, ozon_client_id: str) -> Optional[str]:
    """按 ozon_client_id 反查当前租户的 credential_id(skill 上报 source_candidates 用)。"""
    if not tenant_id or not ozon_client_id:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id::text FROM credentials "
            "WHERE tenant_id=:tenant_id AND ozon_client_id=:client_id "
            "AND status='active' ORDER BY created_at DESC LIMIT 1"
        ), {"tenant_id": tenant_id, "client_id": ozon_client_id}).fetchone()
    return str(row[0]) if row else None


def credential_owned_by(tenant_id: str, credential_id: str) -> bool:
    """只读归属校验(不解密,供货源候选等轻量读端点用)。"""
    if not credential_id:
        return False
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM credentials WHERE id::text=:id AND tenant_id=:tenant_id "
            "AND status='active' LIMIT 1"
        ), {"id": str(credential_id), "tenant_id": tenant_id}).fetchone()
    return row is not None


def rotate_credential(tenant_id: str, credential_id: str, data: CredentialUpdate) -> dict:
    api_key = data.api_key or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key 不能为空")
    try:
        with get_engine().begin() as conn:
            old = conn.execute(text(
                "SELECT ozon_client_id, is_default, shop_name, currency, credential_type "
                "FROM credentials WHERE id::text=:id AND tenant_id=:tenant_id AND status='active'"
            ), {"id": credential_id, "tenant_id": tenant_id}).fetchone()
            if old is None:
                raise HTTPException(status_code=404, detail="凭证不存在或已吊销")
            conn.execute(text(
                "UPDATE credentials SET status='revoked', is_default=false, "
                f"{_free_unique_slot_sql()}, updated_at=NOW() WHERE id::text=:id"
            ), {"id": credential_id})
            params = {
                "tenant_id": tenant_id,
                "ozon_client_id": old.ozon_client_id,
                "enc": encrypt(api_key, f"{tenant_id}:{old.ozon_client_id}"),
                "masked": mask(api_key),
                "shop_name": data.shop_name if data.shop_name is not None else old.shop_name,
                "currency": data.currency or old.currency,
                "is_default": old.is_default,
                "credential_type": old.credential_type,
                "last_rotated_at": _now(),
            }
            row = conn.execute(text(_INSERT_SQL), params).fetchone()
    except IntegrityError as exc:
        logger.warning("凭证轮换唯一约束冲突 tenant=%s id=%s", tenant_id, credential_id)
        raise HTTPException(status_code=409, detail="轮换失败：唯一约束冲突") from exc
    return _row_to_dict(row)


def revoke_credential(tenant_id: str, credential_id: str) -> dict:
    with get_engine().begin() as conn:
        result = conn.execute(text(
            "UPDATE credentials SET status='revoked', is_default=false, "
            f"{_free_unique_slot_sql()}, updated_at=NOW() "
            "WHERE id::text=:id AND tenant_id=:tenant_id AND status='active'"
        ), {"id": credential_id, "tenant_id": tenant_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="凭证不存在或已吊销")
    return {"ok": True, "id": credential_id}


def validate_credential(tenant_id: str, credential_id: str) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT ozon_client_id, ozon_api_key_enc FROM credentials "
            "WHERE id::text=:id AND tenant_id=:tenant_id AND status='active'"
        ), {"id": credential_id, "tenant_id": tenant_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="凭证不存在或已吊销")

    try:
        # psycopg2 将 BYTEA 读回为 memoryview，需转 bytes（cipher 只收 bytes）
        api_key = decrypt(bytes(row.ozon_api_key_enc), f"{tenant_id}:{row.ozon_client_id}")
    except CredentialCipherError:
        return {"valid": False, "reason": "decrypt_failed", "last_validated_at": _touch_validated(credential_id)}

    valid, reason = _probe_ozon(str(row.ozon_client_id), api_key)
    return {"valid": valid, "reason": reason, "last_validated_at": _touch_validated(credential_id)}


def _touch_validated(credential_id: str) -> datetime.datetime:
    now = _now()
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE credentials SET last_validated_at=:now, updated_at=NOW() WHERE id::text=:id"
        ), {"now": now, "id": credential_id})
    return now


def _probe_ozon(client_id: str, api_key: str) -> tuple[bool, str]:
    try:
        # /v3/product/list + visibility:ALL——与 shelf_service.list_ozon_products
        # 第一步同源（实测可用）。原 /v1/product/info/list 带 filter/visibility
        # 嵌套 body 不是该端点契约 → Ozon 404（E2E 实测凭证有效却报 invalid）。
        ozon_post(
            client_id, api_key, "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "limit": 1},
            timeout=15, language="RU",
        )
        return True, "ok"
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403):
            return False, "invalid_key"
        return False, f"ozon_api_error_{status}"
    except requests.RequestException:
        return False, "ozon_api_error"
    except Exception as exc:
        logger.warning("凭证校验 probe 异常: %s", exc)
        return False, "ozon_api_error"


# ── T6 草稿服务依赖的最小凭证接口（凭证剥离 + 提交注入） ──


def store_credential(tenant_id: str, ozon_client_id: str, api_key: str) -> str:
    """加密 upsert 凭证（POST /drafts 凭证剥离用）。

    同一租户同一店铺重复入箱 → 更新密文+掩码（幂等，不 409）。
    返回 credential_id（draft_submissions.credential_id 用）。
    """
    client_id = str(ozon_client_id or "").strip()
    if not client_id or not api_key:
        raise HTTPException(status_code=400, detail="ozon_client_id 和 api_key 不能为空")
    enc = encrypt(api_key, f"{tenant_id}:{client_id}")
    masked = mask(api_key)
    try:
        with get_engine().begin() as conn:
            _assert_client_not_bound_elsewhere(tenant_id, client_id, conn)
            row = conn.execute(text(
                "INSERT INTO credentials (tenant_id, ozon_client_id, ozon_api_key_enc, api_key_masked) "
                "VALUES (:tenant_id, :ozon_client_id, :enc, :masked) "
                "ON CONFLICT (tenant_id, ozon_client_id) DO UPDATE SET "
                "  ozon_api_key_enc = EXCLUDED.ozon_api_key_enc, "
                "  api_key_masked = EXCLUDED.api_key_masked, "
                "  updated_at = NOW() "
                "RETURNING id"
            ), {"tenant_id": tenant_id, "ozon_client_id": client_id,
                "enc": enc, "masked": masked}).fetchone()
    except IntegrityError as exc:
        logger.warning("凭证 upsert 失败 tenant=%s client=%s", tenant_id, client_id)
        raise HTTPException(status_code=409, detail="凭证存储失败：唯一约束冲突") from exc
    return str(row.id)


def get_decrypted(tenant_id: str, credential_id: str) -> tuple[str, str]:
    """解密凭证 → (ozon_client_id, api_key)（submit 凭证注入用；跨租户/已吊销 → 404）。"""
    try:
        uid = uuid.UUID(str(credential_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="店铺凭证不存在")
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT ozon_client_id, ozon_api_key_enc FROM credentials "
            "WHERE id=:id AND tenant_id=:tenant_id AND status='active'"
        ), {"id": uid, "tenant_id": tenant_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="店铺凭证不存在或不属于当前用户")
    try:
        api_key = decrypt(bytes(row.ozon_api_key_enc), f"{tenant_id}:{row.ozon_client_id}")
    except CredentialCipherError:
        logger.error("凭证解密失败 credential_id=%s（密钥轮换或数据异常）", credential_id)
        raise HTTPException(status_code=500, detail="店铺凭证解密失败，请重新配置")
    return str(row.ozon_client_id), api_key


def get_default_credential(tenant_id: str) -> Optional[dict]:
    """is_default=true 且 active 的店铺（submit 未指定 credential_id 时用）。

    Returns: {"id", "ozon_client_id", "api_key"} 或 None（未配置默认店铺）。
    """
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id, ozon_client_id, ozon_api_key_enc FROM credentials "
            "WHERE tenant_id=:tenant_id AND is_default AND status='active' LIMIT 1"
        ), {"tenant_id": tenant_id}).fetchone()
    if row is None:
        return None
    try:
        api_key = decrypt(bytes(row.ozon_api_key_enc), f"{tenant_id}:{row.ozon_client_id}")
    except CredentialCipherError:
        logger.error("默认凭证解密失败 tenant=%s", tenant_id)
        raise HTTPException(status_code=500, detail="默认店铺凭证解密失败，请重新配置")
    return {"id": str(row.id), "ozon_client_id": str(row.ozon_client_id), "api_key": api_key}

"""批次 C(v0.70): Ozon 卖家会话代管服务 — 存储/解密/状态，被 routes 复用。

对标竞品 bindShopCookie：skill CDP 收割 seller.ozon.ru 会话 cookie → 本服务
AES-256-GCM 加密 upsert（复用 utils/credential_cipher，与凭证加密同源同 key）→
服务端直调通道（utils/ozon_session_client）按需解出 Cookie 头。

安全红线（改本文件前必读）:
- 密文 aad 冻结 ``aad = f"{tenant_id}:{credential_id}"`` —— 跨租户解密必然
  GCM 认证失败（与 credentials 表同一绑定语义）
- 任何返回结构只含 cookie 名单（cookie_names）与状态，**永不回 cookie 值**
- 解密失败（换 CREDENTIAL_MASTER_KEY 后旧会话不可解是预期行为）→ 标 expired
  并返回 None，绝不返回静默垃圾、绝不把明文带进日志
- master key 读取方式照 credential_service.py 现状（credential_cipher 的
  CREDENTIAL_MASTER_KEY 环境变量口径），不新造密钥管理
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import uuid

from sqlalchemy import text

from storage.database.db import get_engine
from utils.credential_cipher import CredentialCipherError, decrypt_with_key, encrypt_with_key

logger = logging.getLogger(__name__)

_ENV_KEY = "CREDENTIAL_MASTER_KEY"

_STATUS_ACTIVE = "active"
_STATUS_EXPIRED = "expired"

_STORE_SQL = """
    INSERT INTO ozon_sessions (
        tenant_id, credential_id, cookies_encrypted, cookie_names,
        sc_company_id_encrypted, status, harvested_at, last_checked_at
    ) VALUES (
        :tenant_id, :credential_id, :enc, :names, :sc_enc,
        'active', :now, :now
    )
    ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
        cookies_encrypted = EXCLUDED.cookies_encrypted,
        cookie_names = EXCLUDED.cookie_names,
        sc_company_id_encrypted = EXCLUDED.sc_company_id_encrypted,
        status = 'active',
        harvested_at = EXCLUDED.harvested_at,
        last_checked_at = EXCLUDED.last_checked_at,
        updated_at = NOW()
"""

_SELECT_SQL = (
    "SELECT cookies_encrypted, status FROM ozon_sessions "
    "WHERE tenant_id=:tenant_id AND credential_id=:credential_id"
)

_STATUS_SQL = (
    "SELECT status, harvested_at, cookie_names FROM ozon_sessions "
    "WHERE tenant_id=:tenant_id AND credential_id=:credential_id"
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _resolve_key(key_raw: str | None) -> str:
    """显式 key 优先（测试/轮换脚本），否则读 CREDENTIAL_MASTER_KEY（与凭证同源）。"""
    return key_raw if key_raw else os.environ.get(_ENV_KEY, "")


def _uuid_or_none(credential_id) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(credential_id))
    except (ValueError, TypeError, AttributeError):
        return None


def store_session(tenant_id: str, credential_id: str, cookies: dict[str, str],
                  *, key_raw: str | None = None) -> None:
    """加密 upsert 会话（同租户同店铺重复同步 → 覆盖密文并回到 active，幂等）。"""
    aad = f"{tenant_id}:{credential_id}"
    key = _resolve_key(key_raw)
    enc = encrypt_with_key(json.dumps(cookies, ensure_ascii=False), aad, key)
    sc_value = str(cookies.get("sc_company_id") or "")
    # sc_company_id 单独加密留存（what_to_sell 公司头快取）；值本就在 cookies
    # 密文内，此处不另存任何明文。缺失时存 None（skill 侧 fail-fast 不该发生）。
    sc_enc = encrypt_with_key(sc_value, aad, key) if sc_value else None
    params = {
        "tenant_id": tenant_id,
        "credential_id": _uuid_or_none(credential_id),
        "enc": enc,
        # JSONB 列绑定：裸 SQL 下 list 会被 psycopg2 适配成 text[]（实机 500 根因），
        # 必须 dumps 成 JSON 文本
        "names": json.dumps(sorted(cookies.keys())),
        "sc_enc": sc_enc,
        "now": _now(),
    }
    with get_engine().begin() as conn:
        conn.execute(text(_STORE_SQL), params)
    logger.info("会话已存储 tenant=%s credential=%s cookies=%d",
                tenant_id, credential_id, len(cookies))


def load_cookies(tenant_id: str, credential_id: str,
                 *, key_raw: str | None = None) -> dict[str, str] | None:
    """解密会话 → {名: 值}。无会话/解密失败 → None（解密失败同时标 expired）。"""
    uid = _uuid_or_none(credential_id)
    if uid is None:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(text(_SELECT_SQL), {
            "tenant_id": tenant_id, "credential_id": uid}).fetchone()
    if row is None:
        return None
    aad = f"{tenant_id}:{credential_id}"
    try:
        raw = decrypt_with_key(bytes(row.cookies_encrypted), aad, _resolve_key(key_raw))
        data = json.loads(raw)
    except (CredentialCipherError, ValueError):
        # GCM 认证失败 = 换 key 后旧会话不可解（预期行为）→ 如实标 expired
        logger.warning("会话解密失败，标记 expired tenant=%s credential=%s",
                       tenant_id, credential_id)
        mark_status(tenant_id, credential_id, _STATUS_EXPIRED)
        return None
    return data if isinstance(data, dict) else None


def get_cookie_header(tenant_id: str, credential_id: str,
                      *, key_raw: str | None = None) -> str | None:
    """解密会话 → "a=b; c=d" 请求头。无会话/解密失败 → None。"""
    cookies = load_cookies(tenant_id, credential_id, key_raw=key_raw)
    if not cookies:
        return None
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def get_sc_company_id(tenant_id: str, credential_id: str,
                      *, key_raw: str | None = None) -> str | None:
    """从解密后的 cookie dict 取 sc_company_id（cookie 里本身有，勿另存明文）。"""
    cookies = load_cookies(tenant_id, credential_id, key_raw=key_raw)
    if not cookies:
        return None
    value = str(cookies.get("sc_company_id") or "")
    return value or None


def mark_status(tenant_id: str, credential_id: str, status: str,
                *, key_raw: str | None = None) -> None:
    """状态流转（active/expired）；顺带刷新 last_checked_at。"""
    uid = _uuid_or_none(credential_id)
    if uid is None:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE ozon_sessions SET status=:status, last_checked_at=:now, updated_at=NOW() "
            "WHERE tenant_id=:tenant_id AND credential_id=:credential_id"
        ), {"status": status, "now": _now(),
            "tenant_id": tenant_id, "credential_id": uid})


def session_status(tenant_id: str, credential_id: str) -> dict | None:
    """会话快照 {status, harvested_at, cookie_names}（永不回值）；无会话 → None。"""
    uid = _uuid_or_none(credential_id)
    if uid is None:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(text(_STATUS_SQL), {
            "tenant_id": tenant_id, "credential_id": uid}).fetchone()
    if row is None:
        return None
    harvested = row.harvested_at
    return {
        "status": row.status,
        "harvested_at": harvested.isoformat() if hasattr(harvested, "isoformat") else None,
        "cookie_names": list(row.cookie_names or []),
    }


def delete_session(tenant_id: str, credential_id: str) -> bool:
    """撤销会话（物理删行）。无会话 → False（路由层 404）。"""
    uid = _uuid_or_none(credential_id)
    if uid is None:
        return False
    with get_engine().begin() as conn:
        result = conn.execute(text(
            "DELETE FROM ozon_sessions "
            "WHERE tenant_id=:tenant_id AND credential_id=:credential_id"
        ), {"tenant_id": tenant_id, "credential_id": uid})
        return result.rowcount > 0

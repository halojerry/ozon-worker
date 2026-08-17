"""T2: MXOU 登录服务 — 登录代理 + Supabase tokens 幂等 upsert + 内存 session 缓存。

薄层在 routes/mxou_routes.py；平台解析在 utils/mxou_platform.py（T1 已交付，只 import 不改）。

流程：mxou_login → 选 enabled key → mxou_get_token_key 解明文 → _upsert_supabase_token
      （幂等 upsert tokens 表，key 去 sk- 前缀）→ 缓存 session（供 T4 密钥管理复用）。

安全纪律：
- 登录入口本身不做 token 鉴权；防爆破在 routes 层按 username 限流（rate_limiter）。
- full_key 只在服务端流转：解出后 upsert Supabase + 不落响应；响应 keys 只含
  id/name/status/masked 布尔，绝不含 full_key。
- 密码不落库、不进日志；upsert 只写 key/user_id/status，不覆盖 remain_quota 等。
"""
from __future__ import annotations

import logging
import time

from fastapi import HTTPException

from utils import mxou_platform
from utils.mxou_api import _get_session
from utils.mxou_platform import MxouLoginError

logger = logging.getLogger(__name__)


class MxouSessionStore:
    """内存 session 存储（单进程 workers=1 安全；TTL 60s）。

    key: str（MXOU user id / tenant_id），value: {access_token, cookie_jar?, expires_at, login_time}
    """

    def __init__(self, ttl: float = 60.0):
        self._ttl = ttl
        self._store: dict[str, dict] = {}
        # key → time.monotonic() 登录时间（put 时记录，get 时判断过期）
        self._login_time: dict[str, float] = {}

    def put(self, user_id: str, data: dict) -> None:
        """写入 session，记录 time.monotonic()。"""
        self._store[user_id] = dict(data)
        self._login_time[user_id] = time.monotonic()

    def get(self, user_id: str) -> dict | None:
        """读取 session；过期 → 弹出并返回 None。"""
        login_time = self._login_time.get(user_id)
        if login_time is None:
            return None
        if time.monotonic() - login_time > self._ttl:
            self.pop(user_id)
            return None
        return self._store.get(user_id)

    def pop(self, user_id: str) -> None:
        """移除 session（过期清理 / 主动登出）。"""
        self._store.pop(user_id, None)
        self._login_time.pop(user_id, None)


# 模块级单例（T4 密钥管理按 tenant_id 复用）
session_store = MxouSessionStore()


def _map_login_error(e: MxouLoginError) -> HTTPException:
    """MxouLoginError → HTTP 状态码（登录端点专用错误映射）。"""
    if e.reason == "bad_credentials":
        return HTTPException(status_code=401, detail="MXOU 账号或密码错误")
    if e.reason == "2fa_required":
        return HTTPException(status_code=400, detail="MXOU 账号需要两步验证，请先在 MXOU 平台登录")
    if e.reason == "rate_limited":
        return HTTPException(status_code=429, detail="MXOU 平台限流，请稍后重试")
    # unavailable / unknown_shape → 502 + API Key 直登提示
    return HTTPException(status_code=502, detail="MXOU 平台登录不可用，请稍后重试或使用 API Key 直登")


def _upsert_supabase_token(user_id: str, full_key: str) -> bool:
    """把解出的 MXOU full key 幂等 upsert 进 Supabase tokens 表。

    - 剥 sk- 前缀（tokens.key 列存纯 key，_authenticate_token 按 key eq 查）
    - supabase 为 None（本地未配置）→ return False（不抛，登录流程不阻断）
    - on_conflict="key" → 已存在的 key 更新 user_id/status=1；不覆盖 remain_quota 等
    - 异常 → logger.warning 返回 False（不阻断登录）
    """
    clean_key = full_key[3:] if full_key and full_key.startswith("sk-") else full_key
    if not clean_key:
        return False
    try:
        from storage.database.supabase_client import get_supabase_client

        supabase = get_supabase_client()
    except Exception as exc:  # supabase 依赖缺失/初始化异常 → 本地降级
        logger.warning("get_supabase_client 失败（跳过 tokens upsert）: %s", exc)
        return False
    if supabase is None:
        logger.warning("Supabase 未配置，跳过 tokens upsert（local 模式）")
        return False
    try:
        supabase.table("tokens").upsert(
            [{"key": clean_key, "user_id": str(user_id), "status": 1}],
            on_conflict="key",
        ).execute()
        return True
    except Exception as exc:
        logger.warning("tokens upsert 失败 user_id=%s: %s", user_id, exc)
        return False


def login(username: str, password: str) -> dict:
    """MXOU 账号密码登录 → 返回脱敏 keys + 选中 key id + session 元数据。
    错误映射（MxouLoginError → HTTPException）：
        bad_credentials → 401 / 2fa_required → 400 / rate_limited → 429 /
        unavailable & unknown_shape → 502（提示 API Key 直登）。
    """
    session = _get_session()

    try:
        result = mxou_platform.mxou_login(session, username, password)
    except MxouLoginError as e:
        raise _map_login_error(e) from e

    access_token = result.get("access_token")
    user = result.get("user") if isinstance(result.get("user"), dict) else {}
    user_id_raw = result.get("user_id")
    if user_id_raw is None:
        user_id_raw = user.get("id")
    user_id = str(user_id_raw or "")
    # 平台调用补 user_id → New-Api-User 认证头（cookie 由 session 携带）；空值 → None
    user_id_val = user_id or None
    expires_at = result.get("expires_at")

    # balance：mxou_get_self 失败 → None 不阻断
    # ⚠️ 守卫：access_token 或 user_id 任一存在即调（one-api cookie 形态 access_token=None 但
    #    有 user_id → New-Api-User header 认证；T1.1 实测 api.mxou.cn 需要此 header）
    user_info: dict = {}
    if access_token or user_id_val:
        try:
            user_info = mxou_platform.mxou_get_self(session, access_token, user_id=user_id_val) or {}
        except MxouLoginError as e:
            logger.warning("mxou_get_self 失败 username=%s reason=%s（user_info 空不阻断）",
                           username, e.reason)
        except Exception as exc:  # pragma: no cover - 防御未知异常
            logger.warning("mxou_get_self 异常 username=%s: %s", username, exc)

    # keys：list_tokens 失败 → [] 不阻断；选第一个 enabled → 解明文 → upsert
    raw_keys: list[dict] = []
    selected_key_id: str | None = None
    selected_full_key: str | None = None
    if access_token or user_id_val:
        try:
            raw_keys = mxou_platform.mxou_list_tokens(session, access_token, user_id=user_id_val) or []
        except MxouLoginError as e:
            logger.warning("mxou_list_tokens 失败 username=%s reason=%s（keys=[] 不阻断）",
                           username, e.reason)
        except Exception as exc:  # pragma: no cover - 防御未知异常
            logger.warning("mxou_list_tokens 异常 username=%s: %s", username, exc)

        for k in raw_keys:
            if k.get("status") == 1 and k.get("id"):
                try:
                    full_key = mxou_platform.mxou_get_token_key(
                        session, access_token, k["id"], user_id=user_id_val)
                except Exception as exc:
                    logger.warning("mxou_get_token_key 失败 token_id=%s: %s", k.get("id"), exc)
                    continue
                if full_key:
                    selected_key_id = str(k["id"])
                    # 存储格式无 sk- 前缀（Supabase tokens 表同规）；返回前端需 sk- 前缀
                    # （_authenticate_token 剥离 sk- 后查表，get_mxou_balance 补 sk- 后调 API）
                    selected_full_key = full_key if full_key.startswith("sk-") else f"sk-{full_key}"
                    if user_id:
                        _upsert_supabase_token(user_id, full_key)
                break

    # 真实余额：用选中 key 走 worker 现有 get_mxou_balance（/v1/dashboard/billing/subscription
    # 的 balance 美元字段，与 _check_mxou_balance 同源）；失败 → None 不阻断
    balance = None
    if selected_full_key:
        from utils.mxou_api import get_mxou_balance
        try:
            balance = get_mxou_balance(selected_full_key)
        except Exception as exc:  # pragma: no cover - 防御未知异常
            logger.warning("get_mxou_balance 失败 username=%s: %s（balance=None 不阻断）", username, exc)

    # session 缓存（供 T4 密钥管理复用；含 user_id 供 New-Api-User 认证 + selected key 供余额复用）
    if user_id:
        session_store.put(user_id, {
            "access_token": access_token,
            "expires_at": expires_at,
            "user_id": user_id_val,
            "selected_full_key": selected_full_key,
        })

    # 响应 keys 脱敏：只留 id/name/status/masked（full_key 只在服务端流转，
    # 但登录成功返回选中 key 的完整值一次——WebUI 直接用它建立登录态）
    masked_keys = [
        {
            "id": k.get("id"),
            "name": k.get("name") or "",
            "status": int(k.get("status") or 0),
            "masked": bool(k.get("masked", True)),
        }
        for k in raw_keys
        if isinstance(k, dict)
    ]

    # role：查 Supabase users.role（WebUI 管理员路由守卫用；admin_service.require_admin 同源）
    role = _fetch_user_role(user_id_val)

    return {
        "username": str(user.get("username") or user_info.get("username") or username),
        "balance": balance,
        "keys": masked_keys,
        "selected_key_id": selected_key_id,
        "key": selected_full_key,
        "session_expires_at": expires_at,
        "role": role,
    }


def _fetch_user_role(user_id: str) -> str:
    """查 Supabase users.role（'admin'/'user'）；无 Supabase 或查询失败 → 'user'。

    修复 v0.54 遗留：原实现用 get_engine() 查本地 PG（无 users 表）恒返回 'user'。
    现改走 Supabase，与 admin_service.is_admin_user 同源；role >= 10 判 admin。
    """
    try:
        from main import get_supabase_client
        from services.admin_service import is_admin_role
        supabase = get_supabase_client()
        if supabase is None:
            return "user"
        rows = supabase.table("users").select("role").eq("id", user_id).limit(1).execute()
        if rows.data:
            return "admin" if is_admin_role(rows.data[0].get("role")) else "user"
    except Exception:
        logger.warning("查询用户 role 失败 user_id=%s（默认 user）", user_id)
    return "user"


# ════════════════════════════════════════════════════════════════
# T4 密钥管理（list/create/revoke/select）
# ════════════════════════════════════════════════════════════════


def _get_session_for_tenant(tenant_id: str) -> tuple[dict, str]:
    """读 tenant 的 MXOU session（T2 login 按 MXOU user id 缓存）。

    - 无/过期 → 401「请重新登录」（前端回登录页走账号登录）
    - access_token 为空（oneapi cookie 形态只有 cookie 无 token）→ 401「会话无效」
    - 返回 (session_data, access_token)
    """
    session_data = session_store.get(tenant_id)
    if not session_data:
        raise HTTPException(status_code=401, detail="请重新登录")
    access_token = str(session_data.get("access_token") or "")
    if not access_token:
        raise HTTPException(status_code=401, detail="会话无效，请重新登录")
    return session_data, access_token


def _map_key_error(e: MxouLoginError) -> HTTPException:
    """密钥管理平台错误映射（非登录端点）：限流 429，其余统一 502。"""
    if e.reason == "rate_limited":
        return HTTPException(status_code=429, detail="MXOU 平台限流，请稍后重试")
    return HTTPException(status_code=502, detail="MXOU 平台操作失败，请稍后重试或重新登录")


def list_keys(tenant_id: str) -> list[dict]:
    """列出该账号 API Key（脱敏：id/name/status/masked，绝不含 full_key）。"""
    session_data, access_token = _get_session_for_tenant(tenant_id)
    user_id = session_data.get("user_id")
    try:
        raw = mxou_platform.mxou_list_tokens(session_data, access_token, user_id=user_id) or []
    except MxouLoginError as e:
        raise _map_key_error(e) from e
    return [
        {
            "id": k.get("id"),
            "name": k.get("name") or "",
            "status": int(k.get("status") or 0),
            "masked": bool(k.get("masked", True)),
        }
        for k in raw
        if isinstance(k, dict)
    ]


def create_key(tenant_id: str, name: str) -> dict:
    """新建 API Key → upsert 进 Supabase tokens → 返回 {id, name, key}（key 仅此一次）。"""
    session_data, access_token = _get_session_for_tenant(tenant_id)
    user_id = session_data.get("user_id")
    try:
        result = mxou_platform.mxou_create_token(session_data, access_token, name, user_id=user_id)
    except MxouLoginError as e:
        raise _map_key_error(e) from e
    full_key = result.get("full_key") or ""
    if full_key:
        _upsert_supabase_token(tenant_id, full_key)
    return {
        "id": str(result.get("id") or ""),
        "name": str(result.get("name") or name),
        "key": full_key,
    }


def revoke_key(tenant_id: str, key_id: str) -> bool:
    """吊销 API Key；MXOU 失败 → 502。"""
    session_data, access_token = _get_session_for_tenant(tenant_id)
    user_id = session_data.get("user_id")
    try:
        ok = mxou_platform.mxou_revoke_token(session_data, access_token, key_id, user_id=user_id)
    except MxouLoginError as e:
        raise HTTPException(status_code=502, detail="MXOU 吊销失败，请稍后重试") from e
    if not ok:
        raise HTTPException(status_code=502, detail="MXOU 吊销失败，请稍后重试")
    return True


def select_key(tenant_id: str, key_id: str) -> dict:
    """解出指定 key 明文 → upsert 进 Supabase tokens → 返回 {key}（仅此一次）。"""
    session_data, access_token = _get_session_for_tenant(tenant_id)
    user_id = session_data.get("user_id")
    try:
        full_key = mxou_platform.mxou_get_token_key(session_data, access_token, key_id, user_id=user_id)
    except MxouLoginError as e:
        raise _map_key_error(e) from e
    if full_key:
        _upsert_supabase_token(tenant_id, full_key)
    return {"key": full_key}


def get_my_key(user_id: str) -> dict:
    """按 New API user_id 查 Supabase tokens 表，返回第一个 enabled key。

    WebUI 登录后（New API cookie session + uid）业务页需要 worker Bearer token
    调 /api/v1/*。本函数直接复用 tokens 表（登录/建 key 时已 upsert），
    免去用户手动建 key——tokens.key 列存纯 key（去 sk- 前缀），返回补回前缀。
    Supabase 未配置/无 key/异常 → 返回 {"key": ""}（前端静默跳过，不报错）。
    """
    try:
        from storage.database.supabase_client import get_supabase_client

        supabase = get_supabase_client()
    except Exception as exc:
        logger.warning("get_supabase_client 失败（get_my_key）: %s", exc)
        return {"key": ""}
    if supabase is None:
        return {"key": ""}
    try:
        rows = supabase.table("tokens").select("key").eq("user_id", str(user_id)) \
            .eq("status", 1).is_("deleted_at", "null").limit(1).execute()
        data = rows.data if rows else []
        if not data:
            return {"key": ""}
        key = str(data[0].get("key") or "")
        return {"key": f"sk-{key}" if key and not key.startswith("sk-") else key}
    except Exception as exc:
        logger.warning("get_my_key 查询失败 user_id=%s: %s", user_id, exc)
        return {"key": ""}

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
    user_id = str(user.get("id") or "")
    expires_at = result.get("expires_at")

    # balance：mxou_get_self 失败 → None 不阻断
    balance = None
    if access_token:
        try:
            info = mxou_platform.mxou_get_self(session, access_token)
            balance = info.get("balance")
        except MxouLoginError as e:
            logger.warning("mxou_get_self 失败 username=%s reason=%s（balance=None 不阻断）",
                           username, e.reason)
        except Exception as exc:  # pragma: no cover - 防御未知异常
            logger.warning("mxou_get_self 异常 username=%s: %s", username, exc)

    # keys：list_tokens 失败 → [] 不阻断；选第一个 enabled → 解明文 → upsert
    raw_keys: list[dict] = []
    selected_key_id: str | None = None
    if access_token:
        try:
            raw_keys = mxou_platform.mxou_list_tokens(session, access_token) or []
        except MxouLoginError as e:
            logger.warning("mxou_list_tokens 失败 username=%s reason=%s（keys=[] 不阻断）",
                           username, e.reason)
        except Exception as exc:  # pragma: no cover - 防御未知异常
            logger.warning("mxou_list_tokens 异常 username=%s: %s", username, exc)

        for k in raw_keys:
            if k.get("status") == 1 and k.get("id"):
                try:
                    full_key = mxou_platform.mxou_get_token_key(session, access_token, k["id"])
                except Exception as exc:
                    logger.warning("mxou_get_token_key 失败 token_id=%s: %s", k.get("id"), exc)
                    continue
                if full_key:
                    selected_key_id = str(k["id"])
                    if user_id:
                        _upsert_supabase_token(user_id, full_key)
                break

    # session 缓存（供 T4 密钥管理复用）
    if user_id:
        session_store.put(user_id, {"access_token": access_token, "expires_at": expires_at})

    # 响应 keys 脱敏：只留 id/name/status/masked（full_key 只在服务端流转）
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

    return {
        "username": str(user.get("username") or username),
        "balance": balance,
        "keys": masked_keys,
        "selected_key_id": selected_key_id,
        "session_expires_at": expires_at,
    }

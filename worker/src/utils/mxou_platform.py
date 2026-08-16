"""MXOU 平台防御式解析客户端（newapi/one-api 架构，多形态兼容）。

MXOU 是 newapi 定制 fork，响应形态多变，防御解析是核心价值：

- 登录可能返回 ``data.access_token`` (newapi_jwt) 或纯 session cookie (oneapi_cookie)；
  且 **HTTP 200 但 success=false 表示凭据错误**——不能只信 status。
- token 列表可能分页 (``data.items``)、数组 (``data``) 或双层包裹 (``data.data``)。
- key 可能脱敏 (``sk-1234**********abcd``)，需要 ``POST /api/token/{id}/key`` 解出明文。
- ``/api/user/self`` 可能回传 password/access_token——白名单字段剥离。

本模块为**纯函数层**：所有函数接收注入的 session（鸭子类型，mock 友好），
不新建 session、不 import requests 的具体类型、不依赖 DB、不产生真实网络调用。

密码/access_token 永不进日志；异常统一 ``MxouLoginError(reason, message)``，
message 不含任何敏感值。
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

# base URL 常量（可被环境变量 MXOU_BASE 覆盖，测试用假 base）
MXOU_BASE = "https://api.mxou.cn"

_REQUEST_TIMEOUT = 15

# /api/user/self 白名单（输入字段；quota/balance 输出时合并为 balance）
_SELF_WHITELIST = {"id", "username", "display_name", "role", "status"}
# 防御剥离字段（即使 data 回传也绝不外泄）
_SENSITIVE_KEYS = ("password", "access_token", "session")

_REASON_MESSAGES = {
    "bad_credentials": "MXOU 账号或密码错误",
    "unavailable": "MXOU 平台不可用",
    "unknown_shape": "MXOU 登录响应形态未知",
    "2fa_required": "MXOU 账号需要二次验证",
    "rate_limited": "MXOU 请求被限流",
}

# 脱敏 key 判定模式：sk-<4位>****...（如 sk-1234**********abcd）
_MASKED_KEY_PATTERN = re.compile(r"sk-\w{4}\*{4,}")


class MxouLoginError(Exception):
    """reason ∈ {bad_credentials, unavailable, unknown_shape, 2fa_required, rate_limited}"""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        super().__init__(message or _REASON_MESSAGES.get(reason, reason))


def _base_url() -> str:
    return os.environ.get("MXOU_BASE", MXOU_BASE)


def _auth_headers(access_token: str | None, user_id: str | int | None = None,
                  content_type_json: bool = False) -> dict:
    """认证头：有 access_token → Bearer；有 user_id → New-Api-User header（cookie 由 session 携带）。

    两者都有 → 都带（newapi 兼容）；都无 → 空 dict。真实平台（one-api cookie 形态）
    只认 New-Api-User，Bearer 是 newapi 形态的兼容路径。
    """
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if user_id is not None:
        headers["New-Api-User"] = str(user_id)
    if content_type_json:
        headers["Content-Type"] = "application/json"
    return headers


def _call(session, method: str, url: str, *, headers: dict, json=None, params=None):
    """统一网络调用：网络异常 → MxouLoginError(unavailable)。

    鸭子类型：session 需支持 .post/.get/.delete，返回 .status_code / .json()。
    """
    kwargs = {"headers": headers, "timeout": _REQUEST_TIMEOUT}
    if json is not None:
        kwargs["json"] = json
    if params is not None:
        kwargs["params"] = params
    try:
        return getattr(session, method)(url, **kwargs)
    except MxouLoginError:
        raise
    except Exception as e:
        raise MxouLoginError("unavailable", f"MXOU 请求失败: {type(e).__name__}") from e


def _safe_json(resp) -> dict:
    """响应解析：JSONDecodeError / 非 dict → MxouLoginError(unavailable)。"""
    try:
        body = resp.json()
    except (ValueError, AttributeError):
        raise MxouLoginError("unavailable", "MXOU 响应不是合法 JSON") from None
    if not isinstance(body, dict):
        raise MxouLoginError("unavailable", "MXOU 响应结构异常（非 JSON 对象）")
    return body


def _is_masked(key: str) -> bool:
    """脱敏判定：key 含 '*' 或 '****' 或匹配 sk-\\w{4}\\*{4,}。"""
    if not key:
        return False
    if "*" in key:
        return True
    return bool(_MASKED_KEY_PATTERN.search(key))


# ════════════════════════════════════════════════════════════════
# 登录
# ════════════════════════════════════════════════════════════════

def mxou_login(session, username: str, password: str) -> dict:
    """登录 MXOU 平台。

    返回 {"access_token", "user_id", "user", "expires_at", "shape"}。
    user_id 取自 data.id（top-level）或 user.id 兜底——T2 用它补 New-Api-User 认证头。
    shape ∈ {"newapi_jwt", "oneapi_cookie"}，均不匹配抛 unknown_shape。

    解析纪律：先看 JSON success 字段（HTTP 200 但 success=false → bad_credentials）；
    429 → rate_limited；5xx/网络异常 → unavailable；4xx（非 429）→ bad_credentials。
    """
    url = f"{_base_url()}/api/user/login"
    logger.info("mxou login: POST /api/user/login")
    resp = _call(session, "post", url,
                 headers={"Content-Type": "application/json"},
                 json={"username": username, "password": password})

    status = getattr(resp, "status_code", None)
    # 429 优先判定（限流响应可能非 JSON）
    if status == 429:
        raise MxouLoginError("rate_limited", "MXOU 登录请求被限流 (HTTP 429)")

    body = _safe_json(resp)

    # 解析纪律：先看 success 字段（不信任 status）
    if body.get("success") is False:
        raise MxouLoginError("bad_credentials", "MXOU 账号或密码错误（success=false）")

    if status is not None and status >= 500:
        raise MxouLoginError("unavailable", f"MXOU 服务不可用 (HTTP {status})")

    # 4xx（非 429）一律归凭据错误，即使 body 无 success 字段
    if status is not None and 400 <= status < 500:
        raise MxouLoginError("bad_credentials", f"MXOU 账号或密码错误 (HTTP {status})")

    data = body.get("data")
    if not isinstance(data, dict):
        data = {}

    # 2FA
    if data.get("require_2fa"):
        raise MxouLoginError("2fa_required", "MXOU 账号需要二次验证 (2FA)")

    # token 阶梯：access_token → token → session_token → None（cookie 形态）
    token_val: str | None = None
    for field in ("access_token", "token", "session_token"):
        v = data.get(field)
        if isinstance(v, str) and v:
            token_val = v
            break

    session_obj = data.get("session")
    expires_at = session_obj.get("expires_at") if isinstance(session_obj, dict) else None
    if expires_at is None:
        expires_at = data.get("expires_at")

    user = data.get("user")
    if not isinstance(user, dict):
        user = None

    # user_id：真实平台 data.id（top-level）优先；user 子对象兜底
    user_id = data.get("id")
    if user_id is None and isinstance(user, dict):
        user_id = user.get("id")
    if not isinstance(user_id, (str, int)):
        user_id = None

    # 形态判定：有 token 且 session.expires_at → newapi_jwt；
    # 无任何 token 字段但有 user 或 data.id → oneapi_cookie（access_token=None）
    shape = None
    if token_val and expires_at:
        shape = "newapi_jwt"
    elif token_val is None and (user is not None or user_id is not None):
        shape = "oneapi_cookie"

    if shape is None:
        # 附原始 keys 摘要（不含值）
        raise MxouLoginError(
            "unknown_shape",
            "MXOU 登录响应形态未知: "
            f"top_keys={sorted(body.keys())}, data_keys={sorted(data.keys())}",
        )

    logger.info("mxou login: success shape=%s", shape)
    return {
        "access_token": token_val,
        "user_id": user_id,
        "user": user,
        "expires_at": expires_at,
        "shape": shape,
    }


# ════════════════════════════════════════════════════════════════
# 用户信息
# ════════════════════════════════════════════════════════════════

def mxou_get_self(session, access_token: str | None = None, user_id: str | int | None = None) -> dict:
    """查询用户信息。

    白名单字段 {id, username, display_name, role, status, balance}：
    quota/balance 取存在的那个（都无 → None）。绝不允许 password/access_token
    出现在返回值（防御：从 data 中剥掉）。
    """
    url = f"{_base_url()}/api/user/self"
    logger.info("mxou self: GET /api/user/self")
    resp = _call(session, "get", url, headers=_auth_headers(access_token, user_id))

    status = getattr(resp, "status_code", None)
    body = _safe_json(resp)
    if status is not None and status != 200:
        raise MxouLoginError("unavailable", f"MXOU 查询用户信息失败 (HTTP {status})")

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        # 部分 fork 直接把用户对象作为 body 返回
        data = body

    result: dict = {}
    for field in _SELF_WHITELIST:
        if field in data:
            result[field] = data[field]

    quota = data.get("quota")
    balance = data.get("balance")
    if quota is not None:
        result["balance"] = quota
    elif balance is not None:
        result["balance"] = balance
    else:
        result["balance"] = None

    # 防御：剥掉敏感字段（绝不外泄）
    for key in _SENSITIVE_KEYS:
        result.pop(key, None)
    return result


# ════════════════════════════════════════════════════════════════
# token 列表 / 解钥 / 新建 / 吊销
# ════════════════════════════════════════════════════════════════

def mxou_list_tokens(session, access_token: str | None = None, user_id: str | int | None = None) -> list[dict]:
    """列出该账号下 API Key。

    兼容三种形态：分页 (data.items) / 数组 (data) / 双层包裹 (data.data)。
    每个 item 归一化为 {"id", "name", "status", "masked", "full_key": None}。
    """
    url = f"{_base_url()}/api/token/"
    logger.info("mxou tokens: GET /api/token/")
    resp = _call(session, "get", url, headers=_auth_headers(access_token, user_id))

    status = getattr(resp, "status_code", None)
    body = _safe_json(resp)
    if status is not None and status != 200:
        raise MxouLoginError("unavailable", f"MXOU 查询 token 列表失败 (HTTP {status})")

    data = body.get("data") if isinstance(body, dict) else None

    items = None
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            items = inner          # 双层包裹 data.data
        elif isinstance(data.get("items"), list):
            items = data["items"]  # 分页 data.items
    elif isinstance(data, list):
        items = data               # 数组形态

    if items is None:
        return []

    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        key_str = key if isinstance(key, str) else ""
        normalized.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "status": int(item.get("status") or 0),
            "masked": _is_masked(key_str),
            "full_key": None,
        })
    return normalized


def mxou_get_token_key(session, access_token: str | None = None, token_id: str | None = None,
                       user_id: str | int | None = None) -> str:
    """POST /api/token/{id}/key 解出明文 key。解析 data.key 或 data 是字符串。失败 → unavailable。"""
    url = f"{_base_url()}/api/token/{token_id}/key"
    logger.info("mxou token key: POST /api/token/{token_id}/key")
    resp = _call(session, "post", url, headers=_auth_headers(access_token, user_id))

    status = getattr(resp, "status_code", None)
    body = _safe_json(resp)
    if status is not None and status != 200:
        raise MxouLoginError("unavailable", f"MXOU 解出 token key 失败 (HTTP {status})")

    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict):
        key = data.get("key")
        if isinstance(key, str) and key:
            return key
    raise MxouLoginError("unavailable", "MXOU 解出 token key 失败: 响应无 key 字段")


def mxou_create_token(session, access_token: str | None = None, name: str | None = None,
                      user_id: str | int | None = None) -> dict:
    """创建 API Key（newapi 语义：remain_quota=-1 无限额度）。返回 {"id", "name", "full_key"}。"""
    url = f"{_base_url()}/api/token"
    logger.info("mxou create token: POST /api/token")
    resp = _call(session, "post", url,
                 headers=_auth_headers(access_token, user_id, content_type_json=True),
                 json={"name": name, "remain_quota": -1})

    status = getattr(resp, "status_code", None)
    body = _safe_json(resp)
    if status is not None and status != 200:
        raise MxouLoginError("unavailable", f"MXOU 创建 token 失败 (HTTP {status})")

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        data = body

    full_key = data.get("key") or data.get("full_key") or ""
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or name),
        "full_key": full_key if isinstance(full_key, str) else "",
    }


def mxou_revoke_token(session, access_token: str | None = None, token_id: str | None = None,
                      user_id: str | int | None = None) -> bool:
    """DELETE /api/token/{id} 吊销 API Key；2xx → True。"""
    url = f"{_base_url()}/api/token/{token_id}"
    logger.info("mxou revoke token: DELETE /api/token/{token_id}")
    resp = _call(session, "delete", url, headers=_auth_headers(access_token, user_id))

    status = getattr(resp, "status_code", None)
    if status is not None and 200 <= status < 300:
        return True
    raise MxouLoginError("unavailable", f"MXOU 吊销 token 失败 (HTTP {status})")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 MXOU 平台（api.mxou.cn，newapi/one-api 定制 fork）登录相关端点响应形态。

T0 一次性只读探测脚本（用户手动运行，凭真实账号），供防御式解析器（T1 mxou_platform）设计参考。

用途：
- 确认 POST /api/user/login 是「new-api >= rc.22 JWT 形态」（data.access_token + session.expires_at）、
  「one-api cookie 形态」（无 token 字段，靠 Set-Cookie）还是「未知形态」；
- 确认 GET /api/token/ 是「data.items 分页」/「data 数组」/「未知」；key 是否服务端脱敏；
- 确认 POST /api/token/{id}/key 能否取回完整 key（只打印前 6 位 + 后 4 位）。

纪律：
- 只读：只发 GET/POST 探测请求，不修改任何平台数据；
- 脱敏：password/access_token/token 等字段值一律替换为 ***；完整 API key / JWT 只显示前 6 位 + 后 4 位；
- 不落盘：不写任何文件、不持久化会话；
- 仅依赖 requests（缺失时回退标准库 urllib），不调 worker 内部模块。

用法：
    python3.12 worker/scripts/probe_mxou_login.py --username <账号> --password <密码>
    python3.12 worker/scripts/probe_mxou_login.py --base https://api.mxou.cn --username <账号> --password <密码> --verify-ssl
（--password 缺省时读环境变量 MXOU_PASSWORD；--verify-ssl 默认 false，即默认用 unverified SSL context）
"""

import argparse
import json
import os
import re
import ssl
import sys
import time

try:
    import requests  # noqa: F401
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.error as _urlerr
    import urllib.request as _urlreq

# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

_SENSITIVE_FIELD = re.compile(
    r"^(password|pwd|passwd|access_token|session_token|session_id|apikey|api_key|secret|token)$",
    re.I,
)
_FULL_SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_MASKED_KEY_RE = re.compile(r"(sk-\w{4}\*+\w{4})|(\*{4,})")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_.-]{15,}")


def _mask_key(v):
    """完整 sk- key -> 前6位 + *** + 后4位；服务端已脱敏的 key 原样保留（本身安全）。"""
    if _MASKED_KEY_RE.search(v):
        return v
    m = _FULL_SK_RE.search(v)
    if m:
        k = m.group(0)
        return v[: m.start()] + k[:6] + "***" + k[-4:] + v[m.end():]
    return v


def _redact_value(v):
    if isinstance(v, str):
        if _FULL_SK_RE.search(v) or _MASKED_KEY_RE.search(v):
            return _mask_key(v)
        if _JWT_RE.search(v):
            return "eyJ***"  # JWT（access token）不展示
        return v
    return v


def _redact(obj):
    """递归脱敏：敏感字段名 -> ***；长 token 字符串 -> 掩码。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SENSITIVE_FIELD.match(k):
                out[k] = "***"
            elif isinstance(v, (dict, list)):
                out[k] = _redact(v)
            elif isinstance(v, str):
                out[k] = _redact_value(v)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, str):
        return _redact_value(obj)
    return obj


# ---------------------------------------------------------------------------
# HTTP 传输（requests 优先，urllib 回退；会话级 cookie 自动携带）
# ---------------------------------------------------------------------------

_URLLIB_VERIFY = False
_urllib_opener = None
_requests_session = None


def _get_requests_session():
    global _requests_session
    if _requests_session is None:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _requests_session = requests.Session()
    return _requests_session


def _get_urllib_opener():
    global _urllib_opener
    if _urllib_opener is None:
        import http.cookiejar
        ctx = ssl.create_default_context() if _URLLIB_VERIFY else ssl._create_unverified_context()
        cj = http.cookiejar.CookieJar()
        _urllib_opener = _urlreq.build_opener(
            _urlreq.HTTPSHandler(context=ctx),
            _urlreq.HTTPCookieProcessor(cj),
        )
    return _urllib_opener


def _http(method, url, headers=None, json_body=None, timeout=20, verify_ssl=False):
    """返回 (status, headers_dict, body_bytes, set_cookie_names)。

    连接级错误（DNS/超时/SSL）直接抛异常由调用方捕获；4xx/5xx 不抛，作为正常结果返回。
    """
    headers = headers or {}
    if HAS_REQUESTS:
        sess = _get_requests_session()
        fn = sess.post if method == "POST" else sess.get
        if json_body is not None:
            resp = fn(url, headers=headers, json=json_body, timeout=timeout, verify=verify_ssl)
        else:
            resp = fn(url, headers=headers, timeout=timeout, verify=verify_ssl)
        sc_names = []
        raw_headers = getattr(resp.raw, "headers", None)
        if raw_headers is not None:
            sc_names = [h.split("=", 1)[0].strip() for h in raw_headers.getlist("Set-Cookie")]
        return resp.status_code, dict(resp.headers), resp.content, sc_names

    # urllib 回退
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
    req = _urlreq.Request(url, data=data, headers=headers, method=method)
    opener = _get_urllib_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (
                resp.getcode(),
                dict(resp.headers),
                resp.read(),
                [h.split("=", 1)[0].strip() for h in resp.headers.get_all("Set-Cookie", [])],
            )
    except _urlerr.HTTPError as e:
        return (
            e.code,
            dict(e.headers),
            e.read(),
            [h.split("=", 1)[0].strip() for h in e.headers.get_all("Set-Cookie", [])],
        )


# ---------------------------------------------------------------------------
# 响应解析 / 形态分类
# ---------------------------------------------------------------------------

_LOGIN_KNOWN_TOP = {"success", "message", "data", "error", "code", "trace_id", "request_id"}
_LOGIN_KNOWN_DATA = {
    "access_token", "token", "session_token", "session", "user", "username", "id", "role",
    "status", "quota", "used_quota", "balance", "expire_date", "expires_at", "avatar",
    "last_accessed_time", "name", "request_count", "account", "created_time",
}
_TOKEN_KNOWN_TOP = {"success", "message", "data", "error"}
_TOKEN_KNOWN_DATA = {"items", "total", "page", "page_size", "count", "pages"}
_TOKEN_KNOWN_ITEM = {
    "id", "name", "key", "masked_key", "status", "created_time", "accessed_time",
    "expire_time", "unlimited_quota", "remain_quota", "used_quota", "model_limits",
    "group", "is_anonymous", "priority", "auto_ban", "last_used_time", "editable", "deletable",
}
_SELF_KNOWN_TOP = {"success", "message", "data", "error", "code"}
_SELF_KNOWN_DATA = {
    "id", "username", "name", "role", "status", "quota", "used_quota", "balance",
    "request_count", "expire_date", "avatar", "last_accessed_time", "created_time",
    "email", "aff_code", "wechat_id", "github_id", "telegram_id", "openai_api_key",
}


def _report_unmapped(title, body, layer_specs):
    """layer_specs: [(path_tuple, known_set), ...]，逐层打印未映射字段。"""
    for path, known in layer_specs:
        node = body
        for p in path:
            if not isinstance(node, dict) or p not in node:
                node = None
                break
            node = node[p]
        if node is None:
            continue
        if isinstance(node, dict):
            actual = set(node.keys())
        elif isinstance(node, list):
            actual = set()
            for x in node:
                if isinstance(x, dict):
                    actual |= set(x.keys())
        else:
            continue
        extra = actual - known
        if extra:
            label = "顶层" if not path else "/".join(path)
            print(f"    未映射字段({label}): {sorted(extra)}")


def _extract_access_token(body):
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    for name in ("access_token", "token", "session_token"):
        v = data.get(name)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None


def _extract_token_id(body):
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    items = data.get("items") if isinstance(data, dict) else data
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0].get("id")
    return None


def _classify_login(body, set_cookie_names):
    """login 形态：new-api JWT / one-api cookie / 未知。"""
    if not isinstance(body, dict):
        return "未知形态（响应非 JSON）"
    data = body.get("data")
    tok = _extract_access_token(body)
    has_expires = False
    if isinstance(data, dict):
        sess = data.get("session")
        has_expires = isinstance(sess, dict) and "expires_at" in sess
    if tok and has_expires:
        return "new-api >= rc.22 JWT 形态（data.access_token + session.expires_at）"
    if tok:
        return "new-api 变体（有 access_token，无 session.expires_at）"
    if set_cookie_names:
        return "one-api cookie 形态（无 token 字段，靠 Set-Cookie）"
    return "未知形态"


def _classify_token_list(body):
    """/api/token/ 形态：data.items 分页 / data 数组 / 未知；key 脱敏情况。"""
    if not isinstance(body, dict):
        return "未知（响应非 JSON）", None, None
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        shape = "data.items 分页"
        items = data["items"]
    elif isinstance(data, list):
        shape = "data 数组"
        items = data
    else:
        return "未知", None, None
    keys = [it.get("key") for it in items if isinstance(it, dict) and isinstance(it.get("key"), str)]
    if not keys:
        key_state = "未见 key 字段"
    elif any(_MASKED_KEY_RE.search(k) for k in keys):
        key_state = "key 已脱敏（服务端掩码，sk-xxxx****xxxx）"
    else:
        key_state = "key 未脱敏（返回完整值，展示时已掩码）"
    return shape, key_state, items


# ---------------------------------------------------------------------------
# 探测主流程
# ---------------------------------------------------------------------------


def _print_body(status, body_bytes, pretty=True):
    text = body_bytes.decode("utf-8", errors="replace")
    print(f"    HTTP {status}")
    try:
        parsed = json.loads(text)
        dumped = json.dumps(_redact(parsed), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        dumped = text if len(text) <= 1000 else text[:1000] + "\n    ...(截断)"
    print("    --- body（脱敏）---")
    for line in dumped.splitlines():
        print(f"    {line}")


def _probe_login(base, username, password, verify_ssl):
    print("=== [1/4] POST /api/user/login ===")
    url = base.rstrip("/") + "/api/user/login"
    try:
        status, headers, body_bytes, sc_names = _http(
            "POST", url,
            headers={"Content-Type": "application/json"},
            json_body={"username": username, "password": password},
            verify_ssl=verify_ssl,
        )
    except Exception as e:  # noqa: BLE001 —— 探测脚本，单端点失败继续
        print(f"    ERROR: {e}")
        return None, None, []
    _print_body(status, body_bytes)
    if sc_names:
        print(f"    Set-Cookie（仅名称）: {sc_names}")
    else:
        print("    Set-Cookie: 无")
    body = None
    try:
        body = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except ValueError:
        pass
    shape = _classify_login(body, sc_names)
    print(f"    形态: {shape}")
    if isinstance(body, dict):
        _report_unmapped(
            "login", body,
            [((), _LOGIN_KNOWN_TOP), (("data",), _LOGIN_KNOWN_DATA)],
        )
    tok = _extract_access_token(body)
    return tok, body, sc_names


def _probe_self(base, tok, verify_ssl):
    print("\n=== [2/4] GET /api/user/self ===")
    url = base.rstrip("/") + "/api/user/self"
    headers = {}
    if tok:
        headers["Authorization"] = "Bearer " + tok
        print("    凭据: Authorization: Bearer {access_token}（值不展示）")
    elif HAS_REQUESTS and len(_get_requests_session().cookies) > 0:
        print("    凭据: 会话 cookie（登录 Set-Cookie 自动携带，值不展示）")
    else:
        print("    凭据: 无（匿名探测）")
    try:
        status, _, body_bytes, _ = _http("GET", url, headers=headers, verify_ssl=verify_ssl)
    except Exception as e:  # noqa: BLE001
        print(f"    ERROR: {e}")
        return None
    _print_body(status, body_bytes)
    body = None
    try:
        body = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except ValueError:
        pass
    if isinstance(body, dict):
        _report_unmapped(
            "self", body,
            [((), _SELF_KNOWN_TOP), (("data",), _SELF_KNOWN_DATA)],
        )
        data = body.get("data")
        if isinstance(data, dict):
            if "quota" in data:
                print("    余额字段: quota 存在")
            if "balance" in data:
                print("    余额字段: balance 存在")
            for f in ("password", "access_token", "token"):
                if f in data:
                    print(f"    注意: data 含 {f} 字段（已脱敏，解析器需防泄漏）")
    return body


def _probe_token_list(base, tok, verify_ssl):
    print("\n=== [3/4] GET /api/token/ ===")
    url = base.rstrip("/") + "/api/token/"
    headers = {}
    if tok:
        headers["Authorization"] = "Bearer " + tok
        print("    凭据: Authorization: Bearer {access_token}（值不展示）")
    elif HAS_REQUESTS and len(_get_requests_session().cookies) > 0:
        print("    凭据: 会话 cookie")
    try:
        status, _, body_bytes, _ = _http("GET", url, headers=headers, verify_ssl=verify_ssl)
    except Exception as e:  # noqa: BLE001
        print(f"    ERROR: {e}")
        return None
    _print_body(status, body_bytes)
    body = None
    try:
        body = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except ValueError:
        pass
    shape, key_state, _items = _classify_token_list(body)
    print(f"    形态: {shape} | {key_state}")
    if isinstance(body, dict):
        _report_unmapped(
            "token list", body,
            [((), _TOKEN_KNOWN_TOP), (("data",), _TOKEN_KNOWN_DATA)],
        )
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            _report_unmapped("token list", body, [(("data", "items"), _TOKEN_KNOWN_ITEM)])
        elif isinstance(data, list):
            _report_unmapped("token list", body, [(("data",), _TOKEN_KNOWN_ITEM)])
    return body


def _probe_unmask(base, tok, token_id, verify_ssl):
    print("\n=== [4/4] POST /api/token/{id}/key（取完整 key，只打印前6后4）===")
    url = f"{base.rstrip('/')}/api/token/{token_id}/key"
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
        print(f"    目标 token id: {token_id} | 凭据: Bearer（值不展示）")
    elif HAS_REQUESTS and len(_get_requests_session().cookies) > 0:
        print(f"    目标 token id: {token_id} | 凭据: 会话 cookie")
    try:
        status, _, body_bytes, _ = _http("POST", url, headers=headers, json_body={}, verify_ssl=verify_ssl)
    except Exception as e:  # noqa: BLE001
        print(f"    ERROR: {e}")
        return
    _print_body(status, body_bytes)
    if status >= 400:
        print("    unmask 失败（4xx/5xx，见上方 body 摘要）")
        return
    try:
        text = body_bytes.decode("utf-8", errors="replace")
        parsed = json.loads(text)
        key_candidate = None
        if isinstance(parsed, dict):
            data = parsed.get("data")
            if isinstance(data, dict):
                key_candidate = data.get("key")
            if key_candidate is None:
                key_candidate = parsed.get("key")
        if isinstance(key_candidate, str):
            print(f"    完整 key 获取成功，掩码展示: {_mask_key(key_candidate)}")
        else:
            m = _FULL_SK_RE.search(text)
            if m:
                print(f"    响应内含 sk- key，掩码展示: {_mask_key(m.group(0))}")
            else:
                print("    未在响应中找到 key 字段（见上方 body）")
    except ValueError:
        print("    unmask 响应非 JSON，无法提取 key")


def main():
    parser = argparse.ArgumentParser(description="MXOU 平台登录端点形态探测（T0，只读）")
    parser.add_argument("--base", default="https://api.mxou.cn", help="平台 base URL（默认 https://api.mxou.cn）")
    parser.add_argument("--username", required=True, help="MXOU 登录账号")
    parser.add_argument("--password", default=None, help="MXOU 登录密码（缺省读 MXOU_PASSWORD 环境变量）")
    parser.add_argument("--verify-ssl", action="store_true", default=False,
                        help="校验 SSL 证书（默认 false，用 unverified context）")
    args = parser.parse_args()

    if not args.password:
        args.password = os.environ.get("MXOU_PASSWORD")
    if not args.password:
        print("ERROR: 缺少 --password 或 MXOU_PASSWORD 环境变量", file=sys.stderr)
        sys.exit(2)

    global _URLLIB_VERIFY
    _URLLIB_VERIFY = args.verify_ssl

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    print(f"目标: {args.base} | 账号: {args.username} | verify_ssl: {args.verify_ssl}")
    print("注: 本脚本只读探测，密码/access_token/完整 key 均不展示，不落盘任何数据。\n")

    tok, login_body, sc_names = _probe_login(args.base, args.username, args.password, args.verify_ssl)
    time.sleep(1)

    _probe_self(args.base, tok, args.verify_ssl)
    time.sleep(1)

    tok_body = _probe_token_list(args.base, tok, args.verify_ssl)
    time.sleep(1)

    token_id = _extract_token_id(tok_body) if isinstance(tok_body, dict) else None
    if token_id is not None:
        _probe_unmask(args.base, tok, token_id, args.verify_ssl)
    else:
        print("\n=== [4/4] 跳过 POST /api/token/{id}/key ===")
        print("    未从 /api/token/ 响应解析出第一个 token id（登录失败或形态未知）。")

    print("\n=== 形态分类结论 ===")
    print(f"[login] {_classify_login(login_body, sc_names)}")
    if isinstance(tok_body, dict):
        shape, key_state, _ = _classify_token_list(tok_body)
        print(f"[/api/token/] {shape} | {key_state}")
    else:
        print("[/api/token/] 无有效响应")
    if token_id is not None:
        print(f"[/api/token/{{id}}/key] 端点存在，首个 token id = {token_id}（完整 key 已掩码展示）")
    else:
        print("[/api/token/{id}/key] 未探测（无 token id）")
    print("\n完成。以上形态供 T1 防御式解析器（mxou_platform）设计参考。")


if __name__ == "__main__":
    main()

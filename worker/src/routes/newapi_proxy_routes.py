"""v0.55.1: New API 通用代理路由 — 把 webui 同源 /api/* 请求转发到 api.mxou.cn。

背景：webui 部署在 worker 域（worker.mxou.cn / 本地 8080），其 features（登录 /api/user/login、
订阅 /api/subscription/*、钱包 /api/user/topup* 等）走同源 /api/* 请求。worker 无这些端点 →
本地/生产全部 404（v0.54 webui 登录链路根因）。本路由把所有 /api/{path}（排除 /api/v1、/api 本身、
已注册的具体路由优先匹配）转发到 MXOU_BASE（默认 https://api.mxou.cn，env 可覆盖），
完整透传 method/headers/body/query/cookie，响应 status/headers/body 原样返回。

鉴权：New API 用 New-Api-User header + session cookie（webui withCredentials），
worker 不校验/不注入——纯透传。上游不可达 → 502（不 raise，防 webui 白屏）。

注册位置：main.py 在 app.include_router(v1) 之后注册（catch-all 必须在具体路由后）。
"""
import logging
import os
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["newapi-proxy"])

MXOU_BASE = "https://api.mxou.cn"

# New API 特征前缀（命中才代理；其余 /api/{path} 不代理 → 404 由默认路由处理）
_NEWAPI_PREFIXES = (
    "user/", "subscription/", "option/", "log/", "group", "ratio_config",
    "ratio_sync/", "pricing", "performance/", "custom-oauth-provider/",
    "upload/", "redemption/", "status", "notice",
)

# 透传白名单：转发给上游的请求头（排除 hop-by-hop + 代理自身 host）
_FORWARD_HEADERS = (
    "authorization", "new-api-user", "cookie", "content-type", "accept",
    "accept-language", "x-request-id", "x-client-trace", "user-agent",
)

_TIMEOUT = (5, 60)  # (connect, read) 秒


def _base_url() -> str:
    return os.environ.get("MXOU_BASE", MXOU_BASE)


def _should_proxy(path: str) -> bool:
    """命中 New API 前缀才代理；/api/v1/* 与未知前缀不代理。"""
    if path.startswith("v1/") or path == "v1":
        return False
    return any(path == p or path.startswith(p) for p in _NEWAPI_PREFIXES)


def _proxy_request(method: str, path: str, headers: dict, body: Optional[bytes],
                   params: Optional[dict], cookies: Optional[dict]) -> Response:
    """同步转发到上游并返回透传响应。上游异常 → 502 JSONResponse。"""
    url = f"{_base_url()}/api/{path}"
    fwd_headers = {k: v for k, v in headers.items() if k.lower() in _FORWARD_HEADERS}
    try:
        upstream = requests.request(
            method, url, headers=fwd_headers, data=body, params=params,
            cookies=cookies, timeout=_TIMEOUT,
        )
        resp_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() in ("content-type", "set-cookie", "cache-control")}
        return Response(content=upstream.content, status_code=upstream.status_code,
                        headers=resp_headers)
    except Exception as exc:  # noqa: BLE001 — 代理失败不 raise，返回 502 保 webui 不白屏
        logger.warning("NewAPI 代理失败 %s %s: %s", method, path, exc)
        return JSONResponse(status_code=502, content={"error": "upstream unavailable"})


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def newapi_proxy(path: str, request: Request):
    """catch-all：命中 New API 前缀 → 转发 api.mxou.cn；否则 404。"""
    if not _should_proxy(path):
        raise HTTPException(status_code=404, detail="Not Found")
    body = await request.body()
    return _proxy_request(
        request.method, path, dict(request.headers),
        body or None, dict(request.query_params), request.cookies,
    )

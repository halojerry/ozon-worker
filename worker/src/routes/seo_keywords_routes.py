"""SEO 流量关键词公开读端点 — skill what-to-sell all-queries 采集的流量关键词只读消费。

端点（挂载在 /api/v1 下，main.py v1.include_router 注册）：
    GET /seo/keywords?q=<搜索词>&limit=<1-50，默认 20>

鉴权：Bearer token → main._verify_analytics_token（Supabase 未配置 → 本地放行），
按 token 复用 main.RateLimiter（与 commissions lookup / analytics 同款）。
读取全局共享表 blue_ocean_queries，无 tenant 隔离（对齐 W11 纪律），只读不改。
错误不回显内部异常（对齐 analytics 端点安全纪律）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from services import queries_service

router = APIRouter(prefix="/seo/keywords", tags=["seo"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


@router.get("")
@router.get("/")
async def http_seo_keywords(request: Request):
    """流量关键词公开查询。q 空 → top 流量；q 非空 → ILIKE 过滤。"""
    from main import (  # 延迟导入防循环（对齐 admin_queries_routes）
        RATE_LIMIT_PER_MINUTE,
        _verify_analytics_token,
        rate_limiter,
    )

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean_token)

    allowed, _remaining = rate_limiter.check(clean_token)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute",
        )

    raw_limit = (request.query_params.get("limit") or "").strip() or str(_DEFAULT_LIMIT)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit must be an integer")
    limit = max(1, min(limit, _MAX_LIMIT))

    q = (request.query_params.get("q") or "").strip()
    try:
        keywords = queries_service.search_public(q=q, limit=limit)
    except Exception:
        # 错误不回显内部异常（对齐 analytics 端点安全纪律）
        raise HTTPException(status_code=500, detail="seo_keywords query failed")

    return {"keywords": keywords, "total": len(keywords)}

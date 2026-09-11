"""租户 guard 一期：get_tenant FastAPI Dependency（BL-17 / A9 S12-01）。

设计：docs/audit/2026-09-11-repo-gov/design-b2b-tenant-guard.md §3 Phase 1
（dependency + 规约先行，main.py 18 处内联收敛是 Phase 2，本一期不做）。

背景：v0.73 四端点租户漂移（error_reports/forensics/categories/attributes 用
_key_user_id 哈希租户，生产 tenant 是 Supabase user_id 整数如 "28"——哈希租户
查不到任何行）实证「过滤下推全靠各端点手工拼 tenant，每新增一个读端点就重掷
一次骰子」。本 dependency 把租户解析收敛为声明式注入点：
``tenant: str = Depends(get_tenant)``，解析结果缓存到 ``request.state.tenant``
供同请求内复用。

行为基线（与 main.py error_reports/forensics 内联序列逐字等价）：
    Authorization: Bearer 提取 → 空 token 401 "Token is required" →
    剥 ``sk-`` 前缀得 clean_token → _verify_analytics_token(clean_token)
    （Supabase 未配置 → 放行）→ 限流（可选，见下）→
    resolve_tenant(token)（60s LRU + fail-closed 401/503）。

注意：
- ``resolve_tenant`` 收到的是**原始 token**（含 sk-，其内部自剥）——与写入侧
  同源语义。
- ``_verify_analytics_token`` / ``rate_limiter`` / ``RATE_LIMIT_PER_MINUTE``
  定义在 main.py，此处运行时惰性导入（routes/tasks_routes.py 的
  ``from main import _authenticate_token`` 同款先例——延迟导入防循环；调用
  发生时 app 已起，main 必在 sys.modules）。
- 限流有界变体：现状 main.py 中 POST /error_reports 与 forensics 有
  rate_limiter.check，GET /error_reports 没有——为逐字等价提供两个实例：
  :data:`get_tenant`（含限流）与 :data:`get_tenant_no_rate_limit`（不含）。

⚠️ 接线状态（一期）：dependency 与试点路由文件
``routes/error_reports_routes.py`` 已就绪；main.py 属另一会话占用区（禁碰），
include/删旧端点步骤见该文件头注释。新代码规约（写入 AGENTS「纪律」属文档
面，Phase 2 一并收敛）：新增租户面读端点必须用 ``Depends(get_tenant)``。
"""
from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request


def _extract_bearer(request: Request) -> tuple[str, str]:
    """提取 Bearer token → (原始 token, 剥 sk- 的 clean_token)；缺失即 401。

    与 main.py 内联写法逐字同源（auth[7:].strip() / replace("sk-", "", 1)）。
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    return token, clean_token


def _make_get_tenant(enforce_rate_limit: bool) -> Callable[[Request], str]:
    """构造 get_tenant dependency（限流开关参数化——对齐现状端点差异）。"""

    def get_tenant(request: Request) -> str:
        # 同请求缓存：dependency 已解析过（request.state.tenant）直接复用，
        # 不重复验 token/限流/查租户（FastAPI dependency 默认也按签名缓存，
        # 此处显式兜底供非 dependency 场景手工调用）。
        cached = getattr(request.state, "tenant", None)
        if cached:
            return cached

        # 惰性导入（防循环 + 调用时 main 必已加载）
        from main import RATE_LIMIT_PER_MINUTE, _verify_analytics_token, rate_limiter

        token, clean_token = _extract_bearer(request)
        _verify_analytics_token(clean_token)
        if enforce_rate_limit:
            allowed, _remaining = rate_limiter.check(clean_token)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute",
                )
        from services.tenant_service import resolve_tenant

        tenant = resolve_tenant(token)  # 传原始 token（与写入侧同源，内部自剥 sk-）
        request.state.tenant = tenant
        return tenant

    return get_tenant


# 含限流版：POST /error_reports、forensics（现状内联序列带 rate_limiter.check）
get_tenant = _make_get_tenant(enforce_rate_limit=True)

# 无限流版：GET /error_reports（现状内联序列无 rate_limiter.check）
get_tenant_no_rate_limit = _make_get_tenant(enforce_rate_limit=False)


__all__ = [
    "get_tenant",
    "get_tenant_no_rate_limit",
]

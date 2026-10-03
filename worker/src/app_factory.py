"""FastAPI 应用装配层（W3c 自 main.py 迁出）：MCP 挂载 + 全量路由注册 + WebUI SPA 托管。

此前该装配块住在 main.py 顶层（main 是唯一 import app 的入口）。本模块是
唯一权威；main.py 保留 ``app`` re-export（零行为变化，测试/uvicorn/gen_api_docs
的 ``from main import app`` 与 ``uvicorn.run("main:app")`` 契约不变）。

注册顺序契约（改本文件前必读——openapi 快照与 gen_api_docs --check 逐字比对）：
- MCP Mount 与 ``/mcp`` 裸路径转交 Route 先于一切业务路由（Route insert 在 Mount 前）；
- 四族路由（task_queue/ops/catalog/analytics_ingest）注册位置与旧 main 逐字一致；
- ``app.include_router(v1)`` 在所有 v1 子路由之后；
- newapi catch-all ``/api/*`` 必须最后（否则吞掉 /api/v1）；
- WebUI SPA ``/app`` 路由在最后挂载（未构建跳过不阻断）。

MCP 装配：mcp_server.py 模块级构建 mcp_app（FastMCP streamable-http ASGI +
Bearer 鉴权中间件），不反向 import main（工具内延迟导入）。MCP_ENABLED=0 或
fastmcp 缺失 → 跳过挂载（HTTP 面完全不受影响）。``_root_lifespan``（合并
FastMCP session manager lifespan）随本模块——@见 tests/test_mcp_server.py。
"""

import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

from runtime.lifespan import lifespan
from utils.logger import get_logger

logger = get_logger(__name__)

# ── MCP 远程服务（v0.67.0 批次 1）──
# mcp_server.py 模块级构建 mcp_app（FastMCP streamable-http ASGI + Bearer 鉴权中间件），
# 不反向 import main（工具内延迟导入，routes 同款防循环模式）。
# MCP_ENABLED=0 或 fastmcp 缺失 → 跳过挂载（HTTP 面完全不受影响）。
_MCP_ENABLED = os.getenv("MCP_ENABLED", "1") == "1"
_mcp_asgi_app = None
if _MCP_ENABLED:
    try:
        from mcp_server import mcp_app as _mcp_asgi_app
    except Exception as _mcp_err:  # fastmcp 未安装等：降级为无 MCP，不阻断主服务
        logger.warning(f"⚠️ MCP 服务加载失败，已跳过（HTTP API 不受影响）: {_mcp_err}")
        _mcp_asgi_app = None

_mcp_lifespan = getattr(_mcp_asgi_app, "lifespan", None) if _mcp_asgi_app is not None else None


@asynccontextmanager
async def _root_lifespan(app: FastAPI):
    """合并 worker 主 lifespan 与 FastMCP session manager lifespan（挂载必需）。"""
    if _mcp_lifespan is not None:
        async with AsyncExitStack() as _stack:
            await _stack.enter_async_context(_mcp_lifespan(app))
            async with lifespan(app):
                yield
    else:
        async with lifespan(app):
            yield


app = FastAPI(
    lifespan=_root_lifespan,
    title="Ozon Worker API",
    description="Ozon 产品上架 Worker — 接收信封、执行 LangGraph 管线、上传 Ozon",
    version="1.0.0",
)

# MCP 端点挂载（/mcp；webui 在 /app、newapi 代理在 /api 白名单，路径零冲突）
if _mcp_asgi_app is not None:
    app.mount("/mcp", _mcp_asgi_app)

    class _McpNoSlash:
        """精确 /mcp（无尾斜杠）内部转交：Mount 对裸路径会在鉴权前 307 外部重定向，
        这里复刻 Mount 的 scope 语义（root_path+=/mcp, path=/）直接进鉴权中间件。
        可调用类实例 → Starlette 按 raw ASGI app 消费（函数会被包成 request handler）。"""

        def __init__(self, app_):
            self.app = app_

        async def __call__(self, scope, receive, send):
            scope = dict(scope)
            scope["root_path"] = scope.get("root_path", "") + "/mcp"
            scope["path"] = "/"
            await self.app(scope, receive, send)

    from starlette.routing import Route as _Route
    # 插在 Mount 之前，精确路径优先命中
    app.router.routes.insert(
        len(app.router.routes) - 1,
        _Route("/mcp", _McpNoSlash(_mcp_asgi_app),
               methods=["GET", "POST", "DELETE"], include_in_schema=False),
    )
    logger.info("🔌 MCP 远程服务已挂载: POST/GET /mcp（streamable-http，Bearer=mxou key）")

# ── API v1 路由 ──
v1 = APIRouter(prefix="/api/v1", tags=["v1"])


@app.get("/task/{task_id}", include_in_schema=False)
async def http_get_task(task_id: str) -> dict:
    """[REMOVED] 端点已删除（2026-09-23）：无鉴权且自 async runtime 重构起 100% 500
    （AsyncTaskRuntime 无 get 方法）。真实路由已移除，此处仅占位返回 410 Gone 引导迁移。
    """
    raise HTTPException(status_code=410,
                        detail="endpoint removed; use GET /task_status/{task_id}")


# ── R3a 路由族归位（2026-09-23）：四族自 main 迁出，注册在原内联定义等价位置 ──
# task 队列 / analytics 上报 / 类目佣金 / ops 四族 → routes/{task_queue,analytics_ingest,catalog,ops}_routes.py。
# 路径/response_model/responses/示例装饰器逐字随迁（openapi 零漂移）；注册一律在
# newapi catch-all /api/* 之前。禁止新增 routes→main 反向依赖（新模块走权威模块）。
from routes.task_queue_routes import router as task_queue_router
from routes.task_queue_routes import v1_router as task_queue_v1_router
from routes.analytics_ingest_routes import router as analytics_ingest_router
from routes.catalog_routes import root_router as catalog_root_router
from routes.catalog_routes import router as catalog_v1_router
from routes.ops_routes import root_router as ops_root_router
from routes.ops_routes import router as ops_v1_router

app.include_router(task_queue_router)       # /submit_task /task_status /cancel_task /resubmit_task /task_statistics（legacy 绝对路径）
app.include_router(ops_root_router)         # /health /api/v1/store/health /auth/verify(双挂) /progress/{run_id} /graph_parameter /api/v1/logistics/quote
app.include_router(catalog_root_router)     # /categories/* /commissions/lookup（legacy + /api/v1 双挂）
app.include_router(task_queue_v1_router)    # /api/v1/submit_task 等任务队列 v1 别名
v1.include_router(analytics_ingest_router)  # /api/v1/analytics/* /api/v1/discovery/runs*
v1.include_router(catalog_v1_router)        # /api/v1/mappings/lookup
v1.include_router(ops_v1_router)            # /api/v1/health


# ── WebUI 凭证端点（T5）：routes/services 分层，业务逻辑在 services/credential_service.py ──
from routes.credentials_routes import router as credentials_router
v1.include_router(credentials_router)

# B4 租户 guard 一期试点（design-b2b-tenant-guard Phase 1）：
# error_reports/forensics 三端点自 main 内联迁至 routes/error_reports_routes.py，
# 鉴权四段内联替换为 Depends(get_tenant)（行为等价，见该文件头说明）。
from routes.error_reports_routes import (
    router as error_reports_router,
    root_router as error_reports_root_router,
)
v1.include_router(error_reports_router)
app.include_router(error_reports_root_router)

# ── 货源匹配上报（M5b）：skill 图搜/跟卖结果 → source_candidates ──
from routes.source_candidates_routes import router as source_candidates_router
v1.include_router(source_candidates_router)

# ── 用户设置 / 工作台聚合(上生产前演示清零)──
from routes.settings_routes import router as settings_router
v1.include_router(settings_router)
from routes.dashboard_routes import router as dashboard_router
v1.include_router(dashboard_router)

# ── WebUI 上架配置模板端点（P0-1）：routes/services 分层，业务逻辑在 services/template_service.py ──
from routes.templates_routes import router as templates_router
v1.include_router(templates_router)

# ── WebUI 订单端点（P0-4）：routes/services 分层，业务逻辑在 services/order_service.py ──
from routes.orders_routes import router as orders_router
v1.include_router(orders_router)
# ── WebUI 管理员面板端点（v0.51）：routes/services 分层，业务逻辑在 services/admin_service.py ──
from routes.admin_routes import router as admin_router
v1.include_router(admin_router)

# ── WebUI 生图工作台端点（T7a）：生图缓存版本化 + 强制重生成 ──
from routes.images_routes import router as images_router
v1.include_router(images_router)


# ── WebUI 任务列表端点（T8）：routes/services 分层，业务逻辑在 services/task_service.py ──
from routes.tasks_routes import router as tasks_router
v1.include_router(tasks_router)

# ── WebUI 草稿端点（T6 采集箱 CRUD/submit + T14b AI 字段）：routes/services 分层 ──
from routes.drafts_routes import router as drafts_router
app.include_router(drafts_router)

# ── WebUI 草稿预估售价端点（M1.2）：routes/services 分层，定价公式在 utils/pricing_estimate.py 单处定义 ──
from routes.estimate_routes import router as estimate_router
app.include_router(estimate_router)
# P2a 独立定价器（无 draft_id）：POST /api/v1/estimate
from routes.estimate_routes import router_estimate
app.include_router(router_estimate)

# ── WebUI MXOU 登录端点（T2）：routes/services 分层，业务逻辑在 services/mxou_login_service.py ──
# 唯一无 token 鉴权端点（登录入口本身），防爆破在端点层按 username 限流
from routes.mxou_routes import router as mxou_router
app.include_router(mxou_router)

# ── WebUI 在线商品更新端点（T14 改图全量重传）：routes/services 分层，业务逻辑在 services/image_service.py ──
from routes.products_routes import router as products_router
v1.include_router(products_router)

# ── WebUI 在售商品列表端点（M2.1）：routes/services 分层，业务逻辑在 services/shelf_service.py ──
from routes.shelf_routes import router as shelf_router
v1.include_router(shelf_router)

# ── 店铺数据同步（v0.56）：手动同步 + 同步状态 ──
from routes.store_sync_routes import detail_router as store_sync_detail_router
from routes.store_sync_routes import router as store_sync_router
from routes.tasks_routes import sse_router as task_sse_router
v1.include_router(store_sync_router)
v1.include_router(store_sync_detail_router)
v1.include_router(task_sse_router)

# ── 店铺执行端点（todo 7）：改价/库存/上下架 + 营销活动（接线 store_operation_log）──
from routes.store_actions_routes import router as store_actions_router
v1.include_router(store_actions_router)

# ── 系统设置：站点运营（v0.55）：站点 Banner/通告 管理（仅管理员）──
from routes.admin_site_routes import router as admin_site_router
v1.include_router(admin_site_router)

# ── 系统设置：站点公开端点（v0.55）：Banner/通告 只读公开 ──
from routes.site_public_routes import router as site_public_router
v1.include_router(site_public_router)

# ── 系统设置：引擎配置（v0.55）：提示词编辑/运费费率/选品库（仅管理员）──
from routes.admin_config_routes import router as admin_config_router
v1.include_router(admin_config_router)
from routes.admin_logistics_routes import router as admin_logistics_router
v1.include_router(admin_logistics_router)
from routes.admin_queries_routes import router as admin_queries_router
v1.include_router(admin_queries_router)

# ── SEO 流量关键词公开读端点（v0.59+）：what-to-sell 流量关键词只读消费（Bearer + RateLimiter）──
from routes.seo_keywords_routes import router as seo_keywords_router
v1.include_router(seo_keywords_router)

from routes.admin_categories_routes import router as admin_categories_router
app.include_router(admin_categories_router)
from routes.admin_data_sources_routes import router as admin_data_sources_router
app.include_router(admin_data_sources_router)
from routes.admin_audit_routes import router as admin_audit_router
app.include_router(admin_audit_router)

# ── Batch 5: Analytics aggregation endpoints ──
from routes.analytics_routes import router as analytics_agg_router
v1.include_router(analytics_agg_router)

# ── Batch 5: Image tasks CRUD ──
from routes.image_tasks_routes import router as image_tasks_router
v1.include_router(image_tasks_router)


# 注册 v1 路由（/api/v1/* 端点）
# 旧路径（/health, /submit_task 等）仍然可用，向后兼容
app.include_router(v1)

# ── New API 通用代理（v0.55.1）：webui 同源 /api/* → api.mxou.cn（登录/订阅/钱包） ──
# catch-all 必须在 v1 具体路由之后注册，否则吞掉 /api/v1
from routes.newapi_proxy_routes import router as newapi_proxy_router
app.include_router(newapi_proxy_router)


# ── WebUI SPA 静态托管（/app，archive/docs/legacy/PLAN-webui-v1.md §1.4 T4） ──
# dist 默认 webui/dist（env WEBUI_DIST 覆盖）；未构建时跳过挂载不阻断 worker。
# SPA fallback：非静态文件路径回 index.html（前端路由直连/刷新不 404），
# 仅允许 dist 目录内的文件（防路径穿越）。
# v0.62.1 P1-5: 用 APP_WORKSPACE_PATH 拼接（容器内外语义一致），
# 不再依赖 __file__ 三次 dirname（宿主/容器路径差异会导致算出错误默认值）。
_WEBUI_DIST_DEFAULT = os.path.normpath(os.path.join(
    os.environ.get("APP_WORKSPACE_PATH") or os.getcwd(),
    "webui", "dist",
))
WEBUI_DIST = os.environ.get("WEBUI_DIST", _WEBUI_DIST_DEFAULT)


def _mount_webui_static(app: FastAPI) -> None:
    dist = os.path.realpath(WEBUI_DIST)
    index_file = os.path.join(dist, "index.html")
    if not os.path.isfile(index_file):
        logger.warning(
            "WebUI dist 未构建（%s），/app 挂载跳过 —— 先 cd webui && npm run build", WEBUI_DIST)
        return

    @app.get("/app", include_in_schema=False)
    @app.get("/app/", include_in_schema=False)
    @app.get("/app/{full_path:path}", include_in_schema=False)
    async def spa_serve(full_path: str = ""):
        if full_path:
            candidate = os.path.realpath(os.path.join(dist, full_path))
            if candidate.startswith(dist + os.sep) and os.path.isfile(candidate):
                return FileResponse(candidate)
        return FileResponse(index_file)


_mount_webui_static(app)

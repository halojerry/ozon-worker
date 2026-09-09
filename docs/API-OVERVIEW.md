# API-OVERVIEW.md — Worker 对外 API 总览（叙述层）

> 生成日期 2026-09-08 · 对应 v0.70.0 · 端点清单见 docs/API-REFERENCE.md（自动生成）

本文只写「对外约定」：base URL、鉴权、限流、错误信封、任务生命周期、版本策略与变更记录。
**不列端点清单**——完整清单以 `docs/API-REFERENCE.md`（从代码自动生成）为准；本文所有事实
括注代码位置 `文件:行`，行号以当前 dev 分支为准，冲突时**以代码为准**。

## 1. 概览

Worker 是一个 FastAPI 应用：`FastAPI(title="Ozon Worker API", version="1.0.0")`
（`worker/src/main.py:668-673`，description 为「Ozon 产品上架 Worker — 接收信封、执行
LangGraph 管线、上传 Ozon」）。对外有两个面：

- **REST**：规范路径挂 `/api/v1` 前缀（`APIRouter(prefix="/api/v1")`，`main.py:703`，
  `app.include_router(v1)` 在 `main.py:2899`），部分端点同时以旧裸路径双挂（见 §9 版本策略）。
- **远程 MCP**：`/mcp` 端点，Streamable HTTP transport（v0.67 起）。FastMCP ASGI app
  mount 进同一 FastAPI 进程（`main.py:675-676`），裸路径 `/mcp`（无尾斜杠）由 `_McpNoSlash`
  内部转交避免鉴权前 307（`main.py:681-699`）。接入配置与 17 个工具清单见 `docs/MCP-SERVER.md`。

交互式文档走 FastAPI 默认：Swagger UI `/docs`、ReDoc `/redoc`、`/openapi.json`
（`main.py:668-673` 未自定义 `docs_url/redoc_url`，即默认值）。webui 产物挂在 `/app`
（`main.py:2928-2929`）。

**集成方三类，各读什么**：

| 集成方 | 用什么 | 该读的文档 |
|---|---|---|
| skill（本地采集/信封组装） | REST（`submit_task`/`auth/verify`/`error_reports` 等） | `docs/CONTRACT-v4.md`、`skill/SKILL.md`、`skill/references/error-codes.md` |
| webui（完整 ERP 后台） | REST 全量 | `webui/src/imports/generated.d.ts`（OpenAPI 生成类型）、`docs/API-INTEGRATION-GUIDE.md`、Swagger `/docs` |
| pounding-harness（桌面客户端，经 dsh MCP） | 远程 MCP `/mcp`（部分场景直连 REST） | `docs/MCP-SERVER.md`、`docs/PLAN-harness-mcp-adoption-v1.md` |

## 2. Base URL 双环境

| 环境 | Base URL | 说明 |
|---|---|---|
| 本地 | `http://localhost:8080` | Docker 端口映射 `0.0.0.0:8080:5000`（`deploy/docker-compose.yml:36-37`），容器内监听 5000 |
| 生产 | `https://worker.mxou.cn` | skill 默认值：`CLOUD_API_BASE = os.environ.get('WORKER_URL', 'https://worker.mxou.cn')`（`skill/scripts/_const.py:31`），可被 `WORKER_URL` env 覆盖 |

**红线**：功能测试只打本地环境（`http://localhost:8080`）。生产是真实数据 + 真实上架凭证，
云端只允许「用户视角」验证（服务在线/问题复现），不留测试痕迹（AGENTS.md「测试环境规范」）。

## 3. 鉴权矩阵（两种形态并存）

### 3.1 请求体 `token` 字段（legacy/LangGraph 面）

从请求体 JSON 提取 `token`（`_extract_token_from_body`，`main.py:1197-1204`），再走
`_authenticate_token`（`main.py:1217-1234`）。覆盖端点：

- `POST /submit_task`（`main.py:1595`；提取在 `main.py:1620-1623`；token 缺失 → 401
  `"Token is required"`，`main.py:1679`）
- `POST /run`（`main.py:789`）、`POST /stream_run`（`main.py:901`）、
  `POST /node_run/{node_id}`（`main.py:976`）、`POST /v1/chat/completions`（`main.py:1026`）
- `POST /auth/verify`（双挂，`main.py:1355-1356`）——skill 的轻量鉴权检查端点

`_authenticate_token` 行为：空 token → 401；内存吊销表命中（含剥 `sk-` 后比对）→ 401
`"Token is revoked"`（`main.py:1223-1228`）；限流超限 → 429；通过后 `resolve_tenant(token)`
解析租户（`main.py:1233-1234`）。

### 3.2 `Authorization: Bearer <mxou key>`（REST 面）

手工解析 header（`auth[7:].strip()`，无 FastAPI Security 依赖），校验同样落在
`_authenticate_token` 或 analytics 同源的 `_verify_analytics_token`（`main.py:2199`，
Supabase 未配置 → 本地放行）。用于：analytics 上报/榜单（`main.py:2263`/`2334`）、
`/api/v1/discovery/runs`（`main.py:2400/2448`）、`/api/v1/error_reports`（`main.py:2496/2533`）、
`/forensics/task/{task_id}`（双挂，`main.py:2564-2565`）、`/api/v1/mappings/lookup`
（`main.py:2591`）、`/categories/search` + `/categories/attributes`（双挂，
`main.py:2639-2640`/`2678-2679`）、`/commissions/lookup`（双挂，`main.py:2738-2739`），
以及 `routes/` 下多数业务路由（模式示例：`routes/dashboard_routes.py:18-31`——Bearer 优先、
body token 兜底）。部分路由（credentials/mxou keys/products 等）两种形态都收。

**MCP**：`_BearerAuthMiddleware`（`mcp_server.py:64-92`）在 MCP 协议层先挡——复用
`main._authenticate_token`（限流/租户/吊销全同源），失败按状态码回 JSON，不进 MCP 会话；
通过后 token/租户写 ContextVar 供工具层取用（`mcp_server.py:93-95`）。

### 3.3 租户解析（两套口径，集成方须知）

- **业务面（submit_task 等）**：`resolve_tenant`（`services/tenant_service.py:39-70`）——
  剥 `sk-` 前缀（`:35-36`）→ 查 Supabase `tokens.user_id`（带 TTL 缓存）→ **未配置
  Supabase 时回退 key 哈希租户** `user_<sha256[:16]>`（`key_derived_tenant`，
  `tenant_service.py:30-32`）；已配置但查询失败 fail-closed 503（`tenant_service.py:64-66`），
  查无此 token → 401 `"token_invalid or account_inactive"`。
- **analytics/取证面（error_reports/forensics/discovery/runs）**：key 哈希租户恒定
  `_key_user_id`（`main.py:1206-1209`；error_reports POST `main.py:2524`、GET `main.py:2543`、
  forensics `main.py:2585`）——即使 Supabase 已配置也按 key 哈希，与业务面租户**不是同一体系**。

**本地开发 fail-open 语义**：Supabase 未配置时租户 = key 哈希、余额检查放行
（`main.py:1284-1286`）、`_verify_analytics_token` 直接放行——任何非空假 token 都能过。
**auth 短路验证须用空 token**（空 token 在任何配置下都 401）。

### 3.4 免鉴权端点（5 个）

| 端点 | 位置 |
|---|---|
| `GET /health` | `main.py:1122`（compose healthcheck 在用，异常只回错误类型不回详情） |
| `GET /api/v1/store/health` | `main.py:1151`（Ozon 店铺配额健康） |
| `POST /api/v1/mxou/login` | `routes/mxou_routes.py:3,26`（同路由其余 keys 端点仍要鉴权） |
| `GET /api/v1/site/banners` | `routes/site_public_routes.py:13-15`（挂 v1，`main.py:2867`） |
| `GET /api/v1/site/announcements` | `routes/site_public_routes.py:17-21` |

### 3.5 402 余额判定

`_check_mxou_balance`（`main.py:1237-1318`）：MXOU 平台实查余额优先（经 30s TTL 缓存的
`_check_balance_cached`）→ 查询失败降级 Supabase `users.quota` 兜底 → 本地无 Supabase 放行。
402 文案带来源标识 `source ∈ {mxou_real, mxou_session, supabase, unknown}`
（`_balance_source_label`，`main.py:1322` 起；组装在 `main.py:1700-1715`）。

**余额判定红绿灯（引 AGENTS.md，改余额链前必读）**：真欠费 = MXOU 实查返回**负数**；
订阅/无限账号字面 `balance: 0` 是**哨兵不是欠费**；Supabase `users.quota` 是 stale 镜像只在
MXOU 实查失败时兜底。**不要**把任一 0.0 简单当欠费拒绝。

### 3.6 状态码语义

| 状态码 | 语义 | 代码位置 |
|---|---|---|
| 401 | token 缺失 / 无效（查无此 key 或 status≠1）/ 已吊销 | `main.py:1221`、`main.py:1223-1228`、`tenant_service.py:55-56` |
| 402 | 余额不足（MXOU 真欠费，或降级判定后无额度） | `main.py:1700-1715`、`api/errors.py:52` |
| 403 | 账号禁用/过期（错误码映射保留 `TOKEN_DISABLED/TOKEN_EXPIRED`）；实际更多出现在 admin 守卫与跨租户拦截 | `api/errors.py:49-50`、`services/admin_service.py:75`、`routes/credentials_routes.py:101` |
| 404 | 资源不存在（含 forensics 跨租户——等价不存在） | `main.py:2587`、`api/errors.py:53` |
| 409 | 状态冲突（不可取消/不可重提/重复提交） | `api/errors.py:54-56` |
| 422 | 请求体校验失败（如 credentials 字段缺失/类型错，v0.63.1 起） | `routes/credentials_routes.py` |
| 429 | 限流超限 | `main.py:1230-1232` |
| 503 | Supabase 不可用（fail-closed）/ task processor 未初始化 / 依赖存储不可用 | `tenant_service.py:64-66`、`main.py:1598` |
| 500 | 内部错误（`INTERNAL_ERROR`/`TASK_SUBMIT_FAILED`） | `api/errors.py:57-58` |

注意：主鉴权链对「禁用/过期」token 实际也返回 401（`"token_invalid or account_inactive"`，
`tenant_service.py:55-56`）；403 目前主要来自 admin 守卫与错误码映射保留位。

## 4. 限流与并发

- `RATE_LIMIT_PER_MINUTE` 默认 **300**（`main.py:218`）。
- `RateLimiter` 为滑动窗口 60s 限流器，按**原始 token**（含 `sk-` 前缀）计数，
  `threading.Lock` 保护（`main.py:221-243`）。
- 429 响应形态（FastAPI 默认信封）：`{"detail": "Rate limit exceeded: max 300 requests per minute"}`
  （`main.py:1231`；`/submit_task` 内同款 `main.py:1686`）。
- **MCP 一次工具调用计 2 次**：Bearer 中间件 `_authenticate_token` 记 1 次 + 工具经进程内
  httpx ASGITransport 回调内层 REST 时业务路由再记 1 次（`mcp_server.py:96-110`；
  口径见 AGENTS.md v0.67 节）——比 REST 更保守。
- 任务并发 `MAX_CONCURRENT` 默认 **30**（`main.py:473`），`num_workers` 与其联动
  （`main.py:579`）。这是管线吞吐上限，与 API 限流相互独立。

## 5. 错误信封双形态（集成方应同时兼容）

1. **统一错误码信封** `error_response()`（`worker/src/api/errors.py:64-80`）：
   `{"ok": false, "error_code": "...", "message": "...", "detail"?: ...}`，
   HTTP 状态码由 `ERROR_STATUS_MAP` 决定（`errors.py:46-61`）。submit_task 的入参校验/
   余额拒绝等业务错误走此形态（`main.py:1630-1676`、`main.py:1712-1715`）。
2. **FastAPI `HTTPException` 默认信封**：`{"detail": "..."}`。鉴权/限流/资源不存在类多为
   此形态（如 `main.py:1221`、`main.py:1231`、`main.py:2587`）。
3. **MCP 中间件信封**：`{"error": {"status": <int>, "detail": "..."}}`
   （`mcp_server.py:85`）。MCP 工具层业务错误另有结构化 error dict（不 raise，
   `docs/MCP-SERVER.md`）。

## 6. 错误码表（14 个）

权威源：`worker/src/api/errors.py:19-42`（枚举）+ `errors.py:46-61`（HTTP 映射）。
**本表只镜像，新增错误码以代码为准。**

| 错误码 | HTTP | 语义 |
|---|---|---|
| `TOKEN_MISSING` | 401 | 请求未携带 token |
| `TOKEN_INVALID` | 401 | token 无效 |
| `TOKEN_DISABLED` | 403 | token 被禁用（映射保留位） |
| `TOKEN_EXPIRED` | 403 | token 已过期（映射保留位） |
| `INSUFFICIENT_BALANCE` | 402 | 余额不足 |
| `RATE_LIMITED` | 429 | 限流 |
| `TASK_NOT_FOUND` | 404 | 任务不存在 |
| `TASK_NOT_CANCELLABLE` | 409 | 任务当前状态不可取消 |
| `TASK_NOT_RESUBMITTABLE` | 409 | 任务当前状态不可重提 |
| `TASK_SUBMIT_FAILED` | 500 | 任务入队失败 |
| `DUPLICATE_SUBMIT` | 409 | 重复提交 |
| `INTERNAL_ERROR` | 500 | 内部错误 |
| `SERVICE_UNAVAILABLE` | 503 | 服务不可用（依赖故障） |
| `INVALID_REQUEST` | 400 | 请求参数非法（信封结构/负值/物理合理性） |

## 7. 分页约定

- **列表类端点**：`limit`/`offset` query 参数，`limit` 上限 **200** 封顶
  （discovery/runs：`limit = max(1, min(limit, 200))`，`main.py:2471`；error_reports 同款
  `main.py:2553`），默认值各端点自定（常见 50）。
- 响应统一形态：`{"items": [...], "total": <int>, "limit": <int>, "offset": <int>}`
  （`main.py:2493`）。
- **订单类 Ozon 透传接口例外**：Ozon Seller `/v4/posting/fbs/list` 用 `cursor`/`has_next`
  游标分页（无 offset/total），worker 原样透传（`services/order_service.py:295`、
  `services/store_sync_service.py:470`）。

## 8. 任务生命周期

```
submit_task → pending → running → completed / failed / cancelled
                  └──────────────────────────┘ (cancel 仅 pending)
```

- 提交 `POST /submit_task`（`main.py:1595`）→ PG 队列 `ozon_product_tasks`；查询
  `GET /task_status/{task_id}`（`main.py:1816`）；取消 `POST /cancel_task/{task_id}`
  （**仅 pending 可取消**，`main.py:1863-1868`）；重提 `POST /resubmit_task/{task_id}`
  （`main.py:1895`）；统计 `GET /task_statistics`（`main.py:1989`）。
- **`task_status.progress`**：`{stage, percent, stages_completed[], stages_remaining[], message}`，
  基于 `STAGE_ORDER` 计算（`main.py:82-85`，**13 个阶段**：auth/ingest/category_match/
  pricing/attributes/description/image_generation/prepare_ozon_upload/ozon_validate/
  check_quota/ozon_upload/ozon_status/learning_record）。进度内存优先、PG `progress` 列
  回退持久化（`main.py:91-110`，2s 节流异步写 PG `main.py:114-136`）——worker 重启后仍可
  从 PG 读到最近一次进度（内存中已完成超 1 小时的条目会被清理，`main.py:139-144`）。
- **completed 必须过真实商品证据校验**：`_has_real_product_evidence`
  （`utils/task_processor.py:73`）——product_id 为空或等于 import task_id 的「假成功」
  会被改判 failed（completed 兜底分支，`task_processor.py:616`；v0.69 收口）。
- LangGraph 细粒度进度另有 `GET /progress/{run_id}`（内存态，`main.py:1449`）与
  任务中心 SSE（v0.61 `task_progress_events`）。

## 9. 版本策略

- `/api/v1/` 前缀为规范路径（`main.py:703`）；旧裸路径**双挂兼容**但非全体适用——
  显式双挂的端点成对声明（如 `auth/verify` `main.py:1355-1356`、`forensics/task/{task_id}`
  `main.py:2564-2565`、`categories/*` `main.py:2639-2640`/`2678-2679`、
  `commissions/lookup` `main.py:2738-2739`）；而 v0.56 起的部分 analytics 类端点
  （`discovery/runs`、`error_reports`、`mappings/lookup`）**仅挂 `/api/v1` 前缀**
  （`@v1.*`，`main.py:2400/2448/2496/2533/2591`）。新集成方一律用 `/api/v1/` 前缀。
- FastAPI `version="1.0.0"` 为**硬编码占位**（`main.py:672`），≠ 产品版本；产品版本真值在
  仓库根 `VERSION`（四源同步，见 AGENTS.md「版本管理」）。`GET /health` **不返回版本**
  （返回 status/message/db/queue，`main.py:1122-1146`）；运行时版本经 `APP_VERSION` env
  注入（如 error_reports 响应附 `worker_version`，`main.py:2526`）。
- 已标 DEPRECATED（未来版本移除，勿新接）：`POST /async_run`（`main.py:710-712`，改用
  `/submit_task`）、`GET /task/{task_id}`（`main.py:774-776`，改用 `/task_status/{task_id}`）。
- Skill↔Worker 接口契约版本：`docs/CONTRACT-v4.md`（v4.0）。信封结构变更必须同步该文档
  （AGENTS.md「更新联动规则」）。

## 10. API 变更记录（v0.56 起，对外端点/MCP 工具）

> 后续按 AGENTS.md 联动规则：**改 API 必须追加本表**（新增 | 变更/弃用 一行）。
> 注：CHANGELOG.md 缺 v0.57–v0.59 独立条目，该三行以 AGENTS.md + 代码为准。

| 版本 | 新增 | 变更/弃用 |
|---|---|---|
| v0.56.0 | `POST/GET /api/v1/discovery/runs`（选品归档）、`GET /api/v1/mappings/lookup`、上架配置模板 `/api/v1/templates` 系列、店铺手动同步 `POST /api/v1/stores/{id}/sync` + sync-status、采集箱 `/api/v1/drafts` 系列 | `ListingTemplateOut` 补 `store_overrides` |
| v0.57* | `GET /api/v1/stores/{credential_id}/stats`（店铺卡统计） | 订单接口 Ozon 侧 v3→v4（`/v4/posting/fbs/list` cursor 分页） |
| v0.59* | `GET /api/v1/commissions/lookup?category_id=`（佣金缓存查询） | 佣金解析统一进 `commission_resolver` |
| v0.60.0 | `GET /api/v1/seo/keywords?q=&limit=`（SEO 流量词） | estimate 端点支持三档价覆盖键（margin_anchor/margin_floor/variable_cost_rate/promo_variable_cost_rate） |
| v0.61.0 | `GET /api/v1/dashboard/overview`、任务进度事件 + SSE、drafts `/resubmit`/`/batch-submit`、五域店铺读端点 | credentials 存储启用主密钥加密（`CREDENTIAL_MASTER_KEY` 必配） |
| v0.63.1 | — | credentials 创建/轮换校验失败 500 → **422**（带 detail） |
| v0.67.0 | **`/mcp` 远程 MCP 端点**（streamable-http，Bearer 鉴权），14 个工具 | — |
| 未发版（shopbang-parity） | 店铺会话代管 `POST/GET/DELETE /api/v1/credentials/{id}/session`、会话直调 `GET /api/v1/analytics/what-to-sell`；drafts 请求体/PATCH 顶层 `notes` 运营备注（不进信封） | discovery_meta 扩 4 键（follow_profit_cny/follow_margin/ozon_old_price/match_1688_freight_cny） |
| v0.69.0 | `POST/GET /api/v1/error_reports`（错误报告；`?report_id=` 详情、`?status=` 筛选） | skill CLI 提交失败 exit 3（原静默 exit 0） |
| v0.70.0 | `GET /api/v1/forensics/task/{task_id}`（取证一站式只读）、`GET /api/v1/categories/search`、`GET /api/v1/categories/attributes`（缓存只读不回源 Ozon） | MCP 工具 14→17（+`report_issue`/`list_error_reports`/`get_task_forensics`） |

## 11. 文档地图

| 文档 | 用途 | 维护方式 |
|---|---|---|
| `docs/API-REFERENCE.md` | 完整端点清单 | **自动生成，勿手改** |
| `docs/API-OVERVIEW.md` | 本文：对外约定叙述层 | 手写，改 API 时人工维护 §10 |
| `docs/CONTRACT-v4.md` | Skill↔Worker 信封/端点契约 v4.0 | 手写，契约变更三处同步 |
| `docs/MCP-SERVER.md` | `/mcp` 接入指南 + 17 工具清单 + 客户端配置 | 手写，MCP 工具增删时同步 |
| `docs/API-INTEGRATION-GUIDE.md` | webui/第三方 REST 对接参考 | 手写（快照式，标注提取版本） |
| `webui/src/imports/generated.d.ts` | webui 类型（OpenAPI 生成） | 自动生成 |
| Swagger `/docs`、`/openapi.json` | 运行时交互文档 | FastAPI 自动 |

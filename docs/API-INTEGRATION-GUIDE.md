# Ozon AI 自动化运营 ERP · API 对接文档

> 面向 WebUI 前端与第三方系统的 REST API 参考。端点清单从 `worker/src`（`main.py` 挂载 + `routes/*_routes.py`）代码提取，代码为唯一真相源。
> 版本：v0.56.6 · 更新时间：2026-08-19

---

## 1. 基础信息

| 项 | 值 |
|---|---|
| 项目 | Ozon AI 自动化运营 ERP（worker + webui） |
| 版本 | v0.56.6 |
| 基础 URL | `https://<worker-host>:8080` |
| API 前缀 | `/api/v1`（旧路径 `/submit_task` 等保持兼容） |
| 前端 | `webui/`（Vite + React，同域托管于 `/app`，零 CORS） |
| 交互式文档 | `GET /docs`（Swagger UI）/ `GET /redoc`（ReDoc）/ `GET /openapi.json` |
| 健康检查 | `GET /api/v1/store/health` / `GET /health` |

### 端点总量

共 **117** 个端点（98 个路径），分 10 组 + 代理通配。

### v0.56.6→v0.57 增量（对接方关注）

- `GET /api/v1/stores/{credential_id}/stats` — 店铺卡今日统计（订单/销售额/佣金/利润/件数）
- `GET /api/v1/discovery/runs` — 选品归档**全局共享**（A 用户可见 B 的归档，含贡献者标注；订单/商品/草稿仍租户隔离）
- `GET /api/v1/analytics/bestsellers` — 榜单浏览全局共享（同 discovery 规则，token 不再作数据过滤）
- `GET /api/v1/task_statistics` — 任务统计（KPI 卡数据源，今日订单/AI 上品数/成功率）
- `/api/{path}` 通配 — New API 代理（命中 `api.mxou.cn` 前缀转发，否则 404）

## 2. 鉴权

### 2.1 通用规则

```http
Authorization: Bearer <token>
```

- token 即 MXOU API Key（`sk-` 前缀自动剥离后查 Supabase `tokens` 表）
- 无 token / 无效 → `401 Invalid token`；速率超限 → `429`；Supabase 不可达 → `503`（fail-closed，**不** 500 白屏）
- **本地开发**（未配置 Supabase 环境变量）→ 任意 token 通过，返回 `local_dev`；
- **Docker 部署**（配置了 SUPABASE_URL/KEY）→ 生产鉴权，token 必须为 Supabase `tokens` 表中有效记录，否则 `401 Invalid token`
- WebUI 前端由 Axios 拦截器自动注入，token 存 `localStorage`

### 2.2 免鉴权端点（仅 5 个）

| 端点 | 说明 |
|---|---|
| `POST /api/v1/mxou/login` | MXOU 账号登录（按 username 限流防爆破） |
| `GET /api/v1/site/announcements` | 站点公开通告 |
| `GET /api/v1/site/banners` | 站点公开横幅 |
| `GET /health` | 健康检查 |
| `GET /api/v1/store/health` | worker 健康检查 |

### 2.3 登录流程

```text
1. 用户输入 MXOU token（或账号密码 → POST /api/v1/mxou/login 换 key）
2. POST /api/v1/auth/verify  验证 token → 返回会话
3. 前端 localStorage 持久化 token，后续请求自动注入 Bearer
```

## 3. 端点参考

### 1. `账号·鉴权`（2 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `POST` | `/api/v1/auth/verify` | 登录验证 |
| 🔒 `POST` | `/auth/verify` | 登录验证 |

### 2. `账号·MXOU`（6 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `GET` | `/api/v1/mxou/keys` | keys 列表 |
| 🔒 `POST` | `/api/v1/mxou/keys` | 创建 key |
| 🔒 `DELETE` | `/api/v1/mxou/keys/{key_id}` | 吊销 key |
| 🔒 `POST` | `/api/v1/mxou/keys/{key_id}/select` | 选择 key |
| 🔓 `POST` | `/api/v1/mxou/login` | MXOU 登录（免鉴权） |
| 🔒 `GET` | `/api/v1/mxou/my-key` | 我的 key |

### 3. `核心业务`（47 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `GET` | `/api/v1/credentials` | 凭证列表 |
| 🔒 `POST` | `/api/v1/credentials` | 创建凭证 |
| 🔒 `PATCH` | `/api/v1/credentials/{credential_id}` | 轮换凭证 |
| 🔒 `DELETE` | `/api/v1/credentials/{credential_id}` | 吊销凭证 |
| 🔒 `POST` | `/api/v1/credentials/{credential_id}/validate` | 校验凭证 |
| 🔒 `POST` | `/api/v1/drafts` | 创建草稿 |
| 🔒 `GET` | `/api/v1/drafts` | 草稿列表（采集箱） |
| 🔒 `GET` | `/api/v1/drafts/{draft_id}` | 草稿列表（采集箱） |
| 🔒 `PATCH` | `/api/v1/drafts/{draft_id}` | 乐观锁更新草稿 |
| 🔒 `DELETE` | `/api/v1/drafts/{draft_id}` | 删除草稿 |
| 🔒 `POST` | `/api/v1/drafts/{draft_id}/ai/{field}` | 草稿 AI 字段生成 |
| 🔒 `POST` | `/api/v1/drafts/{draft_id}/estimate` | 草稿预估售价 |
| 🔒 `GET` | `/api/v1/drafts/{draft_id}/submissions` | 提交历史 |
| 🔒 `POST` | `/api/v1/drafts/{draft_id}/submit` | 提交上架 |
| 🔒 `POST` | `/api/v1/estimate` | 独立预估售价 |
| 🔒 `POST` | `/api/v1/logistics/quote` | 物流运费报价 |
| 🔒 `GET` | `/api/v1/orders` | 订单列表 |
| 🔒 `POST` | `/api/v1/orders/batch/labels` | 批量运单标签 |
| 🔒 `POST` | `/api/v1/orders/batch/ship` | 批量发货 |
| 🔒 `GET` | `/api/v1/orders/message-templates` | 消息模板 |
| 🔒 `GET` | `/api/v1/orders/messages` | 消息记录 |
| 🔒 `POST` | `/api/v1/orders/{posting_number}/cancel` | 取消订单 |
| 🔒 `GET` | `/api/v1/orders/{posting_number}/cancel-reasons` | 取消原因 |
| 🔒 `GET` | `/api/v1/orders/{posting_number}/label` | 运单标签 |
| 🔒 `POST` | `/api/v1/orders/{posting_number}/message` | 发送消息 |
| 🔒 `GET` | `/api/v1/orders/{posting_number}/notes` | 订单备注 |
| 🔒 `PUT` | `/api/v1/orders/{posting_number}/notes` | 订单备注 |
| 🔒 `POST` | `/api/v1/orders/{posting_number}/ship` | 订单发货 |
| 🔒 `GET` | `/api/v1/products` | 在售货架 |
| 🔒 `POST` | `/api/v1/products/bulk-archive` | 批量归档 |
| 🔒 `POST` | `/api/v1/products/bulk-prices` | 批量改价 |
| 🔒 `POST` | `/api/v1/products/bulk-stocks` | 批量改库存 |
| 🔒 `GET` | `/api/v1/products/ozon` | OZON 在线商品 |
| 🔒 `GET` | `/api/v1/products/{product_id}/edit` | 商品编辑信息 |
| 🔒 `POST` | `/api/v1/products/{product_id}/update_images` | 改图重传 |
| 🔒 `GET` | `/api/v1/stores/{credential_id}/stats` | **店铺卡今日统计（v0.57 新增）** |
| 🔒 `POST` | `/api/v1/stores/{credential_id}/sync` | 触发同步 |
| 🔒 `GET` | `/api/v1/stores/{credential_id}/sync-status` | 同步状态 |
| 🔒 `GET` | `/api/v1/tasks` | 任务列表 |
| 🔒 `GET` | `/api/v1/tasks/{task_id}/draft` | 任务→草稿 |
| 🔒 `GET` | `/api/v1/tasks/{task_id}/images` | 任务生图 |
| 🔒 `POST` | `/api/v1/tasks/{task_id}/images/{slot}/regen` | 重生成图片 |
| 🔒 `GET` | `/api/v1/templates` | 模板列表 |
| 🔒 `POST` | `/api/v1/templates` | 创建模板 |
| 🔒 `PATCH` | `/api/v1/templates/{template_id}` | 更新模板 |
| 🔒 `DELETE` | `/api/v1/templates/{template_id}` | 删除模板 |
| 🔒 `POST` | `/api/v1/templates/{template_id}/default` | 设默认模板 |

### 4. `任务·运行`（17 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `POST` | `/api/v1/cancel_task/{task_id}` | 取消任务 |
| 🔒 `POST` | `/api/v1/resubmit_task/{task_id}` | 提交上架任务 |
| 🔒 `POST` | `/api/v1/submit_task` | 提交上架任务 |
| 🔒 `GET` | `/api/v1/task_statistics` | 任务统计 |
| 🔒 `GET` | `/api/v1/task_status/{task_id}` | 任务状态 |
| 🔒 `POST` | `/async_run` | 异步运行 |
| 🔒 `POST` | `/cancel_task/{task_id}` | 取消任务 |
| 🔒 `GET` | `/progress/{run_id}` | 运行进度 |
| 🔒 `POST` | `/resubmit_task/{task_id}` | 提交上架任务 |
| 🔒 `POST` | `/run` | 运行图 |
| 🔒 `POST` | `/stream_run` | 流式运行 |
| 🔒 `POST` | `/submit_task` | 提交上架任务 |
| 🔒 `GET` | `/task_statistics` | 任务统计 |
| 🔒 `GET` | `/task_status/{task_id}` | 任务状态 |
| 🔒 `POST` | `/v1/chat/completions` | OpenAI 兼容接口 |
| 🔒 `GET` | `/graph_parameter` | 图输入参数（旧接口） |
| 🔒 `POST` | `/node_run/{node_id}` | 运行单节点（旧接口） |

### 5. `管理后台`（26 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `GET` | `/api/v1/admin/config` | 引擎配置/备份/回滚（管理员） |
| 🔒 `GET` | `/api/v1/admin/config/` | 引擎配置/备份/回滚（管理员） |
| 🔒 `GET` | `/api/v1/admin/config/{name}` | 引擎配置/备份/回滚（管理员） |
| 🔒 `PUT` | `/api/v1/admin/config/{name}` | 引擎配置/备份/回滚（管理员） |
| 🔒 `GET` | `/api/v1/admin/config/{name}/backups` | 引擎配置/备份/回滚（管理员） |
| 🔒 `POST` | `/api/v1/admin/config/{name}/rollback` | 引擎配置/备份/回滚（管理员） |
| 🔒 `GET` | `/api/v1/admin/logistics/rates` | 物流费率（管理员） |
| 🔒 `POST` | `/api/v1/admin/logistics/rates/import` | 物流费率（管理员） |
| 🔒 `PUT` | `/api/v1/admin/logistics/rates/{rate_id}` | 物流费率（管理员） |
| 🔒 `GET` | `/api/v1/admin/overview` | 管理概览 |
| 🔒 `GET` | `/api/v1/admin/queries` | 查询词库（管理员） |
| 🔒 `GET` | `/api/v1/admin/queries/` | 查询词库（管理员） |
| 🔒 `POST` | `/api/v1/admin/queries/import` | 查询词库（管理员） |
| 🔒 `DELETE` | `/api/v1/admin/queries/{query_id}` | 查询词库（管理员） |
| 🔒 `GET` | `/api/v1/admin/site/announcements` | 站点通告/横幅管理（管理员） |
| 🔒 `POST` | `/api/v1/admin/site/announcements` | 站点通告/横幅管理（管理员） |
| 🔒 `PUT` | `/api/v1/admin/site/announcements/{announcement_id}` | 站点通告/横幅管理（管理员） |
| 🔒 `DELETE` | `/api/v1/admin/site/announcements/{announcement_id}` | 站点通告/横幅管理（管理员） |
| 🔒 `GET` | `/api/v1/admin/site/banners` | 站点通告/横幅管理（管理员） |
| 🔒 `POST` | `/api/v1/admin/site/banners` | 站点通告/横幅管理（管理员） |
| 🔒 `PUT` | `/api/v1/admin/site/banners/{banner_id}` | 站点通告/横幅管理（管理员） |
| 🔒 `DELETE` | `/api/v1/admin/site/banners/{banner_id}` | 站点通告/横幅管理（管理员） |
| 🔒 `GET` | `/api/v1/admin/stores` | 店铺管理（管理员） |
| 🔒 `GET` | `/api/v1/admin/tasks` | 任务管理（管理员） |
| 🔒 `GET` | `/api/v1/admin/users` | 用户管理（管理员） |
| 🔒 `GET` | `/api/v1/admin/users/{user_id}` | 用户管理（管理员） |

### 6. `选品分析`（6 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `GET` | `/api/v1/analytics/bestsellers` | 榜单浏览（**全局共享**，含贡献者列，v0.57） |
| 🔒 `POST` | `/api/v1/analytics/market-bestsellers` | 市场榜单上报 |
| 🔒 `POST` | `/api/v1/analytics/ozon-bestsellers` | OZON 榜单上报 |
| 🔒 `POST` | `/api/v1/analytics/queries` | 蓝海查询词上报 |
| 🔒 `POST` | `/api/v1/discovery/runs` | 选品 discovery 运行（租户隔离上报） |
| 🔒 `GET` | `/api/v1/discovery/runs` | 选品归档列表（**全局共享**，含贡献者标注，v0.57） |

> ⚠️ 全局共享规则（v0.57）：`bestsellers` / `discovery/runs` 读端点 token 只作鉴权不作数据过滤——A 用户可见 B 用户贡献的榜单与选品归档（保留贡献者列）；订单/商品/草稿/凭证/任务仍严格租户隔离。蓝海（admin/queries 仅管理员）与榜单写端保持关闭。

### 7. `系统·健康`（3 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `GET` | `/api/v1/health` | 健康检查 |
| 🔓 `GET` | `/api/v1/store/health` | 健康检查 |
| 🔓 `GET` | `/health` | 健康检查 |

### 8. `系统·公开`（2 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔓 `GET` | `/api/v1/site/announcements` | 站点公开信息（免鉴权） |
| 🔓 `GET` | `/api/v1/site/banners` | 站点公开信息（免鉴权） |

### 9. `系统·映射`（1 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `GET` | `/api/v1/mappings/lookup` | SKU/类目映射查询 |

### 10. `其他`（7 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| 🔒 `POST` | `/cancel/{run_id}` | 取消运行（图编排旧接口） |
| 🔒 `GET` | `/task/{task_id}` | 查询任务详情（旧接口，⚠️ DEPRECATED，用 `/task_status/{task_id}`） |
| 🔒 `GET` | `/api/{path}` | New API 代理（命中 api.mxou.cn 前缀转发） |
| 🔒 `POST` | `/api/{path}` | New API 代理 |
| 🔒 `PUT` | `/api/{path}` | New API 代理 |
| 🔒 `PATCH` | `/api/{path}` | New API 代理 |
| 🔒 `DELETE` | `/api/{path}` | New API 代理 |

> `/api/{path}` 为 catch-all 代理：命中 `api.mxou.cn` 相关前缀 → 转发上游（LLM/生图 API）；否则 `404`。前端**不要**直接调用，仅供内部工具使用。

## 4. 关键数据模型

> 完整字段以 `generated.d.ts` 的 `components['schemas']` 为准；此处列高频模型结构。

### DraftCreate（POST /api/v1/drafts）

```json
{
  "token": "MXOU_API_KEY（sk- 前缀可选）",
  "ozon_client_id": "Ozon 卖家 Client-Id",
  "ozon_api_key": "Ozon 卖家 Api-Key",
  "envelope": { "draft": {...}, "source": {...}, "extensions": {...} },
  "source": "skill | webui"
}
```

> 凭证由 worker 剥离存储（AES-256-GCM 加密），草稿 payload 不落明文凭证。

### DraftPatch（PATCH /api/v1/drafts/{id}）

```json
{ "version": 1, "payload": { ... } }
```

> 乐观锁：`version` 必须匹配当前值，否则 `409`。

### SubmitResponse（POST /api/v1/drafts/{id}/submit）

```json
{ "ok": true, "draft_id": "…", "submission_id": "…", "task_id": "…",
  "status": "pending", "confirm_required": false, "existing_stores": [] }
```

### AuthVerifyResponse（POST /api/v1/auth/verify）

```json
{ "valid": true, "user_id": "…", "token": "…" }
```

### SubmitTaskResponse（POST /api/v1/submit_task）

```json
{ "ok": true, "task_id": "3f2a1c…-uuid", "message": "任务提交成功" }
```

> 请求体 = GraphInput 三层信封：`{token, ozon_client_id, ozon_api_key, envelope:{draft, source, extensions}}`。envelope.draft 必填字段校验：`item_id / title / images / weight / dimensions / purchase_cost / purchase_url`（缺任一 → 422 + missing 明细）。

### TaskStatusResponse（GET /api/v1/task_status/{task_id}）

```json
{
  "id": "3f2a1c…", "status": "completed", "tenant_id": "…",
  "result": { "product_id": "6017452168", "product_summary": [...], "stages": {...} },
  "error_message": "", "retry_count": 0, "max_retries": 3,
  "progress": { "stage": "completed", "percent": 100,
                "stages_completed": ["check_quota","ozon_upload",...],
                "stages_remaining": [], "message": "…" }
}
```

> `status` 枚举：`pending / running / completed / failed / cancelled`。`progress` 基于内存 12 阶段 STAGE_ORDER 计算，**worker 重启后丢失**（降级为无进度模式，仅返回 result）。`result.product_id` 即 Ozon 商品 ID（completed 时）。

### TaskStatisticsResponse（GET /api/v1/task_statistics）— KPI 卡数据源

```json
{ "total": 8, "pending": 0, "running": 0,
  "completed": 7, "failed": 1, "cancelled": 0,
  "avg_duration_seconds": 184.5 }
```

### 店铺统计（GET /api/v1/stores/{credential_id}/stats）— v0.57 新增

```json
{
  "credential_id": "uuid", "ozon_client_id": "4718259",
  "stats_date": "2026-08-19",
  "today_orders": 2, "today_sales_amount": 75.0,
  "today_commission": 3.2, "today_profit": 71.8,
  "today_product_count": 3
}
```

> 数据源 `ozon_orders_cache`（今日 UTC 自然日聚合）。**无评分字段**——缓存无 rating 数据，店铺卡不显示评分。归属校验失败（跨租户）→ 404。

### OrderListResponse（GET /api/v1/orders）

```json
{
  "items": [
    { "posting_number": "…", "status": "delivering", "raw_status": "…",
      "created_at": "2026-08-18T10:00:00Z",
      "products": [ { "name": "…", "quantity": 1, "offer_id": "…",
                      "product_id": "…", "image": "https://…" } ],
      "product_count": 1, "total_amount": 1234.5, "commission_amount": 120.0,
      "profit": 1000.0, "warehouse": "…", "delivery_method": "…" }
  ],
  "total": 257, "limit": 20, "offset": 0,
  "store": { "id": "uuid", "ozon_client_id": "4718259" },
  "last_synced_at": "2026-08-19T03:00:00Z", "sync_error": ""
}
```

> 查询参数：`credential_id`（店铺）/ `status`（待发货|已发货|配送中|已取消）/ `limit` / `offset` / `since_days` / `refresh`。数据源 PG 缓存 `ozon_orders_cache`（15min 调度器 + 手动同步 + 懒同步），非实时调 Ozon。

### 商品列表（GET /api/v1/products 与 GET /api/v1/products/ozon）

```json
// /products（在售货架，product_task_index 索引）
{ "items": [ { "product_id": "…", "offer_id": "…", "task_id": "…",
               "draft_id": null, "credential_id": "uuid",
               "created_at": "…", "moderation_status": "approved" } ],
  "total": 231, "limit": 20, "offset": 0 }

// /products/ozon（Ozon 在线商品，ozon_products_cache 缓存）
{ "items": [ { "product_id": "…", "offer_id": "…", "name": "…",
               "image": "https://…", "price": 1234.5, "stock": 10,
               "currency": "RUB" } ],
  "total": 100, "limit": 20, "offset": 0,
  "store": { "id": "uuid", "ozon_client_id": "4718259" },
  "last_synced_at": "…", "sync_error": "" }
```

### TaskListResponse（GET /api/v1/tasks）

```json
{ "items": [ { "id": "uuid", "status": "completed",
               "progress": { "stage": "completed", "percent": 100 },
               "product_summary": [...], "title": "宠物饮水机",
               "image": "https://…", "item_id": "1035536839701",
               "ozon_client_id": "4718259", "shop_name": "主店铺",
               "follow_sell": false, "update_mode": false,
               "parent_task_id": null } ],
  "total": 8, "limit": 20, "offset": 0 }
```

> 查询参数：`status` / `limit` / `offset`。`update_mode=true` = 编辑更新任务（改图/改价重传）；`parent_task_id` 有值 = 重上任务。

### CredentialOut（GET /api/v1/credentials）

```json
{ "id": "uuid", "ozon_client_id": "4718259",
  "api_key_masked": "****13d7", "shop_name": "主店铺",
  "currency": "RUB", "is_default": true, "credential_type": "api_key",
  "status": "active", "last_validated_at": "…", "last_rotated_at": null }
```

### ListingTemplateOut（GET /api/v1/templates）

```json
{ "id": "uuid", "tenant_id": "…", "name": "默认模板", "description": "",
  "platform": "ozon", "is_default": true,
  "config": { "margin_rate": 0.25, "commission_rate": 0.10, "fx_buffer": 0.05,
              "offer_id_prefix": "", "follow_type": "hand", "stock": 0,
              "warehouse_id": null },
  "store_overrides": { "<credential_id>": { "margin_rate": 0.30 } } }
```

> `store_overrides` 支持按店铺覆盖定价参数（PATCH/POST 同结构）。

### 提交历史（GET /api/v1/drafts/{draft_id}/submissions）

```json
{ "items": [ { "id": "uuid", "store_client_id": "4718259",
               "status": "published", "error_message": "",
               "extensions": { "margin_rate": 0.25 },
               "submitted_task_id": "task-uuid", "created_at": "…" } ] }
```

### HealthResponse（GET /health 或 /api/v1/store/health）

```json
{ "status": "ok", "message": "…", "db": "connected" }
```

## 5. 前端对接示例

```ts
import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1' })
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('ozon_webui_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 登录 → 采集箱 → 提交上架
const v = await api.post('/auth/verify', { token })
localStorage.setItem('ozon_webui_token', v.data.token)
const drafts = await api.get('/drafts')
const submit = await api.post(`/drafts/${id}/submit`, { token, credential_id })
const status = await api.get(`/task_status/${submit.data.task_id}`)

// v0.57 常用场景速查
// 1) 仪表盘 KPI：任务统计（今日订单/AI 上品数/成功率）
const stats = await api.get('/task_statistics')   // → TaskStatisticsResponse

// 2) 店铺卡：今日订单/销售额/利润
const s = await api.get(`/stores/${credentialId}/stats`)  // → 店铺统计 JSON
await api.post(`/stores/${credentialId}/sync`)            // 手动触发同步
const syncStatus = await api.get(`/stores/${credentialId}/sync-status`)

// 3) 订单中心（缓存读取，非实时）
const orders = await api.get('/orders', { params: { status: '配送中', limit: 20 } })

// 4) 商品管理（在售货架索引 + Ozon 在线商品双通道）
const shelf = await api.get('/products', { params: { limit: 20 } })
const ozonItems = await api.get('/products/ozon', { params: { credential_id } })

// 5) 任务中心（进度轮询）
const task = await api.get(`/task_status/${taskId}`)   // progress.percent 驱动进度条

// 6) 选品归档（全局共享，跨用户可见）
const runs = await api.get('/discovery/runs', { params: { limit: 50 } })
```

## 6. 实测验证（2026-08-19 对运行实例）

以下为对 `http://127.0.0.1:8080`（docker 容器 `deploy-worker-1`）的真实调用结果，供对接方对照：

| 请求 | 结果 | 结论 |
|---|---|---|
| `GET /health` | `200 {"status":"ok","db":"connected"}` | 服务与数据库正常 |
| `GET /api/v1/store/health` | `200 {"status":"unknown","message":"需要 client_id 和 api_key"}` | 需带店铺凭证 |
| `GET /api/v1/site/banners` | `200 []` | 公开端点免鉴权 |
| `GET /api/v1/site/announcements` | `200 []` | 公开端点免鉴权 |
| `GET /api/v1/drafts`（无 token） | `401 {"detail":"Token is required"}` | 业务端点强制鉴权 |
| `GET /api/v1/admin/overview`（无 token） | `401 {"detail":"Token is required"}` | 管理端点强制鉴权 |
| `GET /api/v1/analytics/bestsellers`（无 token） | `401` | 需 Bearer |
| `POST /api/v1/analytics/queries`（body 带 token） | `401 {"detail":"token_invalid or account_inactive"}` | body token 兜底机制生效 |
| `POST /api/v1/mxou/login`（错误凭据） | `401 {"detail":"MXOU 账号或密码错误"}` | 限流防爆破已接 |
| `GET /api/v1/stores/{credential_id}/stats`（有效凭证） | `200 今日订单/销售额/利润` | v0.57 店铺卡统计（跨租户 404） |
| `GET /api/v1/discovery/runs` | `200 全局归档列表` | v0.57 全局共享（跨用户可见） |

> 关键结论：部署实例为**生产鉴权模式**（Supabase 已配置），token 必须真实有效；唯一免鉴权入口为 `mxou/login` 与 `site/*` 公开端点。

## 7. 错误码

| 状态码 | 含义 | 处理 |
|---|---|---|
| 200 / 201 / 204 | 成功 | — |
| 401 | 无效 token | 重新登录 |
| 403 | 权限不足 / 跨租户 | 校验账号 |
| 404 | 资源不存在 | 检查路径参数 |
| 409 | 乐观锁冲突（draft PATCH） | 重新拉取后重试 |
| 422 | 参数校验失败 | 按 detail 修正 |
| 429 | 速率超限 | 退避重试 |
| 503 | 鉴权服务不可达 | 稍后重试（勿清 token） |
| 500 | 服务端错误 | 查 worker 日志 |

> 统一错误体：`{"detail": "错误信息"}`（FastAPI 标准）。

---

*端点自动提取自 worker 代码（117 个），与 `/api/v1/docs` Swagger 一致。*
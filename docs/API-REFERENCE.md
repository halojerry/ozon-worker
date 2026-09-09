# Ozon Worker API 参考（自动生成）

> 由 `worker/scripts/gen_api_docs.py` 从 FastAPI `app.openapi()` 生成 · 对应 v0.72.0 · 156 个 path / 63 个 schema · **勿手改**（CI Step 5d 校验漂移）。
> 对外约定（Base URL / 鉴权 / 限流 / 错误信封 / 分页 / 版本策略）见 `docs/API-OVERVIEW.md`；MCP 面见 `docs/MCP-SERVER.md`；交互式 Swagger `GET /docs`。

规范路径为 `/api/v1/...`；带「兼容别名」的端点同时挂在旧裸路径，语义一致。示例 JSON 只填 required 字段（schema 声明了 `examples` 的按声明渲染）。

## 目录

- [admin](#admin) （47）
- [analytics](#analytics) （9）
- [api](#api) （5）
- [async_run](#async-run) （1）
- [auth](#auth) （1）
- [cancel](#cancel) （1）
- [cancel_task](#cancel-task) （1）
- [categories](#categories) （2）
- [commissions](#commissions) （1）
- [credentials](#credentials) （9）
- [dashboard](#dashboard) （1）
- [discovery](#discovery) （2）
- [drafts](#drafts) （14）
- [error_reports](#error-reports) （2）
- [estimate](#estimate) （1）
- [forensics](#forensics) （1）
- [graph_parameter](#graph-parameter) （1）
- [health](#health) （1）
- [image-tasks](#image-tasks) （6）
- [logistics](#logistics) （1）
- [mappings](#mappings) （1）
- [mxou](#mxou) （9）
- [node_run](#node-run) （1）
- [orders](#orders) （12）
- [products](#products) （10）
- [progress](#progress) （2）
- [resubmit_task](#resubmit-task) （1）
- [run](#run) （1）
- [seo](#seo) （2）
- [settings](#settings) （2）
- [site](#site) （2）
- [source-candidates](#source-candidates) （1）
- [store](#store) （1）
- [stores](#stores) （12）
- [stream_run](#stream-run) （1）
- [submit_task](#submit-task) （1）
- [sync-jobs](#sync-jobs) （1）
- [task](#task) （1）
- [task_statistics](#task-statistics) （1）
- [task_status](#task-status) （1）
- [tasks](#tasks) （5）
- [templates](#templates) （5）
- [v1](#v1) （1）

## admin

### `GET /api/v1/admin/audit-logs`
List Logs

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/audit-logs`
Create Log

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `GET /api/v1/admin/audit-logs/`
List Logs

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/audit-logs/`
Create Log

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `GET /api/v1/admin/categories`
List Categories

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/admin/categories`
Create Category

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `GET /api/v1/admin/categories/`
List Categories

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/admin/categories/`
Create Category

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `PATCH /api/v1/admin/categories/{cat_id}`
Rename Category

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `cat_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/admin/categories/{cat_id}`
Delete Category

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `cat_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/config`
List Configs

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[ConfigListItem](#schema-configlistitem)] |

### `GET /api/v1/admin/config/`
List Configs

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[ConfigListItem](#schema-configlistitem)] |

### `GET /api/v1/admin/config/{name}`
Read Config

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `name` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PUT /api/v1/admin/config/{name}`
Write Config

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `name` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/config/{name}/backups`
List Backups

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `name` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[BackupItem](#schema-backupitem)] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/config/{name}/rollback`
Rollback Config

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `name` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/data-sources`
List Sources

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/data-sources`
Create Source

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `GET /api/v1/admin/data-sources/`
List Sources

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/data-sources/`
Create Source

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `POST /api/v1/admin/data-sources/import/csv`
Import Csv

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `GET /api/v1/admin/data-sources/{ds_id}`
Get Source

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `ds_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PATCH /api/v1/admin/data-sources/{ds_id}`
Update Source

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `ds_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/admin/data-sources/{ds_id}`
Delete Source

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `ds_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/logistics/rates`
Admin Logistics List Rates — 费率列表（limit ≤ 200，offset ≥ 0）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/logistics/rates/import`
Admin Logistics Import Rates — CSV 批量导入（键匹配 upsert；坏行跳过并记录）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [LogisticsImportResult](#schema-logisticsimportresult) |

### `PUT /api/v1/admin/logistics/rates/{rate_id}`
Admin Logistics Update Rate — 更新单条费率：校验失败 → 400，id 不存在 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `rate_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [LogisticsRateRow](#schema-logisticsraterow) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/overview`
Admin Overview

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AdminOverviewOut](#schema-adminoverviewout) |

### `GET /api/v1/admin/queries`
List Queries

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |
| `search` | query | string |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [QueryListOut](#schema-querylistout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/queries/`
List Queries

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |
| `search` | query | string |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [QueryListOut](#schema-querylistout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/admin/queries/import`
Import Queries

**请求体**（application/json，必填）：[QueryImportIn](#schema-queryimportin)

```json
{
  "items": [
    {}
  ],
  "csv": "string"
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [QueryImportResult](#schema-queryimportresult) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/admin/queries/{query_id}`
Delete Query

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `query_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [QueryDeleteOut](#schema-querydeleteout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/site/announcements`
Admin Site Announcements

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[SiteAnnouncementOut](#schema-siteannouncementout)] |

### `POST /api/v1/admin/site/announcements`
Admin Site Create Announcement

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | [SiteAnnouncementOut](#schema-siteannouncementout) |

### `PUT /api/v1/admin/site/announcements/{announcement_id}`
Admin Site Update Announcement

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `announcement_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [SiteAnnouncementOut](#schema-siteannouncementout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/admin/site/announcements/{announcement_id}`
Admin Site Delete Announcement

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `announcement_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/site/banners`
Admin Site Banners

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[SiteBannerOut](#schema-sitebannerout)] |

### `POST /api/v1/admin/site/banners`
Admin Site Create Banner

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | [SiteBannerOut](#schema-sitebannerout) |

### `PUT /api/v1/admin/site/banners/{banner_id}`
Admin Site Update Banner

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `banner_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [SiteBannerOut](#schema-sitebannerout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/admin/site/banners/{banner_id}`
Admin Site Delete Banner

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `banner_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/admin/stores`
Admin Stores

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[AdminStoreOut](#schema-adminstoreout)] |

### `GET /api/v1/admin/sync-health`
Admin Sync Health — 全部 active 店同步健康总览(仅 admin)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/admin/tasks`
Admin Tasks — 任务统计（全租户）——get_task_stats 是 async，必须 await。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/admin/users`
Admin Users

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[AdminUserOut](#schema-adminuserout)] |

### `POST /api/v1/admin/users`
Admin Create User

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |

### `GET /api/v1/admin/users/{user_id}`
Admin User Detail

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AdminUserDetailOut](#schema-adminuserdetailout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PATCH /api/v1/admin/users/{user_id}`
Admin Update User

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## analytics

### `GET /api/v1/analytics/bestsellers`
V1 Analytics List Bestsellers — T4b.1 榜单浏览：读 skill 上报的 ozon-bestsellers（全局共享，含贡献者列）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/analytics/categories`
Http Categories — 按类目聚合 discovery_runs 的选品次数和产品数(用户只看自己,admin 全局)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/analytics/hot-queries`
Http Hot Queries — 热门蓝海关键词:仅 admin(PRD:蓝海数据管理端独享)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/analytics/market-bestsellers`
V1 Analytics Market Bestsellers — skill market-bestsellers 全平台榜单数据上报（去重键 product_name+token）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AnalyticsReportResponse](#schema-analyticsreportresponse) |

### `GET /api/v1/analytics/market-overview`
Http Market Overview — 聚合市场概览:用户看自己店铺,admin 看全平台;热销品数保持全局共享目录。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/analytics/ozon-bestsellers`
V1 Analytics Ozon Bestsellers — skill ozon-bestsellers 榜单数据上报（去重键 sku_or_id+token）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AnalyticsReportResponse](#schema-analyticsreportresponse) |

### `POST /api/v1/analytics/queries`
V1 Analytics Queries — skill what-to-sell all-queries 关键词蓝海数据上报（去重键 query+token，重复上报 upsert 更新）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AnalyticsReportResponse](#schema-analyticsreportresponse) |

### `GET /api/v1/analytics/sales-trend`
Http Sales Trend — 销售趋势:用户看自己店铺,admin 看全平台。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/analytics/what-to-sell`
Http What To Sell — GET /api/v1/analytics/what-to-sell?credential_id=&sku=&limit= → {found, data}。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## api

### `GET /api/{path}`
Newapi Proxy — catch-all：命中 New API 前缀 → 转发 api.mxou.cn；否则 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/{path}`
Newapi Proxy — catch-all：命中 New API 前缀 → 转发 api.mxou.cn；否则 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PUT /api/{path}`
Newapi Proxy — catch-all：命中 New API 前缀 → 转发 api.mxou.cn；否则 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PATCH /api/{path}`
Newapi Proxy — catch-all：命中 New API 前缀 → 转发 api.mxou.cn；否则 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/{path}`
Newapi Proxy — catch-all：命中 New API 前缀 → 转发 api.mxou.cn；否则 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## async_run

### `POST /async_run`
Http Async Run — [DEPRECATED] 使用 POST /submit_task 代替。此端点将在未来版本移除。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |

## auth

### `POST /api/v1/auth/verify`
Auth Verify — Skill 鉴权端点。
> 兼容别名：`POST /auth/verify`（旧裸路径，语义相同）

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AuthVerifyResponse](#schema-authverifyresponse) |

响应示例：

```json
{
  "expires_in": 86400,
  "ozon_valid": true,
  "reason": "ok",
  "valid": true
}
```

## cancel

### `POST /cancel/{run_id}`
Http Cancel — 取消指定run_id的执行

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## cancel_task

### `POST /api/v1/cancel_task/{task_id}`
V1 Cancel Task — 取消待处理的任务。
> 兼容别名：`POST /cancel_task/{task_id}`（旧裸路径，语义相同）

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [CancelTaskResponse](#schema-canceltaskresponse) |
| 404 | Not Found | [ErrorBody](#schema-errorbody) |
| 409 | Conflict | [ErrorBody](#schema-errorbody) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## categories

### `GET /api/v1/categories/attributes`
V1 Categories Attributes — 类目属性 schema + 字典值（缓存优先，未命中按需拉取回写）。
> 兼容别名：`GET /categories/attributes`（旧裸路径，语义相同）

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/categories/search`
V1 Categories Search — 类目树搜索（ZH_HANS）：?q=关键词&limit=20 → 候选 {dc, tp, node_name, category_path}。
> 兼容别名：`GET /categories/search`（旧裸路径，语义相同）

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## commissions

### `GET /api/v1/commissions/lookup`
Http Commissions Lookup — 类目佣金查询：按 description_category_id 查 category_commission 缓存表。
> 兼容别名：`GET /commissions/lookup`（旧裸路径，语义相同）

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## credentials

### `GET /api/v1/credentials`
List Credentials

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[CredentialOut](#schema-credentialout)] |

### `POST /api/v1/credentials`
Create Credential

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | [CredentialOut](#schema-credentialout) |

响应示例：

```json
{
  "api_key_masked": "****0000",
  "created_at": "2026-09-01T00:00:00Z",
  "credential_type": "api_key",
  "currency": "CNY",
  "id": "3c9d2f4e-1111-4222-8333-444455556666",
  "is_default": true,
  "last_validated_at": "2026-09-08T10:00:00Z",
  "ozon_client_id": "5381204",
  "shop_name": "测试店",
  "status": "active",
  "updated_at": "2026-09-08T10:00:00Z"
}
```

### `PATCH /api/v1/credentials/{credential_id}`
Rotate Credential

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [CredentialOut](#schema-credentialout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

响应示例：

```json
{
  "api_key_masked": "****0000",
  "created_at": "2026-09-01T00:00:00Z",
  "credential_type": "api_key",
  "currency": "CNY",
  "id": "3c9d2f4e-1111-4222-8333-444455556666",
  "is_default": true,
  "last_validated_at": "2026-09-08T10:00:00Z",
  "ozon_client_id": "5381204",
  "shop_name": "测试店",
  "status": "active",
  "updated_at": "2026-09-08T10:00:00Z"
}
```

### `DELETE /api/v1/credentials/{credential_id}`
Revoke Credential

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/credentials/{credential_id}/data`
Hard Delete Credential Data — PRD M5(P2): 硬删除该店缓存/历史数据(管理端授权 + confirm 二次确认,默认关闭)。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/credentials/{credential_id}/session`
Get Session — 会话状态快照 {status, harvested_at, cookie_names}（永不回 cookie 值）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/credentials/{credential_id}/session`
Upload Session — 上传会话（加密存储）。跨租户/不存在 credential → 404；未配主密钥 → 500（同凭证端点文案）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**请求体**（application/json，必填）：object

```json
{
  "cookies": {
    "sc_company_id": "5371047"
  }
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/credentials/{credential_id}/session`
Revoke Session — 撤销会话（物理删行）。无会话 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/credentials/{credential_id}/validate`
Validate Credential

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ValidateResponse](#schema-validateresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## dashboard

### `GET /api/v1/dashboard/overview`
Dashboard Overview — 工作台聚合:今日订单/销售额/在售/待办/趋势/热销/最近订单。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## discovery

### `GET /api/v1/discovery/runs`
V1 Discovery List Runs — discover 选品结果历史读取（W4b.2）：全局共享（A 可见 B 的归档，含贡献者标注）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/discovery/runs`
V1 Discovery Report Run — discover 选品结果归档（W10 D12）：单次上报一条 run（keyword+filters+candidates），按 tenant 隔离。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [AnalyticsReportResponse](#schema-analyticsreportresponse) |

## drafts

### `GET /api/v1/drafts`
List Drafts — 列表（T-P3.1 批次契约）：可选 ?batch= 按 source_batch 精确过滤；缺席 = 不过滤（行为不变）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `batch` | query | string \| null |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[DraftOut](#schema-draftout)] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/drafts`
Create Draft

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [DraftOut](#schema-draftout) |

### `POST /api/v1/drafts/batch-submit`
Batch Submit Drafts — 批量提交草稿(≤50):逐条进行中守卫;返回 submitted/skipped/failed 明细。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/drafts/export`
Export Drafts — PRD M5(P2): 采集箱导出 CSV(租户隔离,UTF-8 BOM 兼容 Excel)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/drafts/import`
Import Drafts Csv — PRD M5b(P2): CSV/JSON 批量导入采集箱(竞品对标)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/drafts/{draft_id}`
Get Draft

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [DraftOut](#schema-draftout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PATCH /api/v1/drafts/{draft_id}`
Patch Draft

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [DraftOut](#schema-draftout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/drafts/{draft_id}`
Delete Draft — T10 采集箱删除：租户隔离；draft_submissions 由 FK CASCADE 级联删（验收：清空/删除级联删 submissions）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/drafts/{draft_id}/ai/{field}`
Draft Ai Field — 单字段 AI 重新生成（T14b）：只读，返回 RU 值，不写回草稿（前端 PATCH 保存）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |
| `field` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [DraftAiResponse](#schema-draftairesponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/drafts/{draft_id}/assemble`
Draft Assemble — 一键预组装（v0.70）：LLM 生成整卡上架信息并写回 payload（version++）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**请求体**（application/json，必填）：DraftAiRequest（内联）

```json
{
  "token": "string"
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [DraftAssembleResponse](#schema-draftassembleresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

响应示例：

```json
{
  "assembled": [
    "title",
    "description",
    "attributes",
    "tags"
  ],
  "estimated_pricing": {
    "old_price": 910.0,
    "price": 729.0,
    "promo_price": 547.0
  },
  "skipped": [],
  "suggested_category": {
    "category_name": "Автопоилка для животных",
    "description_category_id": "17029651",
    "type_id": "91633"
  },
  "version": 2
}
```

### `POST /api/v1/drafts/{draft_id}/estimate`
Estimate Draft — 预估售价/利润/物流费（纯读：不落库、不调 Ozon 上架）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/drafts/{draft_id}/resubmit`
Resubmit Draft — 失败/被拒草稿重新提交(进行中 → 409)。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [SubmitResponse](#schema-submitresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

响应示例：

```json
{
  "confirm_required": false,
  "draft_id": "a1b2c3d4-0000-4000-8000-000000000001",
  "existing_stores": [],
  "ok": true,
  "status": "pending",
  "submission_id": "a1b2c3d4-0000-4000-8000-000000000002",
  "task_id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f"
}
```

### `GET /api/v1/drafts/{draft_id}/submissions`
List Submissions — M2.2 提交时间线：草稿被提交过几次、到过哪些店、结果如何（created_at 倒序）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[SubmissionTimelineItem](#schema-submissiontimelineitem)] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/drafts/{draft_id}/submit`
Submit Draft

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `draft_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [SubmitResponse](#schema-submitresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

响应示例：

```json
{
  "confirm_required": false,
  "draft_id": "a1b2c3d4-0000-4000-8000-000000000001",
  "existing_stores": [],
  "ok": true,
  "status": "pending",
  "submission_id": "a1b2c3d4-0000-4000-8000-000000000002",
  "task_id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f"
}
```

## error_reports

### `GET /api/v1/error_reports`
V1 List Error Reports — 本租户错误报告列表（新→旧，status 可筛，limit≤200）。详情：?report_id=。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/error_reports`
V1 Create Error Report — 用户问题反馈错误报告（v0.69）：agent 按模板填写（含复现方式/证据）→ 落库。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## estimate

### `POST /api/v1/estimate`
Estimate Envelope Standalone — P2a 独立定价器：直接传 envelope（无 draft_id）→ 同源公式预估。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## forensics

### `GET /api/v1/forensics/task/{task_id}`
V1 Task Forensics — 任务取证一站式只读聚合（v0.70）：任务快照 + listing_result_log +
category_match_log + attr_match_log 四路事实。
> 兼容别名：`GET /forensics/task/{task_id}`（旧裸路径，语义相同）

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## graph_parameter

### `GET /graph_parameter`
Http Graph Inout Parameter

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## health

### `GET /api/v1/health`
V1 Health — 健康检查（含 PG 连通性）。
> 兼容别名：`GET /health`（旧裸路径，语义相同）

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [HealthResponse](#schema-healthresponse) |

## image-tasks

### `GET /api/v1/image-tasks`
List Image Tasks

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ImageTaskListResponse](#schema-imagetasklistresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/image-tasks`
Create Image Task — 同步 stub：status=completed, result=input_image_url。真实处理留后续批次。

**请求体**（application/json，必填）：[ImageTaskCreateRequest](#schema-imagetaskcreaterequest)

```json
{
  "type": "string",
  "input_image_url": "string"
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | [ImageTaskResponse](#schema-imagetaskresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/image-tasks/`
List Image Tasks

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ImageTaskListResponse](#schema-imagetasklistresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/image-tasks/`
Create Image Task — 同步 stub：status=completed, result=input_image_url。真实处理留后续批次。

**请求体**（application/json，必填）：[ImageTaskCreateRequest](#schema-imagetaskcreaterequest)

```json
{
  "type": "string",
  "input_image_url": "string"
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | [ImageTaskResponse](#schema-imagetaskresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/image-tasks/{task_id}`
Get Image Task

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ImageTaskResponse](#schema-imagetaskresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/image-tasks/{task_id}/cancel`
Cancel Image Task

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## logistics

### `POST /api/v1/logistics/quote`
Logistics Quote — 物流运费报价端点（v0.29.x, skill 选品利润估算用）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## mappings

### `GET /api/v1/mappings/lookup`
V1 Mappings Lookup — 类目映射查询（W11）：skill 端按关键词查已学习 Ozon 类目映射。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## mxou

### `GET /api/v1/mxou/balance`
Mxou Balance — 当前登录 token 的 MXOU 平台余额(登录页余额卡真实化)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/mxou/keys`
List Mxou Keys — 密钥列表（脱敏，走 _authenticate 鉴权）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[MxouKeyItem](#schema-mxoukeyitem)] |

### `POST /api/v1/mxou/keys`
Create Mxou Key — 新建密钥（响应含完整 key 仅一次；同时幂等 upsert 进 tokens 表）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [MxouKeyCreateResponse](#schema-mxoukeycreateresponse) |

### `DELETE /api/v1/mxou/keys/{key_id}`
Revoke Mxou Key — 吊销密钥（MXOU 删除成功 → 204）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/mxou/keys/{key_id}/select`
Select Mxou Key — 切换密钥：解出明文 key（仅此一次返回）+ 幂等 upsert 进 tokens 表。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [MxouKeySelectResponse](#schema-mxoukeyselectresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/mxou/login`
Mxou Login — MXOU 账号密码登录（无 token 鉴权——登录入口本身；限流防爆破）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [MxouLoginResponse](#schema-mxouloginresponse) |

### `POST /api/v1/mxou/logout`
Mxou Logout

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/mxou/me`
Mxou Me

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/mxou/my-key`
Get My Key — WebUI 登录后自动获取该用户已有的 enabled key（免手动建 key）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `uid` | query | string |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## node_run

### `POST /node_run/{node_id}`
Http Node Run

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `node_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## orders

### `GET /api/v1/orders`
List Orders — 订单列表：PG 缓存读取（v0.56）——未同步自动懒同步，?refresh=1 强制同步后返回。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | query | string \| null |  |  |
| `status` | query | string \| null |  |  |
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |
| `since_days` | query | integer |  |  |
| `refresh` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OrderListResponse](#schema-orderlistresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/orders/batch/labels`
Batch Labels — P1-3 批量面单：{posting_numbers: [...], credential_id?} → items + failed（失败隔离）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/orders/batch/ship`
Batch Ship — P1-3 批量备货：{posting_numbers: [...], credential_id?} → shipped + failed（失败隔离）。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/orders/message-templates`
Message Templates

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/orders/messages`
List Messages

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/orders/{posting_number}/cancel`
Cancel Order

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OrderActionResponse](#schema-orderactionresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/orders/{posting_number}/cancel-reasons`
List Cancel Reasons

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[CancelReasonOut](#schema-cancelreasonout)] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/orders/{posting_number}/label`
Get Order Label

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |
| `credential_id` | query | string \| null |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OrderLabelResponse](#schema-orderlabelresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/orders/{posting_number}/message`
Send Message

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/orders/{posting_number}/notes`
Get Order Notes

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OrderNoteOut](#schema-ordernoteout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PUT /api/v1/orders/{posting_number}/notes`
Upsert Order Notes

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OrderNoteOut](#schema-ordernoteout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/orders/{posting_number}/ship`
Ship Order

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `posting_number` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OrderActionResponse](#schema-orderactionresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## products

### `GET /api/v1/products`
List Products

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ProductListResponse](#schema-productlistresponse) |

### `POST /api/v1/products/bulk-archive`
Bulk Archive

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/products/bulk-prices`
Bulk Prices

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/products/bulk-stocks`
Bulk Stocks

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/products/ozon`
List Ozon Products — Ozon 店铺在线商品：PG 缓存读取（v0.56）——未同步懒同步，?refresh=1 强制。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [OzonProductListResponse](#schema-ozonproductlistresponse) |

### `GET /api/v1/products/{product_id}/cost`
Get Product Cost — 商品成本主数据 + 成本历史;归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `product_id` | path | string | ✓ |  |
| `credential_id` | query | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/products/{product_id}/edit`
Get Product Edit — T6: 在线商品编辑初值（product_task_index 关联草稿 envelope + 审核状态）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `product_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ProductEditResponse](#schema-producteditresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PATCH /api/v1/products/{product_id}/source`
Update Product Source — 手动维护商品成本/货源(manual 优先,写历史 + 重算订单利润);归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `product_id` | path | string | ✓ |  |

**请求体**（application/json，必填）：[ProductSourceUpdate](#schema-productsourceupdate)

```json
{
  "credential_id": "string",
  "purchase_cost": 0.0
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/products/{product_id}/source-candidates`
Get Source Candidates — 货源匹配候选列表(skill 上报 / discover 派生 / 手动维护),归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `product_id` | path | string | ✓ |  |
| `credential_id` | query | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/products/{product_id}/update_images`
Update Product Images

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `product_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [UpdateProductImagesResponse](#schema-updateproductimagesresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## progress

### `GET /api/v1/progress/{task_id}/stream`
Task Progress Stream — SSE 实时进度:Last-Event-ID 增量回放,断线重连不丢(PRD M4)。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /progress/{run_id}`
Http Progress — 查询工作流执行进度。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## resubmit_task

### `POST /api/v1/resubmit_task/{task_id}`
V1 Resubmit Task — 重新提交被拒(rejected)/失败(failed)的任务（P0-2 自动修复链入口）。
> 兼容别名：`POST /resubmit_task/{task_id}`（旧裸路径，语义相同）

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [SubmitTaskResponse](#schema-submittaskresponse) |
| 404 | Not Found | [ErrorBody](#schema-errorbody) |
| 409 | Conflict | [ErrorBody](#schema-errorbody) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

响应示例：

```json
{
  "message": "任务已提交",
  "ok": true,
  "task_id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f"
}
```

## run

### `POST /run`
Http Run

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |

## seo

### `GET /api/v1/seo/keywords`
Http Seo Keywords — 流量关键词公开查询。q 空 → top 流量；q 非空 → ILIKE 过滤。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/seo/keywords/`
Http Seo Keywords — 流量关键词公开查询。q 空 → top 流量；q 非空 → ILIKE 过滤。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## settings

### `GET /api/v1/settings`
Get Settings — 读当前用户设置(合并默认值,返回全量键)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `PUT /api/v1/settings`
Put Settings — 合并更新用户设置(仅已知键,数值范围校验)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## site

### `GET /api/v1/site/announcements`
Site Public Announcements

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/site/banners`
Site Public Banners

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## source-candidates

### `POST /api/v1/source-candidates`
Report Source Candidates — skill 图搜/跟卖匹配结果上报(POST /api/v1/source-candidates)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## store

### `GET /api/v1/store/health`
Store Health — 查询 Ozon 店铺配额健康状态。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `client_id` | query | string |  |  |
| `api_key` | query | string |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## stores

### `POST /api/v1/stores/sync-all`
Sync All Stores — 一键全店同步:入队所有 active 店 manual job(60s 冷却,去重)。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `GET /api/v1/stores/warehouses`
List Warehouses — 默认店铺的仓库字典(上架选仓下拉);未配置默认店 → 空列表。

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

### `POST /api/v1/stores/{credential_id}/actions`
Store Actions — 单店执行端点：operation 分发 + 接线 `_write_operation_log`。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/analysis`
Store Analysis — 店铺分析（todo 6）：利润率/库存/候选清单（summary + profit_trend + 三组清单）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/analytics-daily`
Store Analytics Daily — 该店店铺分析日表(访问/加购/转化/广告展示);归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |
| `days` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/daily-metrics`
Store Daily Metrics — 该店日聚合指标(趋势图数据源);归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |
| `days` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/returns`
Store Returns — 该店退货列表(PG 缓存,ozon_returns_cache);归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/stats`
Store Stats — 店铺卡统计（T4.6）：今日订单数/销售额/佣金/利润/件数（ozon_orders_cache 聚合）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/stores/{credential_id}/sync`
Sync Store — 手动同步单店:任务化入队 → 202 {job_id}；归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `PATCH /api/v1/stores/{credential_id}/sync-config`
Update Store Sync Config — 更新店铺同步配置(免 api_key;间隔下限 5min);归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**请求体**（application/json，必填）：[StoreSyncConfigUpdate](#schema-storesyncconfigupdate)

```json
{
  "sync_enabled": false,
  "sync_interval_minutes": 0,
  "sync_products_interval_minutes": 0
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/sync-jobs`
Store Sync Jobs History — 该店同步任务历史(分页);归属校验失败 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |
| `limit` | query | integer |  |  |
| `offset` | query | integer |  |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/stores/{credential_id}/sync-status`
Sync Status — 同步状态：最后同步时间 + 错误（webui 展示「上次同步 xx」）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `credential_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## stream_run

### `POST /stream_run`
Http Stream Run

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## submit_task

### `POST /api/v1/submit_task`
V1 Submit Task — 提交任务到队列。鉴权通过 Supabase tokens 表校验。
> 兼容别名：`POST /submit_task`（旧裸路径，语义相同）

**请求体**（application/json，必填）：SubmitTaskRequest（内联）

```json
{
  "envelope": {
    "draft": {
      "attributes": {
        "材质": "硅胶",
        "颜色": "蓝色"
      },
      "currency": "CNY",
      "dimensions": {
        "height": 60,
        "length": 150,
        "width": 90
      },
      "images": [
        "https://cbu01.alicdn.com/img/ibank/O1CN01example.jpg"
      ],
      "item_id": "812345678901",
      "purchase_cost": 8.5,
      "purchase_url": "https://detail.1688.com/offer/812345678901.html",
      "title": "便携折叠水杯 500ml 硅胶",
      "weight": 120
    },
    "extensions": {},
    "source": {
      "purchase_cost": 8.5,
      "purchase_url": "https://detail.1688.com/offer/812345678901.html"
    }
  },
  "max_retries": 3,
  "ozon_api_key": "00000000-0000-0000-0000-000000000000",
  "ozon_client_id": "5381204",
  "timeout_seconds": 1800,
  "token": "sk-xxxxxxxxxxxxxxxxxxxx"
}
```

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [SubmitTaskResponse](#schema-submittaskresponse) |
| 401 | Unauthorized | [ErrorBody](#schema-errorbody) |
| 402 | Payment Required | [ErrorBody](#schema-errorbody) |
| 403 | Forbidden | [ErrorBody](#schema-errorbody) |
| 429 | Too Many Requests | [ErrorBody](#schema-errorbody) |

响应示例：

```json
{
  "message": "任务已提交",
  "ok": true,
  "task_id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f"
}
```

## sync-jobs

### `GET /api/v1/sync-jobs/{job_id}`
Sync Job Detail — 单个同步任务状态/进度(前端轮询目标);跨租户 → 404。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `job_id` | path | integer | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## task

### `GET /task/{task_id}`
Http Get Task — [DEPRECATED] 使用 GET /task_status/{task_id} 代替。此端点将在未来版本移除。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | dict[str, any] |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## task_statistics

### `GET /api/v1/task_statistics`
V1 Task Statistics — 获取任务统计信息。
> 兼容别名：`GET /task_statistics`（旧裸路径，语义相同）

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [TaskStatisticsResponse](#schema-taskstatisticsresponse) |

## task_status

### `GET /api/v1/task_status/{task_id}`
V1 Task Status — 查询任务状态（v0.73: Bearer 鉴权 + 租户校验，TASK_STATUS_AUTH=0 应急关）。
> 兼容别名：`GET /task_status/{task_id}`（旧裸路径，语义相同）

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [TaskStatusResponse](#schema-taskstatusresponse) |
| 401 | Unauthorized | [ErrorBody](#schema-errorbody) |
| 404 | Not Found | [ErrorBody](#schema-errorbody) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

响应示例：

```json
{
  "created_at": "2026-09-08T10:00:00Z",
  "id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f",
  "max_retries": 3,
  "priority": 0,
  "progress": {
    "message": "生成主图 2/5",
    "percent": 53,
    "stage": "image_generation",
    "stages_completed": [
      "auth",
      "ingest",
      "category_match",
      "pricing",
      "attributes",
      "description"
    ],
    "stages_remaining": [
      "image_generation",
      "prepare_ozon_upload",
      "ozon_validate",
      "check_quota",
      "ozon_upload",
      "ozon_status",
      "learning_record"
    ]
  },
  "retry_count": 0,
  "started_at": "2026-09-08T10:00:05Z",
  "status": "running",
  "tenant_id": "user_0123456789abcdef",
  "timeout_seconds": 1800,
  "updated_at": "2026-09-08T10:01:30Z"
}
```

## tasks

### `GET /api/v1/tasks`
List Tasks

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [TaskListResponse](#schema-tasklistresponse) |

### `GET /api/v1/tasks/{task_id}/draft`
Get Task Draft — M1.1: task → 采集箱草稿（失败/被拒任务回采集箱改 → 重上）。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [TaskDraftResponse](#schema-taskdraftresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/tasks/{task_id}/images`
List Task Images

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [TaskImagesResponse](#schema-taskimagesresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/tasks/{task_id}/images/{slot}/regen`
Regen Task Image

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |
| `slot` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ImageRegenResponse](#schema-imageregenresponse) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `GET /api/v1/tasks/{task_id}/progress`
Task Progress Detail — 任务进度事件列表 + 汇总(PRD M4 时间线数据源)。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## templates

### `GET /api/v1/templates`
List Templates

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | list[[ListingTemplateOut](#schema-listingtemplateout)] |

### `POST /api/v1/templates`
Create Template

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 201 | Successful Response | [ListingTemplateOut](#schema-listingtemplateout) |

### `PATCH /api/v1/templates/{template_id}`
Update Template

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ListingTemplateOut](#schema-listingtemplateout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `DELETE /api/v1/templates/{template_id}`
Delete Template

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 204 | Successful Response | — |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

### `POST /api/v1/templates/{template_id}/default`
Set Default

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | string | ✓ |  |

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | [ListingTemplateOut](#schema-listingtemplateout) |
| 422 | Validation Error | [HTTPValidationError](#schema-httpvalidationerror) |

## v1

### `POST /v1/chat/completions`
Openai Chat Completions — OpenAI Chat Completions API 兼容接口

**响应**

| 状态码 | 说明 | Schema |
|---|---|---|
| 200 | Successful Response | — |

## Schemas

### AdminOverviewOut <a id="schema-adminoverviewout"></a>
平台概览。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `user_count` | integer |  | 用户数（默认 `0`） |
| `store_count` | integer |  | 活跃店铺数（默认 `0`） |
| `task_total` | integer |  | 任务总数（默认 `0`） |
| `task_today` | integer |  | 今日任务数（默认 `0`） |
| `success_rate` | number |  | 成功率（%）（默认 `0.0`） |
| `statistics` | dict[str, any] |  | 任务统计明细 |

### AdminStoreOut <a id="schema-adminstoreout"></a>
店铺行（跨用户平台视角）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 凭证 UUID |
| `tenant_id` | string | ✓ | 归属用户 ID |
| `ozon_client_id` | string | ✓ | Ozon Client-Id |
| `shop_name` | string |  | 店铺名（默认 `""`） |
| `currency` | string |  | 货币（默认 `"CNY"`） |
| `is_default` | boolean |  | 默认店铺（默认 `false`） |
| `status` | string |  | active/revoked（默认 `"active"`） |
| `last_validated_at` | string \| null |  | 最近校验 |

### AdminUserDetailOut <a id="schema-adminuserdetailout"></a>
用户详情（店铺 + 任务统计）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 用户 ID |
| `stores` | list[[AdminStoreOut](#schema-adminstoreout)] |  | 店铺列表 |
| `task_total` | integer |  | 任务总数（默认 `0`） |
| `task_completed` | integer |  | 已完成（默认 `0`） |
| `task_failed` | integer |  | 失败（默认 `0`） |

### AdminUserOut <a id="schema-adminuserout"></a>
用户行（平台视角）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 用户 ID |
| `username` | string |  | 用户名/显示名（默认 `""`） |
| `quota` | number \| null |  | 余额 |
| `role` | string |  | user/admin（默认 `"user"`） |
| `created_at` | string \| null |  | 注册时间 |
| `store_count` | integer |  | 活跃店铺数（默认 `0`） |
| `task_count` | integer |  | 任务总数（默认 `0`） |

### AnalyticsReportResponse <a id="schema-analyticsreportresponse"></a>
上报成功响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string |  | 状态: ok / error（默认 `"ok"`） |
| `inserted` | integer |  | 本次新增行数（默认 `0`） |
| `upserted` | integer |  | 本次覆盖更新行数（默认 `0`） |

### AuthVerifyResponse <a id="schema-authverifyresponse"></a>
Skill 鉴权响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | boolean | ✓ | 是否有效 |
| `reason` | string |  | 原因: ok / token_invalid / balance_insufficient / account_inactive（默认 `"ok"`） |
| `expires_in` | integer |  | 缓存有效期（秒）（默认 `86400`） |
| `ozon_valid` | boolean \| null |  | Ozon API 是否有效（仅当传了 client_id/api_key 时返回） |

示例：

```json
{
  "expires_in": 86400,
  "ozon_valid": true,
  "reason": "ok",
  "valid": true
}
```

### BackupItem <a id="schema-backupitem"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✓ |  |
| `size` | integer | ✓ |  |
| `mtime` | number | ✓ |  |

### CancelReasonOut <a id="schema-cancelreasonout"></a>
订单取消原因（/v1/posting/fbs/cancel-reason）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | integer | ✓ | 原因 ID |
| `title` | string |  | 原因标题（默认 `""`） |

### CancelTaskResponse <a id="schema-canceltaskresponse"></a>
取消任务响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `task_id` | string | ✓ | 任务 UUID |
| `message` | string | ✓ | 取消结果消息 |

### ConfigListItem <a id="schema-configlistitem"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✓ |  |

### CredentialOut <a id="schema-credentialout"></a>
凭证响应 — 仅掩码，永不包含明文 api_key / ozon_api_key_enc。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 凭证 UUID |
| `ozon_client_id` | string | ✓ | Ozon 卖家 Client-Id |
| `api_key_masked` | string | ✓ | 掩码 ****abcd（仅后 4 位） |
| `shop_name` | string \| null |  | 店铺名称 |
| `currency` | string |  | CNY/RUB（默认 `"CNY"`） |
| `is_default` | boolean |  | 默认店铺标记（默认 `false`） |
| `credential_type` | string |  | api_key \| oauth（预留）（默认 `"api_key"`） |
| `status` | string |  | active/revoked（默认 `"active"`） |
| `last_validated_at` | string(date-time) \| null |  | 最近一次校验时间 |
| `last_rotated_at` | string(date-time) \| null |  | 最近一次轮换时间 |
| `created_at` | string(date-time) \| null |  | 创建时间 |
| `updated_at` | string(date-time) \| null |  | 更新时间 |

示例：

```json
{
  "api_key_masked": "****0000",
  "created_at": "2026-09-01T00:00:00Z",
  "credential_type": "api_key",
  "currency": "CNY",
  "id": "3c9d2f4e-1111-4222-8333-444455556666",
  "is_default": true,
  "last_validated_at": "2026-09-08T10:00:00Z",
  "ozon_client_id": "5381204",
  "shop_name": "测试店",
  "status": "active",
  "updated_at": "2026-09-08T10:00:00Z"
}
```

### DraftAiResponse <a id="schema-draftairesponse"></a>
T14b: 单字段 AI 重新生成响应（只读结果，前端决定 PATCH 保存）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `field` | string | ✓ | title/description/attributes/tags |
| `value` | string | ✓ | 俄语 RU 值（非空，无中文/拉丁残留） |

### DraftAssembleResponse <a id="schema-draftassembleresponse"></a>
POST /drafts/{id}/assemble 响应（v0.70 一键预组装：整卡生成并写回 payload）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assembled` | list[string] |  | 实际生成成功并写回 payload 的字段（title/description/attributes/tags 子集） |
| `skipped` | list[string] |  | 跳过的字段（已含西里尔幂等跳过 / 源为空 / ozon_attributes 已有内容不混源 / 生成失败） |
| `suggested_category` | dict[str, any] \| null |  | 类目建议 {description_category_id, type_id, category_name}（展示字段；只写 draft.suggested_category，绝不写 draft.ozon_category 劫持管线仲裁链） |
| `estimated_pricing` | dict[str, any] \| null |  | 三档预估价 RUB {price, old_price, promo_price?}（展示字段；pricing_node 永远按成本+margin 重算，管线不消费） |
| `version` | integer | ✓ | 写回后的草稿版本（version++） |

示例：

```json
{
  "assembled": [
    "title",
    "description",
    "attributes",
    "tags"
  ],
  "estimated_pricing": {
    "old_price": 910.0,
    "price": 729.0,
    "promo_price": 547.0
  },
  "skipped": [],
  "suggested_category": {
    "category_name": "Автопоилка для животных",
    "description_category_id": "17029651",
    "type_id": "91633"
  },
  "version": 2
}
```

### DraftOut <a id="schema-draftout"></a>
草稿详情（payload = envelope，不含任何凭证）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string(uuid) | ✓ | 草稿 ID |
| `tenant_id` | string | ✓ | 归属用户（_authenticate_token 的 user_id） |
| `payload` | dict[str, any] | ✓ | envelope {draft, source, extensions}；无 api_key 明文 |
| `source` | string |  | 'skill' \| 'webui'（默认 `"skill"`） |
| `version` | integer |  | 乐观并发版本（PATCH 带旧 version，不匹配 → 409）（默认 `1`） |
| `created_at` | string(date-time) \| null |  | 创建时间 |
| `updated_at` | string(date-time) \| null |  | 更新时间 |
| `submission_status` | string \| null |  | 最新一次提交状态（draft_submissions.status）：pending/uploading/published/failed；NULL = 未上架（C1 状态机，T10 采集箱列） |
| `image_mirror_state` | string |  | 图片镜像状态（M5b）：''=未启用/未镜像；pending=镜像中；mirrored=已转存 COS；failed=失败保持外链（默认 `""`） |
| `notes` | string \| null |  | 运营备注（采集/选品依据人工标注）；不进信封 payload |
| `source_batch` | string \| null |  | 来源批次标识（T-P3.1 批次契约）：采集批次精确过滤用；NULL = 无批次（老 skill 创建的草稿） |

### ErrorBody <a id="schema-errorbody"></a>
统一错误响应体。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `false`） |
| `error_code` | string | ✓ | 错误码，如 TOKEN_INVALID、RATE_LIMITED |
| `message` | string | ✓ | 人类可读的错误描述 |
| `detail` | any \| null |  | 附加详情（调试用） |

示例：

```json
{
  "error_code": "TOKEN_INVALID",
  "message": "token_invalid or account_inactive",
  "ok": false
}
```

### HTTPValidationError <a id="schema-httpvalidationerror"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | list[[ValidationError](#schema-validationerror)] |  |  |

### HealthResponse <a id="schema-healthresponse"></a>
健康检查响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | ✓ | 服务状态: ok / degraded |
| `message` | string | ✓ | 状态描述 |
| `db` | string | ✓ | 数据库连接状态: connected / disconnected |

### ImageRegenResponse <a id="schema-imageregenresponse"></a>
POST /tasks/{id}/images/{slot}/regen 响应（新版本行）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `task_id` | string | ✓ | 任务 UUID |
| `slot` | string | ✓ | 槽位 |
| `version` | integer | ✓ | 新版本号（prev+1） |
| `url` | string | ✓ | 新图片 URL |
| `params` | dict[str, any] \| null |  | 节点 Input schema 快照 |
| `image_parent_task_id` | string \| null |  | resubmit 图片血缘 |

### ImageTaskCreateRequest <a id="schema-imagetaskcreaterequest"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | ✓ |  |
| `input_image_url` | string | ✓ |  |
| `params` | dict[str, any] \| null |  |  |

### ImageTaskListResponse <a id="schema-imagetasklistresponse"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | list[[ImageTaskResponse](#schema-imagetaskresponse)] | ✓ |  |
| `total` | integer | ✓ |  |
| `limit` | integer | ✓ |  |
| `offset` | integer | ✓ |  |

### ImageTaskResponse <a id="schema-imagetaskresponse"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ |  |
| `type` | string | ✓ |  |
| `input_image_url` | string | ✓ |  |
| `status` | string | ✓ |  |
| `result_image_url` | string \| null |  |  |
| `params` | dict[str, any] \| null |  |  |
| `error_message` | string \| null |  |  |
| `created_at` | string \| null |  |  |
| `updated_at` | string \| null |  |  |

### ListingTemplateConfig <a id="schema-listingtemplateconfig"></a>
模板扩展参数（白名单；全部可选，None 表示不注入）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `margin_rate` | number \| null |  | 利润率（0-1），不设则 worker 默认 0.25 |
| `commission_rate` | number \| null |  | 佣金率；0=让 worker 自动查店铺真实佣金 |
| `fx_buffer` | number \| null |  | 汇率缓冲（0-0.5），不设则 worker 默认 0.05 |
| `margin_floor` | number \| null |  | 三档定价促销利润率下限（0-2），不设则 worker 默认 |
| `margin_anchor` | number \| null |  | 三档定价锚点倍数（0-5），old_price=anchor 档，不设则 worker 默认 |
| `variable_cost_rate` | number \| null |  | 日常变动成本率（0-0.5），不设则 worker 默认 0.155 |
| `promo_variable_cost_rate` | number \| null |  | 促销变动成本率（0-0.5），不设则 worker 默认 0.245 |
| `traffic_keywords` | list[string] \| null |  | 标题流量关键词列表（extensions.traffic_keywords 扁平键） |
| `offer_id_prefix` | string \| null |  | 货号前缀（仅新建上架生效；更新模式忽略） |
| `follow_type` | string \| null |  | 跟卖方式：hand 防侵权 / api 强制 |
| `stock` | integer \| null |  | 上架后库存（extensions.stock） |
| `warehouse_id` | string \| null |  | 仓库（extensions.warehouse_id） |

### ListingTemplateOut <a id="schema-listingtemplateout"></a>
上架配置模板响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 模板 UUID |
| `tenant_id` | string | ✓ | 所属租户 |
| `name` | string | ✓ | 配置名称 |
| `description` | string |  | 备注（默认 `""`） |
| `platform` | string |  | 平台（默认 `"OZON"`） |
| `is_default` | boolean |  | 默认模板标记（默认 `false`） |
| `config` | [ListingTemplateConfig](#schema-listingtemplateconfig) |  | 扩展参数 |
| `store_overrides` | dict[str, [ListingTemplateConfig](#schema-listingtemplateconfig)] \| null |  | 按店铺（credential_id）差异化覆盖配置 |
| `created_at` | string(date-time) \| null |  | 创建时间 |
| `updated_at` | string(date-time) \| null |  | 更新时间 |

### LogisticsImportResult <a id="schema-logisticsimportresult"></a>
导入结果：inserted/updated 计数 + 逐行错误。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `imported` | integer | ✓ |  |
| `updated` | integer | ✓ |  |
| `errors` | list[dict[str, any]] |  |  |

### LogisticsRateRow <a id="schema-logisticsraterow"></a>
单条费率行（服务返回结构，供文档/校验用）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | integer | ✓ |  |
| `scoring_group` | string | ✓ |  |
| `service_level` | string | ✓ |  |
| `tpl_provider` | string | ✓ |  |
| `delivery_method` | string \| null |  |  |
| `base_cost` | number | ✓ |  |
| `per_gram_rate` | number | ✓ |  |
| `weight_min` | integer | ✓ |  |
| `weight_max` | integer | ✓ |  |
| `sum_limit_cm` | integer | ✓ |  |
| `longest_limit_cm` | integer | ✓ |  |
| `charge_type` | string | ✓ |  |
| `vol_weight_divisor` | integer |  | （默认 `0`） |
| `created_at` | string \| null |  |  |

### MxouKeyCreateResponse <a id="schema-mxoukeycreateresponse"></a>
新建 API Key 响应（key 仅此一次返回——用户复制后不再可查）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | token id |
| `name` | string | ✓ | token 名称 |
| `key` | string | ✓ | 新建密钥完整值（仅此一次返回） |

### MxouKeyItem <a id="schema-mxoukeyitem"></a>
MXOU API Key 条目（脱敏展示，绝不含 full_key）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | token id |
| `name` | string |  | token 名称（默认 `""`） |
| `masked` | boolean |  | key 是否为脱敏形态（masked=true 时不含明文）（默认 `true`） |
| `status` | integer |  | token 状态（1=enabled）（默认 `1`） |

### MxouKeySelectResponse <a id="schema-mxoukeyselectresponse"></a>
切换密钥响应（key 仅此一次返回——用户复制后不再可查）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | string | ✓ | 所选密钥完整值（仅此一次返回） |

### MxouLoginResponse <a id="schema-mxouloginresponse"></a>
MXOU 登录成功响应（keys 已脱敏；选中 key 完整值仅此一次返回用于建立登录态）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | string | ✓ | MXOU 用户名 |
| `balance` | number \| null |  | 平台真实余额（美元，/v1/dashboard/billing/subscription 同源；查询失败 None） |
| `keys` | list[[MxouKeyItem](#schema-mxoukeyitem)] |  | API Key 列表（已脱敏，无 full_key） |
| `selected_key_id` | string \| null |  | 选中的 enabled key id（未选到 None） |
| `key` | string \| null |  | 选中 key 完整值（sk- 前缀；仅登录成功返回一次，WebUI 用它建立登录态） |
| `session_expires_at` | string \| null |  | MXOU 登录 session 过期时间 |
| `role` | string |  | 用户角色（admin/user，WebUI 管理员路由守卫用）（默认 `"user"`） |

### OrderActionResponse <a id="schema-orderactionresponse"></a>
订单写入操作响应（备货/取消）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | 操作是否提交成功（默认 `true`） |
| `posting_number` | string | ✓ | 货件编号 |
| `result` | dict[str, any] |  | Ozon 返回 result |

### OrderLabelResponse <a id="schema-orderlabelresponse"></a>
面单 PDF 响应（base64，路由层编码）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `posting_number` | string | ✓ | 货件编号 |
| `content_type` | string |  | MIME（默认 `"application/pdf"`） |
| `label_base64` | string | ✓ | PDF base64 |

### OrderListResponse <a id="schema-orderlistresponse"></a>
订单列表响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | list[[OrderOut](#schema-orderout)] |  |  |
| `total` | integer |  | 订单总数（默认 `0`） |
| `limit` | integer |  | 本次页大小（默认 `50`） |
| `offset` | integer |  | 偏移（默认 `0`） |
| `store` | dict[str, any] |  | 查询店铺 {id, ozon_client_id} |
| `last_synced_at` | string \| null |  | 最近同步时间（v0.56 缓存） |
| `sync_error` | string \| null |  | 最近同步错误（v0.56） |
| `sync_status` | string \| null |  | 数据新鲜度 never/syncing/ok/stale（PRD M1） |

### OrderNoteOut <a id="schema-ordernoteout"></a>
订单货源/采购信息标注（P1-1 本地元数据）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `posting_number` | string | ✓ | Ozon FBS 货件编号 |
| `tenant_id` | string | ✓ | 所属租户 |
| `source_url` | string |  | 货源地址（默认 `""`） |
| `source_cost` | number \| null |  | 货源价格（CNY） |
| `source_remark` | string |  | 货源备注（默认 `""`） |
| `purchase_no` | string |  | 采购单号（默认 `""`） |
| `purchase_carrier` | string |  | 采购快递（默认 `""`） |
| `purchase_tracking` | string |  | 采购快递单号（默认 `""`） |
| `created_at` | string \| null |  | 创建时间 |
| `updated_at` | string \| null |  | 更新时间 |

### OrderOut <a id="schema-orderout"></a>
订单行（Ozon FBS posting 标准化）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `posting_number` | string | ✓ | 货件编号 |
| `status` | string | ✓ | 统一态：pending/awaiting/waiting/delivering/delivered/cancelled/other |
| `raw_status` | string |  | Ozon 原始状态（默认 `""`） |
| `created_at` | string \| null |  | 下单时间（ISO） |
| `products` | list[[OrderProductOut](#schema-orderproductout)] |  | 商品行 |
| `product_count` | integer |  | 商品总件数（默认 `0`） |
| `total_amount` | number |  | 订单金额（默认 `0.0`） |
| `commission_amount` | number |  | 平台费用（默认 `0.0`） |
| `profit` | number \| null |  | 估算利润（金额-费用） |
| `real_profit` | number \| null |  | 真实利润(有成本才填,PRD M3) |
| `warehouse` | string |  | 仓库（默认 `""`） |
| `delivery_method` | string |  | 配送方式（默认 `""`） |
| `cancel_reason` | string |  | 取消原因（默认 `""`） |
| `cancellation` | string |  | 取消方/类型（默认 `""`） |

### OrderProductOut <a id="schema-orderproductout"></a>
订单内商品行。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string |  | 商品名称（默认 `""`） |
| `sku` | integer \| null |  | Ozon SKU |
| `quantity` | integer |  | 数量（默认 `0`） |
| `price` | number \| null |  | 单价 |
| `offer_id` | string |  | 货号（默认 `""`） |
| `product_id` | integer \| null |  | Ozon product_id（与 sku 同值，供图查） |
| `image` | string \| null |  | 主图 URL（T4.3：/v3/product/info/list images[0]） |

### OzonProductListResponse <a id="schema-ozonproductlistresponse"></a>
Ozon 在线商品列表响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | list[[OzonProductOut](#schema-ozonproductout)] |  |  |
| `total` | integer |  | 商品总数（默认 `0`） |
| `limit` | integer |  | 本次页大小（默认 `50`） |
| `offset` | integer |  | 偏移（默认 `0`） |
| `store` | dict[str, any] |  | 查询店铺 {id, ozon_client_id} |
| `last_synced_at` | string \| null |  | 最近同步时间（v0.56 缓存） |
| `sync_error` | string \| null |  | 最近同步错误（v0.56） |
| `sync_status` | string \| null |  | 数据新鲜度 never/syncing/ok/stale（PRD M1） |

### OzonProductOut <a id="schema-ozonproductout"></a>
Ozon 店铺在线商品（v0.50 实时拉取，覆盖非本系统上架商品）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `product_id` | string | ✓ | Ozon product_id |
| `offer_id` | string |  | 货号（默认 `""`） |
| `name` | string |  | 商品名称（默认 `""`） |
| `image` | string \| null |  | 主图 URL |
| `price` | number \| null |  | 售价 |
| `old_price` | number \| null |  | 划线价(PRD M3) |
| `min_price` | number \| null |  | 最低价(PRD M3) |
| `stock` | integer \| null |  | 可用库存 |
| `currency` | string |  | 货币代码（默认 `""`） |
| `status` | string |  | visible/archived/error(PRD M3)（默认 `""`） |
| `error` | list[any] \| null |  | Ozon 错误明细(PRD M3) |
| `archived` | boolean |  | 是否归档(PRD M3)（默认 `false`） |

### ProductEditResponse <a id="schema-producteditresponse"></a>
T6: GET /products/{product_id}/edit — 在线商品编辑初值。  数据来源：product_task_index 关联草稿（直连任务无草稿 → 409，仅改图走 update_images）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `product_id` | string | ✓ | Ozon product_id |
| `offer_id` | string | ✓ | 信封 offer_id（sku_id / follow_{id}） |
| `credential_id` | string \| null |  | 店铺凭证 id |
| `draft_id` | string | ✓ | 关联草稿 id（product_task_index.draft_id） |
| `draft_version` | integer |  | 关联草稿乐观锁版本(PATCH /drafts 提交用)（默认 `1`） |
| `payload` | dict[str, any] | ✓ | 关联草稿 envelope（编辑表单初值） |
| `moderation_status` | string \| null |  | 审核状态（从任务 result JSONB 尽力提取；无 → null，不实时调 Ozon） |

### ProductListItem <a id="schema-productlistitem"></a>
在售商品列表项 — 只读，product_task_index 行 + 任务 result 审核状态。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `product_id` | string | ✓ | Ozon product_id（上传成功后回填） |
| `offer_id` | string | ✓ | 信封 offer_id（sku_id / follow_{id}） |
| `task_id` | string | ✓ | 上架任务 UUID |
| `draft_id` | string \| null |  | 采集箱草稿 id；直连任务为 null |
| `credential_id` | string \| null |  | 店铺凭证 id |
| `created_at` | string(date-time) \| null |  | 索引创建时间 |
| `moderation_status` | string \| null |  | 审核状态（从任务 result JSONB 尽力提取，无 → null；不实时调 Ozon，任务终态即最新） |

### ProductListResponse <a id="schema-productlistresponse"></a>
在售商品列表响应（M2.1）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | list[[ProductListItem](#schema-productlistitem)] |  | 在售商品列表（created_at DESC） |
| `total` | integer |  | 该租户商品总数（分页前）（默认 `0`） |
| `limit` | integer |  | 本次分页大小（1-100）（默认 `20`） |
| `offset` | integer |  | 本次偏移（默认 `0`） |

### ProductSourceUpdate <a id="schema-productsourceupdate"></a>
成本/货源手动维护(PATCH /products/{id}/source,manual 最高优先级)。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `credential_id` | string | ✓ | 店铺凭证 id(归属校验) |
| `purchase_url` | string |  | 1688 货源链接（默认 `""`） |
| `purchase_cost` | number | ✓ | 到仓成本(CNY,含国内运费) |
| `freight_cny` | number \| null |  | 1688 国内运费(可选) |
| `supplier` | string |  | 1688 店铺名（默认 `""`） |

### QueryDeleteOut <a id="schema-querydeleteout"></a>
删除结果。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `deleted` | boolean |  | （默认 `true`） |

### QueryImportIn <a id="schema-queryimportin"></a>
导入请求体：csv 文本与 items 数组二选一。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | list[dict[str, any]] \| null |  |  |
| `csv` | string \| null |  |  |

### QueryImportResult <a id="schema-queryimportresult"></a>
导入结果：新增/更新计数 + 逐行错误。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `imported` | integer |  | （默认 `0`） |
| `updated` | integer |  | （默认 `0`） |
| `errors` | list[dict[str, any]] |  |  |

### QueryListOut <a id="schema-querylistout"></a>
库浏览响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `total` | integer |  | （默认 `0`） |
| `items` | list[[QueryRow](#schema-queryrow)] |  |  |

### QueryRow <a id="schema-queryrow"></a>
关键词行（库浏览返回项）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | integer | ✓ |  |
| `query` | string | ✓ |  |
| `count` | integer |  | （默认 `0`） |
| `ca` | number \| null |  |  |
| `avg_ca_rub` | number \| null |  |  |
| `avg_count_items` | number \| null |  |  |
| `items_views` | number \| null |  |  |
| `uniq_queries_wca` | integer \| null |  |  |
| `uniq_sellers` | number \| null |  |  |
| `source` | string |  | （默认 `"fetched"`） |
| `created_at` | string \| null |  |  |

### SiteAnnouncementOut <a id="schema-siteannouncementout"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string |  | （默认 `""`） |
| `content` | string | ✓ |  |
| `announcement_type` | string |  | （默认 `"banner"`） |
| `enabled` | boolean |  | （默认 `true`） |
| `id` | integer | ✓ |  |
| `created_at` | string(date-time) \| null |  |  |

### SiteBannerOut <a id="schema-sitebannerout"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `image_url` | string | ✓ |  |
| `link_url` | string \| null |  |  |
| `title` | string |  | （默认 `""`） |
| `sort_order` | integer |  | （默认 `0`） |
| `enabled` | boolean |  | （默认 `true`） |
| `id` | integer | ✓ |  |
| `created_at` | string(date-time) \| null |  |  |
| `updated_at` | string(date-time) \| null |  |  |

### StoreSyncConfigUpdate <a id="schema-storesyncconfigupdate"></a>
店铺同步配置更新(PATCH /stores/{id}/sync-config,免 api_key;间隔下限 5min)。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sync_enabled` | boolean \| null |  | 定时同步开关(手动同步仍可用) |
| `sync_interval_minutes` | integer \| null |  | 订单同步间隔(分钟) |
| `sync_products_interval_minutes` | integer \| null |  | 商品同步间隔(分钟) |

### SubmissionTimelineItem <a id="schema-submissiontimelineitem"></a>
M2.2: 草稿提交时间线条目（draft_submissions 行，created_at 倒序）。  供 WebUI 展示「这个草稿被提交过几次、到过哪些店、结果如何」。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string(uuid) | ✓ | submission 记录 ID（draft_submissions.id） |
| `store_client_id` | string \| null |  | 目标店铺 Ozon Client-Id |
| `status` | string | ✓ | 提交状态：pending/uploading/published/failed/rejected（M0.3 写回） |
| `error_message` | string \| null |  | 失败/被拒原因 |
| `extensions` | dict[str, any] |  | 提交时 extensions 快照（定价/仓库/库存） |
| `submitted_task_id` | string \| null |  | 关联任务 ID（ozon_product_tasks.id） |
| `created_at` | string(date-time) \| null |  | 提交时间 |

### SubmitResponse <a id="schema-submitresponse"></a>
提交成功响应（含 C5 跨店确认标记）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `draft_id` | string(uuid) | ✓ | 草稿 ID（多次提交永不变） |
| `submission_id` | string(uuid) \| null |  | 本次提交记录 ID（draft_submissions.id） |
| `task_id` | string |  | ozon_product_tasks.id（默认 `""`） |
| `status` | string |  | 提交记录状态：pending/uploading/published/failed（默认 `"pending"`） |
| `confirm_required` | boolean |  | 跨店提醒：该草稿已提交到其他店铺（不硬拦）（默认 `false`） |
| `existing_stores` | list[string] |  | 已有提交的店铺 client_id 列表 |

示例：

```json
{
  "confirm_required": false,
  "draft_id": "a1b2c3d4-0000-4000-8000-000000000001",
  "existing_stores": [],
  "ok": true,
  "status": "pending",
  "submission_id": "a1b2c3d4-0000-4000-8000-000000000002",
  "task_id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f"
}
```

### SubmitTaskResponse <a id="schema-submittaskresponse"></a>
提交任务成功响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `task_id` | string | ✓ | 任务 UUID，用于轮询状态 |
| `message` | string | ✓ | 提交成功消息 |

示例：

```json
{
  "message": "任务已提交",
  "ok": true,
  "task_id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f"
}
```

### TaskDraftResponse <a id="schema-taskdraftresponse"></a>
GET /tasks/{task_id}/draft 响应（M1.1 失败/被拒任务 → 找回采集箱草稿）。  解析顺序：draft_submissions.submitted_task_id → product_task_index.task_id → None （直连任务无 submission 行时回落到 product_task_index；都无 → draft_id=None）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `draft_id` | string \| null |  | 采集箱草稿 UUID；无关联草稿（直连任务）→ None |

### TaskImageItem <a id="schema-taskimageitem"></a>
单张生图缓存行（URL 元数据，不存二进制）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `slot` | string | ✓ | 槽位: main/white_bg/multi_angle/detail/social_proof/comparison/scene_1..3/variant_{idx} |
| `version` | integer | ✓ | 生成版本（1 起；regen 递增） |
| `url` | string | ✓ | 图片 URL（COS/1688 alicdn/Ozon，前端自行处理失效） |
| `params` | dict[str, any] \| null |  | 节点 Input schema 原样快照 |
| `image_parent_task_id` | string \| null |  | resubmit 图片血缘（原 task_id；区别于任务级 payload.parent_task_id） |
| `created_at` | string(date-time) \| null |  | 生成时间 |

### TaskImagesResponse <a id="schema-taskimagesresponse"></a>
GET /tasks/{id}/images 响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `task_id` | string | ✓ | 任务 UUID |
| `images` | list[[TaskImageItem](#schema-taskimageitem)] |  | 全部槽位 × 版本 |

### TaskListItem <a id="schema-tasklistitem"></a>
任务列表项 — 只读摘要，不含 payload（体积大且含敏感 token）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 任务 UUID |
| `status` | [TaskStatus](#schema-taskstatus) | ✓ | 任务状态 |
| `progress` | dict[str, any] \| null |  | 实时进度 {stage, percent, stages_completed[], stages_remaining[], message} |
| `product_summary` | list[dict[str, any]] |  | 产品摘要（result.product_summary，completed 时有值） |
| `created_at` | string(date-time) \| null |  | 创建时间 |
| `updated_at` | string(date-time) \| null |  | 更新时间 |
| `title` | string \| null |  | 产品标题（payload envelope.draft.title） |
| `image` | string \| null |  | 产品主图 URL（draft.images[0]） |
| `item_id` | string \| null |  | 货号（draft.item_id，筛选用） |
| `ozon_client_id` | string \| null |  | 账号（payload.ozon_client_id） |
| `shop_name` | string \| null |  | 店铺名（payload.shop_name，可为空） |
| `follow_sell` | boolean |  | 跟卖标记（envelope.extensions.follow_sell）（默认 `false`） |
| `update_mode` | boolean |  | 编辑更新标记（extensions.update_product_id，在线商品改后重传）（默认 `false`） |
| `parent_task_id` | string \| null |  | 重上来源任务 ID（resubmit 注入，有值=重上任务） |

### TaskListResponse <a id="schema-tasklistresponse"></a>
任务列表响应（T8）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | list[[TaskListItem](#schema-tasklistitem)] |  | 任务列表（created_at DESC） |
| `total` | integer |  | 该租户任务总数（分页前）（默认 `0`） |
| `limit` | integer |  | 本次分页大小（1-100）（默认 `20`） |
| `offset` | integer |  | 本次偏移（默认 `0`） |

### TaskStatisticsResponse <a id="schema-taskstatisticsresponse"></a>
任务统计响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `total` | integer |  | 总任务数（默认 `0`） |
| `pending` | integer |  | 待处理（默认 `0`） |
| `running` | integer |  | 执行中（默认 `0`） |
| `completed` | integer |  | 已完成（默认 `0`） |
| `failed` | integer |  | 已失败（默认 `0`） |
| `cancelled` | integer |  | 已取消（默认 `0`） |
| `avg_duration_seconds` | number \| null |  | 平均执行时长（秒） |

### TaskStatus <a id="schema-taskstatus"></a>
枚举：`"pending"`, `"running"`, `"completed"`, `"failed"`, `"cancelled"`, `"rejected"`, `"pending_moderation"`

### TaskStatusResponse <a id="schema-taskstatusresponse"></a>
任务状态响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 任务 UUID |
| `status` | [TaskStatus](#schema-taskstatus) | ✓ | 任务状态 |
| `tenant_id` | string | ✓ | 用户 ID |
| `priority` | integer |  | 任务优先级（默认 `0`） |
| `result` | dict[str, any] \| null |  | 任务执行结果（completed 时有值） |
| `error_message` | string \| null |  | 错误信息（failed 时有值） |
| `retry_count` | integer |  | 已重试次数（默认 `0`） |
| `max_retries` | integer |  | 最大重试次数（默认 `3`） |
| `created_at` | string(date-time) \| null |  | 创建时间 |
| `updated_at` | string(date-time) \| null |  | 更新时间 |
| `started_at` | string(date-time) \| null |  | 开始执行时间 |
| `completed_at` | string(date-time) \| null |  | 完成时间 |
| `timeout_seconds` | integer |  | 超时时间（秒）（默认 `1800`） |
| `progress` | dict[str, any] \| null |  | 实时进度 {stage, percent, stages_completed[], stages_remaining[], message} |

示例：

```json
{
  "created_at": "2026-09-08T10:00:00Z",
  "id": "8f1c2c1e-3b7a-4c58-9d2e-1a2b3c4d5e6f",
  "max_retries": 3,
  "priority": 0,
  "progress": {
    "message": "生成主图 2/5",
    "percent": 53,
    "stage": "image_generation",
    "stages_completed": [
      "auth",
      "ingest",
      "category_match",
      "pricing",
      "attributes",
      "description"
    ],
    "stages_remaining": [
      "image_generation",
      "prepare_ozon_upload",
      "ozon_validate",
      "check_quota",
      "ozon_upload",
      "ozon_status",
      "learning_record"
    ]
  },
  "retry_count": 0,
  "started_at": "2026-09-08T10:00:05Z",
  "status": "running",
  "tenant_id": "user_0123456789abcdef",
  "timeout_seconds": 1800,
  "updated_at": "2026-09-08T10:01:30Z"
}
```

### UpdateProductImagesResponse <a id="schema-updateproductimagesresponse"></a>
T14 在线商品改图重传响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean |  | （默认 `true`） |
| `product_id` | string | ✓ | Ozon product_id |
| `offer_id` | string | ✓ | 信封 offer_id（sku_id / follow_{id}） |
| `import_task_id` | string |  | Ozon /v3/product/import 返回的 task_id（默认 `""`） |
| `status` | string | ✓ | 'pending_moderation' 商品重新审核中 \| 'approved' 已通过 |
| `re_under_review` | boolean | ✓ | 「重新审核中」标记（改图触发重新审核） |
| `message` | string | ✓ | 人类可读消息 |
| `images` | list[string] |  | 实际提交的存活图片 URL |
| `images_filtered` | list[string] |  | 被过滤的死 URL |

### ValidateResponse <a id="schema-validateresponse"></a>
凭证校验响应。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | boolean | ✓ | key 是否有效 |
| `reason` | string | ✓ | ok / invalid_key / ozon_api_error / decrypt_failed |
| `last_validated_at` | string(date-time) \| null |  | 本次校验时间 |

### ValidationError <a id="schema-validationerror"></a>

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `loc` | list[string \| integer] | ✓ |  |
| `msg` | string | ✓ |  |
| `type` | string | ✓ |  |
| `input` | any |  |  |
| `ctx` | object |  |  |

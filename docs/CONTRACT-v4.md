# Skill ↔ Worker 接口契约 v4.0

> 版本: v4.0 | 日期: 2026-07-30 | 分支: dev
>
> **v4 变更摘要**: 外部 API 合约规格化 + 内部节点合约模板化 + Skill 调度器规范化 + 架构审计问题 PRD

---

## 目录

- [Part 1: Worker 外部 API 合约](#part-1-worker-外部-api-合约)
  - [1.1 POST /api/v1/submit_task](#11-post-apiv1submit_task)
  - [1.2 GET /api/v1/task_status/{task_id}](#12-get-apiv1task_statustask_id)
  - [1.3 POST /api/v1/cancel_task/{task_id}](#13-post-apiv1cancel_tasktask_id)
  - [1.4 GET /api/v1/task_statistics](#14-get-apiv1task_statistics)
  - [1.5 POST /api/v1/auth/verify](#15-post-apiv1authverify)
  - [1.6 GET /api/v1/health](#16-get-apiv1health)
  - [1.7 自测用例（API 合约）](#17-自测用例api-合约)
- [Part 2: Worker 内部节点合约](#part-2-worker-内部节点合约)
  - [2.1 auth](#21-auth)
  - [2.2 check_quota](#22-check_quota)
  - [2.3 ingest](#23-ingest)
  - [2.4 follow_sell_import](#24-follow_sell_import)
  - [2.5 pricing](#25-pricing)
  - [2.6 assemble_ozon_product](#26-assemble_ozon_product)
  - [2.7 prepare_ozon_upload](#27-prepare_ozon_upload)
  - [2.8 ozon_validate](#28-ozon_validate)
  - [2.9 ozon_upload + ozon_status](#29-ozon_upload--ozon_status)
  - [2.10 validation_retry_loop](#210-validation_retry_loop)
  - [2.11 图片生成能力模块](#211-图片生成能力模块)
- [Part 3: Skill 调度器合约](#part-3-skill-调度器合约)
- [Part 4: PRD — 问题→方案→执行链路](#part-4-prd--问题方案执行链路)

---

## Part 1: Worker 外部 API 合约

### 端点总览

| 端点 | 方法 | 鉴权 | 超时 |
|------|------|------|------|
| `/api/v1/submit_task` | POST | token (Supabase) | 5s |
| `/api/v1/task_status/{task_id}` | GET | 无 | 3s |
| `/api/v1/cancel_task/{task_id}` | POST | 无 | 3s |
| `/api/v1/task_statistics` | GET | 无 | 5s |
| `/api/v1/auth/verify` | POST | token (Supabase) | 5s |
| `/api/v1/health` | GET | 无 | 2s |
| `/api/v1/analytics/queries` | POST | token (Supabase) | 10s |
| `/api/v1/analytics/ozon-bestsellers` | POST | token (Supabase) | 10s |
| `/api/v1/analytics/market-bestsellers` | POST | token (Supabase) | 10s |

**基础 URL**: `http://<worker-host>:8080`

**通用强制要求**（所有端点）:
- 响应头 `X-Trace-Id: <uuid>` — 请求级链路追踪 ID
- 响应头 `X-Elapsed-Ms: <int>` — 服务端处理耗时（毫秒）
- 所有错误响应遵循 `{ok: false, error_code, message, detail?}` 格式
- 请求和响应均在服务端以 JSON 格式记录日志

---

### 1.1 POST /api/v1/submit_task

#### 1.1.1 职责

接收 Skill 组装的 GraphInput 信封，经鉴权、限流、配额预检后入队，返回 task_id。**不执行管线，仅入队**。

#### 1.1.2 请求格式

| 字段 | 类型 | 必填 | 默认值 | 校验规则 |
|------|------|------|--------|----------|
| `token` | string | ✅ | — | 非空，长度 ≤ 256 |
| `ozon_client_id` | string | ✅ | — | 非空，纯数字字符串 |
| `ozon_api_key` | string | ✅ | — | 非空 |
| `envelope` | object | ✅ | — | 必须包含 `draft` 字段 |
| `envelope.draft.item_id` | string | ✅ | — | 非空 |
| `envelope.draft.title` | string | ✅ | — | 非空，≤ 255 字符 |
| `envelope.draft.images` | string[] | ✅ | — | 非空数组，每项为 http/https URL |
| `envelope.draft.weight` | int | ✅ | — | ≥ 0，单位克(g) |
| `envelope.draft.dimensions` | object | ✅ | — | `{length, width, height}`，均为 int ≥ 0，单位 mm |
| `envelope.draft.weight_estimated` | bool | ❌ | v0.37 | 重量为估算/兜底值（非 1688 原始抓取），worker 审计用 |
| `envelope.draft.dimensions_estimated` | bool | ❌ | v0.21 | 尺寸为估算值，worker 决策用 |
| `envelope.extensions.competitor_weight_g` | int | ❌ | v0.22 | what_to_sell 竞品重量（克），draft.weight 缺失时兜底 |
| `envelope.extensions.competitor_dimensions_mm` | object | ❌ | v0.22 | what_to_sell 竞品尺寸（mm），draft.dimensions 缺失时兜底 |
| `envelope.draft.purchase_cost` | float | ✅ | — | ≥ 0 |
| `envelope.draft.purchase_url` | string | ✅ | — | 非空，http/https URL |
| `envelope.draft.currency` | string | ✅ | — | 固定 `"CNY"` |
| `envelope.source` | object | ❌ | — | `{purchase_url, purchase_cost}` |
| `envelope.extensions` | object | ❌ | — | `{margin_rate, commission_rate, fx_buffer, follow_sell, max_skus}` |
| `timeout_seconds` | int | ❌ | 1800 | 300-7200 |
| `max_retries` | int | ❌ | 3 | 0-10 |

#### 1.1.3 执行逻辑

```
Step 1: 生成 trace_id (uuid4)，注入请求上下文
Step 2: 字段存在性校验 → 缺失必填字段返回 INVALID_REQUEST
Step 3: 提取 token，剥离 "sk-" 前缀
Step 4: 限流检查：token 在滑动窗口 (60s) 内请求数 ≥ RATE_LIMIT_PER_MINUTE → 返回 RATE_LIMITED
Step 5: 鉴权：查询 Supabase tokens 表 WHERE key = token AND deleted_at IS NULL
        - 不存在 → TOKEN_INVALID
        - status != 1 → TOKEN_DISABLED
        - remain_quota < 5.0 → INSUFFICIENT_BALANCE
Step 6: 配额预检：调用 Ozon /v4/product/info/limit，检查日配额和总配额
        - 配额耗尽 → 返回错误（但任务仍可入队，非阻断）
Step 7: DB 插入：INSERT INTO ozon_product_tasks (tenant_id, payload, status='pending', timeout_seconds, max_retries)
Step 8: 日志记录：log_task_event('submitted', task_id, user_id)
Step 9: 返回 {ok: true, task_id, message}
```

#### 1.1.4 状态上报

| 状态 | 时机 | 上报方式 |
|------|------|----------|
| **执行中** | Step 1 生成 trace_id 后 | `set_trace_context(trace_id, task_id="", user_id="")` |
| **成功** | Step 9 返回前 | `log_task_event('submitted', task_id, user_id)` → logger `task.lifecycle` INFO |
| **失败** | Step 4/5/6 任何一步失败 | 返回错误响应，`log_task_event('failed', task_id, user_id, error=...)` ERROR 级别 |

#### 1.1.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| Supabase 查询超时 | 1 次 | 1s | 返回 SERVICE_UNAVAILABLE |
| DB INSERT 失败 | 2 次 | 0.5s | 返回 TASK_SUBMIT_FAILED |
| Ozon 配额查询超时 | 0 次 | — | 不阻断入队，记录 warning 日志 |

**注意**: 此端点不做业务管线重试，仅保证入队成功。管线级重试由 `task_processor` 的 `max_retries` 控制。

#### 1.1.6 响应格式

**成功 (200)**:
```json
{
  "ok": true,
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Task submitted to queue (user: 123, balance: 100.0)"
}
```

**失败 (4xx/5xx)**:
```json
{
  "ok": false,
  "error_code": "INSUFFICIENT_BALANCE",
  "message": "Balance too low: remain_quota=3.2, required=5.0",
  "detail": {"remain_quota": 3.2, "required": 5.0}
}
```

错误码全集: `TOKEN_MISSING`(401) | `TOKEN_INVALID`(401) | `TOKEN_DISABLED`(403) | `TOKEN_EXPIRED`(403) | `INSUFFICIENT_BALANCE`(402) | `RATE_LIMITED`(429) | `TASK_SUBMIT_FAILED`(500) | `SERVICE_UNAVAILABLE`(503) | `INVALID_REQUEST`(400) | `INTERNAL_ERROR`(500)

---

### 1.2 GET /api/v1/task_status/{task_id}

#### 1.2.1 职责

查询任务当前状态、进度、结果。支持进度三级回退（内存 → LangGraph checkpointer → PG）。

#### 1.2.2 请求格式

| 参数 | 类型 | 位置 | 必填 | 校验 |
|------|------|------|------|------|
| `task_id` | string | path | ✅ | UUID v4 格式 |

#### 1.2.3 执行逻辑

```
Step 1: 校验 task_id UUID 格式 → 非法返回 TASK_NOT_FOUND
Step 2: 查询 PG: SELECT * FROM ozon_product_tasks WHERE id = task_id
        - 不存在 → TASK_NOT_FOUND
Step 3: 并行获取进度数据:
        - 内存 _task_progress[task_id] → 命中直接使用
        - 未命中 → LangGraph checkpointer (AsyncPostgresSaver) → get_state(config)
        - 仍未命中 → PG progress JSONB 列
Step 4: 组装 TaskStatusResponse（含 progress 字段）
Step 5: 返回
```

#### 1.2.4 状态上报

| 状态 | 时机 | 上报方式 |
|------|------|----------|
| **执行中** | 查询处理中 | 无（读操作，不上报） |
| **成功** | 返回响应 | logger INFO: `task_status queried: {task_id} status={status}` |
| **失败** | task_id 不存在 | 返回 TASK_NOT_FOUND，logger WARNING |

#### 1.2.5 异常重试

| 场景 | 重试 | 兜底 |
|------|------|------|
| PG 查询超时 | 1 次 | 返回 SERVICE_UNAVAILABLE |
| Progress JSON 解析失败 | 0 次 | progress 字段返回 null（不阻断状态查询） |

#### 1.2.6 响应格式

**成功 (200)**:
```json
{
  "id": "550e8400-...",
  "status": "running",
  "tenant_id": "123",
  "priority": 0,
  "result": null,
  "error_message": null,
  "retry_count": 0,
  "max_retries": 3,
  "created_at": "2026-07-30T10:00:00Z",
  "started_at": "2026-07-30T10:00:05Z",
  "completed_at": null,
  "timeout_seconds": 1800,
  "progress": {
    "stage": "pricing",
    "percent": 30,
    "stages_completed": ["auth", "ingest", "category_match"],
    "stages_remaining": ["pricing", "attributes", "description", "image_generation", "prepare_ozon_upload", "ozon_validate", "check_quota", "ozon_upload", "ozon_status", "learning_record"],
    "message": "计算定价中..."
  }
}
```

**失败 (404)**:
```json
{
  "ok": false,
  "error_code": "TASK_NOT_FOUND",
  "message": "Task not found: 550e8400-..."
}
```

---

### 1.3 POST /api/v1/cancel_task/{task_id}

#### 1.3.1 职责

取消处于 `pending` 状态的任务。`running` 状态不可取消。

#### 1.3.2 请求格式

| 参数 | 类型 | 位置 | 必填 | 校验 |
|------|------|------|------|------|
| `task_id` | string | path | ✅ | UUID v4 格式 |

#### 1.3.3 执行逻辑

```
Step 1: 校验 task_id UUID 格式
Step 2: 查询 PG: SELECT status FROM ozon_product_tasks WHERE id = task_id
        - 不存在 → TASK_NOT_FOUND
Step 3: 检查 status
        - == 'pending' → UPDATE status='cancelled', completed_at=NOW()
        - != 'pending' → TASK_NOT_CANCELLABLE
Step 4: log_task_event('cancelled', task_id)
Step 5: 返回
```

#### 1.3.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| 成功 | UPDATE 完成后 | `log_task_event('cancelled', task_id)` |
| 失败 | status != pending | `logger WARNING: "cancel blocked: task in {status}"` |

#### 1.3.5 异常重试

| 场景 | 重试 | 兜底 |
|------|------|------|
| PG 查询/更新超时 | 1 次 | SERVICE_UNAVAILABLE |

#### 1.3.6 响应格式

```json
// 成功 200
{"ok": true, "task_id": "...", "message": "Task cancelled successfully"}

// 不可取消 409
{"ok": false, "error_code": "TASK_NOT_CANCELLABLE", "message": "Task is in 'running' status, cannot cancel"}
```

---

### 1.4 GET /api/v1/task_statistics

#### 1.4.1 职责

返回任务聚合统计（总数、各状态数量、平均耗时）。支持按 `tenant_id` 过滤。

#### 1.4.2 请求格式

| 参数 | 类型 | 位置 | 必填 | 默认值 |
|------|------|------|------|--------|
| `tenant_id` | string | query | ❌ | 无（全量统计） |

#### 1.4.3 执行逻辑

```
Step 1: 构建 SQL 聚合查询:
        SELECT status, COUNT(*), AVG(EXTRACT(epoch FROM (completed_at - started_at)))
        FROM ozon_product_tasks
        [WHERE tenant_id = :tid]
        GROUP BY status
Step 2: 计算成功率和 avg_duration
Step 3: 返回
```

#### 1.4.4 状态上报

无状态变更，仅查询。记录 logger INFO。

#### 1.4.5 异常重试

| 场景 | 重试 | 兜底 |
|------|------|------|
| PG 查询超时 | 1 次 | SERVICE_UNAVAILABLE |

#### 1.4.6 响应格式

```json
{
  "total": 150,
  "pending": 5,
  "running": 10,
  "completed": 120,
  "failed": 12,
  "cancelled": 3,
  "avg_duration_seconds": 420.5
}
```

---

### 1.5 POST /api/v1/auth/verify

#### 1.5.1 职责

轻量鉴权接口，Skill `check` 命令调用。验证 token 有效性、MXOU 余额、账户状态，可选验证 Ozon API 连通性。

#### 1.5.2 请求格式

| 字段 | 类型 | 必填 | 校验 |
|------|------|------|------|
| `token` | string | ✅ | 非空 |
| `client_id` | string | ❌ | 非空时校验 Ozon API |
| `api_key` | string | ❌ | 与 client_id 配对 |

#### 1.5.3 执行逻辑

```
Step 1: 生成 trace_id
Step 2: 剥离 "sk-" 前缀
Step 3: 查询 Supabase tokens 表
        - 不存在 → {valid: false, reason: "token_invalid"}
        - status != 1 → {valid: false, reason: "account_inactive"}
Step 4: 查询 users 表，检查 remain_quota ≥ 5.0
        - 不足 → {valid: false, reason: "balance_insufficient"}
Step 5: 如果提供了 client_id + api_key:
        调用 Ozon /v1/seller/info 验证 API 凭证
        - 成功 → ozon_valid: true
        - 失败 → ozon_valid: false (不影响 valid 判定)
Step 6: 返回 {valid: true, reason: "ok", expires_in, ozon_valid}
```

#### 1.5.4 状态上报

纯读操作，logger INFO: `"auth/verify: token={masked} valid={valid}"`

#### 1.5.5 异常重试

| 场景 | 重试 | 兜底 |
|------|------|------|
| Supabase 不可用 | 0 次 | 安全降级返回 `valid: false`（不误放行） |
| Ozon API 超时 | 0 次 | ozon_valid: null |

#### 1.5.6 响应格式

```json
// 全部通过
{"valid": true, "reason": "ok", "expires_in": 86400, "ozon_valid": true}

// token 无效
{"valid": false, "reason": "token_invalid", "expires_in": 0, "ozon_valid": null}

// 余额不足
{"valid": false, "reason": "balance_insufficient", "expires_in": 0, "ozon_valid": null}

// 服务不可用（Supabase 降级）
{"valid": false, "reason": "service_unavailable", "expires_in": 0, "ozon_valid": null}
```

---

### 1.6 GET /api/v1/health

#### 1.6.1 职责

健康检查。用于负载均衡器探活和 Skill `check` 命令。

#### 1.6.2 请求格式

无参数。

#### 1.6.3 执行逻辑

```
Step 1: 检查 DB 连接: SELECT 1 FROM ozon_product_tasks LIMIT 1
Step 2: 统计队列: COUNT(*) GROUP BY status
Step 3: 返回 {status, message, db, queue}
```

#### 1.6.4 状态上报

无。返回响应体本身就是状态。

#### 1.6.5 异常重试

无。健康检查自身不应重试。

#### 1.6.6 响应格式

```json
// 正常 200
{
  "status": "ok",
  "message": "Service is running",
  "db": "connected",
  "queue": {"pending": 5, "running": 10, "completed": 120, "failed": 12}
}

// 异常 503
{
  "status": "degraded",
  "message": "Database connection failed",
  "db": "disconnected",
  "queue": null
}
```

---

### 1.7 POST /api/v1/analytics/*（选品数据上报，v0.34 C5）

#### 1.7.1 职责

接收 Skill what-to-sell 采集的蓝海/榜单数据，去重落库到 worker PG（数据来源于用户、服务于用户）。三个端点共享同一套鉴权与 upsert 逻辑，仅字段不同。

#### 1.7.2 端点与请求格式

| 端点 | body 列表字段 | 每项字段 |
|------|-------------|----------|
| `/api/v1/analytics/queries` | `queries` | `query`(必填), `count`, `ca`, `avg_ca_rub`, `avg_count_items`, `items_views`, `uniq_queries_wca`, `uniq_sellers` |
| `/api/v1/analytics/ozon-bestsellers` | `items` | `sku_or_id`(必填), `brand`, `category_id`, `category_path`, `ordering_amount`, `ordering_count`, `avg_price_rub` |
| `/api/v1/analytics/market-bestsellers` | `items` | `product_name`(必填), `brand`, `category_id`, `category_path`, `ordering_amount`, `daily_avg`, `other_platform_price` |

通用字段：

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|----------|
| `token` | string | ✅ | 非空；Supabase `tokens` 表校验 `status=1` 且未软删；Supabase 未配置时本地放行（开发模式） |
| 列表字段 | array | ✅ | 非空数组；每项经 Pydantic 校验（必填字段缺失 → 422） |

#### 1.7.3 执行逻辑

```
Step 1: token 鉴权（_verify_analytics_token：Supabase 未配置 → 本地放行）
Step 2: 列表字段 Pydantic 校验（非法项 → 422）
Step 3: 字段白名单过滤（未知字段丢弃）+ INSERT ON CONFLICT (唯一键, contributed_by_token_id) DO UPDATE 去重
Step 4: 返回 {status: "ok", inserted, upserted}
```

**去重键**（`contributed_by_token_id` 为去 sk- 前缀后的 token key）：
- queries: `(query, contributed_by_token_id)`
- ozon-bestsellers: `(sku_or_id, contributed_by_token_id)`
- market-bestsellers: `(product_name, contributed_by_token_id)`

#### 1.7.4 响应

```json
// 成功 200
{ "status": "ok", "inserted": 50, "upserted": 3 }
// 鉴权失败 401
{ "ok": false, "error_code": "AUTH_INVALID", "message": "token_invalid or account_inactive" }
// 列表为空/字段非法 422
{ "ok": false, "error_code": "INVALID_REQUEST", "message": "..." }
```

#### 1.7.5 说明

- **无加密回传**：明文 JSON over HTTPS 到自家 worker（不复制竞品插件 gzip+AES 链路）。
- **只入库指标数据**：不上传 cookie / Ozon 凭证 / PII（隐私边界）。
- **`contributed_by_token_id` 为完整 token key（非指纹）**：与 `ozon_product_tasks.payload` 既有明文存储同先例；如需收紧可改 sha256。

---

### 1.8 自测用例（API 合约）

以下 curl 命令可在部署后直接执行验证。

#### 用例 1: 正常提交任务

```bash
# 预期: 200, ok:true, 返回 task_id UUID
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/api/v1/submit_task \
  -H "Content-Type: application/json" \
  -d '{
    "token": "sk-test-valid-token",
    "ozon_client_id": "123456",
    "ozon_api_key": "test-key",
    "envelope": {
      "draft": {
        "item_id": "980815374096",
        "title": "测试产品",
        "currency": "CNY",
        "images": ["https://example.com/img1.jpg"],
        "weight": 500,
        "dimensions": {"length": 100, "width": 50, "height": 30},
        "purchase_cost": 25.5,
        "purchase_url": "https://detail.1688.com/offer/980815374096.html"
      },
      "source": {"purchase_url": "https://detail.1688.com/offer/980815374096.html", "purchase_cost": 25.5},
      "extensions": {"margin_rate": 0.25, "commission_rate": 0.10}
    }
  }'
```

#### 用例 2: 参数错误 — 缺少必填字段

```bash
# 预期: 400, ok:false, error_code=INVALID_REQUEST
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/api/v1/submit_task \
  -H "Content-Type: application/json" \
  -d '{"token": "sk-test", "ozon_client_id": "123", "ozon_api_key": "x", "envelope": {}}'
```

#### 用例 3: 鉴权失败 — token 无效

```bash
# 预期: 401, ok:false, error_code=TOKEN_INVALID
curl -s -w "\n%{http_code}" -X POST http://localhost:8080/api/v1/submit_task \
  -H "Content-Type: application/json" \
  -d '{
    "token": "sk-nonexistent-token-99999",
    "ozon_client_id": "123456",
    "ozon_api_key": "test-key",
    "envelope": {
      "draft": {
        "item_id": "980815374096",
        "title": "Test Product",
        "currency": "CNY",
        "images": ["https://example.com/img1.jpg"],
        "weight": 500,
        "dimensions": {"length": 100, "width": 50, "height": 30},
        "purchase_cost": 25.5,
        "purchase_url": "https://example.com/offer/123.html"
      }
    }
  }'
```

#### 用例 4: 限流触发

```bash
# 连续发送 11 次请求（超过默认 10/min），第 11 次应返回 429
for i in $(seq 1 11); do
  curl -s -o /dev/null -w "req $i: %{http_code}\n" -X POST http://localhost:8080/api/v1/submit_task \
    -H "Content-Type: application/json" \
    -d '{"token":"sk-test","ozon_client_id":"123","ozon_api_key":"x","envelope":{"draft":{"item_id":"x","title":"t","currency":"CNY","images":["http://a.com/1.jpg"],"weight":1,"dimensions":{"length":1,"width":1,"height":1},"purchase_cost":1,"purchase_url":"http://a.com"}}}' &
done
wait
```

#### 用例 5: 健康检查

```bash
# 预期: 200, status=ok, db=connected
curl -s http://localhost:8080/api/v1/health | python3 -m json.tool
```

#### 用例 6: 鉴权验证

```bash
# 预期: 200, valid=true/false, reason 说明原因
curl -s -X POST http://localhost:8080/api/v1/auth/verify \
  -H "Content-Type: application/json" \
  -d '{"token": "sk-test-valid-token", "client_id": "123456", "api_key": "test-key"}' | python3 -m json.tool
```

---

## Part 2: Worker 内部节点合约

### 通用规范

所有内部节点必须遵守以下强制要求:

| # | 要求 | 说明 |
|---|------|------|
| R1 | **装饰器** | 所有节点函数使用 `@audit_node("node_name")` 装饰器，自动记录执行开始/成功/失败日志 |
| R2 | **进度上报** | 节点开始时调用 `update_progress(task_id, stage_name, message)`，stage_name 对应 `STAGE_ORDER` 中一个阶段 |
| R3 | **状态变更** | 所有 GlobalState 变更通过返回 TypedDict 实现（LangGraph reducer 自动合并）。不允许直接修改 `state.field = value` |
| R4 | **无全局依赖** | 不依赖模块级全局变量（`_task_progress` 等仅通过 `update_progress()` / `get_progress()` 接口访问） |
| R5 | **异常处理** | 节点内部 catch 所有异常，通过 `error_message` + `failed_stage` 字段上报，**不抛出未捕获异常**（除 auth 节点外） |
| R6 | **日志** | 使用 `get_logger(__name__)` 获取 logger，调用 `set_trace_context(task_id=state.task_id)` 注入上下文 |
| R7 | **类型安全** | 输入/输出必须使用明确的 TypedDict（BaseModel），不能返回裸 `dict` |

### 进度上报接口

```python
# 所有节点统一使用的上报接口
from main import update_progress
from utils.logger import get_logger, set_trace_context, log_task_event

# 执行中
update_progress(task_id, "pricing", "计算定价中...")

# 成功
log_task_event("completed", task_id, user_id, node="pricing", duration_ms=1234)

# 失败
log_task_event("failed", task_id, user_id, node="pricing", error="佣金查询超时")
```

---

### 2.1 auth

#### 2.1.1 职责

验证 MXOU token 有效性、查询用户余额、获取 Ozon 店铺货币。解析 envelope 三层结构（draft/source/extensions）。**这是唯一允许阻断管线的节点** — 鉴权失败返回 `error_code`，由 `route_after_auth` 条件边导向 END。

#### 2.1.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `token` | str | GraphInput.token | MXOU API Key |
| `ozon_client_id` | str | GraphInput.ozon_client_id | Ozon 店铺 Client-Id |
| `ozon_api_key` | str | GraphInput.ozon_api_key | Ozon 店铺 Api-Key |
| `envelope` | dict | GraphInput.envelope | 三层信封 |

> 输入 TypedDict: `AuthInput`

#### 2.1.3 执行逻辑

```
Step 1: 设置 trace context: set_trace_context(trace_id=uuid4(), task_id="pending")
Step 2: 更新进度: update_progress(task_id, "auth", "验证凭证中...")
Step 3: 校验 token 非空 → 空则返回 AUTH_SUCCESS 以外 error_code
Step 4: 查询 Supabase tokens 表: GET /rest/v1/tokens?key=eq.{token}&deleted_at=is.null&select=*
        - 不存在 → error_code="TOKEN_INVALID", 返回
Step 5: 校验 token 状态: status==1 (active) → 否则 TOKEN_DISABLED/TOKEN_EXPIRED
Step 6: 查询 Supabase users 表: GET /rest/v1/users?id=eq.{user_id}&select=*
Step 7: 校验余额: remain_quota >= 5.0 → 否则 error_code="INSUFFICIENT_BALANCE"
Step 8: 查询 Ozon 店铺: POST /v1/seller/info (Client-Id + Api-Key)
        - 提取 currency_code (CNY/RUB)，失败时默认 "CNY"
Step 9: 解析 envelope → 提取 draft / source / extensions / original_images
Step 10: 更新进度: update_progress(task_id, "auth", "鉴权通过")
Step 11: 返回 AuthOutput
```

**API 调用**:
- Supabase REST: `GET /rest/v1/tokens`, `GET /rest/v1/users`
- Ozon Seller API: `POST /v1/seller/info`

#### 2.1.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 2 | `update_progress(task_id, "auth", "验证凭证中...")` |
| **执行中** | Step 10 | `update_progress(task_id, "auth", "鉴权通过")` |
| **成功** | Step 11 | `log_task_event("completed", task_id, user_id, node="auth")` — logger INFO |
| **失败** | Step 4/5/7 | 返回 `error_code` + `error_message`，`log_task_event("failed", ..., error=error_code)` — logger ERROR |

#### 2.1.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| Supabase 连接超时 | 2 次 | 1s | error_code="SERVICE_UNAVAILABLE"，导向 END |
| Supabase REST 返回 5xx | 2 次 | 1s | 同上 |
| Ozon seller/info 超时 | 0 次 | — | currency_code 默认 "CNY"，不阻断 |
| tokens 表无此 token | 0 次 | — | error_code="TOKEN_INVALID"，直接阻断 |

#### 2.1.6 输出结果

**成功** (`AuthOutput`):
```json
{
  "progress_counter": 1,
  "user_id": "123",
  "token_id": "456",
  "balance": 100.0,
  "supabase_url": "https://...",
  "supabase_key": "eyJ...",
  "ozon_client_id": "123456",
  "ozon_api_key": "abc-def",
  "currency_code": "CNY",
  "draft": {...},
  "source": {...},
  "extensions": {...},
  "original_images": ["https://..."],
  "error_code": "AUTH_SUCCESS",
  "error_message": ""
}
```

**失败** (`AuthOutput`):
```json
{
  "progress_counter": 0,
  "error_code": "TOKEN_INVALID",
  "error_message": "Token not found in Supabase tokens table"
}
```

**写入 GlobalState 的字段**: `progress_counter`, `user_id`, `token_id`, `balance`, `supabase_url`, `supabase_key`, `ozon_client_id`, `ozon_api_key`, `currency_code`, `draft`, `source`, `extensions`, `original_images`, `error_code`, `error_message`

#### 2.1.7 自测用例

```python
# 用例 1: 正常鉴权
input_1 = AuthInput(token="sk-valid", ozon_client_id="123", ozon_api_key="key", envelope={"draft": {...}})
# 预期: error_code="AUTH_SUCCESS", user_id 非空, balance > 0

# 用例 2: token 无效
input_2 = AuthInput(token="sk-invalid-xxx", ozon_client_id="123", ozon_api_key="key", envelope={"draft": {...}})
# 预期: error_code="TOKEN_INVALID", user_id=""

# 用例 3: Supabase 超时
# 模拟: 设置 Supabase URL 为不可达地址
input_3 = AuthInput(token="sk-valid", ozon_client_id="123", ozon_api_key="key", envelope={"draft": {...}})
# 预期: error_code="SERVICE_UNAVAILABLE", error_message 含 "timeout"
```

---

### 2.2 check_quota

#### 2.2.1 职责

预检 Ozon 每日创建配额和总产品配额。在管线早期（auth 之后、ingest 之前）调用，避免浪费 MXOU 生图/LLM 额度。**配额耗尽时阻断管线**。

#### 2.2.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `ozon_client_id` | str | GlobalState (auth 写入) | Ozon Client-Id |
| `ozon_api_key` | str | GlobalState (auth 写入) | Ozon Api-Key |

> 输入 TypedDict: `OzonUploadInput` (复用)

#### 2.2.3 执行逻辑

```
Step 1: 更新进度: update_progress(task_id, "check_quota", "检查店铺配额...")
Step 2: 调用 ozon_check_quota(client_id, api_key)
        内部: GET /v4/product/info/limit
Step 3: 解析结果:
        - remaining_daily <= 0 → error_message="[QUOTA_BLOCKED] 日配额耗尽"
        - remaining_total <= 0 → error_message="[QUOTA_BLOCKED] 总配额耗尽"
        - API 超时/错误 → 记录 warning，不阻断（降级放行）
Step 4: 返回结果
```

**API 调用**:
- Ozon Seller API: `GET /v4/product/info/limit`

#### 2.2.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 1 | `update_progress(task_id, "check_quota", "检查店铺配额...")` |
| **成功** (配额充足) | Step 4 | logger INFO: "quota OK: daily={}/{} total={}/{}" |
| **失败** (配额耗尽) | Step 3 | `update_progress(task_id, "check_quota", "配额耗尽")` + `error_message="[QUOTA_BLOCKED]..."` |
| **降级** (API 不可用) | Step 3 (timeout) | logger WARNING: "quota check failed, passing through" |

#### 2.2.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| Ozon API 超时 (5s) | 1 次 | 2s | **降级放行** — 不阻断管线 |
| Ozon API 返回 5xx | 1 次 | 2s | 降级放行 |
| 网络不可达 | 0 次 | — | 降级放行 |

#### 2.2.6 输出结果

**配额充足**:
```json
{
  "progress_counter": 2,
  "product_id": "",
  "upload_status": "",
  "error_message": "",
  "stages": {"check_quota": "ok"}
}
```

**配额耗尽（阻断）**:
```json
{
  "progress_counter": 2,
  "product_id": "",
  "upload_status": "",
  "error_message": "[QUOTA_BLOCKED] 日配额耗尽: 已用500/500, 剩余0",
  "failed_stage": "check_quota",
  "stages": {"check_quota": "blocked"}
}
```

#### 2.2.7 自测用例

```python
# 用例 1: 配额充足
# 预期: error_message="", stages={"check_quota": "ok"}

# 用例 2: 日配额耗尽
# 模拟: Ozon API 返回 daily_create_used=500, daily_create_limit=500
# 预期: error_message="[QUOTA_BLOCKED] 日配额耗尽..."

# 用例 3: API 超时（降级）
# 模拟: Ozon API 5s 超时
# 预期: error_message="", logger WARNING "quota check failed, passing through"
```

---

### 2.3 ingest

#### 2.3.1 职责

解析 GraphInput envelope 三层结构，提取 draft/source/extensions/variants/item_id/original_images。生成 task_id (UUID)。**纯数据提取，无外部 API 调用**。

#### 2.3.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `envelope` | dict | GlobalState | 三层信封 |
| `user_id` | str | GlobalState (auth) | 用户 ID |
| `currency_code` | str | GlobalState (auth) | 店铺货币 |

> 输入 TypedDict: `IngestInput`

#### 2.3.3 执行逻辑

```
Step 1: 生成 task_id: str(uuid.uuid4())
Step 2: 更新进度: update_progress(task_id, "ingest", "解析信封数据...")
Step 3: 解析 envelope.draft → draft dict，提取:
        - item_id, title, description, currency, images, weight, dimensions, purchase_cost, purchase_url
        - attributes, ozon_category, source_category, variants
Step 4: 解析 envelope.source → source dict
Step 5: 解析 envelope.extensions → extensions dict
Step 6: 提取 original_images: draft.images (或 envelope 级 images)
Step 7: 设置 status: "pending" → "running"
Step 8: 日志: log_task_event("started", task_id, user_id)
Step 9: 更新进度: update_progress(task_id, "ingest", "数据解析完成")
Step 10: 返回 IngestOutput
```

#### 2.3.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 2 | `update_progress(task_id, "ingest", "解析信封数据...")` |
| **成功** | Step 10 | `log_task_event("started", task_id, user_id)` |

#### 2.3.5 异常重试

| 场景 | 重试 | 兜底 |
|------|------|------|
| envelope 格式异常 | 0 次 | 返回 error_message + failed_stage="ingest" |
| 关键字段缺失 | 0 次 | 同上（降级：缺失字段填默认值，记录 warning） |

#### 2.3.6 输出结果

```json
{
  "progress_counter": 2,
  "task_id": "550e8400-...",
  "status": "running",
  "draft": {...},
  "source": {...},
  "extensions": {...},
  "currency_code": "CNY",
  "variants": [],
  "item_id": "980815374096",
  "original_images": ["https://..."]
}
```

**写入 GlobalState 的字段**: `progress_counter`, `task_id`, `status`, `draft`, `source`, `extensions`, `currency_code`, `variants`, `item_id`, `original_images`

#### 2.3.7 自测用例

```python
# 用例 1: 完整三层信封
input_1 = IngestInput(envelope={"draft": {...full...}, "source": {...}, "extensions": {...}}, user_id="123", currency_code="CNY")
# 预期: task_id 非空 UUID, variants=[], item_id 匹配

# 用例 2: 最小信封（仅 draft 必填字段）
input_2 = IngestInput(envelope={"draft": {"item_id": "123", "title": "t", "images": ["http://a.com/1.jpg"], "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0}, "purchase_cost": 1.0, "purchase_url": "http://a.com", "currency": "CNY"}}, user_id="123", currency_code="CNY")
# 预期: 成功，variants=[], source={}, extensions={}

# 用例 3: 多 SKU 信封（含 variants）
input_3 = IngestInput(envelope={"draft": {..."variants": [{"sku_id": "x_0", "price": 5.0}]}}, user_id="123", currency_code="CNY")
# 预期: variants 列表非空
```

---

### 2.4 follow_sell_import

#### 2.4.1 职责

跟卖管线入口。通过 import-by-sku 拷贝 Ozon 竞品产品卡，获取 product_id。调用 import-by-sku 后**短暂轮询（10×3s=30s）**确认结果，有 product_id 则后续走 UPDATE 模式。解析 Ozon 面包屑类目（Widget API ID）→ 跨 ID 空间转换为 Seller API 的 description_category_id + type_id。

#### 2.4.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `envelope` | dict | GlobalState | 含 draft.extensions.follow_sell, draft.ozon_category (面包屑) |
| `ozon_client_id` | str | GlobalState | Ozon Client-Id |
| `ozon_api_key` | str | GlobalState | Ozon Api-Key |
| `currency_code` | str | GlobalState | 店铺货币 |
| `token` | str | GlobalState | MXOU token（LLM 翻译用） |

> 输入类型: `GlobalState` (直接使用，非 TypedDict — 待修复为专用 TypedDict)

#### 2.4.3 执行逻辑

```
Step 1: 提取竞品信息: envelope.draft.ozon_category, envelope.draft.competitor_images
Step 2: 更新进度: update_progress(task_id, "ingest", "跟卖导入中...")
Step 3: 调用 import-by-sku:
        POST /v1/product/import-by-sku
        body: {items: [{sku: envelope.draft.item_id, name: envelope.draft.title, images: [...], ...}]}
        获取 ozon_task_id
Step 4: 短暂轮询 (10×3s=30s):
        for i in range(10):
            info = POST /v1/product/import/info {task_id: ozon_task_id}
            if info.items[0].product_id:
                state.product_id = product_id  ← 后续走 UPDATE
                break
            time.sleep(3)
Step 5: 类目解析 (跨 ID 空间):
        a. 面包屑传入 description_category_id (数字) → 直查 category_tree_nodes
        b. 未命中 → pg_trgm ZH_HANS 模糊搜索
        c. 仍未命中 → LLM 翻译为俄语 → pg_trgm RU 模糊搜索
        d. 全部失败 → 写 error_message（不降级为空类目）
Step 6: 设置硬编码属性: brand="Нет бренда" (126745801), country="Китай" (90296)
Step 7: 拉取属性 schema: POST /v1/description-category/attribute
Step 8: 写入 competitor_price（竞品原始价格，供 pricing 节点参考）
Step 9: 返回状态
```

**API 调用**:
- Ozon: `POST /v1/product/import-by-sku`, `POST /v1/product/import/info`, `POST /v1/description-category/attribute`
- MXOU LLM: `call_mxou_chat_api` (类目名翻译)

#### 2.4.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 2 | `update_progress(task_id, "ingest", "跟卖导入中...")` |
| **执行中** | Step 4 (轮询中) | `update_progress(task_id, "ingest", f"等待import-by-sku...{i*3}s")` |
| **成功** | Step 9 | logger INFO: "follow_sell_import OK: product_id={pid}" |
| **失败** (no product_id) | Step 4 超时 | error_message="ozon_product_id 为空"，导向 END |
| **失败** (类目解析全部失败) | Step 5d | error_message 含类目解析失败详情，走 retry loop |

#### 2.4.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| import-by-sku 轮询超时 (30s) | 0 次 | — | 标记 error="ozon_product_id 为空"，管线结束 |
| import-by-sku API 5xx | 1 次 | 5s | 同上 |
| 类目解析三层均失败 | 0 次 | — | error_message 传播 → retry loop |

#### 2.4.6 输出结果

**成功**:
```json
{
  "product_id": "1111111111",
  "competitor_price": "2500.00",
  "description_category_id": "17027918",
  "type_id": "971311385",
  "competitor_name": "Оригинальное название товара",
  "original_images": ["https://cdn.ozon.ru/..."],
  "pricing_info": {...},
  "final_attributes": [{...brand...}, {...country...}],
  "attributes_schema": [...],
  "upload_status": "",
  "error_message": ""
}
```

**失败** (import-by-sku 无结果):
```json
{
  "error_message": "import-by-sku 失败: ozon_product_id 为空, 30s 内未获取到 product_id",
  "failed_stage": "follow_sell_import"
}
```

#### 2.4.7 自测用例

```python
# 用例 1: 正常跟卖
input_1 = GlobalState(envelope={"extensions": {"follow_sell": True}, "draft": {"item_id": "12345", "ozon_category": {"description_category_id": 17027918}, ...}}, ...)
# 预期: product_id 非空, description_category_id 已解析, competitor_price 有值

# 用例 2: import-by-sku 30s 超时
input_2 = GlobalState(envelope={"draft": {"item_id": "invalid-sku"}}, ...)
# 预期: error_message="ozon_product_id 为空", 导向 END

# 用例 3: 类目跨 ID 空间全部失败
# 模拟: Widget breadcrumb ID=99999 (不存在于 category_tree_nodes)
input_3 = GlobalState(envelope={"draft": {"ozon_category": {"description_category_id": 99999, "breadcrumbs": [{"name": "Неизвестная категория"}]}}, ...})
# 预期: error_message 含类目解析失败, description_category_id 仍为空 (由 retry loop 修复)
```

---

### 2.5 pricing

#### 2.5.1 职责

计算最终 Ozon 售价。公式: `售价 = (采购成本 + 物流费 + 包装费) × (1 + 利润率) / (1 - 佣金率)`。CNY 店铺不加 fx_buffer。支持多 SKU 变体定价。

**关键变更 (v4)**: 跟卖路径也走此节点统一计算，不再在 `follow_sell_import` 中内联定价。

#### 2.5.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `draft` | dict | GlobalState | 含 purchase_cost, weight, dimensions, variants |
| `extensions` | dict | GlobalState | 含 margin_rate (默认 0.25), commission_rate (默认 0.10), fx_buffer (默认 0.05) |
| `currency_code` | str | GlobalState (auth) | CNY 还是 RUB |
| `ozon_client_id` | str | GlobalState | 查询佣金率用 |
| `ozon_api_key` | str | GlobalState | 查询佣金率用 |
| `description_category_id` | str | GlobalState | 查询佣金率用 |
| `competitor_price` | str | GlobalState (跟卖时 follow_sell_import 写入) | 竞品价，若大于成本×1.3 则保留 |

> 输入 TypedDict: `PricingInput`

#### 2.5.3 执行逻辑

```
Step 1: 更新进度: update_progress(task_id, "pricing", "计算售价中...")
Step 2: 提取参数:
        purchase_cost = draft.purchase_cost (CNY)
        weight_g, dims_mm, wd_marks = normalize_weight_dimensions(draft, extensions)
            # ⚠️ v0.37: 统一走 utils.weight_dimension_normalizer（与 prepare 同源）
        dimensions = draft.dimensions
        margin_rate = extensions.get("margin_rate", 0.25)
        commission_rate = extensions.get("commission_rate", 0.10)
        fx_buffer = extensions.get("fx_buffer", 0.05)
Step 3: 重量合理性检查（v0.37 A2 修复，仅标记不修正）:
        - weight_g < 10g 且 max(dimensions) > 50mm → 标记 light_weight_suspect
          （真实轻物如 3g 薄膜/5g 垫片是正常商品，旧逻辑 ×1000 误伤已废除）
        - 缺失/0 → 竞品 competitor_weight_g 兜底 → 默认 100g（weight_source 标记）
Step 4: 密度检查（v0.37 仅标记不改写）:
        - volume_cm3 = (length*width*height) / 1000
        - density = weight_g / volume_cm3
        - 超范围 → 标记 dimensions_suspected，保留真实值（不再体积推算覆盖重量）
        - wd_marks → pricing_info.wd_audit {weight_source, weight_estimated, dimensions_suspected, reasons}
          + Sentry 事件 [WEIGHT_DIM_SUSPECT]（放行但留痕）
Step 5: 查询物流费率: SELECT * FROM logistics_rates WHERE weight_g BETWEEN min_weight AND max_weight
        - 命中 → logistics_cost = rate_per_g * weight_g
        - 未命中 → 兜底: logistics_cost = weight_g * 0.05 (CNY/g)
Step 6: 包装成本: packaging_cost = 2.0 CNY (固定)
Step 7: 查询佣金率: 通过 description_category_id 查 Ozon API (store-level fallback)
Step 8: 汇率 (仅 RUB 店铺): exchange_rate = get_exchange_rate("CNY", "RUB") 或默认 12.5
Step 9: 总成本 = (purchase_cost + logistics_cost + packaging_cost) * exchange_rate (RUB 店铺)
Step 10: 售价 = 总成本 * (1 + margin_rate) / (1 - commission_rate)
         CNY 店铺: 总成本 = purchase_cost + logistics_cost + packaging_cost (无汇率)
Step 11: 跟卖定价覆盖: 如果 competitor_price 存在且 > 售价*0.7 → 使用 competitor_price (更有竞争力)
Step 12: 多 SKU 变体: 每个 variant.price 独立计算（复用同一公式）
Step 13: 返回 PricingOutput (含 pricing_info 和 profit_estimation)
```

**API 调用**:
- Ozon: `GET /v5/product/info/prices` (佣金率)
- PG: logistics_rates 表, exchange_rates 表

#### 2.5.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 1 | `update_progress(task_id, "pricing", "计算售价中...")` |
| **成功** | Step 13 | logger INFO: "pricing: cost={} price={} margin={}%" |
| **失败** | 任何异常 | `error_message` + `failed_stage="pricing"` |

#### 2.5.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| 物流费率查询失败 | 1 次 | 1s | 兜底费率 0.05 CNY/g |
| 佣金率查询超时 | 1 次 | 2s | 默认 commission_rate (0.10) |
| 汇率查询失败 | 1 次 | 1s | 默认 12.5 (CNY→RUB) |

#### 2.5.6 输出结果

```json
{
  "progress_counter": 5,
  "pricing_info": {
    "purchase_cost": 25.5,
    "logistics_cost": 15.0,
    "packaging_cost": 2.0,
    "total_cost_cny": 42.5,
    "exchange_rate": 12.5,
    "total_cost_rub": 531.25,
    "margin_rate": 0.25,
    "commission_rate": 0.10,
    "commission_divisor": 0.9,
    "final_price": 738.0,
    "profit_per_item": 123.0,
    "profit_estimation": {
      "purchase_cost_cny": 25.5,
      "logistics_cost_cny": 15.0,
      "price_rub": 738.0,
      "profit_rub": 123.0,
      "margin_pct": 16.7
    }
  },
  "price": 738.0,
  "old_price": 950.0,
  "error_message": ""
}
```

#### 2.5.7 自测用例

```python
# 用例 1: 正常定价 (CNY 店铺)
input_1 = PricingInput(draft={"purchase_cost": 25.5, "weight": 500, "dimensions": {"length": 100, "width": 50, "height": 30}}, extensions={"margin_rate": 0.25, "commission_rate": 0.10}, currency_code="CNY", ...)
# 预期: final_price > purchase_cost, profit_estimation.margin_pct > 0

# 用例 2: 小重量修正 (kg→g)
input_2 = PricingInput(draft={"purchase_cost": 100, "weight": 2, "dimensions": {"length": 200, "width": 200, "height": 200}}, ...)
# 预期: weight 被修正为 2000, logistics_cost 相应增大

# 用例 3: 物流费率查询超时 (降级)
# 模拟: logistics_rates 表不可访问
input_3 = PricingInput(draft={"purchase_cost": 50, "weight": 1000, ...}, ...)
# 预期: logistics_cost = 1000 * 0.05 = 50 CNY, pricing_info 仍正常返回
```

---

### 2.6 assemble_ozon_product

#### 2.6.1 职责

统一类目匹配 + 属性装配（替代旧 4 节点管线: category_lookup → attributes_fetch → attributes_llm → attributes_learning）。

**类目匹配**: pg_trgm 相似度 + jieba 分词 + 泛化词过滤。优先使用 Skill 传入的 `draft.ozon_category`。

**属性装配**: 1688 中文属性 → Ozon 字典值映射。品牌强制 "Нет бренда" (126745801)。制造商用 supplier 填充。

#### 2.6.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `draft` | dict | GlobalState | 1688 产品数据 (title, attributes, ozon_category, supplier) |
| `token` | str | GlobalState | MXOU token (LLM 类目匹配 fallback) |
| `ozon_client_id` | str | GlobalState | Ozon API |
| `ozon_api_key` | str | GlobalState | Ozon API |
| `currency_code` | str | GlobalState | 店铺货币 |
| `pricing_info` | dict | GlobalState (pricing) | 定价结果 (可能为空，跟卖路径 pricing 在此节点之后) |
| `description_category_id` | str | GlobalState | 已有类目 (跟卖路径 follow_sell_import 已设定) |
| `type_id` | str | GlobalState | 已有 type_id |
| `envelope` | dict | GlobalState | 完整信封 (检测 extensions.follow_sell) |
| `assembly_retry_count` | int | GlobalState | 类目匹配重试计数 |

> 输入类型: 直接使用 GlobalState（待修复为专用 TypedDict）

#### 2.6.3 执行逻辑

```
Step 1: 检测是否为跟卖路径:
        if envelope.extensions.follow_sell and description_category_id:
            → 走 _assemble_follow_sell() 快速路径 (Step 8)
Step 2: 更新进度: update_progress(task_id, "category_match", "类目匹配中...")
Step 3: 类目匹配:
        if draft.ozon_category:  ← Skill 已解析
            → 直接使用 + 一致性校验 (pg_trgm 验证)
        else:
            → pg_trgm search_nodes(draft.title, node_type="type", language="ZH_HANS")
            → jieba 分词提取末级类目名
            → 泛化词过滤 ("运动"/"休闲"/"传统"/"新品" 等)
            → 取最高相似度匹配
Step 4: LLM fallback: pg_trgm 相似度 < 0.3 → 调 LLM 辅助匹配
Step 5: 如果类目有变更 → assembly_retry_count++, 重新构建
Step 6: 更新进度: update_progress(task_id, "attributes", "属性匹配中...")
Step 7: 属性装配 (主路径):
        a. 获取属性 schema: get_attribute_schema(description_category_id, type_id) 或 Ozon API
        b. 遍历 draft.attributes (1688 中文属性名 → Ozon 属性):
           - 字典属性: 查 dictionary_value_cache (ZH_HANS) → 匹配 dict_id
           - 未命中: 调 Ozon /v1/description-category/attribute/values/search (ZH_HANS)
           - 仍失败: LLM 翻译 → 搜索 RU
        c. 强制设置: attr 22508 (品牌注册国) = "Китай"
        d. 强制设置: attr 23487 (Производитель) = draft.supplier
        e. 强制覆盖: 所有品牌属性 → "Нет бренда" (126745801)
        f. 生成 hashtag (不含品牌名, 不含 #ozon)
        g. 富文本描述 attr 4191: LLM 生成 HTML
Step 8: _assemble_follow_sell 快速路径:
        - 跳过 Step 3 类目匹配 (已有 description_category_id + type_id)
        - 执行 Step 7 属性装配 (拉 schema + 硬编码属性)
Step 9: 类目一致性检查: 如果 assemble 前后 type_id 不一致 → 重新构建属性
Step 10: 返回结果
```

**API 调用**:
- Ozon: `POST /v1/description-category/attribute`, `POST /v1/description-category/attribute/values/search`
- MXOU LLM: `call_mxou_chat_api` (类目匹配 fallback + 属性翻译)
- PG: category_tree_nodes, attribute_cache, dictionary_value_cache

#### 2.6.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 2 | `update_progress(task_id, "category_match", "类目匹配中...")` |
| **执行中** | Step 6 | `update_progress(task_id, "attributes", "属性匹配中...")` |
| **成功** | Step 10 | logger INFO: "assemble OK: dc={} type={} attrs={}" |
| **失败** | 类目匹配全部失败 | error_message + failed_stage="category_match" |
| **重试** | Step 5 类目变更 | assembly_retry_count++ (max 2) |

#### 2.6.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| 类目匹配 pg_trgm 无结果 | assembly_retry (max 2) | — | LLM fallback |
| 类目匹配 LLM 也失败 | 0 次 | — | error_message, 走 retry loop |
| 字典值搜索超时 | 1 次/属性 | 1s | 跳过该属性 (非阻断) |
| 属性 schema 拉取失败 | 1 次 | 2s | 使用缓存/降级 |

#### 2.6.6 输出结果

```json
{
  "description_category_id": "17027918",
  "type_id": "971311385",
  "attributes_schema": [...],
  "dictionary_values": {...},
  "final_attributes": [
    {"attribute_id": 8229, "complex_id": 0, "values": [{"dictionary_value_id": 971311385, "value": "Подвесной амортизатор"}]},
    {"attribute_id": 22508, "complex_id": 0, "values": [{"value": "Китай"}]},
    {"attribute_id": 23487, "complex_id": 0, "values": [{"value": "深圳XXX科技有限公司"}]},
    ...
  ],
  "llm_attributes": [...],
  "learned_attributes": [...],
  "ozon_payloads": [],
  "error_message": "",
  "assembly_retry_count": 0
}
```

#### 2.6.7 自测用例

```python
# 用例 1: 1688 路径 — 类目匹配 + 属性装配
input_1 = {draft: {title: "喷水玩具儿童户外", attributes: {"颜色": "白色", "材质": "塑料"}, source_category: "玩具"}, ...}
# 预期: description_category_id 非空, final_attributes 包含品牌/国家/制造商

# 用例 2: 跟卖路径 — Skill 已传类目, 跳过类目匹配
input_2 = {envelope: {extensions: {follow_sell: True}}, draft: {ozon_category: {description_category_id: 17027918, type_id: 971311385}}, description_category_id: "17027918", ...}
# 预期: _assemble_follow_sell 快速路径, 类目不变, 属性已装配

# 用例 3: 类目匹配全部失败
input_3 = {draft: {title: "asdfghjkl" (无意义字符串), attributes: {}}, ...}
# 预期: error_message 非空, description_category_id 仍为空
```

---

### 2.7 prepare_ozon_upload

#### 2.7.1 职责

组装 Ozon `/v3/product/import` 完整载荷。这是**最复杂的节点**，包含：
- 图片排序 (IMG_ORDER)
- 单位转换 (mm, g)
- 俄语标题翻译 + 净化 + SEO 优化
- 描述翻译 + 净化 + 富文本描述 (attr 4191)
- 属性格式转换 (1688 → Ozon 字典格式)
- 必填属性默认值填充
- hashtag 生成 + 品牌名过滤
- 颜色去重
- 密度验证 + 自动修正
- 多 SKU 变体载荷构建

#### 2.7.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `draft` | dict | GlobalState | 1688 原始数据 |
| `source` | dict | GlobalState | 采购源 |
| `pricing_info` | dict | GlobalState (pricing) | 定价结果 |
| `description_category_id` | str | GlobalState (assemble) | Ozon 类目 ID |
| `type_id` | str | GlobalState (assemble) | Ozon 类型 ID |
| `final_attributes` | list | GlobalState (assemble) | 最终属性列表 |
| `attributes_schema` | list | GlobalState (assemble) | 属性 schema |
| `token` | str | GlobalState | LLM 翻译用 |
| `product_id` | Optional[str] | GlobalState | 已有 product_id (UPDATE 模式) |
| `*_image` | Optional[str] | GlobalState (各生图节点) | white_bg, multi_angle, main, detail, social_proof, scene_1/2/3, comparison |
| `variants` | list | GlobalState | 多 SKU 变体 |
| `variant_primary_images` | list | GlobalState | 变体主图 |
| `original_images` | list | GlobalState | 1688 原始图 |

> 输入 TypedDict: `PrepareOzonUploadInput`

#### 2.7.3 执行逻辑

```
Step 1: 更新进度: update_progress(task_id, "description", "翻译产品信息...")
Step 2: 标题翻译: LLM (deepseek-v4-flash) 中文→俄语
        - max_tokens=200 (deepseek thinking 消耗)
        - 失败 → pg_trgm 类目名兜底
Step 3: 标题净化: _sanitize_title()
        - 移除拉丁/中文字符
        - 移除营销词 (бесплатно/скидка/акция/топ/лучший/супер/новинка/100% ...)
        - 移除平台名 (ozon/wildberries/aliexpress/amazon ...)
        - 限制 80 字符
        - 删除 #ozon 标签
Step 4: 生成 hashtag (不含品牌名, 不含 #ozon)
Step 5: 描述翻译: LLM 1688 中文描述 → 俄语
Step 6: 描述净化: _sanitize_description()
        - 移除拉丁/中文
        - 移除 URL/邮件/电话
        - 限制 2000 字符
Step 7: 富文本描述: LLM 生成俄语 HTML → _sanitize_rich_description()
Step 8: 更新进度: update_progress(task_id, "description", "组装上传数据...")
Step 9: 图片排序: IMG_ORDER = [main, detail, scene_1, scene_2, scene_3, comparison, social_proof, multi_angle, white_bg]
        - 缺失图片用 original_images 补位
        - 确保 ≤ 10 张
Step 10: 单位处理: dimensions mm (原值), weight g (原值)
Step 11: 密度检查: density < 0.25 → 体积×0.5 推算
Step 12: 组装 ozon_payload = {
          "name": title_ru,
          "offer_id": item_id,
          "description_category_id": int(dc_id),
          "price": str(pricing_info.final_price),
          "old_price": str(pricing_info.final_price * 1.3),
          "vat": "0",
          "currency_code": currency_code,
          "depth": dimensions.length,
          "width": dimensions.width,
          "height": dimensions.height,
          "weight": weight_g,
          "images": ordered_images,
          "attributes": final_attributes_formatted,
          "complex_attributes": [...]
        }
Step 13: 多 SKU: 每个 variant 构建独立 item + 共享 attributes
Step 14: 返回 PrepareOzonUploadOutput
```

**API 调用**:
- MXOU LLM: `call_mxou_chat_api` (标题翻译 + 描述翻译 + 富文本 + hashtag)

#### 2.7.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 1 | `update_progress(task_id, "description", "翻译产品信息...")` |
| **执行中** | Step 8 | `update_progress(task_id, "description", "组装上传数据...")` |
| **成功** | Step 14 | logger INFO: "prepare OK: name={title_ru}, images={n}" |
| **失败** | 标题翻译全部失败 | error_message + failed_stage="description" |

#### 2.7.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| LLM 翻译超时 | 1 次 | 3s | pg_trgm 类目名兜底标题 |
| 富文本生成失败 | 0 次 | — | 跳过 attr 4191 (非阻断) |
| hashtag 生成失败 | 0 次 | — | 空 hashtag |

#### 2.7.6 输出结果

```json
{
  "progress_counter": 17,
  "ozon_payload": {...},
  "ozon_payloads": [],
  "ordered_images": ["url1", "url2", ...],
  "purchase_url": "https://...",
  "purchase_cost": "25.5",
  "sku_id": "980815374096_0",
  "profit_estimation": {...},
  "validation_errors": [],
  "error_message": ""
}
```

#### 2.7.7 自测用例

```python
# 用例 1: 正常组装 (完整数据)
input_1 = PrepareOzonUploadInput(draft={title: "泡沫喷壶", ...}, final_attributes=[...], pricing_info={final_price: 500}, ...)
# 预期: ozon_payload.name 为俄语, ozon_payload.images 按 IMG_ORDER 排序

# 用例 2: LLM 翻译失败 (降级)
# 模拟: MXOU API 超时
input_2 = ... (同上, MXOU 不可用)
# 预期: ozon_payload.name = draft_title (原始中文) 或 pg_trgm 类目名, 非空

# 用例 3: 多 SKU
input_3 = PrepareOzonUploadInput(variants=[{sku_id: "x_0", price: 5.0}, {sku_id: "x_1", price: 6.0}], ...)
# 预期: ozon_payloads 非空, 每个变体有独立 offer_id
```

---

### 2.8 ozon_validate

#### 2.8.1 职责

Ozon 上传前校验。检查载荷完整性、字段合法性、图片可达性。**不调用 Ozon 校验 API**（`/v1/product/validate` 返回 404）。

#### 2.8.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `ozon_payload` | dict | GlobalState (prepare) | 待校验载荷 |
| `ozon_client_id` | str | GlobalState | 字典值查询 |
| `ozon_api_key` | str | GlobalState | 字典值查询 |
| `attributes_schema` | list | GlobalState | 属性 schema |
| `ordered_images` | list | GlobalState | 图片列表 |

> 输入 TypedDict: `OzonValidateInput`

#### 2.8.3 执行逻辑

```
Step 1: 更新进度: update_progress(task_id, "ozon_validate", "校验载荷中...")
Step 2: 必填字段检查: name/offer_id/price/images/description_category_id/weight/depth/width/height
Step 3: 字符检测:
        - name: 不能仅含拉丁/中文 (需含 Cyrillic) → validation_error
        - name: 不能含 "Ozon"/"Wildberries" 等平台名
        - attributes: 值不能含中文
Step 4: 字典属性校验: 检查 dictionary_value_id 是否在 attributes_schema 中存在
Step 5: 尺寸合理性: dimensions 不全是 0, 单边不超过 2000mm
Step 6: 密度修正: density > 10 g/cm³ (太密) → 自动调整
Step 7: 图片可达性: requests.head 检查 URL 返回 200
Step 8: 易燃物扫描: name/description 含 "fire"/"explosive"/"flammable" → warning
Step 9: 返回 is_valid + validation_errors
```

#### 2.8.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 1 | `update_progress(task_id, "ozon_validate", "校验载荷中...")` |
| **成功** (通过) | Step 9 (is_valid=True) | logger INFO: "validate PASS" |
| **失败** (不通过) | Step 9 (is_valid=False) | logger WARNING: "validate FAIL: {n} errors" + validation_errors 详情 |

#### 2.8.5 异常重试

| 场景 | 重试 | 兜底 |
|------|------|------|
| 图片 URL 不可达 | 0 次 | 从 ordered_images 移除 (降低图片数量) |
| 字典值不存在 | 0 次 | validation_error (由 retry loop 修复) |

#### 2.8.6 输出结果

**通过**:
```json
{
  "progress_counter": 18,
  "ozon_payload": {...},
  "ordered_images": [...],
  "validation_errors": [],
  "is_valid": true,
  "auto_fixed": false,
  "error_message": ""
}
```

**不通过**:
```json
{
  "progress_counter": 18,
  "validation_errors": ["name 含拉丁字符", "image[3] 不可达"],
  "is_valid": false,
  "auto_fixed": false,
  "error_message": "2 validation error(s)"
}
```

#### 2.8.7 自测用例

```python
# 用例 1: 载荷完全合法
# 预期: is_valid=True, validation_errors=[]

# 用例 2: 标题含中文
input_2 = OzonValidateInput(ozon_payload={"name": "泡沫喷壶 清洁"}, ...)
# 预期: is_valid=False, validation_errors 含 Latin/CJK 警告

# 用例 3: 图片不可达
input_3 = OzonValidateInput(ozon_payload={"images": ["https://invalid-url-12345.com/img.jpg"]}, ordered_images=["https://invalid-url-12345.com/img.jpg"], ...)
# 预期: is_valid=False, 图片被移除
```

---

### 2.9 ozon_upload + ozon_status

#### 2.9.1 职责（合并两个节点为"上传与审核"能力）

- **ozon_upload**: 提交 `POST /v3/product/import`，获取 `ozon_task_id`
- **ozon_status**: 轮询导入状态 → 审核状态，三态返回 (approved/pending/error)

#### 2.9.2 输入参数

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `ozon_payload` | dict | GlobalState (validate) | 已校验载荷 |
| `ozon_client_id` | str | GlobalState | Ozon API |
| `ozon_api_key` | str | GlobalState | Ozon API |
| `product_id` | Optional[str] | GlobalState | UPDATE 模式时非空 |
| `ozon_task_id` | str | GlobalState (upload) | 上传任务 ID |

> 输入 TypedDict: `OzonUploadInput` / `OzonStatusInput`

#### 2.9.3 执行逻辑

```
─── ozon_upload ───
Step 1: 检查 validation_errors: 如果有 CRITICAL 错误 → 阻断上传
Step 2: 二次配额检查: ozon_check_quota()
Step 3: 更新进度: update_progress(task_id, "ozon_upload", "提交Ozon上传...")
Step 4: POST /v3/product/import → 获取 ozon_task_id
        - 有 product_id → UPDATE 模式 (含 product_id 字段)
        - 无 product_id → CREATE 模式
Step 5: 返回 ozon_task_id, upload_status="pending"

─── ozon_status ───
Step 6: Phase 1 — 轮询导入状态 (最多 10×3s=30s):
        POST /v1/product/import/info {task_id: ozon_task_id}
        - status == "imported" → 提取 product_id, 进入 Phase 2
        - status == "failed" → 提取 errors, 返回 status="error"
        - 超时 → 返回 status="pending"
Step 7: Phase 2 — 轮询审核状态 (最多 120×5s=10min):
        POST /v3/product/info/list {product_id: ...}
        - moderate_status == "approved" → 返回 status="approved"
        - moderate_status == "declined" → 提取 errors, 返回 status="error"
        - moderate_status in ("pending", "skipped") → 继续轮询
        - 超时 → moderation_retry_count++, 返回 status="pending"
Step 8: 返回 OzonStatusOutput {status: approved|pending|error, errors, product_id}
```

**API 调用**:
- Ozon: `POST /v3/product/import`, `POST /v1/product/import/info`, `POST /v3/product/info/list`

#### 2.9.4 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 3 | `update_progress(task_id, "ozon_upload", "提交Ozon上传...")` |
| **执行中** | Step 6-7 轮询中 | `update_progress(task_id, "ozon_status", "轮询中...{elapsed}s")` |
| **成功** (approved) | Step 7 | `log_task_event("completed", task_id)` — logger INFO |
| **失败** (error) | Step 6/7 失败 | `error_message` + errors[], logger ERROR |
| **审核中** (pending) | Step 7 超时 | `moderation_retry_count++`, 由 `should_handle_error` 路由回 `ozon_status` |

#### 2.9.5 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| /v3/product/import 5xx | 1 次 | 5s | upload_status="failed" |
| /v1/product/import/info 轮询 | 10 次 | 3s | status="pending" (由 graph 重试) |
| /v3/product/info/list 审核轮询 | 120 次 | 5s | status="pending", moderation_retry_count++ (最多 3 次) |
| /v3/product/info/list API 5xx | 1 次 | 10s | 继续轮询 |

#### 2.9.6 输出结果

**通过审核**:
```json
{
  "progress_counter": 22,
  "status": "approved",
  "upload_status": "success",
  "product_id": "1111111111",
  "ozon_task_id": "abc-123",
  "purchase_url": "https://...",
  "purchase_cost": "25.5",
  "sku_id": "980815374096_0",
  "profit_estimation": {...},
  "errors": [],
  "error_message": ""
}
```

**审核中** (pending, 需重试):
```json
{
  "status": "pending",
  "moderation_retry_count": 1,
  "product_id": "1111111111"
}
```

**有错误**:
```json
{
  "status": "error",
  "upload_status": "failed",
  "errors": [{"code": "BR_hashtag_brand", "message": "..."}],
  "error_message": "Ozon validation failed: BR_hashtag_brand"
}
```

#### 2.9.7 自测用例

```python
# 用例 1: 正常上传+审核通过
# 预期: status="approved", product_id 非空

# 用例 2: 上传成功但审核超过10分钟
# 预期: status="pending", moderation_retry_count=1 (后续重试)

# 用例 3: 上传立即失败 (Ozon 返回 errors)
# 预期: status="error", errors 列表非空
```

---

### 2.10 validation_retry_loop

#### 2.10.1 职责

LangGraph 子图。接收 Ozon 返回的错误列表，解析 → 分类 → 靶向修复 → 重新校验 → 重新上传 → 再次轮询状态。三条修复路径，避免重跑全管线。

#### 2.10.2 输入参数

| 字段 | 类型 | 说明 |
|------|------|------|
| `ozon_payload` | dict | 最后使用的载荷 |
| `validation_errors` | list | 校验错误 |
| `errors` | list | Ozon API 错误 [{code, message, ...}] |
| `draft` | dict | 原始产品数据 |
| `token` | str | MXOU token |
| `product_id` | Optional[str] | 已有 product_id |
| `description_category_id` | str | 类目 ID |
| `type_id` | str | 类型 ID |
| `final_attributes` | list | 当前属性 |
| `attributes_schema` | list | 属性 schema |
| `pricing_info` | dict | 定价信息 |

> 输入 TypedDict: `ValidationRetryLoopInput`

#### 2.10.3 子图拓扑

```
START → parse_error → classify_error → repair_node_selector
                                              │
                    ┌─────────────────────────┼────────────────────────────┐
                    ▼                         ▼                            ▼
            error_repair_llm          repair_prepare               repair_pricing
                    │                         │                            │
                    └─────────────────────────┼────────────────────────────┘
                                              ▼
                                         revalidate
                                              │
                                     should_continue ──── success ──► reupload → recheck_status
                                         │    │                                       │
                                    parse_error exit                          should_reupload
                                    (loop, max 3)  │                           │    │    │
                                                   ▼                      success exit parse_error
                                              final_result ←──────────────────┘    │    (loop)
                                                   │                               │
                                                   ▼                               │
                                                  END                              │
```

#### 2.10.4 节点职责

| 节点 | 职责 |
|------|------|
| `parse_error` | 从 errors 数组提取 error_code + attribute_id。**按 fix_type 批量分组**，同类错误一次修复。 |
| `classify_error` | 匹配 30+ 错误码 → 修复策略 (error_repair_llm / repair_prepare / repair_pricing / repair_dimensions / unfixable) |
| `error_repair_llm` | LLM 修复属性值/标题/hashtag。字典值搜索。DESCRIPTION_DECLINE → pg_trgm 重新匹配类目。BR_hashtag_brand → 重新生成 hashtag |
| `repair_prepare` | 修复重量/尺寸/密度/双 SKU (double_without_merger_offer) |
| `repair_pricing` | 修复缺失/归零价格字段 |
| `repair_dimensions` | 密度自适应尺寸重算 (0.8/0.3/0.1 g/cm³) |
| `revalidate` | 修复后重新校验载荷 (同 ozon_validate 逻辑) |
| `reupload` | 靶向路由器: 属性错误 → `/v1/product/attributes/update` (~3s) \| 价格错误 → `/v1/product/import/prices` (~3s) \| 类目/描述错误 → `/v3/product/import` (UPDATE) |
| `recheck_status` | 轮询修复后状态 (30s 导入 + 5min 审核) |

#### 2.10.5 修复策略映射

```python
FIX_TYPE_ATTRIBUTES  → POST /v1/product/attributes/update  # 无审核, ~3s
FIX_TYPE_PRICES      → POST /v1/product/import/prices       # 无审核, ~3s
FIX_TYPE_PRODUCT_IMPORT → POST /v3/product/import (UPDATE)  # 需审核轮询
FIX_TYPE_UNFIXABLE   → 标记 success，不浪费重试次数           # 图片/危险品/已存在
```

#### 2.10.6 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| 整体 retry loop | max_retries=3 | — | 超过后标记 success（不等死） |
| 属性修复 API 失败 | 1 次 | 2s | 切换为 FIX_TYPE_PRODUCT_IMPORT |
| prices/update 失败 | 1 次 | 2s | 同上 |
| recheck_status 超时 | 见 ozon_status | — | 返回成功 (pending 也算软成功) |

#### 2.10.7 输出结果

```json
{
  "ozon_payload": {... (修复后)},
  "validation_errors": [],
  "is_valid": true,
  "retry_count": 1,
  "error_type": "attribute",
  "error_message": "",
  "product_id": "1111111111",
  "upload_status": "success"
}
```

---

### 2.11 图片生成能力模块

#### 2.11.1 职责

使用 MXOU 图片生成 API 生成 Ozon 产品卡所需的 8-10 张 AI 图片。分为 Phase 1 (白底图 + 多角度图) 和 Phase 2 (营销图 + 场景图 + 详情图等)。

#### 2.11.2 包含节点

| Phase | 节点 | 输出字段 | 说明 |
|-------|------|----------|------|
| Phase 1 | `white_bg_gen` | `white_bg_image` | 白底产品图 |
| Phase 1 | `multi_angle_gen` | `multi_angle_image` | 多角度展示图 |
| Phase 1 | `scene_generation_llm` | `scene_context_{1,2,3}` | LLM 生成使用场景文本 |
| Phase 2 | `main_image_gen` | `main_image` | 营销主图 (多SKU跳过，走 variant_primary_loop) |
| Phase 2 | `variant_primary_loop` | `variant_primary_images` | 多SKU变体主图循环 |
| Phase 2 | `detail_gen` | `detail_image` | 产品细节图 |
| Phase 2 | `social_proof_gen` | `social_proof_image` | 社会证明图 (5星/好评) |
| Phase 2 | `scene_1_gen` | `scene_1_image` | 使用场景图 1 |
| Phase 2 | `scene_2_gen` | `scene_2_image` | 使用场景图 2 |
| Phase 2 | `scene_3_gen` | `scene_3_image` | 使用场景图 3 |
| Phase 2 | `comparison_gen` | `comparison_image` | 产品对比图 |

> ⚠️ `multi_info_gen` 已废弃并从图中移除 (v4)。Ozon 禁止附加图含文字。

#### 2.11.3 统一输入

所有图片生成节点接收相同的核心输入：

| 字段 | 类型 | 说明 |
|------|------|------|
| `draft` | dict | 产品数据 (title, description) |
| `token` | str | MXOU API Key |
| `white_bg_image` | Optional[str] | Phase 1 白底图 (Phase 2 节点使用) |
| `multi_angle_image` | Optional[str] | Phase 1 多角度图 (Phase 2 节点使用) |
| `original_images` | Optional[list] | 1688 原始图 (Phase 1 节点使用) |
| `scene_context_N` | str | LLM 生成的场景描述 (scene_N_gen 使用) |
| `variants` | list | 多 SKU 变体 (main_image_gen 检测跳过) |

#### 2.11.4 统一执行逻辑

```
Step 1: 更新进度: update_progress(task_id, "image_generation", "生成{image_type}图...")
Step 2: 构建 prompt:
        - 从 config/image_prompts.json 读取模板
        - 填充产品名: clean_title_for_image_prompt(draft.title)
        - 填充参考图: ref_images (Phase 1: 1688 原图, Phase 2: Phase 1 输出)
Step 3: 调用 MXOU 图片 API:
        call_mxou_image_api(token, prompt, ref_images, aspect_ratio="3:4", timeout=150, max_retries=3)
Step 4: 失败处理:
        - 主模型失败 → 自动 fallback nano-banana-fast
        - 异步任务 → _poll_grsai_task (3s 间隔, 90s 超时)
        - grsai 不可用 → _poll_mxou_task_fallback
Step 5: 全部失败 → 返回 None (不阻断管线，后续 prepare 节点用 original_images 补位)
Step 6: 返回 {image_field: url}
```

**API 调用**:
- MXOU Image API: `POST /image/generate` (或对应端点)

#### 2.11.5 状态上报

| 状态 | 时机 | 上报 |
|------|------|------|
| **执行中** | Step 1 | `update_progress(task_id, "image_generation", "生成{type}图...")` |
| **成功** | Step 6 | logger INFO: "{image_type} OK: {url[:50]}..." |
| **失败** (降级) | Step 5 | logger WARNING: "{image_type} failed after 3 retries, will use original" |

#### 2.11.6 异常重试

| 场景 | 重试次数 | 间隔 | 兜底 |
|------|----------|------|------|
| MXOU API 5xx/429 | 3 次 | 指数退避 (2s, 4s, 8s) | 主模型失败 → nano-banana-fast |
| 生成超时 (150s) | 0 次 (单次) | 异步轮询 90s | None (管线不阻断) |
| grsai 轮询超时 | — | 3s × 30 次 | _poll_mxou_task_fallback |

#### 2.11.7 自测用例

```python
# 用例 1: Phase 1 白底图正常生成
input_1 = WhiteBgInput(draft={title: "泡沫喷壶"}, token="sk-...", original_images=["https://..."])
# 预期: white_bg_image 非空 URL

# 用例 2: Phase 2 主图生成 (单SKU)
input_2 = MainImageInput(draft={title: "喷水玩具"}, token="sk-...", white_bg_image="https://...", multi_angle_image="https://...", variants=[])
# 预期: main_image 非空 URL

# 用例 3: 图片生成全部失败
# 模拟: MXOU API 一直返回 500
input_3 = WhiteBgInput(draft={title: "test"}, token="sk-invalid", original_images=[])
# 预期: white_bg_image=None, 不抛异常, logger WARNING
```

---

## Part 3: Skill 调度器合约

### 3.1 职责

Skill 是**无状态本地调度器**，职责边界：
- ✅ 接收用户 CLI 请求
- ✅ 参数校验和路由分发
- ✅ 调用 CDP/AK API 采集数据
- ✅ 组装 GraphInput 信封
- ✅ 提交信封到 Worker（或本地展示）
- ❌ **不上架** — 不调 Ozon Seller API 写操作
- ❌ **不执行管线** — 不跑 LangGraph 节点
- ❌ **不写业务逻辑** — 定价/类目匹配/属性映射全部在 Worker 侧

### 3.2 管线路由表

| 管线 | CLI 命令 | 触发条件 | 数据源 | Worker 端点 |
|------|----------|----------|--------|-------------|
| **A: 1688 选品** | `graph --url` | 用户提供 1688 URL | 1688 CDP + AK API | `POST /api/v1/submit_task` (auto-submit) |
| **B: Ozon 跟卖** | `follow --ozon-url` | 用户提供 Ozon URL | Ozon CDP + 1688 图搜 + AK 文本搜 | `POST /api/v1/submit_task` (auto-submit) |
| **C: Ozon 选品** | `discover --keyword\|--url` | 用户提供关键词/URL | Ozon CDP 批量 + 1688 图搜匹配 + 蓝海评分 | 无 (导出 CSV/JSON，用户手动提交) |
| **D: 以图搜款** | `image_search --image` | 用户提供图片 | 1688 CDP 图搜 | 无 (本地展示) |
| **E: 环境检查** | `check` | 用户诊断请求 | Chrome CDP + Worker health + Ozon API | `GET /api/v1/health`, `POST /api/v1/auth/verify` |

### 3.3 入参格式

#### 管线 A: 1688 选品

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|----------|
| `url` | string | ✅ (与 item_id 二选一) | 匹配 `1688.com/offer/(\d+)` |
| `item_id` | string | ✅ (与 url 二选一) | 纯数字, 10-15 位 |
| `store_id` | string | ❌ | 默认 store, 从 config_store 读取 |
| `no_submit` | flag | ❌ | 仅组装信封不提交 |

#### 管线 B: Ozon 跟卖

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|----------|
| `ozon_url` | string | ✅ | 匹配 `ozon.ru/product/...` |
| `store_id` | string | ❌ | 默认 store |
| `auto_submit` | flag | ❌ | 自动提交到 Worker |

#### 管线 C: Ozon 选品

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|----------|
| `keyword` | string | ✅ (与 url 二选一) | 1-100 字符, 非纯空格 |
| `url` | string | ✅ (与 keyword 二选一) | Ozon 页面 URL |
| `max_products` | int | ❌ | 1-200, 默认 50 |
| `min_margin` | float | ❌ | 0-100, 默认 15.0 |

#### 管线 D: 以图搜款

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|----------|
| `image` | string | ✅ | 本地路径 (文件存在) 或 http/https URL |
| `limit` | int | ❌ | 1-20, 默认 10 |

### 3.4 统一出参格式

**成功**:
```json
{
  "ok": true,
  "data": {
    "task_id": "550e8400-...",
    "envelope": {...},
    "candidates": [...],
    "matches": [...],
    "images": [...]
  },
  "trace_id": "trace-uuid-v4",
  "elapsed_ms": 3420
}
```

**失败**:
```json
{
  "ok": false,
  "error_code": "CDP_TIMEOUT",
  "message": "Chrome CDP 操作超时: 30s 内未收到 1688 响应",
  "detail": {"url": "https://detail.1688.com/offer/123.html", "timeout": 30},
  "trace_id": "trace-uuid-v4",
  "elapsed_ms": 30100
}
```

### 3.5 Skill 错误码

| error_code | 含义 | Skill 应对 |
|-----------|------|-----------|
| `INVALID_PARAM` | 参数校验失败 | 提示用户修正参数 |
| `CDP_TIMEOUT` | Chrome CDP 操作超时 (30s) | 建议重试或手动操作 |
| `CDP_VERIFICATION` | 1688 验证码拦截 | 提示用户在浏览器中滑动验证 |
| `LOGIN_REQUIRED` | 1688/Ozon 未登录 | 提示用户登录后重试 |
| `AK_EXHAUSTED` | 1688 AK 额度耗尽 | 提示更换 AK |
| `WORKER_UNREACHABLE` | Worker 服务不可达 | 检查网络或 WORKER_URL |
| `WORKER_ERROR` | Worker 返回错误 | 透传 Worker 的 error_code 和 message |
| `DATA_INCOMPLETE` | 采集数据不完整 | 返回缺失字段列表 |
| `INTERNAL_ERROR` | Skill 内部异常 | 检查日志 |

### 3.6 Worker 调用映射

| 管线 | Worker 端点 | 传入参数 | 接收结果处理 |
|------|------------|----------|-------------|
| A: 1688 | `POST /api/v1/submit_task` | `{token, ozon_client_id, ozon_api_key, envelope}` | 提取 `task_id` → `data.task_id` |
| A: 1688 (no_submit) | 无 | — | `data.envelope` = 完整 GraphInput |
| B: 跟卖 | `POST /api/v1/submit_task` | 同上 + `envelope.extensions.follow_sell=true` | 提取 `task_id` |
| E: 检查 | `GET /api/v1/health` | 无 | `data.worker_health` |
| E: 检查 | `POST /api/v1/auth/verify` | `{token, client_id, api_key}` | `data.auth` |
| 批量 | `POST /api/v1/submit_task` × N | N 次 | 汇总所有 task_id |

### 3.7 异常处理

| 场景 | 检测方式 | 返回 |
|------|----------|------|
| 参数缺失/格式错误 | CLI 解析 / preflight_check | `{ok: false, error_code: "INVALID_PARAM", message: "..."}` |
| CDP 超时 (30s) | `enrich_product_with_cdp` 返回 `ok=false` | `{ok: false, error_code: "CDP_TIMEOUT", detail: {url, timeout}}` |
| 1688 验证码 | CDP 检测滑块/验证码 DOM | `{ok: false, error_code: "CDP_VERIFICATION", message: "请在浏览器中完成验证后按 Enter"}` |
| Worker 不可达 | `requests.post` ConnectionError | `{ok: false, error_code: "WORKER_UNREACHABLE"}` → 3 次重试 (exponential backoff 1s/2s/4s) |
| Worker 返回错误 | 响应 `ok: false` | `{ok: false, error_code: "WORKER_ERROR", detail: {worker_error_code, worker_message}}` |
| 数据不完整 | 信封字段校验 | `{ok: false, error_code: "DATA_INCOMPLETE", detail: {missing_fields: [...]}}` |
| 未登录 1688 | `probe_1688_page` 检测 | `{ok: false, error_code: "LOGIN_REQUIRED"}` |

### 3.8 强制要求

| # | 要求 | 实现 |
|---|------|------|
| R1 | **trace_id 透传** | Skill 生成 uuid4 → 写入请求头 `X-Trace-Id` → Worker 日志自动携带 → 响应头返回 |
| R2 | **入参日志** | 每条 CLI 命令执行前: `logger.info(f"[{trace_id}] 入参: cmd={cmd} args={...}")` |
| R3 | **出参日志** | 返回前: `logger.info(f"[{trace_id}] 出参: ok={ok} elapsed={elapsed_ms}ms")` |
| R4 | **不写业务逻辑** | Skill 不做定价计算、属性映射、俄语翻译。这些都在 Worker 侧。 |
| R5 | **凭证不硬编码** | 全部从 `config_store` 或环境变量读取 |

### 3.9 自测用例

#### 用例 1: 正常 graph 管线

```bash
# 预期: ok=true, data.task_id 非空 UUID, elapsed_ms < 30000
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/980815374096.html" --store default
```

#### 用例 2: 参数错误 — 无 URL 且无 item_id

```bash
# 预期: 打印 usage, 退出码非 0
python3.12 scripts/cli.py graph
```

#### 用例 3: CDP 超时 — Chrome 未启动

```bash
# 先停止 Chrome, 预期: ok=false, error_code=CDP_TIMEOUT
killall "Google Chrome" 2>/dev/null
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/123456.html" --no-submit
```

---

## Part 4: PRD — 问题→方案→执行链路

> 基于 2026-07-30 全管线架构审计发现的 15 个问题 (B1-B12 + A1-A3)

### 🔴 P0: 阻断正确性（3 项 — 立即修复）

#### B2: 跟卖定价双实现撕裂

- **根因**: `follow_sell_import_node` 自己算了一次 pricing + logistics + packaging + exchange_rate，而 `pricing_node` 也有完整的定价逻辑。两个公式虽已被修补为一致，但仍有两套代码维护。
- **方案**: 
  1. 删除 `follow_sell_import_node` 中的内联定价代码（约 25 行）
  2. 改为在 `pricing_node` 中检测 `extensions.follow_sell`，走统一定价公式
  3. `follow_sell_import` 只负责 import-by-sku + 类目解析 + competitor_price 写入
  4. `graph.py` 跟卖路径改为: `follow_sell_import → pricing → assemble → ...`（不再跳过 pricing）
- **改动**: `follow_sell_import_node.py` (-25 行), `pricing_node.py` (+10 行), `graph.py` (修改条件边)
- **预估**: 30 行, 2 文件

#### B3: 类目跨 ID 空间解析失败无传播

- **根因**: `_resolve_category_by_id()` 三层解析 (直查 → pg_trgm → LLM 翻译) 全部失败后，静默降级为空类目。后续节点收到空类目 → Ozon 上传必定失败，且没有明确错误信息。
- **方案**:
  1. 三层失败后写 `error_message` = "类目解析失败: Widget ID={id}, breadcrumb={name}, 三层查询均无结果"
  2. 设置 `failed_stage` = "follow_sell_import"
  3. `graph.py` 条件边检测 → 走 `validation_retry_wrapper`（而非直接 END）
  4. retry loop 中 LLM 修复类目
- **改动**: `follow_sell_import_node.py` (+15 行), `graph.py` (修改条件边)
- **预估**: 20 行, 2 文件

#### B4: assemble/follow_sell 返回裸 dict

- **根因**: `assemble_ozon_product_node` 和 `follow_sell_import_node` 直接返回 `dict[str, Any]` 而非 TypedDict (BaseModel)。LangGraph 不做类型校验，字段拼写错误静默失败。
- **方案**:
  1. 在 `state.py` 中新增 `AssembleOutput` TypedDict 和 `FollowSellImportOutput` TypedDict
  2. 修改两个节点函数返回类型
  3. 删除 `assemble_ozon_product_node` 中直接修改 `state.xxx = value` 的代码
- **改动**: `state.py` (+20 行 TypedDict), `assemble_ozon_product_node.py` (-10 行 state 直接赋值), `follow_sell_import_node.py` (-5 行)
- **预估**: 10 行净增, 3 文件

---

### 🟠 P1: 数据流完整性（4 项 — 本周修复）

#### B1: 类目解析双实现

- **根因**: Skill `build_graph_envelope` 做了类目解析（关键词→pg_trgm），Worker `assemble_ozon_product_node` 又做了一次。两处逻辑不一致。
- **方案**:
  1. Skill 将解析结果写入 `draft.ozon_category: {description_category_id, type_id}`
  2. Worker `assemble` 检测 `draft.ozon_category` 存在 → 跳过 pg_trgm（仅做一致性校验: 检查 type_id 是否在当前 category 下有效）
  3. 不一致时 → 以 Worker pg_trgm 结果为准 + 记录 warning
- **改动**: `cloud_probe.py` (+5 行), `assemble_ozon_product_node.py` (+15 行)
- **预估**: 25 行, 2 文件

#### B5: ozon_payloads 死数据

- **根因**: `assemble_ozon_product_node` 写入 `state.ozon_payloads`（多 SKU 载荷），但 `prepare_ozon_upload_node` 从零重建 `ozon_payload`，完全覆盖。`ozon_payloads` 写入后从未被任何节点读取。
- **方案**:
  1. 方案 A (保守): 删除 `assemble` 中写 `ozon_payloads` 的代码，prepare 不做改动
  2. 方案 B (彻底): prepare 检测 `ozon_payloads` 非空时直接使用，跳过重建
  - **选择方案 A**（改动最小，prepare 重建逻辑已充分验证）
- **改动**: `assemble_ozon_product_node.py` (-15 行)
- **预估**: 15 行删除, 1 文件

#### B8: multi_info_gen 仍在运行但输出废弃

- **根因**: Ozon 规则禁止附加图含文字。`multi_info_gen` 生成含文字的俄罗斯语信息图，IMG_ORDER 已将其排除。但节点仍在图中运行，每次产品都要浪费 1 次 GPU 调用 (~30-60s)。
- **方案**: 从 `graph.py` 中移除:
  1. 删除 `multi_info_gen` 节点注册
  2. 删除所有到/从 `multi_info_gen` 的边
  3. 删除 `prepare_ozon_upload` 中输入参数 `multi_info_image`
- **改动**: `graph.py` (-5 行边), `state.py` (标记废弃), `prepare_ozon_upload_node.py` (移除字段)
- **预估**: 5 行, 3 文件

#### B12: status 字段语义超载

- **根因**: `GlobalState.status` 在不同阶段表示不同含义: auth 阶段 = HTTP 状态码, ozon_status 阶段 = approved/pending/error, upload 阶段 = success/failed。同一字段承载多种语义，条件路由可能误判。
- **方案**:
  1. 拆分为三个独立字段:
     - `auth_status: str` (AUTH_SUCCESS/TOKEN_INVALID/...)
     - `upload_status: str` (success/failed/pending/timeout) — 已存在
     - `moderation_status: str` (approved/pending/error) — 替代 `status`
  2. 修改所有读 `state.status` 的代码 → 读对应语义字段
  3. `should_handle_error` 使用 `moderation_status` 判断
- **改动**: `state.py` (+3 字段, 标记 status 为 deprecated), `ozon_status_node.py` (-5/+5), `graph.py` (-3/+3), `validation_retry_loop.py` (-3/+3)
- **预估**: 50 行, 4 文件

---

### 🟡 P2: 优化（8 项 — 逐步改进）

| # | 问题 | 方案 | 文件 | 预估 |
|---|------|------|------|------|
| B6 | 8个 Phase2 生图节点代码重复 90% | 参数化为 `generic_image_gen_node(image_type, prompt_config_path)`。prompt 从 JSON 配置读取，节点通过工厂函数创建。 | 新建 `_image_gen_factory.py` (+60 行), 修改 8 个生图节点 (-400 行) | ~340 行净删 |
| B7 | `_sanitize_title` 在 prepare + retry_loop 两处重复 | 提取到 `utils/title_sanitizer.py` → `sanitize_title(title: str) -> str`。两处导入同一函数。 | 新建 `utils/title_sanitizer.py` (+80 行), `prepare_ozon_upload_node.py` (-50 行), `validation_retry_loop.py` (-50 行) | ~20 行净删 |
| B9 | Skill↔Worker 校验标准不一致 | 在 CONTRACT-v4.md (本文档) 中统一字段约束表。两侧代码引用同一文档。 | 本文档 | 0 代码行 |
| B10 | Skill 跟卖 CDP 连接冗余 (3次→1次) | `follow_sell_cloud` 改为: 打开 CdpConnection → 复用同一连接完成 Ozon 抓取 + 1688 图搜 → 关闭。1688 AK 文本搜索不经过 CDP。 | `cloud_probe.py` (-15/+10) | 5 行净删 |
| B11 | GlobalState 80+ 字段, 许多废弃或单节点专用 | 删除: `multi_info_image`。标记 deprecated: `scene_context_1/2/3`, `multi_angle_image` (仅 Phase1→Phase2 传递)。添加注释标注每个字段的使用范围。 | `state.py` (-5/+15 注释) | 10 行 |
| A1 | import-by-sku 结果契约未明确 | 在 CONTRACT-v4.md 中明确: import-by-sku 提交后 30s 轮询, 获得 product_id → UPDATE 模式, 超时 → CREATE 模式 | 本文档 | 0 代码行 |
| A2 | 类目树双来源: Skill pg_trgm vs Worker Ozon API | 明确优先级: Skill 传入 `draft.ozon_category` > Worker pg_trgm > LLM fallback > Ozon API 实时拉取 | 本文档 + Assemble node 注释 | 0 代码行 |
| A3 | 定价参数三来源不一致 | 明确优先级: extensions 参数 > Ozon API 实时查询 > 默认值 (margin=0.25, commission=0.10) | 本文档 + Pricing node 注释 | 0 代码行 |

---

### 执行链路与时间线

```
Week 1: P0 修复 (阻断正确性)
  Day 1-2: B2 (跟卖定价统一) + B3 (类目错误传播) + B4 (TypedDict 输出)
  验证: 跟卖和 1688 两条管线全流程测试, 类目解析失败时 retry loop 正确激活

Week 2: P1 修复 (数据流完整性)
  Day 1-2: B1 (类目双实现统一) + B5 (ozon_payloads 清理)
  Day 3-4: B8 (multi_info 彻底移除) + B12 (status 拆分)
  验证: 管线条件路由正确, 无死数据字段, 状态语义清晰

Week 3-4: P2 优化 + CONTRACT-v4.md 完成
  Day 1-2: B6 (生图工厂) + B7 (title sanitizer 提取)
  Day 3: B10 (CDP 合并) + B11 (state 清理)
  Day 4-5: CONTRACT-v4.md 编写 + 全部自测用例验证
  验证: CI 通过, Docker 部署后 6 条 API 自测用例全绿
```

---

### 变更影响矩阵

| 变更 | 影响范围 | 回滚风险 |
|------|----------|----------|
| B2 跟卖定价统一 | follow_sell_import + pricing + graph 条件边 | 🟡 中 — 跟卖路径定价逻辑变化, 需全流程测试 |
| B3 类目错误传播 | follow_sell_import + graph | 🟢 低 — 仅增加错误传播, 不变更正常路径 |
| B4 TypedDict 输出 | state.py + assemble + follow_sell | 🟢 低 — 类型约束, 不影响运行时 |
| B1 类目双实现统一 | cloud_probe + assemble | 🟢 低 — Skill 多写一个字段, Worker 多读一个字段 |
| B5 ozon_payloads 清理 | assemble | 🟢 低 — 仅删除死代码 |
| B8 multi_info 移除 | graph + state + prepare | 🟢 低 — 已废弃节点, 不影响输出 |
| B12 status 拆分 | state + 4 节点 + graph | 🟡 中 — 全局状态字段重命名, 需全链路回归 |
| B6 生图工厂 | 8 生图节点 → 1 工厂 + 8 配置 | 🟡 中 — 重构, 保留原 prompt 不变 |
| B7 title sanitizer | prepare + retry_loop → 1 共享函数 | 🟢 低 — 纯提取, 逻辑不变 |

---

> **文档维护**: 本文档是 ozon-worker 系统的权威契约。所有 API/节点变更必须同步更新本文档。版本号按 `MAJOR.MINOR` 递增：API breaking change → MAJOR+1；节点 contract 变更 → MINOR+1。

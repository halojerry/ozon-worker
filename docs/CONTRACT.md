# Skill ↔ Worker 接口契约

> 版本: v3.0 | 日期: 2026-07-18 | 分支: feat/worker-phase2

## 架构边界

```
┌──────────────────────┐          ┌──────────────────────┐
│ Skill (客户本地)       │ GraphInput │ Worker (云端 Docker)   │
│ 1688 CDP → 信封组装    │ ────────→  │ LangGraph → Ozon 上架   │
└──────────────────────┘          └──────────────────────┘
```

- **Skill**: 采集 1688 数据、组装信封、提交任务、轮询状态
- **Worker**: 鉴权 → 入队 → 执行 22 节点管线 → 返回结果
- **交接点**: `GraphInput` JSON 对象，通过 HTTP API 传输

---

## 1. Worker API 端点

基础 URL: `https://<worker-host>:8080`

所有端点同时暴露在旧路径和 `/api/v1/` 前缀下（向后兼容）：

| 功能 | 旧路径 | v1 路径 | 方法 |
|------|--------|---------|------|
| 提交任务 | `POST /submit_task` | `POST /api/v1/submit_task` | POST |
| 查询状态 | `GET /task_status/{id}` | `GET /api/v1/task_status/{id}` | GET |
| 取消任务 | `POST /cancel_task/{id}` | `POST /api/v1/cancel_task/{id}` | POST |
| 健康检查 | `GET /health` | `GET /api/v1/health` | GET |
| 任务统计 | `GET /task_statistics` | `GET /api/v1/task_statistics` | GET |
| Swagger UI | — | `GET /api/v1/docs` | GET |

> 推荐使用 `/api/v1/` 路径。旧路径保持兼容，未来可能移除。

---

## 2. 提交任务 `POST /api/v1/submit_task`

### 请求体

```json
{
  "token": "sk-xxx",
  "ozon_client_id": "123456",
  "ozon_api_key": "abc-def",
  "envelope": {
    "draft": { ... },
    "source": { ... },
    "extensions": { ... }
  },
  "timeout_seconds": 1800,
  "max_retries": 3
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `token` | string | ✅ | — | MXOU API Key（支持 `sk-` 前缀） |
| `ozon_client_id` | string | ✅ | — | Ozon 卖家 Client-Id |
| `ozon_api_key` | string | ✅ | — | Ozon 卖家 Api-Key |
| `envelope` | object | ✅ | — | 产品数据信封（见 §3） |
| `timeout_seconds` | int | ❌ | 1800 | 任务超时（秒） |
| `max_retries` | int | ❌ | 3 | 最大重试次数 |

### 鉴权流程

1. 提取 `token`，剥离 `sk-` 前缀
2. 限流检查：每 token 每分钟 ≤ 10 次（`RATE_LIMIT_PER_MINUTE` 可配置）
3. 查询 Supabase `tokens` 表：`key = token AND deleted_at IS NULL`
4. 校验 `status = 1`（active）
5. 校验 `remain_quota ≥ 5.0`（MXOU 生图/LLM 需要额度）
6. 提取 `user_id` 作为 `tenant_id`

### 成功响应 `200`

```json
{
  "ok": true,
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Task submitted to queue (user: 123, balance: 100.0)"
}
```

### 错误响应

| 状态码 | error_code | 含义 |
|--------|-----------|------|
| 401 | `TOKEN_MISSING` | 请求体缺少 token |
| 401 | `TOKEN_INVALID` | token 不存在 |
| 403 | `TOKEN_DISABLED` | token 已禁用 (status=2) |
| 403 | `TOKEN_EXPIRED` | token 已过期 (status=3) |
| 402 | `INSUFFICIENT_BALANCE` | 余额不足 (remain_quota < 5.0) |
| 429 | `RATE_LIMITED` | 超过每分钟提交限制 |
| 503 | `SERVICE_UNAVAILABLE` | 任务处理器未初始化 |

错误响应格式：
```json
{
  "ok": false,
  "error_code": "INSUFFICIENT_BALANCE",
  "message": "Insufficient balance: remain_quota must be >= 5.0 (current: 3.2). Please top up your MXOU account."
}
```

---

## 3. GraphInput 信封结构

### 顶层

```json
{
  "token": "...",
  "ozon_client_id": "...",
  "ozon_api_key": "...",
  "envelope": {
    "draft": { ... },
    "source": { ... },
    "extensions": { ... }
  }
}
```

### envelope.draft — 产品数据

| 字段 | 类型 | 单位 | 必填 | 说明 |
|------|------|------|------|------|
| `item_id` | string | — | ✅ | 1688 商品 ID |
| `title` | string | — | ✅ | 中文商品标题（Worker 翻译为俄语） |
| `description` | string | — | ❌ | 产品描述（可空） |
| `currency` | string | — | ✅ | 固定 `"CNY"` |
| `images` | string[] | — | ✅ | 图片 URL 列表（≤10） |
| `attributes` | object | — | ✅ | `{中文属性名: 值}`，≤15 |
| `weight` | int | 克(g) | ✅ | 包裹重量，0=未知 |
| `dimensions` | object | 毫米(mm) | ✅ | `{length, width, height}`，0=未知 |
| `purchase_cost` | float | CNY | ✅ | 1688 采购成本 |
| `purchase_url` | string | — | ✅ | 1688 商品链接 |
| `ozon_category` | object | — | ❌ | `{description_category_id, type_id}` |
| `source_category` | string | — | ❌ | 1688 类目面包屑 |
| `variants` | array | — | ❌ | 多 SKU 变体列表 |

#### variants（多 SKU）

```json
[
  {
    "sku_id": "980815374096_0",
    "name": "白色",
    "color": "白色",
    "size": "one size",
    "image": "https://...",
    "price": 5.5,
    "original_price": 5.5,
    "stock": 100,
    "variant_type": "color"
  }
]
```

**关键约定**:
- `variant.price` = 1688 SKU 原始采购成本(CNY)，**Skill 不做加价**
- 定价由 Worker `pricing_node` 在采购成本基础上叠加佣金+汇率缓冲+利润率
- `dimensions` 单位 mm（1688 原始 cm → Skill ×10）
- `weight` 单位克（直传）

### envelope.source — 采购源（冗余）

| 字段 | 类型 | 说明 |
|------|------|------|
| `purchase_url` | string | 1688 链接 |
| `purchase_cost` | float | 采购成本(CNY) |

### envelope.extensions — 配置（可选）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `margin_rate` | float | 0.25 | 目标利润率 |
| `commission_rate` | float | 0.10 | 平台佣金率 |
| `fx_buffer` | float | 0.05 | 汇率缓冲 |
| `max_skus` | int | 15 | 最大变体数 |

---

## 4. 查询状态 `GET /api/v1/task_status/{task_id}`

### 成功响应 `200`

```json
{
  "id": "550e8400-...",
  "status": "completed",
  "tenant_id": "123",
  "priority": 0,
  "result": {
    "product_id": 123456,
    "offer_id": "980815374096",
    "pricing_info": { ... },
    "all_images": [ ... ],
    "final_attributes": [ ... ],
    "stages": { ... }
  },
  "error_message": null,
  "retry_count": 0,
  "max_retries": 3,
  "created_at": "2026-07-18T10:00:00Z",
  "started_at": "2026-07-18T10:00:05Z",
  "completed_at": "2026-07-18T10:08:30Z",
  "timeout_seconds": 1800
}
```

### 任务状态枚举

| status | 含义 | 终态 |
|--------|------|------|
| `pending` | 队列中等待 | ❌ |
| `running` | 管线执行中 | ❌ |
| `completed` | 执行成功，`result` 有值 | ✅ |
| `failed` | 永久失败，`error_message` 有值 | ✅ |
| `cancelled` | 被用户取消 | ✅ |

### Skill 轮询建议

```python
# 推荐轮询间隔
while not status_info["terminal"]:
    time.sleep(30)  # 每 30 秒查询一次
    status_info = check_task_status(task_id)
```

---

## 5. 取消任务 `POST /api/v1/cancel_task/{task_id}`

仅 `pending` 状态可取消。`running` 状态返回 409。

```json
// 成功
{"ok": true, "task_id": "...", "message": "Task cancelled successfully"}

// 失败
{"ok": false, "task_id": "...", "message": "Task cannot be cancelled (may not in pending status)"}
```

---

## 6. 健康检查 `GET /api/v1/health`

```json
// 正常
{"status": "ok", "message": "Service is running", "db": "connected"}

// 异常 (503)
{"status": "degraded", "message": "...", "db": "disconnected"}
```

---

## 7. 统一错误码

| error_code | HTTP | 含义 | Skill 应对 |
|-----------|------|------|-----------|
| `TOKEN_MISSING` | 401 | 缺少 token | 检查请求体 |
| `TOKEN_INVALID` | 401 | token 不存在 | 检查凭证 |
| `TOKEN_DISABLED` | 403 | token 已禁用 | 联系管理员 |
| `TOKEN_EXPIRED` | 403 | token 已过期 | 续费 |
| `INSUFFICIENT_BALANCE` | 402 | 余额不足 | 充值 MXOU |
| `RATE_LIMITED` | 429 | 超过限流 | 等待后重试 |
| `TASK_NOT_FOUND` | 404 | 任务不存在 | 检查 task_id |
| `TASK_NOT_CANCELLABLE` | 409 | 任务不可取消 | 已在执行中 |
| `SERVICE_UNAVAILABLE` | 503 | 服务不可用 | 稍后重试 |
| `INTERNAL_ERROR` | 500 | 内部错误 | 联系开发者 |

---

## 8. 多 SKU 变体合并规则

- **绑定属性 9048** = `item_id`（1688 商品 ID）
- 同一 `item_id` 的所有变体共享相同 9048 → Ozon 自动合并为一个商品卡
- 变体间只有**颜色/尺码/规格**可以不同，其他属性必须相同
- `double_without_merger_offer` 错误 → Worker 自动追加后缀重试
- 数量变体（如"3 件装"）拆成独立产品，不合并

---

## 9. Skill 端配置

| 环境变量 | 用途 | 示例 |
|---------|------|------|
| `WORKER_URL` | Worker 地址 | `https://worker.your-domain.com` |
| `MXOU_TOKEN` | LLM + 生图 API key | `sk-xxx` |
| `OZON_CLIENT_ID` | Ozon 卖家 API | 从 config_store 读取 |
| `OZON_API_KEY` | Ozon 卖家 API | 从 config_store 读取 |

### Skill 调用示例

```python
from scripts.cloud_probe import build_graph_envelope, submit_envelope, check_task_status

# 1. 组装信封
graph_input = build_graph_envelope(item_id="980815374096", category_query="保护套")

# 2. 提交任务
result = submit_envelope(graph_input)
task_id = result["task_id"]

# 3. 轮询状态
import time
while True:
    status = check_task_status(task_id)
    if status["terminal"]:
        break
    time.sleep(30)

# 4. 获取结果
if status["ok"]:
    print(f"上架成功: {status['result_json'].get('product_id')}")
else:
    print(f"上架失败: {status['error_message']}")
```

# Skill ↔ Worker 接口契约

> 内部文档 | 版本: v2.0 | 日期: 2026-07-15

## 架构边界

```
┌──────────────────────┐          ┌──────────────────────┐
│ Skill (本地)           │ GraphInput │ Worker (云端)          │
│ pounding-ozon-probe    │ ────────→  │ vibe-coding            │
│ 1688 CDP → 信封组装    │          │ LangGraph → Ozon 上架   │
└──────────────────────┘          └──────────────────────┘
```

- **Skill**: 只采集和组装信封，不做定价、不做 Ozon API 调用（类目查询除外）
- **Worker**: 接收信封，执行全流程：类目匹配 → 定价 → 属性 → AI 生图 → 校验 → 上传 → 自学习
- **交接点**: `GraphInput` JSON 对象

---

## GraphInput（Skill → Worker）

### 顶层结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `token` | string | 是 | mxou API Key（worker 用于 LLM 调用和认证） |
| `ozon_client_id` | string | 是 | Ozon Seller API Client-Id |
| `ozon_api_key` | string | 是 | Ozon Seller API Key |
| `envelope` | object | 是 | 三层信封 `{draft, source, extensions}` |

### envelope.draft — 产品数据

| 字段 | 类型 | 单位 | 必填 | 说明 |
|---|---|---|---|---|
| `item_id` | string | — | 是 | 1688 商品 ID |
| `title` | string | — | 是 | 中文商品标题（worker 翻译为俄语） |
| `description` | string | — | 是 | 产品描述（可空，worker 用 title 兜底） |
| `currency` | string | — | 是 | 采购货币，固定 `"CNY"` |
| `images` | string[] | — | 是 | 产品图片 URL 列表（最多 10 张，必须是字符串） |
| `attributes` | object | — | 是 | `{中文属性名: 值}`，最多 15 个 |
| `weight` | int | **克(g)** | 是 | 包裹重量，0 表示未知 |
| `dimensions` | object | **毫米(mm)** | 是 | `{length, width, height}`，0 表示未知 |
| `purchase_cost` | float | **CNY** | 是 | 1688 采购成本 |
| `purchase_url` | string | — | 是 | 1688 商品链接 |
| `supplier` | string | — | 否 | 供应商名称 |
| `stock` | int | — | 否 | 库存（暂硬编码 100） |
| `category` | string | — | 否 | Ozon 类目搜索词（俄语） |
| `ozon_category` | object | — | 否 | `{description_category_id, type_id}`，提供则跳过 LLM 匹配 |
| `shipping` | object | — | 否 | 物流信息，见下方 |

#### dimensions

```json
{"length": 140, "width": 80, "height": 10}
```

- 单位 **mm**，整数
- 1688 原始数据为 cm → Skill 自动 ×10 转换
- 全 0 时 Worker 使用默认值 300×200×50mm

#### shipping

```json
{"origin": "浙江金华", "freightCny": 3, "carrier": "中通"}
```

| 子字段 | 类型 | 说明 |
|---|---|---|
| `origin` | string | 发货地 |
| `freightCny` | float | 运费（CNY） |
| `carrier` | string | 快递公司 |

#### variants（多 SKU）

```json
[
  {
    "sku_id": "980815374096_0",
    "name": "白色",
    "color": "白色",
    "model": "",
    "image": "https://...",
    "price": 5.5,
    "original_price": 5.5,
    "size": "one size",
    "stock": 100
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `sku_id` | string | 变体 ID |
| `name` | string | 变体名称（worker 生图用） |
| `color` | string | 中文颜色名（worker 映射俄语） |
| `image` | string | 变体参考图 URL |
| `price` | float | **1688 采购成本（CNY），不做加价** |
| `original_price` | float | 同上 |
| `model` | string | 型号（可为空） |
| `size` | string | 尺码 |
| `stock` | int | 库存 |

**单 SKU vs 多 SKU**:
- 多 SKU: `draft.variants` 为数组，无顶层 `sku_id`/`price`/`original_price`
- 单 SKU: 不写 `draft.variants`，在 `draft` 顶层平铺 `sku_id`/`price`/`original_price`

### envelope.source — 采购源（冗余）

| 字段 | 类型 | 说明 |
|---|---|---|
| `purchase_url` | string | 1688 链接 |
| `purchase_cost` | float | 采购成本（CNY） |

Worker 优先从 `draft` 读取，`source` 为兜底。

### envelope.extensions — 定价配置（可选）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `margin_rate` | float | 0.25 | 目标利润率 |
| `commission_rate` | float | 0.10 | 平台佣金率 |
| `fx_buffer` | float | 0.05 | 汇率缓冲 |

---

## GraphOutput（Worker → Skill/用户）

### 提交响应 `POST /submit_task`

```json
// 成功
{"ok": true, "task_id": "uuid", "message": "任务已提交"}

// 失败
{"ok": false, "error": "<code>", "message": "<描述>"}
```

### 任务状态 `GET /task_status/<task_id>`

```json
// 进行中
{"status": "processing", "stage": "image_generation"}

// 完成
{
  "status": "completed",
  "product_id": 123456,
  "offer_id": "xxx",
  "purchase_url": "https://detail.1688.com/...",
  "purchase_cost": "5.5",
  "sku_id": "980815374096",
  "profit_estimation": {
    "cost_cny": 5.5,
    "price_rub": 350,
    "profit_rub": 120,
    "margin": 0.35
  }
}

// 失败
{
  "status": "failed",
  "error": "category_match_failed",
  "message": "未找到匹配的Ozon类目，尝试更换俄语搜索词"
}
```

### 错误码

| error | 含义 | 建议 |
|---|---|---|
| `insufficient_quota` | 额度不足（需 ≥5.0） | 告知用户充值 |
| `invalid_token` | Token 无效 | 检查凭证配置 |
| `category_match_failed` | 类目匹配失败 | 用更具体的俄语词重试 |
| `validation_failed` | Ozon 属性校验失败 | Worker 自动进入重试修复 |
| `timeout` | 任务超时（30min） | 重新提交 |

---

## Worker API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/submit_task` | POST | 提交任务，需 `Authorization: Bearer <token>` |
| `/task_status/<task_id>` | GET | 查询任务状态 |
| `/cancel_task/<task_id>` | POST | 取消任务 |
| `/health` | GET | 健康检查 |

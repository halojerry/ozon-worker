# Ozon AI ERP · 后端完整 API 与数据库需求文档

> 本文档覆盖 WebUI 所有页面的接口、字段和数据表需求。  
> 现有已开放接口参考：`src/imports/API-INTEGRATION-GUIDE.md`（v0.56.6/v0.57）  
> 已接入状态参考：`docs/API-INTEGRATION-STATUS.md`  
> **请后端按优先级逐步实现，完成后在对应条目注明版本号。**

---

## 优先级

| 标记 | 含义 |
|------|------|
| 🔴 P0 | 核心链路阻塞，上线必须 |
| 🟡 P1 | 重要功能，当前占位 |
| 🟢 P2 | 增强功能，可分期交付 |

---

## 一、鉴权与会话

### 现有接口（已接入）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/auth/verify` | API Key 验证 |
| `POST` | `/api/v1/mxou/login` | 账号密码登录 |

### 现有接口字段（需联调确认）

```jsonc
// POST /api/v1/mxou/login 响应（前端依赖以下字段）
{
  "key": "sk-xxxx",          // 必须返回，用于后续请求
  "role": "admin",           // "admin" | "user"，前端据此控制管理员入口
  "username": "Kate Lin"     // 可选，显示在侧边栏账号区
}
```

### 待补充

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `POST` | `/api/v1/mxou/logout` | 登出，服务端吊销 token（前端目前仅清 localStorage）|
| 🟡 P1 | `GET` | `/api/v1/mxou/me` | 获取当前登录用户信息（角色、用户名、余额）|

```jsonc
// GET /api/v1/mxou/me 期望响应
{
  "user_id": "uuid",
  "username": "Kate Lin",
  "role": "admin",           // "admin" | "user"
  "email": "kate@example.com",
  "balance": 128.50,         // 账户余额（显示在登录页余额卡）
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

## 二、店铺管理（Credentials / Stores）

> 前端页面：侧边栏 → 店铺管理  
> 现状：页面为演示数据，凭证 CRUD 和店铺统计未接入

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/credentials` | 凭证列表 |
| `POST` | `/api/v1/credentials` | 创建凭证 |
| `PATCH` | `/api/v1/credentials/{id}` | 轮换凭证 |
| `DELETE` | `/api/v1/credentials/{id}` | 吊销凭证 |
| `POST` | `/api/v1/credentials/{id}/validate` | 校验凭证是否有效 |
| `GET` | `/api/v1/stores/{id}/stats` | 今日订单/销售额/利润统计（v0.57）|
| `POST` | `/api/v1/stores/{id}/sync` | 手动触发同步 |
| `GET` | `/api/v1/stores/{id}/sync-status` | 同步状态 |

### 前端依赖字段

```jsonc
// GET /api/v1/credentials 列表每项
{
  "id": "uuid",                        // credential_id，用于店铺统计/同步接口
  "ozon_client_id": "4718259",
  "api_key_masked": "****13d7",
  "shop_name": "深圳跨境旗舰店",        // 店铺卡标题
  "currency": "RUB",
  "is_default": true,
  "credential_type": "api_key",
  "status": "active",                  // "active" | "invalid" | "expired"
  "last_validated_at": "2026-08-19T10:00:00Z",
  "last_rotated_at": null
}

// GET /api/v1/stores/{id}/stats
{
  "credential_id": "uuid",
  "ozon_client_id": "4718259",
  "stats_date": "2026-08-20",
  "today_orders": 128,
  "today_sales_amount": 286420.0,      // ₽
  "today_commission": 12000.0,
  "today_profit": 274420.0,
  "today_product_count": 1284          // 在售商品数
}
```

### 待补充接口

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/admin/stores` | 管理员视角：所有用户的店铺列表 |

---

## 三、商品管理（Products）

> 前端页面：侧边栏 → 商品管理  
> 现状：价格/库存直接编辑已接 bulk-prices/bulk-stocks，列表读取未接真实 API

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/products` | 在售货架（task 索引） |
| `GET` | `/api/v1/products/ozon` | Ozon 在线商品（含价格/库存/图片）|
| `GET` | `/api/v1/products/{id}/edit` | 商品编辑详情 |
| `POST` | `/api/v1/products/bulk-prices` | 批量改价 ✅ 已接入 |
| `POST` | `/api/v1/products/bulk-stocks` | 批量改库存 ✅ 已接入 |
| `POST` | `/api/v1/products/bulk-archive` | 批量归档 |
| `POST` | `/api/v1/products/{id}/update_images` | 改图重传 |

### 前端依赖字段

```jsonc
// GET /api/v1/products/ozon 每项（商品列表主要数据源）
{
  "product_id": "6017452168",
  "offer_id": "SPX1-WHT",              // SKU，批量改价/库存用此字段
  "name": "Беспроводные наушники SoundPro X1",
  "image": "https://cdn.ozon.ru/...",
  "price": 2990.0,                     // 当前售价 ₽
  "stock": 120,                        // 当前库存
  "currency": "RUB",
  "status": "active",                  // "active" | "archived" | "moderation"
  "category": "Электроника > Наушники",
  "credential_id": "uuid"              // 所属店铺，前端按店铺筛选用
}

// POST /api/v1/products/bulk-prices 请求体
{ "items": [{ "offer_id": "SPX1-WHT", "price": 2890.0 }] }

// POST /api/v1/products/bulk-stocks 请求体
{ "items": [{ "offer_id": "SPX1-WHT", "stock": 100 }] }
```

### 待补充接口

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🔴 P0 | 确认 | `/api/v1/products/ozon` 支持分页和筛选参数 | 前端需要 `credential_id`、`status`、`limit`、`offset`、关键词搜索 |
| 🟡 P1 | `GET` | `/api/v1/products/{id}/edit` | 返回完整编辑字段（属性、变体、图片列表）|

```jsonc
// GET /api/v1/products/ozon 期望查询参数
?credential_id=uuid&status=active&limit=20&offset=0&q=耳机
```

---

## 四、订单中心（Orders）

> 前端页面：侧边栏 → 订单中心  
> 现状：列表展示演示数据，所有操作（发货/取消/消息）均为占位按钮

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/orders` | 订单列表（缓存）|
| `POST` | `/api/v1/orders/{posting_number}/ship` | 发货 |
| `POST` | `/api/v1/orders/batch/ship` | 批量发货 |
| `POST` | `/api/v1/orders/{posting_number}/cancel` | 取消订单 |
| `GET` | `/api/v1/orders/{posting_number}/cancel-reasons` | 取消原因列表 |
| `GET` | `/api/v1/orders/{posting_number}/label` | 运单标签 |
| `POST` | `/api/v1/orders/batch/labels` | 批量运单标签 |
| `POST` | `/api/v1/orders/{posting_number}/message` | 发消息给买家 |
| `GET` | `/api/v1/orders/messages` | 消息记录 |
| `GET` | `/api/v1/orders/message-templates` | 消息模板 |
| `GET` | `/api/v1/orders/{posting_number}/notes` | 订单备注 |
| `PUT` | `/api/v1/orders/{posting_number}/notes` | 保存备注 |

### 前端依赖字段

```jsonc
// GET /api/v1/orders 每项
{
  "posting_number": "78442335-0050-1",
  "status": "delivering",              // awaiting_deliver | delivering | delivered | cancelled
  "raw_status": "在途中",
  "created_at": "2026-08-18T10:00:00Z",
  "products": [
    {
      "name": "Беспроводные наушники SoundPro X1",
      "quantity": 1,
      "offer_id": "SPX1-WHT",
      "product_id": "6017452168",
      "image": "https://..."
    }
  ],
  "product_count": 1,
  "total_amount": 2990.0,              // 订单金额 ₽
  "commission_amount": 299.0,
  "profit": 2691.0,
  "warehouse": "Москва (Хоругвино)",
  "delivery_method": "ozon_rocket",
  "buyer_name": "Алексей Петров",      // 前端订单详情展示
  "delivery_address": "Москва, ...",   // 前端订单详情展示
  "credential_id": "uuid"             // 所属店铺
}

// GET /api/v1/orders 查询参数
?credential_id=uuid&status=awaiting_deliver&limit=20&offset=0&since_days=7&refresh=false
```

### 待补充

| 优先级 | 说明 |
|--------|------|
| 🔴 P0 | 确认 `status` 枚举值（前端筛选 Tab 要对应显示：待发货/已发货/配送中/已取消）|
| 🟡 P1 | 确认 `buyer_name`、`delivery_address` 字段路径（详情抽屉需要）|
| 🟡 P1 | `GET /api/v1/orders/message-templates` 返回结构（消息模板选择器）|

---

## 五、任务中心（Tasks）

> 前端页面：侧边栏 → 任务中心  
> 现状：任务列表展示演示数据，任务创建为占位，进度轮询未接

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/tasks` | 任务列表 |
| `GET` | `/api/v1/task_status/{task_id}` | 任务状态与进度 |
| `POST` | `/api/v1/submit_task` | 提交上架任务 |
| `POST` | `/api/v1/resubmit_task/{task_id}` | 重新提交 |
| `POST` | `/api/v1/cancel_task/{task_id}` | 取消任务 |
| `GET` | `/api/v1/tasks/{task_id}/draft` | 任务关联草稿 |
| `GET` | `/api/v1/tasks/{task_id}/images` | 任务生成图片列表 |
| `POST` | `/api/v1/tasks/{task_id}/images/{slot}/regen` | 重生成指定槽位图片 |
| `GET` | `/api/v1/task_statistics` | 任务 KPI 统计 |

### 前端依赖字段

```jsonc
// GET /api/v1/tasks 每项
{
  "id": "uuid",
  "status": "completed",               // pending | running | completed | failed | cancelled
  "title": "宠物饮水机",
  "image": "https://...",              // 任务主图（列表缩略图）
  "item_id": "1035536839701",          // 源商品 ID
  "ozon_client_id": "4718259",
  "shop_name": "深圳跨境旗舰店",
  "update_mode": false,                // true = 改图/改价重传任务
  "parent_task_id": null,             // 有值 = 重上任务
  "created_at": "2026-08-19T10:00:00Z",
  "progress": {
    "stage": "completed",
    "percent": 100,
    "stages_completed": ["check_quota", "ozon_upload"],
    "message": "上架成功，商品 ID: 6017452168"
  }
}

// GET /api/v1/task_statistics（工作台 KPI 卡数据源）
{
  "total": 8,
  "pending": 0,
  "running": 1,
  "completed": 7,
  "failed": 0,
  "cancelled": 0,
  "avg_duration_seconds": 184.5
}

// GET /api/v1/tasks/{id}/images
{
  "images": [
    { "slot": 0, "url": "https://...", "status": "ready" },
    { "slot": 1, "url": "https://...", "status": "generating" }
  ]
}
```

### 待补充接口

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/tasks` 支持筛选 | 需要 `status`、`credential_id`、`limit`、`offset` 查询参数 |
| 🟡 P1 | 确认 | 自动化任务创建入口 | 前端"创建自动化"→ 产品翻新/自动选品/自动上架 调用哪个端点、传什么参数 |

---

## 六、采集箱与草稿（Drafts / Collection）

> 前端页面：侧边栏 → 采集箱  
> 现状：列表已接 `GET /drafts`，创建/编辑/删除草稿为占位

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/drafts` | 草稿列表 ✅ 已接入 |
| `GET` | `/api/v1/drafts/{id}` | 草稿详情 |
| `POST` | `/api/v1/drafts` | 创建草稿 |
| `PATCH` | `/api/v1/drafts/{id}` | 更新草稿（乐观锁）|
| `DELETE` | `/api/v1/drafts/{id}` | 删除草稿 |
| `POST` | `/api/v1/drafts/{id}/ai/{field}` | AI 字段生成 |
| `POST` | `/api/v1/drafts/{id}/estimate` | 预估售价 |
| `GET` | `/api/v1/drafts/{id}/submissions` | 提交历史 |
| `POST` | `/api/v1/drafts/{id}/submit` | 提交上架 |

### 前端依赖字段

```jsonc
// GET /api/v1/drafts 每项（采集箱列表展示）
{
  "id": "uuid",
  "status": "pending",                 // pending | submitted | published | failed
  "source": "webui",                   // 来源平台标注
  "created_at": "2026-08-19T10:00:00Z",
  "envelope": {
    "draft": {
      "title": "无线蓝牙耳机 ANC 降噪",
      "images": ["https://..."],       // 第一张图用作列表缩略图
      "purchase_cost": 38.5,           // 采购价 CNY
      "purchase_url": "https://...",   // 货源地址
      "weight": 0.3,
      "dimensions": { "width": 10, "height": 5, "depth": 5 }
    }
  }
}

// POST /api/v1/drafts 请求体（创建草稿）
{
  "token": "sk-xxxx",
  "ozon_client_id": "4718259",
  "ozon_api_key": "xxxx",
  "source": "webui",
  "envelope": {
    "draft": {
      "title": "商品标题",
      "images": ["https://..."],
      "purchase_cost": 38.5,
      "purchase_url": "https://...",
      "weight": 0.3,
      "dimensions": { "width": 10, "height": 5, "depth": 5 }
    }
  }
}

// POST /api/v1/drafts/{id}/ai/{field} 期望字段名
// field 枚举：title_ru | description_ru | keywords | attributes
```

### 待补充

| 优先级 | 说明 |
|--------|------|
| 🔴 P0 | 确认 `GET /drafts` 返回字段的完整 schema（当前前端做了兼容性回退，联调后收紧）|
| 🟡 P1 | `POST /drafts/{id}/estimate` 返回字段：预估售价、毛利率、佣金明细 |
| 🟡 P1 | `POST /drafts/{id}/ai/{field}` 返回字段：生成内容、置信度 |

---

## 七、上架模板（Listing Templates）

> 前端页面：侧边栏 → 上架模板  
> 现状：列表为演示数据，编辑器 UI 已完成，CRUD 未接入

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/templates` | 模板列表 |
| `POST` | `/api/v1/templates` | 创建模板 |
| `PATCH` | `/api/v1/templates/{id}` | 更新模板 |
| `DELETE` | `/api/v1/templates/{id}` | 删除模板 |
| `POST` | `/api/v1/templates/{id}/default` | 设为默认模板 |

### 前端依赖字段

```jsonc
// GET /api/v1/templates 每项
{
  "id": "uuid",
  "name": "默认通用模板",
  "description": "适用于通用商品上架流程",
  "platform": "ozon",
  "is_default": true,
  "config": {
    "margin_rate": 0.42,               // 加价率（倍率）
    "commission_rate": 0.15,           // 佣金率
    "fx_buffer": 0.035,                // 汇率缓冲
    "stock_buffer_rate": 0.15,         // 库存缓冲比例
    "stock_alert_threshold": 10,       // 低库存预警值
    "auto_publish_score": 85,          // AI 自评分阈值（>= 此分数自动上架）
    "translate_mode": "ai_then_review",// ai_auto | ai_then_review | manual | skip
    "title_template": "{{品牌}} {{型号}} {{颜色}} — {{卖点关键词}}",
    "image_rules": {
      "remove_bg": true,
      "white_bg": true,
      "resize_standard": true,
      "ai_scene": false,
      "min_count": 3
    },
    "follow_type": "hand",
    "warehouse_id": null
  },
  "store_overrides": {
    "<credential_id>": { "margin_rate": 0.30 }  // 按店铺覆盖参数
  },
  "created_at": "2026-08-01T00:00:00Z"
}

// PATCH /api/v1/templates/{id} 请求体（部分更新）
{
  "name": "数码配件高利润",
  "config": { "margin_rate": 1.58, "commission_rate": 0.18 }
}
```

---

## 八、图片工坊（Studio / Image Tasks）

> 前端页面：侧边栏 → 图片工坊  
> 现状：展示演示任务列表，任务图片读取和重生成未接入

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/tasks/{id}/images` | 任务图片列表 |
| `POST` | `/api/v1/tasks/{id}/images/{slot}/regen` | 重生成指定槽位 |
| `POST` | `/api/v1/products/{id}/update_images` | 商品改图重传 |

### 前端依赖字段

```jsonc
// GET /api/v1/tasks/{id}/images
{
  "task_id": "uuid",
  "images": [
    {
      "slot": 0,
      "url": "https://...",
      "status": "ready",              // ready | generating | failed
      "type": "main",                // main | scene | detail
      "generated_at": "2026-08-19T10:00:00Z"
    }
  ]
}

// POST /api/v1/tasks/{id}/images/{slot}/regen 请求体
{
  "prompt_override": "白色背景，商品居中"  // 可选，覆盖默认提示词
}

// 响应
{
  "slot": 0,
  "status": "generating",
  "estimated_seconds": 30
}
```

### 待补充接口

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/image-tasks` | 图片处理任务列表（批量背景替换、去背景、尺寸裁剪等）|
| 🟡 P1 | `POST` | `/api/v1/image-tasks` | 创建图片处理任务 |
| 🟡 P1 | `GET` | `/api/v1/image-tasks/{id}` | 任务进度与结果图片列表 |

```jsonc
// GET /api/v1/image-tasks 每项（图片工坊任务列表）
{
  "id": "uuid",
  "type": "remove_bg",              // remove_bg | white_bg | resize | scene_gen
  "status": "processing",           // pending | processing | completed | failed
  "product_name": "SoundPro X1 耳机",
  "total_images": 50,
  "processed_images": 32,
  "progress_percent": 64,
  "created_at": "2026-08-19T10:00:00Z",
  "completed_at": null,
  "result_urls": []                 // completed 后返回处理结果图
}
```

---

## 九、智能定价（Pricing）

> 前端页面：侧边栏 → 智能定价  
> 现状：页面展示演示数据，定价建议和竞品价格走势未接

### 待补充接口

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `POST` | `/api/v1/estimate` | 独立预估售价（已开放，前端未接）|
| 🟡 P1 | `POST` | `/api/v1/logistics/quote` | 物流运费报价（已开放，前端未接）|
| 🟢 P2 | `GET` | `/api/v1/pricing/suggestions` | AI 定价建议列表 |
| 🟢 P2 | `GET` | `/api/v1/pricing/competitor-trend` | 竞品价格走势（时间序列）|

```jsonc
// POST /api/v1/estimate 请求体
{
  "purchase_cost": 38.5,           // 采购价 CNY
  "weight_kg": 0.3,
  "credential_id": "uuid",
  "category_id": "uuid",
  "margin_rate": 1.42,
  "fx_buffer": 0.035
}

// 响应
{
  "suggested_price_rub": 2890.0,
  "gross_profit_rub": 1102.5,
  "gross_profit_rate": 0.381,
  "commission_rub": 289.0,
  "logistics_rub": 150.0,
  "exchange_rate": 13.2
}

// GET /api/v1/pricing/suggestions
{
  "items": [
    {
      "offer_id": "SPX1-WHT",
      "name": "SoundPro X1",
      "current_price": 2990.0,
      "suggested_price": 2890.0,
      "competitor_median": 2850.0,
      "expected_profit_delta": 1143.0,
      "action": "lower"              // "lower" | "raise" | "ok"
    }
  ]
}
```

---

## 十、选品广场（Bestsellers / Discovery）

> 前端页面：侧边栏 → 选品广场（7 个分类 Tab）  
> 现状：热销产品已接 `GET /analytics/bestsellers`，其余 6 个分类全为占位

### 已开放接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/analytics/bestsellers` | 榜单浏览（全局共享）✅ 已接入 |
| `POST` | `/api/v1/analytics/ozon-bestsellers` | Ozon 榜单上报（Worker 内部用）|
| `POST` | `/api/v1/analytics/market-bestsellers` | 市场榜单上报（Worker 内部用）|
| `POST` | `/api/v1/analytics/queries` | 蓝海查询词上报 |
| `GET` | `/api/v1/discovery/runs` | 选品归档列表（全局共享）|
| `POST` | `/api/v1/discovery/runs` | 上报选品归档 |

### 各 Tab 所需接口

#### 10.1 大盘总览

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/analytics/market-overview` | 全类目大盘 KPI（GMV、订单量、增速、活跃卖家数）|

```jsonc
{
  "date": "2026-08-20",
  "total_gmv": 1234567890,
  "total_orders": 2345678,
  "gmv_growth_rate": 0.18,
  "active_sellers": 45678,
  "top_categories": [
    { "name": "电子产品", "gmv_share": 0.32, "growth": 0.21 }
  ]
}
```

#### 10.2 类目分析

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/analytics/categories` | 类目销量、增速、竞争度聚合 |

```jsonc
// GET /api/v1/analytics/categories?parent_id=uuid&limit=20
{
  "items": [
    {
      "category_id": "uuid",
      "name_zh": "耳机",
      "name_ru": "Наушники",
      "monthly_gmv": 5234567,
      "monthly_growth": 0.15,
      "seller_count": 1234,
      "competition_rate": 0.28,      // 头部品牌搜索占比
      "avg_price": 2890.0
    }
  ]
}
```

#### 10.3 热销产品（已部分接入）

```jsonc
// GET /api/v1/analytics/bestsellers 期望支持的查询参数（当前未确认）
?category_id=uuid
&brand=SoundPro
&fulfillment=ozon_rocket          // 发货方式
&min_monthly_sales=500
&max_monthly_sales=10000
&min_price=500
&max_price=5000
&min_growth=0.1
&limit=50
&offset=0
```

#### 10.4 中国仓热销

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/analytics/china-warehouse` | 中国仓库存 × 市场榜单交叉数据 |

```jsonc
{
  "items": [
    {
      "offer_id": "SPX1-WHT",
      "title": "SoundPro X1 耳机",
      "warehouse_stock": 1200,         // 中国仓现货量
      "monthly_sales": 3456,
      "turnover_days": 11,             // 库存周转天数
      "profit_rate": 0.38,
      "image_url": "https://..."
    }
  ]
}
```

#### 10.5 热词精选

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/analytics/hot-queries` | 热搜词列表（前台用户读取，与 admin/queries 词库分开）|

```jsonc
{
  "items": [
    {
      "query": "беспроводные наушники",
      "search_volume": 125000,
      "growth_rate": 0.23,
      "competition": "medium",         // low | medium | high
      "category": "耳机"
    }
  ]
}
```

#### 10.6 标签反查

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟢 P2 | `GET` | `/api/v1/analytics/tag-reverse` | 输入商品/关键词，返回关联标签列表 |

```jsonc
// GET /api/v1/analytics/tag-reverse?q=耳机&type=product
{
  "tags": [
    { "tag": "bluetooth", "relevance": 0.95, "search_volume": 45000 },
    { "tag": "wireless",  "relevance": 0.91, "search_volume": 38000 }
  ]
}
```

#### 10.7 WB 热销

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟢 P2 | `GET` | `/api/v1/analytics/wb-bestsellers` | Wildberries 榜单读取 |

```jsonc
{
  "items": [
    {
      "title": "Наушники TWS",
      "brand": "BoAt",
      "wb_category": "Электроника",
      "monthly_sales": 8900,
      "price": 1890.0,
      "rating": 4.7,
      "image_url": "https://..."
    }
  ]
}
```

---

## 十一、管理员后台（Admin）

> 前端页面：侧边栏 → 平台后台（仅管理员可见）

### 11.1 用户管理

#### 已开放

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/admin/users` | 用户列表 ✅ 已接入 |
| `GET` | `/api/v1/admin/users/{id}` | 用户详情 |
| `GET` | `/api/v1/admin/overview` | 平台 KPI ✅ 已接入 |

#### 待补充

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `POST` | `/api/v1/admin/users` | 创建成员（邀请）|
| 🟡 P1 | `PATCH` | `/api/v1/admin/users/{id}` | 修改角色 / 停用 / 恢复 |
| 🟡 P1 | `DELETE` | `/api/v1/admin/users/{id}` | 删除成员 |

```jsonc
// GET /api/v1/admin/users 每项
{
  "id": "uuid",
  "username": "Kate Lin",
  "email": "kate@example.com",
  "role": "admin",                   // "admin" | "user"
  "status": "active",                // "active" | "disabled"
  "last_login_at": "2026-08-20T09:00:00Z",
  "created_at": "2026-01-01T00:00:00Z",
  "store_count": 2                   // 该成员关联的店铺数
}

// PATCH /api/v1/admin/users/{id} 请求体
{ "role": "admin", "status": "disabled" }
```

### 11.2 类目配置

> ⚠️ **前提说明**：类目映射表已存在于 PG 数据库。下列接口需要后端基于现有表实现。  
> **请后端确认并回填现有表的实际字段名**，前端将按确认后的字段对齐映射。

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🔴 P0 | `GET` | `/api/v1/admin/categories` | 类目树（含子节点嵌套）|
| 🔴 P0 | `POST` | `/api/v1/admin/categories` | 新建类目 |
| 🔴 P0 | `PATCH` | `/api/v1/admin/categories/{id}` | 更新（改名/排序/启停）|
| 🔴 P0 | `DELETE` | `/api/v1/admin/categories/{id}` | 删除（级联删子类目）|
| 🟡 P1 | `POST` | `/api/v1/admin/categories/reorder` | 批量拖拽排序 |

**现有 PG 表（字段待后端确认）**

前端使用以下字段，请后端确认各字段在现有表中的实际列名：

| 前端期望字段名 | 类型 | 用途 | 待确认实际列名 |
|--------------|------|------|--------------|
| `id` | UUID | 类目唯一标识 | `id` / `category_id` / ? |
| `parent_id` | UUID / null | 父类目（null = 顶级）| `parent_id` / ? |
| `name_zh` | string | 中文类目名（管理界面显示）| ? |
| `name_ru` | string | 俄语类目名（Ozon 对应）| ? |
| `ozon_category_id` | bigint | Ozon 平台类目 ID（选品/上架时关联）| ? |
| `is_active` | boolean | 前端是否展示该类目 | `is_active` / `enabled` / ? |
| `sort_order` | int | 排序权重（越小越靠前）| `sort_order` / `rank` / ? |

**前端依赖的 API 响应结构**

```jsonc
// GET /api/v1/admin/categories （树形嵌套）
[
  {
    "id": "uuid",
    "parent_id": null,
    "name_zh": "电子产品",
    "name_ru": "Электроника",
    "ozon_category_id": 17038062,
    "is_active": true,
    "sort_order": 1,
    "children": [
      {
        "id": "uuid",
        "parent_id": "uuid",
        "name_zh": "耳机",
        "name_ru": "Наушники",
        "ozon_category_id": 17038742,
        "is_active": true,
        "sort_order": 1,
        "children": []
      }
    ]
  }
]

// GET /api/v1/admin/categories?flat=true （扁平列表，用于下拉选择器）
[
  { "id": "uuid", "name_zh": "电子产品 > 耳机", "ozon_category_id": 17038742 }
]
```

### 11.3 数据源管理

> ⚠️ **前提说明**：数据源表已存在于 PG 数据库。下列接口需要后端基于现有表实现。  
> **请后端确认现有表的实际字段名**，前端将按确认后的字段对齐映射。

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🔴 P0 | `GET` | `/api/v1/admin/data-sources` | 数据源列表 |
| 🔴 P0 | `POST` | `/api/v1/admin/data-sources` | 创建数据源 |
| 🔴 P0 | `PATCH` | `/api/v1/admin/data-sources/{id}` | 更新配置/启停 |
| 🔴 P0 | `DELETE` | `/api/v1/admin/data-sources/{id}` | 删除 |
| 🟡 P1 | `POST` | `/api/v1/admin/data-sources/{id}/sync` | 手动触发同步 |
| 🟡 P1 | `GET` | `/api/v1/admin/data-sources/{id}/sync-status` | 同步状态轮询 |
| 🟡 P1 | `POST` | `/api/v1/admin/data-sources/import/csv` | CSV 文件上传（multipart/form-data）|
| 🟡 P1 | `GET` | `/api/v1/admin/data-sources/import/{job_id}` | 导入任务进度 |

**现有 PG 表（字段待后端确认）**

| 前端期望字段名 | 类型 | 用途 | 待确认实际列名 |
|--------------|------|------|--------------|
| `id` | UUID | 数据源唯一标识 | ? |
| `name` | string | 数据源名称（管理界面展示）| ? |
| `type` | string | 类型枚举（见下方枚举值）| ? |
| `category_ids` | UUID[] | 关联填充的类目列表 | ? |
| `config` | JSONB | 连接参数（API Key/URL，敏感字段加密）| ? |
| `is_active` | boolean | 是否启用 | ? |
| `last_sync_at` | timestamptz | 最后同步时间 | ? |
| `sync_status` | string | 同步状态枚举（见下方）| ? |
| `sync_error` | string/null | 同步失败原因 | ? |
| `record_count` | int | 当前数据条数（展示在列表卡片）| ? |

**type 枚举值（前端筛选 Tab 用）**

```
ozon_api        — Ozon 官方接口抓取
wb_api          — Wildberries 接口抓取
user_collection — 用户采集箱草稿汇聚
csv_import      — 手动 CSV 文件导入
custom          — 自定义爬虫/第三方接口
```

**sync_status 枚举值**

```
pending   — 待同步（初始态）
syncing   — 同步中
ok        — 同步成功
error     — 同步失败
```

**前端依赖的 API 响应结构**

```jsonc
// GET /api/v1/admin/data-sources
{
  "items": [
    {
      "id": "uuid",
      "name": "Ozon 热销榜 - 每日抓取",
      "type": "ozon_api",
      "category_ids": ["uuid-1", "uuid-2"],
      "is_active": true,
      "last_sync_at": "2026-08-20T06:00:00Z",
      "sync_status": "ok",
      "sync_error": null,
      "record_count": 12580,
      "config": {
        "url": "https://...",
        "schedule": "0 6 * * *"
        // 敏感 key 字段脱敏，只返回 "****xxxx" 形式
      }
    }
  ],
  "total": 5
}
```

**CSV 标准列格式**

| 列名 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | ✅ | 商品标题 |
| `brand` | string | | 品牌 |
| `category_zh` | string | | 中文类目（匹配 categories 表）|
| `source_url` | string | | 货源链接 |
| `platform` | string | | 平台（AliExpress / 1688 / Ozon）|
| `purchase_cost` | float | | 采购价 CNY |
| `monthly_sales` | int | | 月销量（件）|
| `growth_rate` | float | | 月销售增速（%）|
| `image_url` | string | | 主图 URL |

### 11.4 用户采集信号

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/admin/analytics/collection-signals` | 用户采集热度聚合（管理员）|
| 🟢 P2 | `GET` | `/api/v1/analytics/collection-signals` | 脱敏热度榜（普通用户可见）|

**数据库视图**

```sql
CREATE VIEW collection_signals AS
SELECT
  (envelope->'draft'->>'purchase_url')  AS source_url,
  (envelope->'draft'->>'title')         AS title,
  COUNT(*)                               AS collection_count,
  AVG((envelope->'draft'->>'purchase_cost')::float) AS avg_cost,
  MAX(created_at)                        AS last_collected_at
FROM drafts
WHERE envelope->'draft'->>'purchase_url' IS NOT NULL
GROUP BY source_url, title
ORDER BY collection_count DESC;
```

### 11.5 生图配置（已对接，确认结构）

> 使用已有的 `/api/v1/admin/config/{name}` 接口，无需新建接口。  
> 前端已实现完整 JSON 编辑器 + 备份/回滚 UI。

**前端约定的配置名（需后端确认 name 是否一致）**

| 配置名 | 内容 |
|--------|------|
| `image_generation` 或 `comfyui_config` | 生图提示词、LoRA、采样参数 |
| `selection_modes` | 选品模式默认参数（见第 11.6 节）|
| `prompt_templates` | 各类 AI 生成的提示词模板 |

> ⚠️ **请后端确认实际存在的 config name 列表**，前端 `GET /admin/config` 会读取并展示所有配置名。

### 11.6 选品模式配置持久化

> 复用 `PUT /api/v1/admin/config/selection_modes`，无需新接口。

```jsonc
// PUT /api/v1/admin/config/selection_modes 请求体
{
  "热销商品": {
    "enabled": true,
    "allow_user_override": true,
    "show_in_plaza": true,
    "defaults": {
      "rank_max": 100,
      "monthly_sales_min": 500,
      "profit_rate_min": 0.25
    }
  },
  "热销新品": {
    "enabled": true,
    "allow_user_override": true,
    "show_in_plaza": true,
    "defaults": { "days_range": 30, "growth_rate_min": 0.5, "rating_min": 4.0 }
  },
  "轻仓爆品": {
    "enabled": true,
    "allow_user_override": false,
    "show_in_plaza": true,
    "defaults": { "weight_max_kg": 1, "turnover_days_max": 14, "profit_rate_min": 0.35, "china_stock_only": true }
  },
  "蓝海商品": {
    "enabled": false,
    "allow_user_override": false,
    "show_in_plaza": true,
    "defaults": { "competition_rate_max": 0.3, "search_growth_min": 0.2, "seller_count_max": 50 }
  }
}
```

### 11.7 权限与审计日志

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/admin/audit-logs` | 操作日志列表（分页+筛选）|
| 🟢 P2 | `GET` | `/api/v1/admin/audit-logs/export` | 导出 CSV |

**数据库表**

```sql
CREATE TABLE audit_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,
  username    TEXT NOT NULL,
  action      TEXT NOT NULL,   -- create | update | delete | login | export
  resource    TEXT NOT NULL,   -- user | category | data_source | config | template
  resource_id TEXT,
  detail      JSONB,           -- 变更前后的 diff
  ip_address  TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);
```

```jsonc
// GET /api/v1/admin/audit-logs
?user_id=uuid&action=update&resource=config&limit=50&offset=0&since=2026-08-01
{
  "items": [
    {
      "id": "uuid",
      "username": "Kate Lin",
      "action": "update",
      "resource": "config",
      "resource_id": "image_generation",
      "detail": { "before": {...}, "after": {...} },
      "ip_address": "123.45.67.89",
      "created_at": "2026-08-20T10:30:00Z"
    }
  ],
  "total": 342
}
```

### 11.8 站点公告与横幅

> 接口已存在，前端待排期对接。

| 方法 | 路径 | 状态 |
|------|------|------|
| `GET/POST` | `/api/v1/admin/site/announcements` | 待前端对接 |
| `PUT/DELETE` | `/api/v1/admin/site/announcements/{id}` | 待前端对接 |
| `GET/POST` | `/api/v1/admin/site/banners` | 待前端对接 |
| `PUT/DELETE` | `/api/v1/admin/site/banners/{id}` | 待前端对接 |

### 11.9 查询词库

> 接口已存在，前端待排期对接（管理员蓝海词库管理）。

| 方法 | 路径 | 状态 |
|------|------|------|
| `GET` | `/api/v1/admin/queries` | 待前端对接 |
| `POST` | `/api/v1/admin/queries/import` | 待前端对接 |
| `DELETE` | `/api/v1/admin/queries/{id}` | 待前端对接 |

### 11.10 物流费率

> 接口已存在，前端待排期对接。

| 方法 | 路径 | 状态 |
|------|------|------|
| `GET` | `/api/v1/admin/logistics/rates` | 待前端对接 |
| `POST` | `/api/v1/admin/logistics/rates/import` | 待前端对接 |
| `PUT` | `/api/v1/admin/logistics/rates/{id}` | 待前端对接 |

---

## 十二、数据大屏（Data Screen）

> 前端页面：侧边栏 → 数据大屏  
> 现状：全部演示数据，无真实接口

### 待补充接口

| 优先级 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| 🟡 P1 | `GET` | `/api/v1/task_statistics` | 任务 KPI（已存在，待接入）|
| 🟡 P1 | `GET` | `/api/v1/analytics/sales-trend` | 订单/销售额时间序列（按天）|
| 🟢 P2 | `GET` | `/api/v1/analytics/geo-heatmap` | 全球订单地理分布（经纬度热力数据）|

```jsonc
// GET /api/v1/analytics/sales-trend?days=7&credential_id=uuid
{
  "items": [
    { "date": "2026-08-14", "orders": 98, "gmv": 245000.0, "profit": 89000.0 },
    { "date": "2026-08-15", "orders": 112, "gmv": 278000.0, "profit": 102000.0 }
  ]
}

// GET /api/v1/analytics/geo-heatmap
{
  "points": [
    { "city": "Москва",           "lat": 55.75, "lng": 37.62, "orders": 3450 },
    { "city": "Санкт-Петербург", "lat": 59.93, "lng": 30.32, "orders": 1230 }
  ]
}
```

---

## 十三、图片 URL 展示规范（全局）

> 前端所有图片展示均直接渲染接口返回的 URL（`<img src={url} />`），不做本地代理。  
> **后端须确保所有图片 URL 可被浏览器直接访问（无鉴权、无跨域限制，或提供 CORS 头）。**

### 各模块图片字段汇总

| 模块 | 接口 | 图片字段路径 | 用途 | 占位策略 |
|------|------|------------|------|--------|
| 商品管理 | `GET /products/ozon` | `items[].image` | 列表缩略图 40×40 | 灰色方块占位 |
| 商品管理 | `GET /products/{id}/edit` | `images[]` | 商品图片组（最多9张）| 空槽位 + 上传按钮 |
| 订单中心 | `GET /orders` | `products[].image` | 订单商品缩略图 32×32 | 灰色方块 |
| 采集箱 | `GET /drafts` | `envelope.draft.images[0]` | 列表缩略图 40×40 | 灰色方块 |
| 任务中心 | `GET /tasks` | `image` | 任务卡主图 48×48 | 灰色方块 |
| 图片工坊 | `GET /tasks/{id}/images` | `images[].url` | 生成图预览（大图、可下载）| 加载中骨架屏 |
| 图片工坊 | `GET /image-tasks/{id}` | `result_urls[]` | 批量处理结果（可下载）| 进度条 |
| 选品广场 | `GET /analytics/bestsellers` | `image_url` 或 `image` | 榜单商品封面 56×56 | 灰色方块 |
| 选品广场 | `GET /analytics/china-warehouse` | `image_url` | 中国仓热销商品封面 | 灰色方块 |
| 选品广场 | `GET /analytics/wb-bestsellers` | `image_url` | WB 商品封面 | 灰色方块 |

### 图片 URL 要求

| 要求 | 说明 |
|------|------|
| **协议** | 必须为 `https://`，不接受 `http://` 混合内容 |
| **CORS** | 响应须含 `Access-Control-Allow-Origin: *`（或前端域名），否则浏览器无法渲染 |
| **尺寸** | 列表缩略图建议后端返回带 `?w=120` 类参数的压缩版本，节省带宽；大图返回原始尺寸 |
| **CDN 缓存** | 建议 CDN 缓存 TTL ≥ 24h，列表页频繁滚动时性能敏感 |
| **失效处理** | URL 失效时返回 404（不要返回 403），前端 `onError` 回退到灰色占位图 |
| **Ozon CDN** | Ozon 官方图（`cdn.ozon.ru`）有域名鉴权限制，**建议后端做镜像缓存后返回内部 CDN URL**，不要直接透传 Ozon 原始 URL |

### 前端图片渲染示例（已在 App.tsx 中使用的模式）

```tsx
// 统一的带 fallback 图片组件（建议）
<img
  src={item.image_url || item.image}
  onError={(e) => { (e.target as HTMLImageElement).src = "/placeholder.png" }}
  className="w-10 h-10 rounded object-cover bg-gray-100"
/>
```

> 🔴 **特别注意**：Ozon CDN 图片（`cdn.ozon.ru`）在部分网络环境下有访问限制，前端无法控制。  
> **强烈建议后端在同步数据时，将商品图片转存至自有 CDN（如阿里云 OSS / Cloudflare R2），接口返回自有 CDN URL。**

---

## 十四、全局安全要求

所有 `/api/v1/admin/*` 接口必须：

1. **鉴权**：校验 Bearer token 有效（同现有逻辑）
2. **权限校验**：token 对应账号 `role === "admin"`，否则返回 `403`
3. **跨租户隔离**：管理员接口操作全局数据，普通 token 不得越权调用
4. **写操作审计**：POST / PATCH / PUT / DELETE 写入 `audit_logs` 表
5. **敏感字段**：`config` 表中的 API Key / 密钥等字段 AES-256-GCM 加密存储，接口返回时脱敏（`****xxxx`）

---

## 十四、需求汇总清单

### 新增接口（按模块）

| 模块 | 新增接口数 | 优先级 | 需新建数据表 |
|------|-----------|--------|-------------|
| 鉴权（logout/me）| 2 | 🟡 P1 | ❌ |
| 类目管理 | 5 | 🔴 P0 | ⚠️ 已有表，确认字段名后对接 |
| 数据源管理 + CSV 导入 | 8 | 🔴 P0 + 🟡 P1 | ⚠️ 已有表，确认字段名后对接 |
| 用户采集信号 | 2 | 🟡 P1 | ✅ 视图 `collection_signals` |
| 用户管理写操作 | 3 | 🟡 P1 | ❌（扩展现有 users 表）|
| 审计日志 | 2 | 🟡 P1 | ✅ `audit_logs` |
| 选品广场（6 个 Tab 接口）| 7 | 🟡 P1 ~ 🟢 P2 | ✅ 扩展 `analytics_items` |
| 图片工坊任务 | 3 | 🟡 P1 | ✅ `image_tasks` |
| 定价建议 | 2 | 🟢 P2 | ✅ `pricing_suggestions` |
| 数据大屏时序数据 | 2 | 🟡 P1 | ✅ 视图或 TS 扩展 |
| **合计** | **36 个** | | **6 张新表 + 2 个视图** |

### 现有接口前端待对接（接口已开放，前端未接）

| 接口 | 前端模块 |
|------|---------|
| `GET /products` + `GET /products/ozon` | 商品管理列表 |
| `GET /orders` + 发货/取消/消息系列 | 订单中心 |
| `GET /tasks` + 进度轮询 | 任务中心 |
| `GET/POST/PATCH/DELETE /templates` | 上架模板 |
| `GET/POST/PATCH/DELETE /credentials` | 店铺管理 |
| `GET /admin/users/{id}` + 写操作 | 管理员用户管理 |
| `GET /admin/queries` + import/delete | 管理员查询词库 |
| `GET /admin/logistics/rates` + import/update | 物流费率 |
| `GET /admin/site/announcements` + CRUD | 站点公告 |
| `GET /admin/site/banners` + CRUD | 站点横幅 |
| `POST /estimate` + `POST /logistics/quote` | 智能定价 |
| `GET /task_statistics` | 数据大屏 KPI |

---

*文档版本：v1.0 · 2026-08-20 · 前端整理*  
*对接进度请同步更新至 `docs/API-INTEGRATION-STATUS.md`*

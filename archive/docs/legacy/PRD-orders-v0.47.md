# PRD — 订单列表（P0-4，对标上品帮/毛子ERP orderManage）

> 2026-08-16。竞品调研：`docs/competitor/shangpinbang-full.md` §4.1（我的订单：13 列 + 状态机 + 备货/面单/物流/利润/买家黑名单）、`docs/competitor/maozier-backend-full.md` §七（我的订单：7 状态 tab + 货源/采购信息 + 取消原因 + 费用/利润列）。
> 现状：Worker 无订单模块；WebUI 无订单页。这是 P0 最后一个需要新数据源的项。

## 一、背景与目标

### 1.1 问题
自有 WebUI 缺订单管理——用户无法看到 Ozon 店铺的订单（待备货/发运/运输中/已签收），无法做采购/货源跟进。竞品两家均把订单管理作为核心板块。

### 1.2 目标
1. Worker 新增 `GET /api/v1/orders`：**实时拉取 Ozon FBS 订单**（`/v3/posting/fbs/list`，按店铺凭证）→ 标准化返回（不建表，数据源是 Ozon API）
2. WebUI 新增「订单」页（/orders）：状态筛选 tab + 订单表格（对标两家核心列）+ CSV 导出
3. 订单状态机映射：Ozon FBS 状态 → 统一 7 态（待备货/待发运/运输中/已签收/已取消/已归档/其他）

### 1.3 非目标（P0 范围外）
- 订单持久化建表/自动同步（实时拉取够用；批量备货/面单打印/催评等操作是 P1——需要 posting 写入 API + 状态流转）
- 买家黑名单、退货申请、数据大屏（后续 P1/P3）
- 货源/采购信息编辑回写（P1）

## 二、Worker 设计

### 2.1 Ozon FBS 订单 API（官方）
```
POST /v3/posting/fbs/list
{
  "dir": "ASC",
  "filter": {
    "since": "2026-01-01T00:00:00Z",
    "to": "...",          // 可选
    "status": "arbitrary_available",  // 可选：待发运等
    "provider_id": [], "warehouse_id": []
  },
  "limit": 1000, "offset": 0, "with": {"analytics_data": true, "financial_data": true}
}
```
响应 `result.postings[]` 关键字段：
- `posting_number`（货件编号）、`status`（arbitrary_available/arbitrary_not_enough_for_package/...）、`in_process_at`（下单时间）
- `products[]`：`name/sku/quantity/price/offer_id`
- `financial_data.products[]`：`price/commission_amount/...`（费用利润）
- `analytics_data`：region/warehouse 等
- `delivery_method`：`name/warehouse`（仓库与配送方式）
- `cancel_reason`（取消原因）、`cancellation`（取消方）

### 2.2 状态映射（Ozon FBS → 统一 7 态）
| Ozon status | 统一态 |
|---|---|
| `awaiting_registration` / `acceptance_in_progress` | 待处理 |
| `arbitrary_available` / `arbitrary_not_enough_for_package` | 待备货 |
| `arbitrary_waiting_for_shipment` / `arbitrary_cancelled_by_merchant` | 待发运 |
| `delivering` / `driver_pickup` | 运输中 |
| `delivered` | 已签收 |
| `cancelled_by_merchant` / `cancelled_by_customer` / `cancelled_by_ozon` / `cancelled_arbitrary` / `cancelled` | 已取消 |
| 其他 | 其他 |

### 2.3 新路由 `routes/orders_routes.py` + `services/order_service.py`
```
GET /api/v1/orders?credential_id=&status=&limit=&offset=&since=
```
- 鉴权 `_authenticate` → tenant_id
- `credential_id` 必填（或默认店铺）→ `get_decrypted` 解密 → `ozon_post /v3/posting/fbs/list`（`with.financial_data=true` 拿费用/利润）
- 响应 `OrderOut`：posting_number/status(统一态)/raw_status/created_at/products[](name/sku/quantity/price/offer_id)/total_amount/commission_amount/profit/warehouse/delivery_method/cancel_reason/cancel_cancellation
- 错误：无默认店铺 → 400「请先配置店铺」；Ozon API 失败 → 502 透传
- 租户隔离：credential 归属校验（get_decrypted 已处理跨租户 404）

### 2.4 schemas
`OrderOut` / `OrderProductOut` / `OrderListResponse`（items/total/limit/offset）

## 三、WebUI 设计

### 3.1 新页面 `pages/Orders.tsx`
- 路由 `/orders`（Layout 菜单「订单管理」，在售货架旁）
- 顶部：店铺下拉（listCredentials，默认店铺）+ 状态 tab（全部/待备货/待发运/运输中/已签收/已取消）
- 表格列（对标两家核心）：
  | 列 | 来源 |
  |---|---|
  | 货件编号 | posting_number |
  | 状态 | 统一态 badge |
  | 商品信息 | products[].name + quantity（首个商品 + 共 N 件） |
  | 金额 | total_amount |
  | 费用/利润 | financial_data 汇总（commission_amount / profit 估算） |
  | 仓库/配送 | delivery_method.name + warehouse |
  | 下单时间 | in_process_at |
  | 操作 | 查看详情（弹窗：全部商品 + 取消原因/取消方） |
- 工具栏：导出 CSV（当前筛选结果）
- 加载失败（无店铺/API 错误）→ 友好提示 + 去店铺管理

### 3.2 client.ts
`OrderOut` / `OrderListResponse` / `listOrders(params)` / `getOrderDetail`（可省，列表已含详情数据）

## 四、测试计划

### Worker
- `test_order_service.py`（mock `ozon_post`）：状态映射全枚举、products/financial 提取、无默认店铺 400、Ozon API 失败 502、租户隔离
- `test_orders_api.py`：鉴权 401、正常列表、credential 归属 404

### WebUI
- build + tokens:validate
- 手动冒烟（测试账号无店铺则跳过；有店铺则验证真实拉取）

## 五、验收标准（DoD）
1. `GET /api/v1/orders` 返回标准化订单（状态统一映射 + 商品/金额/费用/仓库/时间）
2. WebUI 订单页：状态 tab 筛选 + 表格 + 详情弹窗 + CSV 导出
3. 无店铺 → 400 提示；Ozon API 错误 → 502 不崩
4. worker 全量回归不破（1016 → 1016+新增）

## 六、实施顺序
T0 order_service（状态映射 + 拉取标准化）→ T1 orders_routes + schemas → T2 worker 测试 → T3 WebUI Orders.tsx + 路由 + client.ts → T4 版本 0.47.0 + 全量回归 + 提交

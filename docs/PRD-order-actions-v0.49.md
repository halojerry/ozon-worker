# PRD — 订单写入操作 P1-2（备货发货 + 取消订单）

> 2026-08-16。竞品调研：`docs/competitor/shangpinbang-full.md` §4.1（批量备货「确定后无法拆分」/取消订单选原因）、`docs/competitor/maozier-backend-full.md` §七（取消货件选原因）。
> 前置：v0.48 订单列表 + 备注/面单（P1-1）。本 PRD 落地**对真实订单的写入操作**：备货发货 + 取消订单。
> ⚠️ 写入操作有真实影响——测试账号无订单无法端到端验证，靠 worker 单测（mock ozon_post）+ 前端逻辑审查覆盖；真实店铺由用户使用后反馈。

## 一、背景与目标

### 1.1 问题
订单只能看/标注，不能处理。竞品两家订单页核心操作：**批量备货**（上品帮：批量备货→确认后不可拆分；毛子：待备货 tab）、**取消订单**（毛子：选原因）。

### 1.2 目标（P1-2 范围）
1. **备货发货**：订单页「待备货」tab 下单条/批量操作 → `POST /v4/posting/fbs/ship` 确认备货（对标上品帮批量备货）
2. **取消订单**：订单行「取消」→ 拉取原因列表（`/v1/posting/fbs/cancel-reason`）→ 选原因 → `POST /v2/posting/fbs/cancel`

### 1.3 非目标
- 催护照/催取货/索好评消息（P1-3：消息模板体系，对标 autoMsg，3 种内置模板 + 占位符）
- 批量面单（多选合并 PDF，P1-3）
- 拆分/合并订单（split/unfulfilled）

## 二、设计

### 2.1 Worker `order_service.py` 新增
```python
def ship_order(tenant_id, posting_number, credential_id=None) -> dict:
    """备货发货：/v4/posting/fbs/ship {packages:[{posting_number, products:[], packages_count:1}]}
    products 空 → Ozon 自动取单内全部商品。返回 Ozon result。"""

def list_cancel_reasons(tenant_id, posting_number, credential_id=None) -> list[dict]:
    """取消原因列表：/v1/posting/fbs/cancel-reason {posting_number}
    返回 [{id, title}]。"""

def cancel_order(tenant_id, posting_number, cancel_reason_id, credential_id=None) -> dict:
    """取消订单：/v2/posting/fbs/cancel {posting_number, cancel_reason_id}
    返回 Ozon result。"""
```
- 全部走 `get_decrypted`（credential_id 或默认店铺）+ `ozon_post`
- 失败：无默认店铺 400 / Ozon API 错误 502（与现有 orders 一致）
- 请求体 `packages[].products`：列表有商品时传 `[{product_id: sku, quantity}]`（从 posting.products 取），空则省略让 Ozon 自动取

### 2.2 路由（`orders_routes.py` 扩展）
```
POST /api/v1/orders/{posting_number}/ship          → ship_order
GET  /api/v1/orders/{posting_number}/cancel-reasons → list_cancel_reasons
POST /api/v1/orders/{posting_number}/cancel        → cancel_order（body: cancel_reason_id）
```
鉴权 `_authenticate`（同现有）。

### 2.3 schemas
`CancelReasonOut {id: int, title: str}` / `CancelRequest {cancel_reason_id: int}` / `OrderActionResponse {ok, posting_number, result}`

### 2.4 WebUI 订单页（Orders.tsx）
- **待备货/待发运 tab 行操作**：「备货发货」按钮（confirm：`确认备货 {posting_number}？备货后订单进入待发运`）→ ship → 成功提示 + 刷新
- **所有行**：「取消」按钮 → 弹窗（拉取 cancel-reasons → 下拉选原因）→ cancel → 成功提示 + 刷新
- 操作失败（502/400）→ 行内错误提示

## 三、测试计划

### Worker（mock ozon_post）
- `test_order_actions.py`：
  1. ship：请求体断言（packages/posting_number/products 映射）+ 成功返回 + 无默认店铺 400 + Ozon 失败 502
  2. cancel-reasons：成功返回 [{id,title}] + 失败 502
  3. cancel：请求体断言（cancel_reason_id）+ 成功 + 失败 502

### WebUI
- build + tokens:validate
- 逻辑审查（写入操作无测试账号订单，无法端到端）

## 四、验收标准（DoD）
1. ship/cancel-reasons/cancel 三端点 worker 单测通过（请求体 + 错误路径）
2. WebUI 备货发货按钮（待备货 tab）+ 取消弹窗（原因下拉）+ 成功/失败提示
3. worker 全量回归不破

## 五、实施顺序
T0 order_service ship/cancel-reasons/cancel → T1 路由 + schemas → T2 worker 测试 → T3 WebUI 按钮 + 取消弹窗 → T4 版本 0.49.0 + 回归 + 提交

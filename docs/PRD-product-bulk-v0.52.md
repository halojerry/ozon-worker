# PRD — 在线商品批量操作（P1a）

> 2026-08-16。竞品调研：上品帮 `/goodsManage`（批量改价/改库存/归档/恢复，价格指数）、毛子 `/product/online`（批量改价/改重量/参加促销/归档/重上）。
> 前置：v0.50 在线商品实时拉取（`/api/v1/products/ozon`）。
> 本 PRD：对拉取到的 Ozon 店铺商品做**批量操作**（改价/改库存/归档/恢复）。

## 一、目标

WebUI 在线商品（店铺商品视图）支持：
1. **批量改价**：勾选商品 → 统一设置新售价/划线价（或按比例调）→ `/v1/product/import/prices`
2. **批量改库存**：勾选商品 → 统一设置库存 → `/v2/products/stocks`
3. **批量归档/恢复**：勾选 → `/v1/product/archive` / `/v1/product/unarchive`
4. 操作后刷新列表

## 二、设计

### 2.1 Worker `shelf_service` 新增（Ozon 写入操作）
```python
def bulk_update_prices(tenant_id, credential_id, prices: list[dict]) -> dict:
    """/v1/product/import/prices {prices:[{offer_id, price, old_price?, min_price?, currency_code?}]}
    返回 Ozon result（含 task_id / errors）。"""

def bulk_update_stocks(tenant_id, credential_id, stocks: list[dict]) -> dict:
    """/v2/products/stocks {stocks:[{offer_id, product_id, stock}]}"""

def bulk_archive(tenant_id, credential_id, product_ids: list[str], archive: bool) -> dict:
    """/v1/product/archive 或 /v1/product/unarchive {product_id:[...]}"""
```
- 复用 `_resolve_credential`（order_service 已有，抽公共或复制）
- 失败：无默认店铺 400 / Ozon 502；返回 Ozon errors 明细

### 2.2 路由（shelf_routes.py 扩展）
```
POST /api/v1/products/bulk-prices    {credential_id?, prices:[]}
POST /api/v1/products/bulk-stocks    {credential_id?, stocks:[]}
POST /api/v1/products/bulk-archive   {credential_id?, product_ids:[], archive: bool}
```

### 2.3 WebUI（OnSale.tsx 店铺商品视图）
- 加**复选框多选**（复用 Tasks 的 selected Set 模式）
- 工具栏批量操作：批量改价（弹窗输入新售价/划线价/最低价 + 应用到 N 个商品）/ 批量改库存（弹窗输入库存）/ 批量归档 / 批量恢复
- 操作确认（真实生效警告）+ 结果提示 + 刷新

## 三、测试
- `test_shelf_bulk.py`（mock ozon_post）：prices/stocks/archive 请求体断言 + 成功 + 400/502 + 租户隔离
- webui build + tokens:validate

## 四、DoD
1. 三个批量端点 worker 单测通过
2. WebUI 店铺商品多选 + 批量操作弹窗 + 结果提示
3. worker 全量回归不破

## 五、实施
T0 shelf_service 三函数 → T1 路由 + 测试 → T2 WebUI 多选 + 批量弹窗 → T3 版本 + 提交

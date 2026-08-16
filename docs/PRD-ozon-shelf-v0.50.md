# PRD — 在线商品实时拉取（修复「配置店铺看不到在线商品」）

> 2026-08-16。用户反馈：配置店铺后订单和在线商品都看不到。
> 根因诊断（实测）：
>   1. ✅ 订单——① 本地 worker 未重启加载新代码（旧进程 404）② 凭证密文与本地 master key 不一致（重配解决）③ **Ozon 400 bug**：`/v3/posting/fbs/list` 的 `since` 用 isoformat（微秒+偏移）且缺 `to` 字段 → `processed_at_to must be set`。已修复（v0.49.1，真实订单拉到 50 条）。
>   2. ❌ 在线商品——OnSale 列表基于 `product_task_index`（只含**本系统上架**的商品），Ozon 店铺手动/其他工具上的商品看不到。实测 `/v3/product/list` 返回店铺 **245 个在线商品**。

## 一、目标

为在线商品（OnSale）新增**实时拉取 Ozon 店铺商品**能力（对标上品帮 `/goodsManage`、毛子 `/product/online`），与订单实现模式一致：
1. `GET /api/v1/shelf/ozon`（新端点）：实时调 `/v3/product/list` + `/v1/product/info/list`（名称/图片/价格/库存）→ 标准化返回
2. WebUI OnSale 页：**新增「同步 Ozon 商品」tab/视图**，展示店铺全部在线商品（含非本系统上架）
3. 保留现有 product_task_index 视图（本系统上架记录，含编辑入口）

## 二、设计

### 2.1 Worker `shelf_service.py` 新增
```python
def list_ozon_products(tenant_id, credential_id=None, limit=50, offset=0) -> dict:
    """实时拉取 Ozon 店铺商品。
    1. /v3/product/list {filter:{visibility:'ALL'}, limit, offset} → [product_id, offer_id]
    2. /v1/product/info/list {product_id:[...]} → name/images/price(price.sources)/stock(stocks.present)
    返回 {items:[{product_id, offer_id, name, image, price, stock, currency}], total, limit, offset}
    """
```
- 凭证：`get_decrypted`（credential_id 或默认店铺，无默认 400）
- 分页：limit ≤ 200；total 来自 list 响应
- 失败：Ozon API 502（与 orders 一致）

### 2.2 路由（shelf_routes.py 扩展）
```
GET /api/v1/shelf/ozon?credential_id=&limit=&offset=
```

### 2.3 schemas
`OzonProductOut {product_id, offer_id, name, image, price, stock, currency}` / `OzonProductListResponse`

### 2.4 WebUI OnSale
- 页内切换：**「本系统上架」**（现有 product_task_index 视图）/ **「店铺商品」**（新拉取视图）
- 「店铺商品」视图：店铺下拉（默认店铺）+ 表格（商品图/名称/offer_id/价格/库存）+ 刷新
- 说明文案：店铺商品 = Ozon 店铺全部在线商品（含手动上架/其他工具）

## 三、测试计划

### Worker
- `test_shelf_ozon.py`（mock ozon_post）：list+info 两段调用拼接、字段提取、无默认店铺 400、Ozon 失败 502

### WebUI
- build + tokens:validate

### 实测（测试账号 4718259，245 商品）
- `GET /api/v1/shelf/ozon` 返回真实商品列表

## 四、验收标准（DoD）
1. `/api/v1/shelf/ozon` 真实返回店铺商品（245+）
2. WebUI 双视图切换可用
3. worker 全量回归不破

## 五、实施顺序
T0 shelf_service.list_ozon_products → T1 路由 + schemas → T2 worker 测试 + 实测 → T3 WebUI 双视图 → T4 版本 0.50.0 + 回归 + 提交

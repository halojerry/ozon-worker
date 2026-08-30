# Ozon 字段映射表 v1(M0 探针冻结)

> 状态:**已冻结(2026-08-30 探针通过,店铺 5423887)**。returns 的 item 字段因该店窗口无退货,按 swagger 冻结,待有数据店复核。
> 对齐规则:权威字段优先(is_archived/errors 等直接用 Ozon 返回值);枚举归一;金额 ₽ NUMERIC(14,2);日期 ISO8601→timestamptz(UTC);无源不编造。

## 0. 探针实测结论(2026-08-30,原始响应 docs/ozon-probe/*.json)

1. **`/v3/product/info/list` 响应是顶层 `items[]`(不是 `result.items`),商品项用 `id` 而非 `product_id`**——现有 `_fetch_info_map_by_ids` 按 `result.items` + `product_id` 取会得到空,商品名/图/价会丢,M1 必须修。
2. **`/v3/product/list` 的 `visibility=ALL` 含归档**:全量 137 vs 归档 87;list 项自带 `archived` 布尔;归档权威字段用 info/list 的 `is_archived` / `is_autoarchived`(已实测存在)。「未出现→archived」启发式确认是错的,废除。
3. **info/list item 实测字段**:id/offer_id/name/images/primary_image/price/old_price/min_price/stocks/is_archived/is_autoarchived/errors/statuses/visibility_details/commissions/price_indexes/updated_at 全部存在(另有 20+ 富字段)。
4. **`/v1/warehouse/list` 与 `/v1/delivery-method/list` 已废弃(HTTP 400 obsolete)**;替代为 **`/v2/warehouse/list`**(body 空,响应 `warehouses[] + has_next + cursor`)与 **`/v2/delivery-method/list`**(body 必带 `limit` ∈ (0,100])。
5. **`/v1/analytics/data` 实测**:`result.data[].dimensions[].id`=日期,`metrics[]` 与请求 metrics 顺序一一对应;`result.totals` 为全量合计;`timestamp` 为 UTC 生成时间。
6. **`/v1/rating/summary` 实测**:顶层 `groups[]`(`group_name` + `items[].{name,current_value,past_value,status,change}`)+ `localization_index` + `premium/premium_plus/penalty_score_exceeded`;店铺评分取 `groups[].items[].current_value`。
7. **`/v5/product/info/prices` 实测**:`items[].{product_id,offer_id,price,price_indexes,commissions,acquiring,marketing_actions,volume_weight}` + `cursor/total`;price 对象内含三档价。
8. **`/v1/returns/list` 实测**:顶层 `returns[] + has_next`;该店 7 天窗口为空,item 字段按 swagger 冻结待复核。

## 1. 商品域(在售/归档/错误/审核)

端点:`POST /v3/product/list`(分页 offset/limit)+ `POST /v3/product/info/list`(按 product_id 批量补详情)

| Ozon 原始字段(info.list item) | 标准化 | DB 列(ozon_products_cache) | webui | 状态 |
|---|---|---|---|---|
| id(info.list) / product_id(list) | product_id | product_id | 商品 ID | ✅ 已接(注意 info.list 用 id) |
| offer_id | offer_id | offer_id | 货号 | ✅ 已接 |
| name | name | name | 标题 | ✅ 已接 |
| images[0] / primary_image | image | image | 主图 | ✅ 已接 |
| price.price / marketing_price | price | price | 售价 | ✅ 已接 |
| old_price | old_price | old_price | 划线价 | 🧪 探针验证 |
| min_price | min_price | min_price | 最低价 | 🧪 探针验证 |
| stocks.present | stock | stock | 库存 | ✅ 已接 |
| is_archived / is_autoarchived | archived + archived_at | archived / archived_at | 状态徽标 | ✅ 探针验证(废除启发式) |
| errors[].{code,message,state} | error jsonb | error | 错误 tab | ✅ 探针验证 |
| statuses[] / visibility_details | status | status | 审核状态 | ✅ 探针验证 |
| commissions / price_indexes | raw 留底 | raw(jsonb 可选) | 促销指数(阶段二) | ✅ 探针验证 |
| updated_at | synced_at | synced_at | 同步时间 | ✅ 已接 |

验证点:visibility=ALL 是否含归档;ARCHIVED 枚举是否可用;errors 数组实际结构;三档价是否恒在。

## 2. 订单域(FBS)

端点:`POST /v4/posting/fbs/list`(cursor/has_next 分页)

| Ozon 原始字段 | 标准化 | DB 列(ozon_orders_cache) | 状态 |
|---|---|---|---|
| posting_number | posting_number | posting_number | ✅ 已接 |
| status | map_status | status / raw_status | ✅ 已接 |
| in_process_at / shipment_date | created_at | order_created_at | ✅ 已接 |
| products[].{name,sku,quantity,price,offer_id,product_id} | products jsonb | products | ✅ 已接 |
| financial_data.products[].price / commission | total_amount / commission_amount | total_amount / commission_amount | ✅ 已接 |
| (计算)real_profit | real_profit | real_profit / order_line_costs | 🧪 探针验证(financial_data 结构) |
| analytics_data.warehouse / delivery_method.name | warehouse / delivery_method | warehouse / delivery_method | ✅ 已接 |
| cancel_reason / cancellation | cancel_reason / cancellation | cancel_reason / cancellation | ✅ 已接 |

验证点:financial_data.products 金额/佣金字段名;cursor 与 has_next 行为;空响应。

## 3. 退货域

端点:`POST /v1/returns/list`(has_next 分页,日期窗口)

| Ozon 原始字段 | 标准化 | DB 列(ozon_returns_cache) | 状态 |
|---|---|---|---|
| id | return_id | return_id | 🧪 探针验证 |
| posting_number | posting_number | posting_number | 🧪 |
| order_id / order_number | order_id | order_id | 🧪 |
| type / schema | return_type / schema | return_type / schema | 🧪 |
| return_reason_name | reason | reason | 🧪 |
| compensation_status | compensation_status | compensation_status | 🧪 |
| product / exemplars | product jsonb | product | 🧪 |
| status | status | status | 🧪 |

验证点:请求体 filter/limit/offset 契约;has_next 分页;日期窗口参数。

## 4. 店铺分析域

端点:`POST /v1/analytics/data`(dimensions + metrics,日级)

| Ozon 原始字段 | 标准化 | DB 列(ozon_store_analytics_daily) | 状态 |
|---|---|---|---|
| result.data[].dimensions | stat_date / metric | stat_date / metric | 🧪 探针验证(metrics 枚举) |
| result.data[].metrics[] | value | value | 🧪 |
| result.totals | raw | raw | 🧪 |

验证点:metrics 合法枚举(hits_view_search/hits_view_pdp/orders_count/revenue…);date_from/date_to 格式;limit 上限。

## 5. 评分域

端点:`POST /v1/rating/summary`(实测:groups[].items[].{name,current_value,status,change} + localization_index)

| Ozon 原始字段 | 标准化 | DB 列(credentials) | 状态 |
|---|---|---|---|
| groups[].items[].{name,current_value,status} | rating_items jsonb | rating_total(取主分组值)/ rating_items jsonb | ✅ 探针验证 |
| localization_index | rating_localization_index | rating_localization_index | ✅ 探针验证 |

验证点:groups 结构;空态;无评分字段时返回什么。

## 6. 促销/活动域

端点:`POST /v1/actions` + `POST /v1/seller-actions/list`

| Ozon 原始字段 | 标准化 | 落点 | 状态 |
|---|---|---|---|
| actions[].{action_id,title,status,products_count} | active_discount_count | store_metrics_history.active_discount_count | 🧪 探针验证 |
| seller_actions[].{id,title,status} | raw | raw | 🧪 |

验证点:请求体契约;空态;计数口径(进行中活动数)。

## 7. 物流/仓库域

端点:`POST /v2/warehouse/list`(v1 已废弃)+ `POST /v2/delivery-method/list`(body 必带 limit)

| Ozon 原始字段 | 标准化 | DB 列(warehouse_cache) | 状态 |
|---|---|---|---|
| warehouses[].{warehouse_id,name,is_rfbs} | warehouse_id / name / is_rfbs | warehouse_id / name / is_rfbs | ✅ 探针验证 |
| result[].{delivery_method_id,name} | raw | raw | ✅ 探针验证 |

验证点:两个端点请求体契约;is_rfbs 字段名。

## 8. 三档价/指数域

端点:`POST /v5/product/info/prices`

| Ozon 原始字段 | 标准化 | 落点 | 状态 |
|---|---|---|---|
| items[].{product_id,offer_id,price{price,old_price,min_price}} | price/old_price/min_price | ozon_products_cache 三价列 | ✅ 探针验证 |
| items[].price_indexes / commissions | raw | raw | ✅ 探针验证 |

验证点:price 对象结构;cursor 分页;与 info.list 三价字段互验。

## 冻结标准

探针输出 `docs/ozon-probe/*.json` 与本表 100% 对齐(字段名/类型/枚举),差异清零后本表标记「已冻结」,实现期以冻结版为准。

# ENVELOPE-STANDARD — 信封与数据边界标准(v1 草案)

> 日期:2026-08-06 ｜ 状态:🟡 草案待评审 ｜ 目的:理清 skill→agent→worker 三方数据边界,
> 终结「信封字段混装、worker 不知道用、agent 看不到结果」的问题。

## 一、三方数据流(谁产出、谁消费)

```
┌─ skill 采集 ─────────────┐   ┌─ agent 判定 ─────────┐   ┌─ worker 执行 ──────────┐
│ 1688/Ozon 抓取           │   │ 看 DiscoveredProduct │   │ 消费信封(入参标准)      │
│ → 组装「判定数据」🅰       │──▶│ 运营/利润/类目/货源   │──▶│ 类目→定价→属性→生图→上传 │
│ → 判定通过 → 组装「信封」🅱 │   │ 判断:值得上?        │   │ → 产出「出参」🅳         │
└──────────────────────────┘   └─────────────────────┘   └──────────┬──────────────┘
                                                                     ▼
                                                          agent 看结果(product_summary)
```

**边界铁律**:
- 🅰 判定数据(**agent 看、skill 判**)→ 留在 skill 侧,**不进信封**
- 🅱 信封(**worker 执行**)→ 只装 worker 必须消费的字段,**worker 必须全部消费**
- 🅳 出参(worker 结果)→ 标准结构返回,agent/skill 据此决策后续
- 三个集合**互不混装**;契约文档(schemas.py/CONTRACT)必须与实现同步

## 二、🅰 Agent 判定层标准(DiscoveredProduct,ozon_discovery.py)

skill 运行(discover/follow)后给 agent 看的分析数据,agent 据此判断「要不要上架/跟卖」:

| 字段 | 类型 | 用途 | 来源 |
|---|---|---|---|
| ozon_product_id / ozon_title / ozon_price / ozon_url / ozon_images | str/list | 竞品基本信息 | Ozon 页 |
| competing_sellers / min_competing_price | int/float | 竞争度(跟卖数/最低价) | widget |
| match_1688_url/title/price/images | str/float | 1688 货源匹配 | 图搜 |
| estimated_logistics_cny / commission / profit_cny / profit_margin | float | 利润分析 | 计算 |
| commission_fbp / commission_rfbs | float | 佣金率 | seller API |
| monthly_sales / monthly_revenue | int/float | 月销/月销额 | what_to_sell |
| sales_growth(drr) / create_days / has_analytics | float/int/bool | 增长/上架天数 | what_to_sell |
| rating / review_count | float/int | 口碑 | widget |
| weight_g / dimensions_mm | int/dict | 物理规格(定价用) | what_to_sell |
| category / brand | str | 竞品类目名/品牌 | 页面/后台 |
| blue_ocean_score | int | 蓝海评分 | 计算 |

**现状**:✅ 字段齐(DiscoveredProduct 已有);❌ 但其中 10 个运营字段被**透传进信封**污染边界(见四;competitor_weight_g/competitor_dimensions_mm 是 worker 兜底用,必须留)。

## 三、🅱 Worker 入参标准(信封 schema)

`GraphInput = {token, ozon_client_id, ozon_api_key, envelope}`
`envelope = {draft, source, extensions}` — **worker 执行契约,所有字段 worker 必须消费**

### draft(产品数据,全部必填或强约束)

| 字段 | 类型 | 必填 | 语义(标准定义) |
|---|---|---|---|
| item_id | str | ✅ | 1688 item id → offer_id |
| title | str | ✅ | 1688 中文标题(直采)/竞品俄语标题(跟卖) |
| description | str | 可选 | 1688 中文描述,worker 翻译 |
| images | list[str] | ✅ | 1688 图 URL(生图参考) |
| weight | int | ✅ | 克(g) |
| dimensions | {l,w,h} | ✅ | mm |
| purchase_cost | float | ✅ | CNY,含 1688 国内运费 |
| purchase_url | str | ✅ | 1688 链接 |
| attributes | dict | 可选 | 1688 中文属性 kv |
| ozon_attributes | dict | 可选 | 竞品俄语属性 kv(跟卖优先) |
| supplier | str | 可选 | 供应商名 → 制造商 23487 |
| **ozon_category** | dict | 可选 | **Seller 空间三级 type 节点 {description_category_id, type_id}**(见五) |
| **source_category** | str | 直采✅ | 1688 中文类目路径(匹配兜底) |
| source_category_id | int | 可选 | 1688 类目 ID(实测常 null) |
| competitor_weight_g / competitor_dimensions_mm | int/dict | 可选 | 竞品兜底物理规格 |
| dimensions_estimated | bool | 可选 | 尺寸为估算标记 |
| competitor_price | float | 可选 | 竞品售价(仅审计参考,不参与定价) |
| variants | list | 预留 | 多 SKU(当前 skill 恒折叠,空) |

**删除(draft)**:stock、shipping(可选移出,cli.py:204 展示用)、max_skus/dropped_skus/drop_reason/filtered_skus — 写了没人用。
**标记废弃(draft)**:price / original_price(单SKU平铺)— worker 不消费(assemble 只读 variant 层),但 skill 折叠计算内部用,保留无害。

**删除(extensions)**:10 个竞品运营字段(competitor_sellers / competitor_min_price / ozon_rating / ozon_reviews / ozon_questions / ozon_seller / ozon_listing_date / ozon_monthly_sales / ozon_gmv / ozon_listing_days)— **属 🅰 判定层,移出信封**;skill 采集/展示逻辑保留。
**⚠️ 必须保留(extensions)**:competitor_weight_g / competitor_dimensions_mm — worker `assemble` 兜底消费(1688 物理数据缺失时),非无用字段。

### source(采购源)

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| purchase_url / purchase_cost | str/float | ✅ | 与 draft 冗余,兜底用 |
| source_category_path | str | 可选 | 1688 全路径(类目匹配用) |

### extensions(流程/定价参数)

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| follow_sell | bool | 可选 | 跟卖路由 |
| follow_type | str(hand/api) | 可选 | 跟卖模式 |
| margin_rate / commission_rate / fx_buffer | float | 可选 | 定价参数(默认 0.25/0.10/0.05) |
| max_skus / dropped_skus / drop_reason / filtered_skus | — | ❌ | **删除**,worker 不消费 |

**删除(extensions)**:10 个竞品运营字段(competitor_sellers / competitor_min_price / ozon_rating / ozon_reviews / ozon_questions / ozon_seller / ozon_listing_date / ozon_monthly_sales / ozon_gmv / ozon_listing_days)— **属 🅰 判定层,移出信封**(skill 采集/展示保留)。
**保留(extensions)**:competitor_weight_g / competitor_dimensions_mm(worker 兜底消费)、competitor_price(审计参考)。

## 四、🅳 Worker 出参标准

### 任务级(task_status / GraphOutput)

| 字段 | 说明 |
|---|---|
| task_id / status / progress(stage/percent/stages[]) | 任务状态与进度 |
| product_id | Ozon 商品 ID |
| error_code / error_message / validation_errors | 失败原因(可行动) |

### 产品明细(product_summary[],v0.22)

每条产品(变体各一条):

| 字段 | 来源 |
|---|---|
| purchase_url / purchase_cost | 信封 draft |
| margin_rate / price / logistics_cost / profit_rate | pricing_info |
| product_id | 终态 |
| sku_id / old_price(变体) | variant_prices |

**缺口**:出参无「上架结果判定」(approved/pending/declined + 原因)——agent 看完任务结束只知道成功/失败,不知道审核状态。**建议**:出参补 `ozon_status`(approved/pending/declined)+ `ozon_error`(declined 原因),由 `ozon_status_node` 终态写入。

## 五、类目语义统一(本版核心修复)

1. **Ozon 类目只有三级**:`category(depth0) > category(depth1) > type(depth2, 7424 个)`;面包屑第四段是品牌,不算类目。
2. **三级 type 节点 = 上架目标**,自带 `description_category_id` + `type_id`。
3. **`draft.ozon_category` 统一语义**:只接受 Seller 空间的三级 type 节点 `{description_category_id, type_id}`;skill 侧 Widget 面包屑 ID **不再写入**该字段(由 search-variant-model 三级类目名解析,或 search_categories 官方搜索)。
4. **匹配路径**(全部收敛到「三级类目名 → type 节点 node_name」):
   - 跟卖:竞品 8229 属性(三级类目名)→ 精确匹配 type 节点(方案 A)
   - 直采:1688 末级中文名 → search_categories(ZH_HANS)+ 同义词表 → type 节点(方案 B)
   - 兜底:全路径 pg_trgm,阈值 ≥0.6;不中不猜,交给 import-by-sku 官方复制
5. **学习表**:清洗现有污染(甩脂机品牌 ID / 手串错配);`success_count ≥ 3` 才固化,`is_active` 校验 dc/type 必须存在于树。

## 六、改造清单(按标准落地)

| # | 改动 | 涉及 | 风险 |
|---|---|---|---|
| 1 | 清洗 category_mapping 污染记录(prod + dev) | DB | 低 |
| 2 | 直采链路:打开 poll_category + assemble 消费 draft.ozon_category(方案 B) | skill+worker | 中 |
| 3 | 信封清理:删 draft.stock/shipping/price/original_price + extensions 11 运营字段 + max_skus 等 | skill | 中(需同步契约) |
| 4 | 跟卖链路:search-variant-model 三级类目名(方案 A) | skill+worker | 高(需真机) |
| 5 | 出参补 ozon_status/ozon_error | worker | 低 |
| 6 | 契约文档(schemas/CONTRACT)与实现同步 | docs | 低 |

## 七、待评审问题

1. 出参补 `ozon_status`/`ozon_error` 是否纳入?(建议纳入,agent 需要知道审核结果)
2. 信封清理(清单 3)是否一次性删字段,还是先标记废弃兼容一版?
3. 学习表固化门槛 success_count=3 是否合适?(当前 1 就固化)

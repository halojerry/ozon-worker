# PRD:类目错配根治 — 接入 seller 后台类目接口 + 直采消费类目

> 日期:2026-08-06 ｜ 状态:**🟡 方案讨论中(未动代码)** ｜ 目标版本:v0.27.0
> 前置调研:两个 subagent 深度调研毛子ERP 3.1.2 + 上品帮 3.1.99 插件,以及本仓库 skill/worker 类目链路(2026-08-06)

## 一、背景与目标

48 商品实测后 17 张 declined,类目错配比例过高(v0.21 实证 13/16 declined 为类目错配;v0.26 诊断产出 44 个错配清单)。用户观察:我们已从 Ozon 拿到竞品类目(面包屑)与运营数据(what_to_sell),为何仍错配?

**目标**:把类目来源从「页面面包屑(错空间 ID)+ 中文文本猜」切换为「seller 后台权威接口(类目名)」,预期大幅降低 declined 率。

## 二、根因链(调研实证)

### 2.1 插件怎么做(毛子/上品帮共识)

两个插件**都不从 www.ozon.ru 页面抓类目**,类目全部来自 **seller.ozon.ru 卖家后台专用接口**:

| 插件 | 接口 | 请求 | 类目字段 |
|---|---|---|---|
| 毛子ERP | `POST /api/site/seller-analytics/what_to_sell/data/v3`(按 SKU)或 `POST /api/v1/search-variant-model`(按名) | filter.sku / name | `category1Id/category2Id/category3Id` + `category1/2/3` 名(what_to_sell 自家视图);`categories[]` + 属性 **8229 三级类目名**(search-variant-model) |
| 上品帮 | `POST /api/v1/search-variant-model` | `{"limit":"10","name":<goodsId>}`,头 `x-o3-company-id` + `x-o3-app-name:"seller-ui"` + `x-o3-language` | `items[0].categories[]`(类目名数组)+ `attributes[8229]` 三级类目名;同响应 85/31 品牌、4497 重量、9454/9455/9456 尺寸 |

关键头:`x-o3-company-id`(cookie `sc_company_id`)、`x-o3-language:zh-Hans`(可能返回中文类目名);fetch 在**已登录的 seller tab 上下文**执行(毛子跨 tab 借道)或 background 带仿 seller UI 头(上品帮)。

插件内部**没有** ID 空间转换表——类目名/ID 直接透传自家后端,由后端匹配上架类目。

### 2.2 我们错在哪(本仓库实证)

| # | 断点 | 位置 |
|---|---|---|
| 1 | **抓的类目是 Widget 空间 ID**:面包屑 `/category/xxx-14500/` 小数字 ≠ Seller 空间 `description_category_id`(17028959 级) | `ozon_scraper.py:673` |
| 2 | **type_id 恒等于 description_category_id**(两维当一维) | `ozon_scraper.py:689` |
| 3 | **what_to_sell 竞品视图无类目字段**(真机 fixture 实证:item 只有 soldCount/gmvSum/drr/退货率 等) | `skill/tests/test_wave1_fixes.py:254` |
| 4 | **search-variant-model 从未接入**(grep 全仓库零结果)——插件类目的权威来源我们没有 | skill/worker 全仓 |
| 5 | worker 直查 Seller 树失败 → 面包屑/1688 中文文本 pg_trgm 猜 → 猜错 declined | `follow_sell_import_node.py:90, 358-399` |
| 6 | 直采路径整段丢弃 `draft.ozon_category`(`if extensions.get("follow_sell")` 才消费);且 `poll_category=False` 让 skill 的 search_categories(Seller 空间,正确)从不执行 | `assemble_ozon_product_node.py:420`、`cloud_probe.py:1885` |
| 7 | 学习表污染固化:错误映射写入 category_mapping,后续同款高置信命中错类目 | `learning_record_node.py` |

**一句话**:插件靠「seller 后台接口拿类目名 → 后端映射上架类目」;我们既没接该接口,又把错空间面包屑 ID 当类目、运营数据(无类目)当来源,最后中文文本猜。

## 三、关键事实(支撑方案选型)

- `category_tree_nodes` 表:**type 节点同时带 `description_category_id` + `type_id` + `node_name`**(`init_data.py:95-110`)→ 拿「三级类目名」匹配 type 节点 `node_name`(RU/ZH_HANS 双语),**一步拿到完整 dc + type 两维**。
- 类目名匹配比 ID 直查可靠:search-variant-model 返回的类目名是 **seller 后台同一棵树的名称**,与 category_tree_nodes 同源。
- what_to_sell 借道 seller Tab 的机制已就绪(`ozon_seller_analytics.py` v0.26,跨 tab 复用 + credentials + sc_company_id)→ search-variant-model 可复用同一通道。

## 四、方案 A(核心):接入 search-variant-model 拿竞品类目名

### 4.1 skill 侧改动

**新模块**:`skill/scripts/lib/ozon_seller_category.py`(或并入 ozon_seller_analytics.py)

1. 复用 `_tab_for_seller` / 借道 seller Tab 机制(与 what_to_sell 相同):
   ```
   POST https://seller.ozon.ru/api/v1/search-variant-model
   body: {"limit":"10","name":<竞品商品ID>}
   headers: Content-Type: application/json, x-o3-company-id:<sc_company_id>,
            x-o3-app-name:"seller-ui", x-o3-page-type:"products-other",
            x-o3-language:"RU"(俄语类目名,与 category_tree_nodes.RU 匹配)
   credentials: include
   ```
2. 解析:`items[0].categories[]`(全层级类目名数组)+ `attributes` 中 `key==8229` 的三级类目名;防御式提取 85/31 品牌、4497 重量、9454/9455/9456 尺寸(顺手补全竞品兜底数据)。
3. **真机验证前置**:登录 seller.ozon.ru 后对 1-2 个真实竞品 SKU 调通,固化 fixture(同 `59ef666` what_to_sell fixture 模式),确认 `categories[]`/8229 存在性与语言(x-o3-language RU vs zh-Hans)。

**信封透传**(`cloud_probe.py` follow_sell_cloud):
- `draft.ozon_category` 增加/改为携带**类目名**字段:`category_names`(俄语全路径数组)或直接写 `category_path`(俄语,`"Электроника > Аксессуары > Наушники"` 格式);
- 保留原面包屑字段做兜底兼容(向后兼容,worker 优先读新字段)。

### 4.2 worker 侧改动

**`follow_sell_import_node.py`** 新增优先路径(在现有 `_resolve_category_by_id` 之前):
1. 读 `draft.ozon_category.category_names`(俄语类目名数组);
2. 用**末级类目名**精确匹配 `category_tree_nodes`(language=RU)type 节点 `node_name`;不中则 full_path 前缀匹配 / ZH_HANS 名匹配 / pg_trgm 高阈值(≥0.6);
3. 命中 → 直接取该节点 `description_category_id` + `type_id`(**两维一步到位**,替代现在「ID 直查失败 → 中文猜」);
4. 未命中 → 保留现有降级链(面包屑 ID → 1688 中文 pg_trgm → import-by-sku 兜底)。

**`assemble_ozon_product_node.py` follow 分支**:同上优先消费类目名。

### 4.3 方案 A 风险

| 风险 | 缓解 |
|---|---|
| search-variant-model 需要 seller 登录态(premium?) | 与 what_to_sell 同通道已实证可查(59ef666);登录态缺失时降级现有链路 |
| x-o3-language 返回中文还是俄语不确定 | 真机验证两种头各调一次,选与 category_tree_nodes 匹配度高的 |
| 竞品商品 ID 查询返回 items 为空 | 尝试商品名/SKU 查询(毛子 variant 用 name);仍空 → 降级 |
| 类目名与节点名不完全一致(大小写/单复数) | node_name 归一化(小写/去空格)+ full_path 兜底 + pg_trgm 高阈值 |

## 五、方案 B(独立修复):直采路径消费 draft.ozon_category

- **`assemble_ozon_product_node.py:420`**:解除 `if extensions.get("follow_sell")` 守卫——直采信封若带 `draft.ozon_category`(skill 用 search_categories 解析的 Seller 空间 ID)同样优先消费(契约 A2 本就要求,实现只对 follow 生效)。
- **`cloud_probe.py:1885`**:`poll_category=False` → 直采默认打开(或至少 follow/discovery 打开),让 `search_categories`(/v1/description-category/tree, Seller 空间)真正执行。
- 风险:多一次 Seller API 调用;直采行为变化需回归(test_full_pipeline_mock_images + follow_sell_v5)。

## 六、方案 C(配套):type_id 补全

- 现状:`ozon_scraper.py:689` type_id = 面包屑数字(错误)。
- 修复:worker 消费类目名/ID 后,若 type_id 缺失或与 dc 同值,用类目名匹配 category_tree_nodes 取 type 节点真实 type_id(方案 A 命中即天然解决;方案 B 场景下用 dc 查该类目下 type 节点候选)。
- **依赖 A**(没有权威类目名,type 选择仍是猜)。

## 七、验收标准

1. **真机 fixture**:search-variant-model 对真实竞品返回 categories[]/8229,固化测试
2. **类目名匹配单测**:末级类目名 → dc+type 正确命中(用真实 44 错配清单里的产品名验证:此前猜错的,新链路应命中)
3. **离线校验器**:`offline_validate.py` 对帽类/错配样本 0 错误
4. **真实上架**:帽类 + 数字属性类 + 正常新品 3 条管线,不再 pending/declined
5. **回归**:pytest 全量 203+ 通过;直采/follow 双管线冒烟

## 八、实施顺序与依赖

```
R0(前置,需用户配合):真机验证 search-variant-model → 固化 fixture
  → A(skill 接口 + 信封) + C(worker 类目名→dc+type)
  → B(直采消费 + poll_category,可独立先行)
  → 全量回归 → 真实产品验证 → 部署
```

## 九、决策点(待讨论)

1. **x-o3-language**:search-variant-model 用 `RU`(俄语,直配 category_tree_nodes.RU)还是 `zh-Hans`(中文,可能返回中文类目名)?→ 建议 RU,真机两种都验证
2. **name 参数**:传竞品**商品 ID**(上品帮)还是 **SKU/商品名**(毛子 variant)?→ 建议先商品 ID,失败换 SKU
3. **信封字段**:新增 `category_names` 数组字段(显式)vs 复用 `category_path`(隐式)?→ 建议新增,契约显式化
4. **B 的 poll_category**:直采默认打开还是仅 follow/discovery 打开?→ 建议默认打开,回归验证
5. **学习表**:类目名链路命中后,学习表写回逻辑是否保留(仍写 dc+type,防漂移)?→ 建议保留

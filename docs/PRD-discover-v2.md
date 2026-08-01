# PRD — Discover v2：先全量采集 → 表格分析 → 挑完再找货源

> 版本: v1.0 · 日期: 2026-08-01 · 分支: `feat/discover-v2`
> 关联审计: `docs/AUDIT-2026-08-01-逻辑审计.md`（P0-1 图搜、P0-5 discover 凭证、P2-9 连接复用）

---

## 1. 背景与问题

当前 `discover` 选品管线（`skill/scripts/lib/ozon_discovery.py`）存在四个结构性缺陷：

1. **采集对象不可信**：`_scroll_and_collect_urls` 用全页 `a[href*="/product/"]` 选择器（`ozon_discovery.py:417`），混入推荐/广告/相关商品区块；关键词拼在 highlight 页 `?text=`（`:135-138`）而非搜索页 `/search/?text=`；代码里已写好的正规实现 `discover_from_keyword`（`:321-329`）从未被调用。→ **"搜宠物用品抓到别的产品"的根因**。
2. **懒加载判定脆弱**：直接跳底部 + 固定 2.5s 等待 + 采集先于滚动（`:414-432`），慢网络下提前判"抓完"，无"等新卡片渲染"的循环，无翻页。
3. **边采集边 1688**：第一个产品就开始识图（`:197`），默认 50 个产品 = 50 次 1688 调用，全部浪费在可能错误的采集集合上；rejected/error 产品不展示（`:259-265`），用户看不到全量数据。
4. **蓝海评分近半权重空转**：`monthly_sales`(20%)、`commission`(10%) 字段全链路从未赋值（恒 0 恒加固定分）；销售增长率/广告占比/上架日期字段根本不存在。

另外两个依赖问题必须一并修复：
- **P0-1 图搜 awaitPromise 缺失**（`ozon_image_search.py:48-117` 顶层 await 但 `tab.evaluate` 未传 `await_promise=True`）→ 图搜恒失败，阶段④批量识图依赖此修复。
- **P0-5 discover 凭证链路断裂**（`cli.py:758` 引用不存在的 `load_store_config`；`build_envelope_from_discovery` 丢弃已解析凭证改用空值）。

## 2. 调研结论（决定数据来源架构）

参考 `上品帮插件-V3.1.94` 与 `maozi-plugin-3.1.0` 两个 Chrome 插件：

| 能力 | 上品帮 | 毛子ERP |
|------|--------|---------|
| 懒加载 | 平滑滚动(剩2/3) → 等2s → 重查 `#contentScrollPaginator .tile-root` → 无新卡重试≥15 → 翻页 | 无自动滚动，MutationObserver 监听新增 `.tile-root` |
| 商品数据 | DOM 卡片 + entrypoint API `/api/entrypoint-api.bx/page/json/v2` | 同左（与我们 `ozon_widget.py` 同源） |
| 跟卖数 | `/modal/otherOffersFromSellers` → sellers[] | 同左 |
| **月销量/增长率/广告占比/上架日期** | **Ozon 公开页没有**，来自云端爬 seller.ozon.ru 后台 | **跨 Tab 借道** seller.ozon.ru `what_to_sell/data/v3`（带 `x-o3-company-id` cookie + `x-o3-language: zh-Hans`） |

**关键结论**：运营指标只能从 seller.ozon.ru 卖家后台分析接口获取（用户已确认采用"借道"方案，我们有 CDP 基础设施，比毛子更好做——浏览器已登录卖家后台，保留登录态）。

## 3. 目标

1. 采集只收**搜索结果容器**内的产品，支持懒加载等待与翻页，搜什么得什么。
2. **先全量采集并展示**所有产品（含拒绝原因），人工/规则挑选后再批量找货源 → 1688 配额只花在值得的产品上。
3. 蓝海评分 100% 权重有真实数据支撑（月销量/佣金/增长率/广告占比/上架天数）。
4. 不破坏现有 `graph`/`follow`/`batch_test` 管线与 Worker 契约。

## 4. 范围

### 4.1 包含
| 模块 | 改动 |
|------|------|
| `skill/scripts/lib/ozon_image_search.py` | P0-1：async IIFE + `await_promise=True`；空结果不写缓存 |
| `skill/scripts/lib/ozon_seller_analytics.py` | **新建**：seller.ozon.ru 借道运营指标客户端 |
| `skill/scripts/lib/ozon_discovery.py` | 重构：新采集器、全量数据、挑选流程、批量货源、评分修正 |
| `skill/scripts/lib/ozon_widget.py` | 补评分/评论数提取（webReviews/rating） |
| `skill/scripts/cli.py` | `cmd_discover` 重写 + 新参数 |
| `skill/scripts/cloud_probe.py` | `build_envelope_from_discovery` 凭证透传 + 降级定价走 store profile |
| `skill/SKILL.md` / `AGENTS.md` | discover 命令文档更新 |
| `docs/PRD-discover-v2.md` | 本文件 |

### 4.2 不包含（后续迭代）
- Worker 侧审计问题（另立 PRD）
- 云端采集服务（上品帮模式，当前规模不划算）
- 1688 标题翻译匹配（两个插件也都是识图，不做标题匹配）

## 5. 方案设计

### 5.1 新流程（四阶段）

```
阶段① 采集          阶段② 全量数据        阶段③ 表格分析挑选      阶段④ 批量货源+提交
──────────────────────────────────────────────────────────────────────────────
搜索页/指定URL        每商品 widget API     全量表格(指标+拒绝原因)   只对选中 N 个
  ├ 容器限定            ├ 价格/标题/图/品牌   ├ 人工按序号挑选          ├ 批量 1688 识图
  ├ 逐屏滚动等渲染       ├ 评分/评论数/跟卖数  ├ 或 --rules 自动筛选      ├ 真实重量/佣金算利润
  ├ 翻页 + 去重          └ 跟卖最低价          ⏸ 此刻不花 1688 配额       └ 确认 → 提交 Worker
  └ 运营指标(借道)
```

### 5.2 阶段① 采集修正（`ozon_discovery.py` 新函数 `_lazy_collect_urls`）

- **URL 构造**：`--keyword` → `https://www.ozon.ru/search/?text=<kw>`；`--url` 直接使用（修复死代码）；`discover_from_highlight` 保留为兼容壳。
- **选择器限定**（JS）：`#contentScrollPaginator .tile-root a[href*="/product/"]`，兜底 `#paginatorContent` / `[data-widget="skuGrid"]`；按 product_id 去重（`-\d{5,}/?$` 或 `/product/\d{5,}/?$`）。
- **滚动循环**：`window.scrollBy(0, innerHeight * 0.85)` 逐屏 → 轮询 `.tile-root` 数量增长（每 0.5s × 最多 20 次 = 10s/屏）→ 连续 3 屏无新卡 → 尝试翻页（点击 `#paginator a[href*="page="]`）→ 仍无 → 结束。
- 新增 `--max-products` 提前停止。

### 5.3 阶段② 全量数据 + 运营指标

**候选模型扩展**（`ProductCandidate` 新增字段）：

```python
rating: float = 0.0          # 评分
review_count: int = 0        # 评论数
sales_growth: float = 0.0    # 月销售动态 %（salesDynamics）
drr: float = 0.0             # 广告费占比 %
create_days: int = 0         # 上架天数
has_analytics: bool = False  # 是否拿到后台运营数据
ozon_category: dict = field(default_factory=dict)  # 面包屑/类目（供提交）
```

**widget API 数据**：`fetch_product_info`（价格/标题/图/品牌）+ `fetch_competing_sellers`（跟卖数/最低价）复用现有（已支持 `cdp=` 连接复用）；新增评分/评论数提取（webReviews widget）。全部候选先落盘 `data/discovery/`（含 rejected/error，现有 `_save_discovery_log` 已支持）。

**运营指标（新模块 `ozon_seller_analytics.py`）**：

```
fetch_sales_analytics(cdp, skus, batch_delay=1.0) -> {sku: metrics}
  1. CDP 打开 seller.ozon.ru（用户已登录，保留登录态）
  2. 读 document.cookie 中 sc_company_id；无 → 降级返回 {}
  3. 页面内 fetch POST /api/site/seller-analytics/what_to_sell/data/v3
     body: {limit:"50", offset:"0",
            filter:{stock:"any_stock", period:"monthly", categories:[], sku:<SKU>},
            sort:{key:"sum_gmv_desc"}}
     headers: x-o3-company-id / x-o3-language: zh-Hans
  4. 提取: soldCount→monthly_sales, gmvSum→monthly_revenue,
           salesDynamics→sales_growth, drr→drr, createDays/upTimeDays→create_days,
           attributes[4497]→weight_g, attributes[9454/9455/9456]→dimensions,
           rfbsRate/fbpRate→commission（防御式字段提取）
  5. 每批间隔 batch_delay 防反爬；tab 在 finally 关闭
```

**降级策略**：任何一步失败 → `{}` → 表格该产品运营列标注"—"，蓝海评分 monthly_sales 走 unknown 分支（现有逻辑 `:631`），**不阻断流程**。

### 5.4 阶段③ 表格分析与挑选（`cli.py cmd_discover` 重写）

- 打印全量表格（含序号/状态）：
  `序号 | 标题 | 价格₽ | 月销量 | 增长率% | 广告占比% | 跟卖数 | 上架天数 | 评分 | 蓝海分 | 状态`
- **人工挑选**：`input()` 输入序号（`1,3,5-8` / `all` / 回车=全选 profitable）。
- **规则自动筛选**：`--rules "monthly_sales>=200,drr<=30,seller_count<=20,margin>=15"`，支持字段：`monthly_sales / gmv / drr / seller_count / margin / price / create_days / sales_growth / rating` 与比较符 `>=/<=/>/</=`
- 挑选结果写入 `selection.json` 缓存；CSV 导出全量（非仅 profitable）。

### 5.5 阶段④ 批量货源 + 提交

- 只对选中产品调 `_search_1688_source`（CDP 图搜 → AK 图搜 → AK 关键词三级，现有逻辑；依赖 P0-1 修复）。
- `_calculate_profit` 增强：`weight_g > 0` 时物流按 `weight_g/1000 × 40 CNY/kg`（保底 8 CNY）估算，否则用默认；佣金用真实 `commission_fbp/rfbs`（analytics 或 store profile）优先，`DEFAULT_COMMISSION_PCT` 兜底。
- 蓝海评分：`monthly_sales`/`commission` 真实赋值，权重全部生效。
- `build_envelope_from_discovery`（`cloud_probe.py`）：主路径优先透传 `build_graph_envelope_with_retry` 返回的 `ozon_client_id/ozon_api_key`（P0-5），缺失才用 store_config；降级分支定价参数改走 `get_store_profile(store_id)`（不再硬编码 0.25）。
- `--auto-submit`：确认后逐产品提交（复用现有逻辑，删 `load_store_config` 引用）。

### 5.6 CLI 新参数

```
discover
  --url <URL>           直接采集指定页面（修复：现在为死代码）
  --keyword <kw>        搜索关键词 → /search/?text=
  --max-products N      最多产品数（默认 50）
  --min-margin %        最低利润率（默认 15）
  --max-sellers N       最大跟卖人数（默认 10）
  --fx-rate             汇率（默认 0.075）
  --store <name>        店铺名（定价参数/凭证来源）
  --analytics / --no-analytics   运营指标强制开/关（默认自动尝试）
  --rules "a>=1,b<=2"   规则自动筛选（跳过交互）
  --select              交互挑选（默认开启）
  --export csv|json|both  --output <path>
  --auto-submit         挑选+货源后确认提交 Worker
```

## 6. 验收标准

1. `python3.12 -m py_compile` 全部改动文件通过；`python3.12 -c "import ..."` 导入通过。
2. `discover --keyword "宠物用品" --max-products 30 --no-analytics`：采集结果 90%+ 标题含"宠物"相关词（修复采集污染）；打印全量表格；挑选后仅选中产品触发 1688 识图（日志可数）。
3. 登录 seller.ozon.ru 后 `--analytics`：表格月销量/增长率/广告占比/上架天数有值；未登录：降级为 "—" 不报错。
4. `discover --url <搜索页URL>` 与 `--keyword` 行为一致（--url 修复）。
5. `--rules` 自动筛选正确过滤；`--export csv` 导出含全部新字段。
6. 回归：`graph`/`follow`/`image_search` 命令不受影响（图搜改动后 `image_search --image <URL>` 能出结果）。

## 7. 风险与对策

| 风险 | 对策 |
|------|------|
| `what_to_sell` 接口随 Ozon 改版 | 隔离在单模块，防御式字段提取，失败即降级 |
| seller.ozon.ru 登录态丢失/未登录 | 自动降级公开指标，表格标注，不阻断 |
| 1688 图搜配额（批量识图） | 只对选中产品 + 并发 ≤3 + 6h 缓存 |
| 采集慢（逐屏等待） | 每屏等待上限 10s；`--max-products` 控制总量 |
| 交互模式在非 TTY 下 | `--rules` 提供非交互路径 |

## 8. 实施顺序

1. P0-1 图搜修复（独立、可立即验证）
2. `ozon_seller_analytics.py` 新模块
3. `ozon_discovery.py` 重构（采集 → 全量 → 挑选 → 批量）
4. `ozon_widget.py` 评分/评论数
5. `cli.py` 重写 + `cloud_probe.py` 修复
6. 测试 + 文档更新
7. 提交 `feat/discover-v2` → 用户实测 → 合入 dev

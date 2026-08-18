# 上品帮客户端 → ozon-worker skill 学习笔记

> **配套**: `docs/competitor/shangpinbang-full.md`（Web 端 ERP UI 字段清单）、`shopbang/上品帮客户端逻辑与清单.md`、`shopbang/上品帮选品筛选SOP与源码对照.md`
> **范围**: 只看 skill 侧。聚焦四个目标 —— 给 worker 的数据更完整 / 上架成功率更高 / 货源匹配更快 / 选品更丝滑
> **调研方式**: 全 35 个 dist-electron js + utils + preload + IPC + memory-monitor/update/account 服务 + 与本项目 skill 源码逐行对照
> **整理日期**: 2026-08-17
> **本文档定位**: 后续开发的查询底稿。所有事实来自源码原文，纠正过的错误认知也保留（避免重蹈）。

---

## 0. 三处曾误判，先纠正

| # | 曾误判 | 真相 | 源码依据 |
|---|---|---|---|
| 0.1 | "上品帮用独立 session 隔离采集任务" | **新版默认 session 共享**。每 8 个并行 BrowserWindow **共享 Electron 默认 session** → 登录一次所有窗口通用 | `services/collection/main-window.services.js:13-58`（无 `session:` 参数）; 旧版 `services/collection.services.js:143` 是死代码（`persist:collect-{ts}`） |
| 0.2 | "8 个并发是单任务内 1688 图搜" | **8 是跨任务并发**（TaskManager `maxConcurrentTasks`）。单任务内仍只有 3 个 1688 图搜窗口（TaskQueueService `maxConcurrency=3`） | `task-manager.services.js:19` + `task-queue.services.js:6,11` |
| 0.3 | "我们多进程并行 CDP 没 bug" | **多 Python 进程同时连 9222 不安全**：`CdpConnection._tabs` 是 in-process list，跨进程无锁；多进程各自拿 `http://127.0.0.1:9222/json` 列 tab 同一组，并发 evaluate 会 WS 归属串错 | `cdp_client.py:267`（`self._tabs: list[CdpTab] = []`），跨进程无锁；多 CLI 并行只能靠一个进程内多线程 |

---

## 1. 整体架构对照（仅 skill 相关）

### 1.1 工艺层对比

| 层 | 上品帮 | ozon-worker 当前 |
|---|---|---|
| **登录态** | Electron 默认 session 共享（同 Chromium cookie jar） | 1 个独立 Chrome 实例 + 1 个独立 profile 目录（`data/browser/profiles/1688/default`）→ 同 Chrome 所有 CdpConnection 共享 |
| **跨任务并行** | **TaskManager 单例，4-8 个跨采集任务并行** | 不具备。每个 CLI 命令 1 Python 进程，多进程同时跑不安全 |
| **单任务内并行** | TaskQueueService 3 个 1688 图搜 BrowserWindow 并发 | `ThreadPoolExecutor(max_workers=4)`，每线程独立 CdpConnection + 独立 tab，**已经做对** |
| **跨任务隔离** | 每任务一个独立 BrowserWindow（独立渲染进程，崩溃互不影响） | 都在同一 Chrome 进程的 tab，一 tab 崩可能整 Chrome 崩 |
| **运行时调并发** | `setMaxConcurrentTasks(count)` IPC 可运行时改 | 启动时读 `MAX_CONCURRENT` env，运行中不能调 |

### 1.2 选品 SOP（六阶段漏斗）对照

```
① 数据入口 → ② 数据补全 → ③ BASE粗筛(18) → ④ 1688匹配 → ⑤ DETAIL精筛+利润 → ⑥ 入库/导出
```

| 阶段 | 上品帮源码 | ozon-worker 当前 | 差距 |
|---|---|---|---|
| ① 列表抓取 | `ozonListParser` 一屏抽全字段（price/oPrice/name/rating/reviewCount/cover/id）+ 同函数内 fetch `webSellerList-4723017` widget 拿 sellerNumber + guessMinPrice | `discover_from_url` 只取 href pid 列表 | **真正漏斗倒着**：我们粗筛前就该抽全字段 |
| ② 批量补全 | `POST /api/goods/hotSales/getOzonSaleDataByIds` 一次吃 50 条拿 25 字段 | `fetch_bestseller_metrics_map` 批量 map + `fetch_sales_analytics` 单 SKU fallback | 字段全等，路径差异（他们后端批量，我们实时分批） |
| ③ BASE 粗筛 | 18 项区间筛（`filterData 'base'`）+ 5 项 AI 阶梯门槛（`aiFilterData`） | 3 项主筛（品牌/关键词/价格）+ 用户 `apply_selection_rules` 9 字段 | **_SELECTION_FIELDS 只接 9 个**，ozon_seller_analytics 27 字段中**仍有 13 个未接入粗筛**（详见 §2） |
| ④ 1688 匹配 | BrowserWindow + DataTransfer 伪造文件上传 + 真实点排序 + 2min 超时，**无护栏**（取第一个非广告结果 = 错配雷） | aibuy mtop 直调（免浏览器秒级）→ CDP → AK → AK keyword，`_pick_best_match` 多信号护栏（图搜位置+badge+词对+LLM 兜底） | **我们更优**；aibuy 通道结构性胜出 |
| ⑤ DETAIL 精筛 | 4 项（price/sellerNumber 重验/reviewCount/rating）+ 后端 `goodsFilter/goodsFilter2` 利润核算 | `calculate_blue_ocean_score` 10 因子 + 27 字段 `_extract_metrics` | **我们评分层更精细**；缺的是跟卖数重诊 + 跟卖最低价低于当前价才作 "可低价抢" 信号 |
| ⑥ 交付物 | 65 列 xlsx + 主图/货源图嵌入 + tmp 文件原子替换 + EBUSY/EPERM 占用检测 | CSV + JSON + Markdown 三件分文件，纯文本无图无样式 | Excel 落盘是 P0 改进点（详见 §4） |

---

## 2. 字段全集差异盘点（ozon_seller_analytics 27 字段 vs 粗筛接入）

### 2.1 ProductCandidate 已有字段

`ozon_seller_analytics._extract_metrics` 抓全了 27 字段：
- 月销量/月销售额/月销售动态/上架天数/佣金分段/促销天数/折扣/促销转化/付费推广天数/搜索浏览/卡片浏览/搜索加购率/卡片加购率/成交率/退货取消率/重量/包装长宽高/广告份额 DRR/平均价/错失销售/可用性/销量增长 ...

### 2.2 粗筛接入层（`_SELECTION_FIELDS`，`ozon_discovery.py:701-711`）只 9 个

**已接入**：`monthly_sales / gmv / drr / seller_count / margin / price / create_days / sales_growth / rating`

**未接入的 13 个粗筛字段**（零成本可加）：

| 字段 | ProductCandidate 属性 | 上品帮对应配置 | 价值 |
|---|---|---|---|
| `sales_dynamics` | `candidate.sales_dynamics` | `monthDynamicsMin/Max` | 月销动态（趋势信号） |
| `days_in_promo` | `candidate.days_in_promo` | `promotionDayMin/Max` | 促销依赖度 |
| `discount` | `candidate.discount` | `promotionDiscountMin/Max` | 折扣力度 |
| `promo_revenue_share` | `candidate.promo_revenue_share` | `promotionDynamicsMin/Max` | 促销转化率 |
| `days_with_trafarets` | `candidate.days_with_trafarets` | `promoteDayMin/Max` | 付费推广 |
| `session_count` | `candidate.session_count` | `viewsMin/Max` | 浏览量 |
| `conv_to_cart_pdp` | `candidate.conv_to_cart_pdp` | `cardRateMin/Max` | 卡片加购率 |
| `conv_to_cart_search` | `candidate.conv_to_cart_search` | `showRateMin/Max` | 搜索加购率 |
| `nullable_redemption_rate` | `candidate.nullable_redemption_rate` | `nullableRedemptionRateMin/Max` | 成交率 |
| `weight_g` | `candidate.weight_g` | `weightRangeMin/Max` | 重量 |
| `dimensions` | `candidate.dimensions_mm` | `packageLength/Width/HeightMin/Max` | 包装长宽高 |
| `return_cancel_rate` | `candidate.return_cancel_rate` | `returnCancelRateMin/Max` | 退货取消率 |
| `review_count` | `candidate.review_count` | `numberOfCommentsMin/Max` | 评论数 |

### 2.3 AI 阶梯门槛（上品帮精华，我们可学）

`data-filter.services.js:110-153` 5 条硬淘汰：

1. 上架 ≤365 天
2. 跟卖 ≤30 人
3. 月销售动态 > 0
4. 广告份额 ≤ 15%
5. **销量阶梯门槛**（核心）：
   - 价 ≤ 500 ₽ → 月销 > 500
   - 价 ≤ 1000 ₽ → 月销 > 150
   - 价 ≤ 5000 ₽ → 月销 > 30
   - 价 ≤ 10000 ₽ → 月销 > 15
   - 其他 → 月销 > 5

**产品哲学**：越便宜利润薄必须走量；越贵单件利润高、放宽销量。可做成 `discover --rules ai` 一键默认门槛。

### 2.4 checkRange 语义（用户友好性差异）

`data-filter.services.js:335-348`：

```js
checkRange(value, min, max) {
  if ((min === undefined && max === undefined) || (min === null && max === null))
    return true;                  // 0/空 = 不限
  ...
  if (min !== undefined && min !== null && num < min) return false;
  if (max !== undefined && max !== null && num > max) return false;
  return true;
}
```

**0/None = 不限**。

我们 `apply_selection_rules` 的 `_check_rule` 是**硬比较**——`monthly_sales>=0` 也是条件，不像上品帮可以"留空 = 不限"。差异是用户友好度。改造可加默认值处理：某规则缺省时 skip 该规则。

---

## 3. graph 信封字段完整性差异（worker 上架成功率根因）

### 3.1 graph vs follow 信封字段对比

| worker 消费端 | graph 路径 | follow 路径 | 影响 |
|---|---|---|---|
| `weight_dimension_normalizer` 兜底链 C2 (consumer.competitor_weight_g) | ❌ 不上 | ✅ | 1688 缺重量时硬编 100g → 物流费失真 |
| 同上 (competitor_dimensions_mm) | ❌ 不上 | ✅ | 1688 缺尺寸时硬编 300×200×50mm |
| `apply_competitor_fallback` (consumer.ozon_attributes 俄语属性) | ❌ 不上 | ✅ | LLM 猜属性 → "属性值不正确" DESC_DECLINE |
| `pricing_node` 跟卖最低价预警 (consumer.follow_min_price) | ❌ 不上 | ✅ | 无低价抢跟卖预警 |
| `pricing_node` 定价参考 (consumer.competitor_price) | 仅 draft 间接 | ✅ | 定价锚不够全 |
| `category_match_node` 类目搜索路径 (source_category_path) | ✅ v0.39 | ✅ | 已对齐 |
| 蓝海评分因子 (seller_number) | ❌ 不上 | ✅ | 蓝海评分缺一项 |

**核心改进点**：`cloud_probe.build_graph_envelope` 加 Ozon 反查同款段 → 把上表 5 项缺失全部塞进 `extensions.competitor`。worker 端消费链**都已就绪**，无需改 worker，只是当前信封不喂数据。

### 3.2 跟卖最低价 fetch 的细节（上品帮比我们多一步过滤）

`parse.services.js:95`:
```js
const priceList = sellers?.sellers?.map((item)=>
  +item?.price?.cardPrice?.price?.replace(/[^\d.]/g,''))
  .filter((subItem)=> subItem && subItem < item.price);   // ⚠️ 只取比当前价低的
item.followMinPrice = priceList?.length ? Math.min(...priceList) : item.price;
```

我们 `fetch_competing_sellers` 返回的 `min_price` 是**所有 sellers 的最低价**，不论是否低于当前价。语义差异：

- **上品帮**：`followMinPrice < item.price` 才是有信息量的信号（"有没有人比我价低"）
- **我们**:`min_competing_price` 包含自己（实际上 widget 不含自己，但包含所有竞品最低）

改 `fetch_competing_sellers` 输出多加一个 `min_price_below_current` 字段，按上品帮规则计算，只标"低于当前价"的最低跟卖价作蓝海信号。

### 3.3 跟卖数据双检机制（BASE 一次，DETAIL 一次）

`data-filter.services.js:285` filter 'detail' 第 2 项仍然校验 `sellerNumber`。设计思路是 base 阶段页面内 fetch，detail 阶段 widget 重 fetch 一次，**数据可能 1 小时内变化** → detail 阶段再验一次。

我们 `fetch_competing_sellers` 有 6h 磁盘缓存（v0.36），所以 base/detail 两阶段 fetch 同源同缓存数据 → 双检没新信息。**不学这条**（时效性反而掉）。

---

## 4. 入选产品深加工（worker 上架成功率 10 字段支撑）

### 4.1 入选产品该准备的 10 个 worker 字段

| # | 字段 | worker 消费端 | skill 申请源 | 我们当前状态 |
|---|---|---|---|---|
| 1 | `draft.weight` | `pricing_node` 物流费 + `weight_dimension_normalizer` | 1688 CDP `packaging_rows[0].weightGrams` | ✅ |
| 2 | `draft.dimensions{L,W,H}` | 同 1 | 1688 CDP packaging_rows + dim_text_candidates + description 正则 | ✅ |
| 3 | `draft.ozon_category` | 类目匹配参与定价/佣金 | ozon_api.search_categories 末级词搜索 | ✅ |
| 4 | `draft.attributes`（中文） | worker LLM 翻译 + 字典匹配 | 1688 AK CPV/SKU + contextPath featureAttributes（v0.40）+ DOM 补 | ✅ |
| 5 | `extensions.competitor.competitor_weight_g` | 尺寸/重量缺失时**先于硬编码兜底** | Ozon 同款 fetch_competing_sellers | ❌ 改进 |
| 6 | `extensions.competitor.competitor_dimensions_mm` | 同 5 | 同 5 | ❌ 改进 |
| 7 | `extensions.competitor.ozon_attributes`（俄语） | worker 翻译前兼容对齐（减少 LLM 猜） | Ozon 同款 attributes widget | ❌ 改进 |
| 8 | `extensions.competitor.seller_number` | 蓝海评分 10 因子之一 + worker 端校验 | fetch_competing_sellers | ❌ 改进 |
| 9 | `extensions.competitor.follow_min_price` | pricing_node 跟卖预警 | fetch_competing_sellers 改 `min_price_below_current` 算法 | ❌ 改进 |
| 10 | `draft.purchase_cost`（含国内运费） | pricing_node 总成本 | 1688 freightCny + 采购价 → `_collapse_variants_to_single` | ✅ |

**5-9 全是 graph 不上的**。改进后 worker 在 graph 路径下上架成功率对齐 follow 路径。

### 4.2 ozon 反查同款（graph 信封补竞品数据）的实现思路

`build_graph_envelope` 在 CDP 富化 1688 数据后，补一段：

```python
# 用 1688 主图搜 Ozon 找已上架同款（类似 follow 用 Ozon 竞品图搜 1688 的反向）
if cdp:
    try:
        from scripts.lib.ozon_widget import fetch_competing_sellers, fetch_product_info
        from scripts.lib.ozon_api import search_categories
        # 方案 A：用 1688 主图反搜 Ozon（图搜最准，但需要新写 ozon_image 反搜能力）
        # 方案 B：用 1688 标题 + Ozon 类目查询找同款（语义匹配，覆盖更广但不一定是同款）
        # 推荐 A 先做，失败降级 B
        # 找到 best Ozon 产品后:
        # - fetch_competing_sellers 拿 sellerNumber + follow_min_price
        # - fetch_product_info 拿竞品重量/尺寸/俄语属性（characteristics 字段）
        # - 塞进 extension.competitor
    except Exception:
        pass  # fail-open，不影响主流程
```

### 4.3 skill 端密度校验（早发现 kg/g 单位错位）

worker `weight_dimension_normalizer` 已经做 `_parse_weight_g` 字符串带小数点判 kg→g（v0.37 A2/B2）。但 1688 数据图画时可能写 "0.5kg" 实际是 50g——我们不能等 worker 算完才发现。

可学：skill 端 build_graph_envelope 组装信封前跑一次密度校验（1.293 ≤ density kg/m³ ≤ 13546），异常 alert。无须改 worker。

---

## 5. 多 CDP 并行能力（"8 窗口并行"的真相）

### 5.1 上品帮真实机制

**两层并行 + 单 Electron 进程内管**：

- **第一层（跨任务并行）** `TaskManager.maxConcurrentTasks = 4 or 8`（内存 > 15GB 时 8）
  - 4-8 个**采集任务**同时跑（"猫玩具 + 宠物饮水机 + 化妆刷" 同时）
  - 每任务**一个独立 BrowserWindow**（独立渲染进程崩溃隔离）
  - 共享 Electron **默认 session**（一次登录所有窗口通用）

- **第二层（单任务内 1688 图搜并行）** `TaskQueueService.maxConcurrency = 3`
  - 单任务里粗筛后剩 ~10 候选，3 个**并发隐藏 BrowserWindow** 跑图搜
  - 单任务 120s 超时

**最坏情况**:8 跨任务 × 3 单任务内 = 24 个 BrowserWindow 同时活着（8 个可见 + 24 个隐藏 ≈ 32 个渲染进程）。15GB 内存阈值是合理的。

### 5.2 ozon-worker 当前能力

**单进程内单任务内**已经能 4-8 线程并行（`ThreadPoolExecutor(max_workers=4)`），每线程独立 CdpConnection + 独立 tab，跑 widget fetch + aibuy + seller analytics。

**不具备**：
- ❌ 单进程内**多任务**并行（一次 CLI 命令一个采集任务）
- ❌ 跨 CLI 进程并行（多 Python 进程同时连 9222 不安全：`CdpConnection._tabs` in-process list 跨进程无锁，多进程 `find_tab` 拿同一组 → WS 归属串错）
- ❌ 运行时调并发数（启动读一次 env）

### 5.3 学什么 —— D7'

**路径 A（推荐，1-2d）**: `discover-multi --keywords "A,B,C"` 多关键词单进程并行

- 1 Python 进程内 1 个 CdpConnection 管 1 个 Chrome
- 滚动阶段**串行**（同一 Chrome 多 tab 同时滚动反爬识别）→ N 关键词滚动加起来约 N × 单关键词滚动时长
- 分析阶段**已有的 ThreadPoolExecutor 4-8 线程并行**吃这 N 批合并 pid 列表
- 收益：N 关键词总时长 ≈ 滚动时长 × N + 分析时长 × 1（ML 分析约 1 倍）

**不学路径 B（TaskManager 单例守护进程，3-5d 重构）**：跨 CLI 子命令的 task orchestrator，与 webui 同事的 task 中心职责重叠。

### 5.4 单 Chrome 多 tab 并行 vs 上品帮多 BrowserWindow

| 维度 | 上品帮 BrowserWindow | 我们多 tab | 影响 |
|---|---|---|---|
| 隔离 | 独立渲染进程，崩不影响其他 | 同一 Chrome 进程，一 tab 崩可能整 Chrome 崩 | 我们弱 |
| 登录态 | 共享 Electron session ✅ | 共享同一 Chrome 同一 profile ✅ | 等价 |
| 资源 | 每窗口独立 ~500MB 内存 | 1 个 Chrome 多 tab，省内存 | 我们强 |
| 跑 8 路 | 8 个 BrowserWindow 用户能看见 | 1 个 Chrome 内 8 tab 用户也易理解 | 等价 |

我们换架构（启动 N 个独立 Chrome 子进程）💽 重，用户体验差，**不推荐**。多 tab 复用一个 Chrome 已经够用。

---

## 6. CDP fallback 路径反爬（与上品帮 BrowserWindow 实现对照）

### 6.1 上品帮 1688 图搜 BrowserWindow 反爬

`1688-window.services.js:1-203`:

- 每个图搜任务**新建隐藏 BrowserWindow** + `winId = randomUUID()`
- `preload: 1688preload.js` + `additionalArguments: ['--winId=...']`
- preload **每秒轮询** `location.href`，含 `tab=imageSearch` 或 `imageId` 时通过 `ipcMain.once('result-page-${winId}')` 通知主进程
- 主进程接到通知 → 等 5s → 注入 JS 点排序按钮（`.sortItem--X1Plgn6V`，价格/销量排序）→ 等 2s → 抓 `outerHTML` → cheerio 解析 `.offerListLayoutWrapper--qlAH8LJK` 第一个非广告结果
- DataTransfer 伪造文件上传：
  ```js
  fetch(cover) → blob → new File → DataTransfer.items.add → input.files = dataTransfer.files
  → dispatchEvent change + input → click [data-tracker='pasteImagePreview'] 或 .search-btn
  → 等 3s 跳转
  ```
- 单条 2 分钟超时

### 6.2 ozon-worker aibuy mtop 直调（结构性跳过反爬）

`ozon_image_search.py:531-687`:

```python
# 1. 从 Chrome 会话拿 cookie（_m_h5_tk 等 4 个）
token_cookies = _fetch_aibuy_cookies_from_chrome(cdp_url)
# 2. md5 签名直调 mtop imagesearch API（GET + JSONP）
sign = _mtop_sign(token, t, data_str)
resp = requests.get(MTOP_BASE_URL, params={sign, appKey, t, ...}, cookies=token_cookies)
# 3. 解析 JSONP 返回结构化结果（offerId/title/price/image/normalizationScore/badge）
```

**结构性胜出**：完全免浏览器，秒级，并发无上限。但需要 1688 Chrome 会话有 cookie，**冷启动 requests 拿不到 token**（必须从 Chrome 会话读）。

### 6.3 我们 CDP fallback 反爬学习点

`ozon_image_search.search_by_image_cdp` 的 CDP fallback 路径（Strategy 2）相比上品帮：

- ❌ 没有 preload 轮询结果页 → 用死等 outerHTML（当前是 `time.sleep` 多段等）
- ❌ 没有 "识别到 imageSearch 跳转就立刻抓" 早出窗口
- ❌ 滚动节奏 `window.scrollTo(0, scrollHeight)` 一跳到底（反爬信号）

可学：

- **S3 学习点**：CDP fallback 加 preload 风格的"识别到 result page 立即抓"早出窗口（30s → 8-10s）
- **S7 学习点**：3000ms 缓动滚动 + 80% 滚动量（上品帮 `scrollPage` 公式 `ease = progress < 0.5 ? 2*p*p : 1 - pow(-2*p+2, 2)/2`）

---

## 7. 反爬 + 稳定性细节

### 7.1 上品帮 stealth 反检测（旧版伪造全部）

`stealth.py` 已极简化我们 v0.28.7 反学过：

- 旧版把 `navigator.webdriver` 改 `undefined`，`hardwareConcurrency` 随机 4/8，`deviceMemory` 改 4，`plugins` 伪造 3 个，`chrome.runtime` 凭空造
- v0.28.7 实证："真实指纹天然干净"，改了反而留与硬件不符的检测信号 → 只保留 `navigator.webdriver=false` 兜底

**不学**上品帮全套 stealth。**学的是滚动节奏**（§6.3）。

### 7.2 稳定性其他

| 机制 | 上品帮 | 我们对应 |
|---|---|---|
| 崩溃重启 | 命令行 `restart-count=N` + 致命错误正则 + 上限 3 次 | Sentry 任务/超时自动上报 + worker 从 PG 队列恢复 |
| 内存监控 | 10min 周期 heap/rss/external + 阈值告警 + 手动 GC | skill 单次命令无内存监控；worker 长跑可加 |
| 网络探测 | 5s 超时 DNS 探测（qq/taobao/baidu） | 直接 requests 失败 |
| 退出清理 | `stopAllTasks` + 删 Excel 目录 + 2s 延迟落盘 | CLI 退出即进程消失 |

memory-monitor / DNS 探测等可作为 worker 长跑进生产性后续调优，不在 skill 核心范围内。

### 7.3 closeHandle 状态机（可学到 worker 域）

`collection.services.js:616-626`：外部注册 `closeHandle`，BrowserWindow 关闭时调用 → `updateStatus('failed')` + `outputLog('窗口异常关闭')`。**用户关窗 → task 失败**（不是静默"暂停"）。

我们 skill CLI 退出时（Ctrl+C / 关终端）本地任务丢失，worker 端不知道。可加 keepalive 信号或 webui 端心跳检测，**跨域修补要靠 webui 同事**。

---

## 8. 其他细节（杂项扫盲）

| # | 找到的 | 评估 |
|---|---|---|
| 8.1 | `releaseDate = dayjs().diff(dayjs(nullableCreateDate), 'day')`（实时算） | 我们 `create_days` 走 API。**可学兜底**：API 缺时客户端实时算 |
| 8.2 | 跟卖数据 fetch 进 `ozonListParser` 同函数 | 我们在 `_analyze_product` 阶段调用 → **位置应提前到列表内联解析** |
| 8.3 | "猜你喜欢" `[data-widget="skuGrid"]` 作拓店后备种子 | 我们裂变拓店无此后备池 |
| 8.4 | 跟卖数据双检（BASE + DETAIL） | 我们有 6h 缓存重验无新信息，**不学** |
| 8.5 | `checkRange` 0/None = 不限 | 我们硬比较，**改友好（小升级）** |
| 8.6 | Excel `isFileInUse` EBUSY/EPERM 占用检测 | 我们未来 Excel 落盘必须学 |
| 8.7 | `taskHandle` 120s 超时 | 我们 graph 路径无整体超时，可加 30-60s |
| 8.8 | `pushData` 写队列 `isWrite` 防竞态 | 我们 `_save_discovery_log` 一次性写无并发问题，**不学** |
| 8.9 | `collection.services.js` 旧版死代码（1160 行）session 隔离思路 | 仅作参考用，**不学** |
| 8.10 | `task-queue.services.js` `Promise.allSettled` 模拟 task slot | 我们 ThreadPoolExecutor 天然等价，**不学** |
| 8.11 | `currencyConversion` + `Decimal.js` 汇率 | 我们 worker pricing_node 同源公式已胜出 |
| 8.12 | `getChineseName` 后端翻译 / Excel 中文名列 | 我们 Excel 落盘可加中文翻译列 |
| 8.13 | `setInterval` 每秒检查 cancel reason | Python `Future.cancel()` 不能真取消已启动，**不学** |
| 8.14 | `interface.services.js:215` "帮豆不足即中断" | 我们可学：worker 入管线后每个 MXOU 调用前复查 quota（W5） |

---

## 9. 我们已胜出的反向输出（如未来有交流）

| 维度 | ozon-worker 现状 | 上品帮差距 |
|---|---|---|
| 1688 图搜主通道 | aibuy mtop 静默直调，秒级免浏览器 | BrowserWindow 隐藏窗口 + DataTransfer 上传 + 2min 超时 |
| 跟卖相关性护栏 | `_pick_best_match` 多信号（图搜位置+badge+词对+LLM 兜底） | 取第一个非广告结果（错配雷：花插 ¥1 / 水龙头被标符合 1/3） |
| 蓝海评分 | 10 因子（competing_sellers/profit_margin/monthly_sales/sales_growth/drr/price_range/commission/chain_depth/category_consistency/keyword_density） | 22 项硬过滤 + 5 项 AI = 实际用 27 项但无评分 |
| 拓店模式 | `run_fission` BFS frontier + chain_depth + 类目一致性 | 单层跳店 + 无类目一致性 |
| pricing 复用 worker 同源公式 | skill 端 `_calculate_profit` + worker `/api/v1/logistics/quote` 真实费率 | 后端 goodsFilter 中专，无 worker 同源公式概念 |
| 27 字段 ozon_seller_analytics 直采 | API 实时拉 | 后端炼数（路径不可走） |
| Sentry 全链路上报 + trace_id | worker 端结构化 JSON 日志 + trace | 仅 electron-log 文件 |

---

## 10. 学习清单（按 ROI 排序，落地用）

| 优先级 | # | 改进点 | 改 skill 还是 worker | 工程量 | 收益 |
|---|---|---|---|---|---|
| ★★★★★ | **S1** | graph 信封补 extensions.competitor（重/尺寸/俄语属性/竞品价/跟卖最低价/卖家数）—— 把 worker 已就绪的消费链全部喂数据 | skill | 1d | graph 路径上架成功率对齐 follow（+10-15%） |
| ★★★★★ | **S5** | 列表内联解析（cheerio 抽 price/name/cover/rating/reviewCount + 同函数内 fetch webSellerList）+ 18 项 BASE 粗筛 | skill | 2d | discover 单次时长 5min → 2min（2.5× 加速） |
| ★★★★ | **B3** | 13 个未接入的粗筛字段加进 `_SELECTION_FIELDS`（零成本，只是新增键值） | skill | 0.5d | 用户粗筛字段从 9 → 22 |
| ★★★★ | **B4** | AI 阶梯门槛默认规则 `discover --rules ai` | skill | 0.5d | 开箱即选品 |
| ★★★★ | **S6** | 销量阶梯门槛 + 上架≤365/跟卖≤30/销售动态>0/DRR≤15% 内置为"AI 默认"规则集 | skill | 0.5d | 用户体验 |
| ★★★★ | **W3** | worker 加 `/api/v1/mappings/lookup` 端点供 skill 走 worker 类目缓存（仿上品帮 cat-lookup webhook） | worker + skill | 1d | skill 走缓存省 2-5s/条 + 减少类目误配 |
| ★★★★ | **W5** | worker 入管线后每个 MXOU 调用前复查 quota，余额耗尽 fast-fail（仿上品帮帮豆不足即中断） | worker | 1d | 烧 5 角钱才发现 vs 烧半张图才停 |
| ★★★ | **D7'** | `discover-multi --keywords` 单进程多关键词并行（滚动串行，分析并行） | skill | 1.5d | N 关键词总时长 ≈ 1 次 + N × 滚动时间 |
| ★★★ | **S3** | CDP fallback 加早出窗口（preload 风格识别 result page 立即抓，30s → 8-10s） | skill | 1d | aibuy 失败时降级速度 3× |
| ★★★ | **S7** | 滚动 3000ms 缓动 + 80% 滚动量（反爬节奏） | skill | 0.5d | 反爬升级 |
| ★★ | **A2** | 跟卖数据进 `ozonListParser` 同函数（位置提前） | skill | 0.5d | 同 S5 一并做 |
| ★★ | **A4** | `min_price_below_current` 字段（跟卖最低价低于当前价才作信号）| skill | 0.5d | 跟卖低价抢决策更准 |
| ★★ | **A7** | `apply_selection_rules` 加 `min/max=None = 不限` 语义 | skill | 0.5d | 用户友好度对齐 |
| ★★ | **A3** | "猜你喜欢"（`[data-widget="skuGrid"]`）作拓店后备种子 | skill | 1d | 裂变拓店补强 |
| ★★ | **C3** | skill 端跑密度校验（1.293 ≤ density kg/m³ ≤ 13546）早发现 kg/g 错位 | skill | 0.5d | 不等 worker 算完才发现 |
| ★★ | **S2** | "猜你喜欢"作 graph 备用种子（1688 抓不到时从 Ozon 同款相关推荐备选） | skill | 1d | 1688 下架不直接挂 |
| ★★ | **D6** | Excel 落盘时 EBUSY/EPERM 占用检测 + tmp 文件 → unlink → rename 原子替换 | skill | 1d | 未来 Excel 落盘必须 |
| ★ | **S4** | aibuy 失败分层返回 `{ok: False, reason: "no_token|rate_limited|empty_result|sign_fail"}` | skill | 0.5d | 降级路径精确化 |
| ★ | **S8** | CLI 进度可视化（progress bar + 完成提示音类比文字） | skill | 1d | 用户感知 |
| ★ | **S9** | closeHandle + task keepalive 信号（CLI 退出时提示 worker 还在跑） | skill + webui | 0.5d | 降用户焦虑 |
| ★ | **S10** | 滚动期间进度输出（"待工作感"） | skill | 0.5d | 同 S8 一起 |
| ★ | **A5/A12** | Excel 落盘中文翻译列 + 30-35 列精简版（不照 65 列复刻） | skill | 2d | 给老板看的交付物 |

### 不学（已确认）

| # | 不学项 | 原因 |
|---|---|---|
| N1 | 后端查询过滤分类热卖模式 | 不可走（依赖 shopbang 私有炼数后端） |
| N2 | 取 1688 第一个非广告结果（无护栏） | 错配雷，我们 `_pick_best_match` 已胜 |
| N3 | DOM 选择器硬编码哈希类名 | 1688 改版即挂，结构性脆弱 |
| N4 | Ozon 详情 API 硬编码 slug `lastik-dlya-obuvi-...` | 上品帮已知 bug |
| N5 | 类目 vm 沙箱执行外部 JS 解析 | 安全面暴露 |
| N6 | Electron 8 个 BrowserWindow 隔离架构 | 用户体验差 + 资源重 |
| N7 | TaskManager 单例守护进程（路径 B） | 与 webui 同事任务中心职责重叠 |
| N8 | 旧 stealth 全套指纹伪造 | v0.28.7 已反学（真实指纹天然干净） |
| N9 | 跟卖数据双检（BASE + DETAIL） | 我们 6h 缓存重验无新信息 |
| N10 | 傲虾备用货源通道 | 性价比低 |

---

## 11. 后续行动

调研已收敛。下一步：

1. **打包写 `.omo/plans/skill-learn-shangbinbang-v1.md`**（21 项改进 + 10 项 worker 协作新发现），让 Momus 评审完整性/可验证性/边界。
2. 评审通过后按 ROI 排序落地：S1（1d）→ S5（2d）→ B3+B4（1d 并行）→ S6（0.5d）。
3. worker 侧改进 W3/W5 单独排期（webui 同事协调）。

---

## 12. 三轮 explore 深扫补充（2026-08-17）

> 本节由三路并行 explore（skill 改进点位置 / worker 改造工作量 / worker listing_templates 与 product_task_index 真相）汇总。含 file:line + 行号引用（与 §0-11 同源验证规则一致）。设七条标号 W6-W12（续接 §10 清单），其中 W6 是最大的修复点（graph 直连不回填索引）。

### 12.1 已核事实

**Q1 listing_templates API 完整度**
- 5 端点全在 `worker/src/routes/templates_routes.py`：GET `/templates`(42)、POST(48)、PATCH(55)、DELETE(62)、POST `/templates/{id}/default`(68，即 `setTemplateDefault` 等价)；`main.py:2177-2178` 注册 router。
- `template_service.py:22-30` CONFIG_KEYS 白名单 7 字段（margin_rate/commission_rate/fx_buffer/offer_id_prefix/follow_type/stock/warehouse_id），`_validate_config`(66-104) 拒非白名单键返 422；`_validate_store_overrides`(107-118) 按 store 白名单过滤。
- Pydantic schema `worker/src/api/schemas.py:325-364`：`ListingTemplateConfig`(325-333) / `Create`(336-342) / `Update`(345-351) / `Out`(354-364)。
- ⚠️ **ListingTemplateOut 不含 `store_overrides`**（354-364 行）+ `ListingTemplateCreate/Update` 也无此字段（路由通过 `await request.json()` raw 接受 → service 处理 store_overrides 但 Pydantic 响应模型 drop）。**D11 skill 端读 store_overrides 需 worker schema 补**（或 skill 不读 store_overrides 仅用顶层 config）。

**Q2 product_task_index 写入路径**
- 写入三处 `upsert_index` 调用（`product_index_service.py:54-63`）：
  1. `learning_record_node.py:114`（T9 上传成功回填，调用点 l.354）
  2. `image_service.py:228`（T14 改图）
  3. `draft_service.py:389`（T7 更新模式）
- **🔴 关键发现：graph 直连路径不上 product_task_index 索引**
  - `main.py:_write_direct_submission_row`(1366-1393) 给直连任务写 `draft_submissions` 时 `credential_id=NULL, draft_id=NULL`。
  - `learning_record_node.py:_resolve_draft_submission`(48-68) 查 draft_submissions 拿 `credential_id` → 返回 `(None, None)`。
  - `_backfill_product_index`(74-121) 在 **l.96-98** `if not credential_id: skip`——直连任务 credential_id=None → **跳过回填**。
  - `ozon_status_node.py` 无任何 product_task_index 写入（grep 确认 0 命中）。
  - **结论：graph 直连 submit_task 上架成功的商品，无法通过 product_id 反查到 task**——影响"重上"/"商品编辑定位"能力。W6 修复点即此。

**Q3 skill get_store_profile 数据源**
- `config_store.py:401-413` `get_store_profile` 只从本地 `stores.json`（`get_store` 87-111 → `_load_stores_file` → `STORES_FILE = CONFIG_DIR/stores.json`）白名单过滤 7 键（`currency`/`shipping_provider`/`shipping_service`/`margin_rate`/`commission_rate`/`fx_buffer`/`fx_rate`）。
- **skill 全代码 grep `templates|listing_template` 0 命中** — 不调 worker /templates API。D11 改造路径清晰：skill 加 worker API client + cli flag。

### 12.2 worker 端改进新清单（W6-W12）

| # | 标题 | 位置 | 改造 | 工程量 | 触发条件 |
|---|---|---|---|---|---|
| **🔴 W6** | **graph 直连路径回填 product_task_index** | `learning_record_node.py:96-98` 改"skip"为"用 submit_task payload credential_id 兜底";`main.py:_write_direct_submission_row`(1366-1393) 落 `ozon_client_id` 同时从信封解析 credential | skill 信封里 credentials 已注入，worker 落库后直接读 task payload 取 credential_id 兜底回填 | 0.5d | graph 路径上架后商品可反查，关键 |
| **🟡 W7** | **pricing_node 重启竞品价定价(**注意单位 bug 复发)** | `pricing_node.py:219-225` (v0.26 已删) 显式删除分支;若 S1 给 graph 上竞品价则需重启 | 防单位 bug 复发——所有 RUB/CNY 比较走 fx_rate 不走 raw 数字 | 1d | S1 改进落地后 |
| **🟡 W8** | **竞品兜底键结构统一** | 当前 worker 兜底消费**扁平键** `extensions.competitor_weight_g` / `competitor_dimensions_mm`(`weight_dimension_normalizer.py:34-115` + `assemble_ozon_product_node.py:54-81`) ;若 S1 改嵌套结构则同步改两处读取 | skill 决策：保持扁平键(零 worker 改) vs 嵌套 wym worker 两处读取适配 | 0或0.5d(取决于 skill 决定) | S1 改进方案选择 |
| **🟡 W9** | **ListingTemplateOut 补 store_overrides 字段** | `worker/src/api/schemas.py:354-364` 添加 `store_overrides: Optional[Dict[str, ListingTemplateConfig]] = None` | skill 端 D11 才能读完整模板配置 | 0.5d | D11 想读取 store_overrides 时 |
| **🟢 W10** | **D12=`/api/v1/discovery/runs` 端点复用 analytics 模式** | `worker/src/main.py:2053-2119` `_handle_analytics_report` 已实现鉴权/限流/upsert/multi-table-dispatch;`_ANALYTICS_KINDS`(1977-2002) 加一个 kind | 新 ORM model + Pydantic item + 2 端点（POST 上报 + GET 读取，GET 参考 `analytics/bestsellers` 2140-2170） | 0.5-1d | D12 落地 |
| **🟢 W11** | **W3=`/api/v1/mappings/lookup` 真相：n8n webhook 不是 worker** | `/webhook/cat-lookup-v1` 是 `worker/assets/category-lookup.json` 的 n8n workflow export，直查 Supabase `category_mapping_verified` (老 hybrid 表);worker 代码无；核心查询逻辑在 `category_mapping_learn.lookup_mapping`(18-54) + `ozon_category_query.get_category_mapping_by_keywords`(815-850) | 新端点 + skill 端 URL 切换;**重要:`category_mapping` 表无 tenant_id 列（model.py:281-307）全局共享**——天然多 SKU 共享 | 1d | W3 落地 |
| **🟢 W12** | **W5 MXOU 余额事中复查** | `main.py:_check_mxou_balance`(1118-1172) 仅 auth_verify(1232) + submit_task(1525) + auth_node.py:400-447 三处;`call_mxou_chat_api`/`call_mxou_image_api` (mxou_api.py 80/233) **无余额 pre-check**只限流 `mxou_acquire` | `get_mxou_balance`(mxou_api.py:718-753) 已现成;建议加在 Phase1 前或 mxou_api 入口(带 TTL 缓存避免逐调用打接口) | 0.5d | 烧帮豆前止血 |

### 12.3 D14 已实现澄清

之前清单 D14「`submit_draft` 加 `template_id` 注入」**已是 worker 现实**：
- `draft_service.py:submit_draft`(302-389) **已收 `template_id` 参数**(l.308)
- `_apply_listing_template`(247-275) **已注入** envelope.extensions(l.372 `payload_envelope = _apply_listing_template(...)`)
- 路由 `drafts_routes.py:95-103` 已透传 body.template_id
- templates CRUD 完整（templates_routes.py + template_service.py + listing_templates 表 model.py:705-727）
- 唯一可能的新工作：模板字段注入**白名单语义**（当前 `apply_template_to_envelope` template_service.py:268-314 无白名单，config 全量注入;如果要限制可注入字段则补）

→ **D14 工程量从 2-3d 降到 ≈0d**（skill D11 改造即可消费已就绪 worker 能力）

### 12.4 17 项学习点完整状态表（按改动段位置）

> **status: NEW = 全新功能; EXTEND = 在已有地方加; 已是现实 = 无需再改**

| # | 标题 | skill 现状精确位置 | 状态 | 改动行数估 |
|---|---|---|---|---|
| S1 | graph 信封补 extensions.competitor | `cloud_probe.py:1903-1930` 注入段（仅 margin/commission/fx/cdp_degraded）;1862 `ozon_category` 来自 search_categories 不是反查同款 | NEW | ~30L(新增段)+ 10L跟进 follow_sell_cloud:3320-3406 已用键命名 |
| S5/B3 | 列表内联解析 + 13 字段 | `ozon_discovery.py:803-809` JS 只取 href pid;`_SELECTION_FIELDS`(701-711) 9 字段;`fetch_product_info`(ozon_widget.py:376-386) 默认 9 key + JS update characteristics/aspects/reviewCount | EXTEND 大改 | ~100L(改 collect JS + _SELECTION_FIELDS 扩 13 + _apply_filters 改) |
| S4 | aibuy 失败分层 reason | `ozon_image_search.py:531-687` 当前返回 [] 一抹黑降级 | EXTEND | ~20L(改 reason 返回 + 调用方分支) |
| S6 | `--rules ai` 销量阶梯门槛 | `ozon_discovery.py:apply_selection_rules`(730-760) 只解析 `field>=value`;cli.py:1734 `--rules` help 无预设概念 | NEW | ~30L(预设规则集 + 调用层加 `ai` 分支) |
| S3 | CDP fallback 早出窗口 | `ozon_image_search.py:327-372` URL 轮询后 `time.sleep(wait_seconds=10)` 硬等 | EXTEND | ~20L(预内容轮询 30-40s→8-10s) |
| S7 | 滚动 3000ms 缓动 + 80% 滚动量 | `ozon_discovery.py:283` / `790` 两处 `scrollTo(0, scrollHeight)` 一跳到底;另 `ozon_image_search.py:378/387` | EXTEND | ~20L(改 2-4 处 scrollTo) |
| D7' | `discover-multi --keywords` 多关键词并行 | `cli.py:cmd_discover` (1178-1522);subparsers 注册 1616;discover 子命令注册 1718-1756;`--keyword` 单值串;无多关键词 | NEW | ~40L(新加 subparser + cmd 或扩展 cmd + 复用 collect_and_analyze 内 ThreadPoolExecutor) |
| D11 | skill 端读 listing_templates 选默认配置 | `config_store.py:get_store_profile`(401-413) 只读本地 stores.json;`cloud_probe.py:1909-1926` 注入 extensions 从 get_store_profile 三个 float | NEW | ~30L(加 worker templates API client + cli `--template-id` + build_graph_envelope 取值优先级) |
| D12 | 选品结果上报 worker 归档 | `ozon_discovery.py:_save_discovery_log`(2613-2639) 只写本地 JSON;调用点 552/691 两处 in-process;cli.py 不直接调 | NEW skill 端 + W10 worker 端 | ~40L(挂上报钩子 552/691 + worker discovery_runs 表+2 端点) |
| D13 | discover `--to-box` | `cli.py:1459-1522` `_submit_one`(1500-1515) 用 submit_envelope;graph 已有 `--to-box`(479-490) 的同套逻辑可搬 | EXTEND | ~15L(|arg 是否+flag+`_submit_one` 分支) |
| A4 | `min_price_below_current` 字段 | `ozon_widget.py:513/524-528` 返回结构 3 key{count,min_price,sellers};`ProductCandidate.min_competing_price`(l.102) 在 365 赋值仅用于导出(878);无任何过滤信号 | EXTEND | ~10L(新字段 + 比较计算) |
| A1 | 上架天数实时算兜底 | `ozon_seller_analytics._extract_metrics` 已拉 `create_days`;**API 失败时 create_days=0** | EXTEND | ~10L(apply_analytics_to_candidate 加 nullableCreateDate 兜底) |
| A2 | 跟卖数据进 ozonListParser 同函数 | 我们在 `_analyze_product`(323-373) 才调 fetch_competing_sellers;位置错 | EXTEND(配合 S5 改) | 0L(并入 S5) |
| A3 | "猜你喜欢"作拓店后备种子 | `ozon_fission.run_fission`(394-) 只走种子商品跟卖者,无 `[data-widget="skuGrid"]` | EXTEND | ~30L(fission 加 guess_like 后备池) |
| A7 | checkRange 0/None=不限 | `ozon_discovery.py:_check_rule`(714-727) 硬比较;不能留空表示"不限" | EXTEND | ~10L(加 None 旁路) |
| C3 | skill 端密度校验 | `cloud_probe.py` 在信封组装后未做密度校验;worker 端 `weight_dimension_normalizer` 在 prepare 才发现 | EXTEND | ~15L(build_graph_envelope 后加密度 1.293-13546 kg/m³ 校验 alert) |
| S2 | "猜你喜欢"作 graph 备用种子 | graph 抓不到 1688 时直接挂,无相关推荐 fallback | NEW | ~20L(失败时 ozon_scaper 同款推荐作为候选) |

### 12.5 总体清单（含已扫盲点 + worker 协作）

- **17 项 skill 学习点**（S1-A7） —— 估 ~390 改动行
- **6 项 worker 协作点**（W3/W5/W6/W7/W8/W9/W10/W11/W12）—— W6 修复 graph 直连索引（独立 bug）/ W10 新增 discovery_runs 端点 / W11 新增 mappings/lookup / W12 MXOU 余额事中复查 / W7-W9 配合 S1 协作
- **D14 已完成澄清** —— 无需新工程，skill D11 直接消费

---

*本文档基于上品帮 v1.0.22（`resources/app.asar`）解包源码 + ozon-worker v0.40.1 skill 实际状态逐行对照整理。所有事实来自源码原文，已纠正的认知错误在 §0 列明，避免后续开发者重蹈。§12 由三路并行 explore 深扫补充，含 skill 改进点精确位置 + worker 改造工作量 + 漏点澄清。*
# A2 · 参考项目能力对标与缺口清单

> 2026-09-11 · 基线：gov worktree v0.74.0。审计对象五家：上品帮（Electron 客户端 v1.0.22 + 插件 v3.2.6 + Web ERP）、毛子ERP（Web 后台 + 插件 3.2.4/3.2.6）、goldminer 采集插件 v2.43（未混淆源码）、AI上品助手家族（卖家国度 ozonAI 2.3.3 / WB AI 1.2.5 / Yandex AI 2.1.3 / bcsMercado 1.8.8）。
> 材料来源：`docs/参考项目/`（shopbang 文档+goldminer 源码+各插件 manifest，git-ignored 本地资产）、`docs/competitor/`（字段级反编译调研 5 文档）。
> 我方事实基线：worker REST 145 path/181 端点（43 路由组）、worker 远程 MCP 22 工具、pounding-mcp 29 工具、skill CLI 25 命令、webui 17 路由。所有「我方无 X」断言均在 gov worktree grep 验证，验证方式随条目标注。

---

## 1. 能力对标矩阵

图例：✅有（等价物存在）/ ⚠️部分（有但不完整或形态不同）/ ❌无。括号内为一句证据。

| 能力域 | 上品帮（客户端+插件） | 毛子ERP（后台+插件） | goldminer | AI上品助手家族 | 我方现状（v0.74.0） |
|---|---|---|---|---|---|
| 采集·1688 | ✅ BrowserWindow 图搜+DataTransfer 伪造上传（1688-window.services.js） | ✅ mtop 协议采集（插件 §2.5） | ✅ CDP+mtop 双通道（background.js） | ✅ 1688-main.js 专 chunk | ✅ aibuy mtop 直调主通道+CDP+AK 三级，`_pick_best_match` 多信号护栏（结构性优于竞品取首条） |
| 采集·淘宝/天猫 | ⚠️ 仅插件版描述提及京东/淘宝 | ✅ 淘宝/天猫 mtop | ✅ taobao/tmall host 全覆盖（manifest L17-21） | ✅ taobao-main.js | ✅ `skill/scripts/lib/taobao_client.py`（页内 mtop 免手签，字段权威=goldminer） |
| 采集·拼多多 | ⚠️ 仅插件版 | ✅ rawData+render/sku 兜底 | ✅ pdd 三形态 rawData（:13723） | ✅ pdd-main.js | ✅ `skill/scripts/lib/pdd_client.py`（三形态+分/元单位换算，红线「失败出声绝不半猜」） |
| 采集·Amazon/AliExpress/JD | ⚠️ 插件 desc 提亚马逊/京东 | ⚠️ JD/速卖通/Amazon 列于插件 §2.5 | ✅ Amazon 9 国 host（manifest L26-36） | ✅ aliexpress-main.js/amazon-main.ts.js | ❌ 无（grep skill/scripts 无 amazon/aliexpress client；仅 cross_source 淘宝/PDD 副通道） |
| 采集·Ozon 前台竞品 | ✅ 滚动+entrypoint-api 详情+跟卖 widget | ✅ 前台卡片 36 字段 widget | ✅ ozon/ozone host | ✅ ozon 平台 | ✅ ozon_scraper/ozon_widget（跟卖/详情/面包屑 truth） |
| 采集·卖家后台运营数据 | ✅ Cookie 驱动店铺分析（后端炼数） | ✅ 跨 Tab 借道 seller API（插件 §8.2） | ✅ seller.ozon.ru 专管 tab+限流（tab-manager） | ✅ vision.bcserp.com 视觉服务 | ✅ ozon_seller_analytics CDP 借道 what_to_sell v3/all-queries v2（27 字段）+ 未发版 session 代管直调 |
| 目标市场多平台 | ⚠️ Ozon+WB 双平台 | ⚠️ Ozon 主+WB 后台 | ⚠️ 采集多平台、上架按 Web 端 | ⚠️ 分插件各主一平台（WB/Yandex/Mercado） | ❌ 仅 Ozon（grep skill/scripts+worker `wildberries|yandex` 0 采集/上架命中） |
| 选品漏斗 | ✅ 六阶段两段式：18 项 BASE 粗筛+4 项 DETAIL+5 条 AI 门槛 | ✅ 22 条件规则+卡片打标 | —（纯采集） | ⚠️ 多维排行榜选品（Mercado 版） | ✅ discover 22 粗筛字段（_SELECTION_FIELDS，ozon_discovery.py:1526）+`--rules ai` 阶梯预设（:1573）+10 因子蓝海评分 |
| 类目/属性特征填写 | ⚠️ is_ai 交给后端 | ✅ AI 匹配类目+DeepSeek 生成 | — | ✅ AI 优化+俄语适配 | ✅ worker 全自动：类目信任序（L0/Skill 权威）+属性字典闸（max_value_count）+LLM 消歧，成熟度高于竞品 |
| 定价 | ✅ 4 个定价器（OZON/WB×定价/利润） | ✅ 售价倍率+划线价 | — | ✅ 利润计算器 | ✅ `compute_price` 唯一入口三档双价格+佣金三段 band pass+logistics/quote 真实费率 |
| 生图/图片处理 | ⚠️ 水印库+图片美化（外部）+压缩 | ✅ AI 套图 4 类（40 毛豆/张） | — | ✅ AI 优化（vision 服务） | ✅ 生图管线 5 槽位（main/white_bg/multi_angle/detail/scene）+COS 托管；⚠️ 无水印叠加能力（image_prompts 仅「严禁水印」） |
| 上架·批量/一键 | ✅ batchCreateGoods 每 100 一批 | ✅ 一键上架弹窗 11 字段 | — | ✅ 一键采集直上 | ✅ batch-submit + 13 阶段管线 + 采集箱多选提交 |
| 上架·记录/异常重上/定时 | ✅ 上架记录状态机+定时任务+下架重上 | ✅ import-history+下架重上+SKU 模板批量 | — | ⚠️ | ✅ tasks 管线+drafts resubmit+UPSERT_BY_OFFER+scheduled_listings 定时上架（drafts_routes.py:130，P1d） |
| 订单履约 | ✅ 7 状态机+备货/面单/取消/催护照/索好评/黑名单 | ✅ 订单 7 状态+消息模板 | — | — | ⚠️ 订单 15 raw 态→7 统一态+ship/label/cancel/chat 模板全有；❌退货操作、❌买家黑名单（见 §2/§4） |
| 物流 | ✅ 交物流/面单/拣货单/轨迹/物流商 | ⚠️ | — | — | ✅ 面单单+批量/ship/仓库/delivery-method+自建费率表 quote；❌拣货单合并、❌轨迹查询 |
| 店铺运营·分析 | ✅ Cookie 驱动六 tab（指标 30+/ABC/漏斗/价格指数） | ✅ 大盘+店铺指标卡 | — | — | ⚠️ /v1/analytics/data 四指标日表+rating+store_analysis 三清单+DataScreen；❌premium 深度分析（§2.4） |
| 店铺运营·商品/促销 | ✅ 在线商品管理+促销+自动移促销 | ✅ 在线商品+有利指数着色 | — | — | ✅ shelf 改价/库存/归档+promo actions/voucher/discount；❌自动移促销排程 |
| 财务/佣金 | ✅ 佣金分段+OrderTotal 全成本利润+回款 | ✅ 毛豆计费（内部） | — | ✅ 利润计算 | ⚠️ commission_resolver 三段佣金+estimate 利润+订单级 profit=amount−commission；❌对账/回款（§2.5） |
| 流量/SEO/榜单 | ✅ 大盘/中国馆/类目分析/热词/标签反查 | ✅ 榜单选品 5 tab | ✅ 热词同步（START_HOT_QUERY_SYNC） | ✅ 行业排行榜（Mercado） | ✅ what-to-sell 三榜+all-queries+seo/keywords+market-overview+sku_metrics_pool 读-回馈（对标 goldminer/毛子） |
| 数据留存与回馈 | ✅ saveAutoPickRecords 云端归档 | ✅ 加密上报后台（§8.3） | ✅ 采集会话 12h TTL+贡献池 | — | ✅ discovery_runs+selection_insights+sku_metrics_pool 贡献收包+补采指令+learning_record 类目闭环 |
| 任务调度与并发 | ✅ TaskManager 4-8 跨任务+单任务 3 图搜窗口 | ⚠️ 后台批量队列 | ✅ 队列+并发上限+冷却（background.js:140-146） | — | ⚠️ worker 50 并发+store_sync 调度器 ✅；skill 单进程单任务+线程池 4（多进程连 9222 不安全，学习笔记 §0.3） |
| 导出 | ✅ 65 列 xlsx+图片嵌入+原子写 | ⚠️ 表格导出 | — | — | ⚠️ xlsx 四区选品簿（ozon_discovery.py:2128）+CSV 三件套；图片嵌入未做（:2141「本期不做」） |

---

## 2. Ozon 运营数据维度对标（重点）

每维度按「参考项目：采集→解析→落库→计算」vs「我方链路（gov worktree 实证）」给出差距结论。

### 2.1 订单

- **参考项目**：上品帮后端代拉——店铺授权（client_id/api_key）上云，`同步近60天订单`+`每日凌晨5点自动同步`+实时拉新；13 个搜索字段（支持 Excel 粘贴批量）；Ozon raw 状态+子状态双机（护照收集/取货点/签收触发索好评）；采购追踪由插件回调（近 15 天、300 条/次、30 分钟冷却）。毛子同构（7 状态+消息模板）。
- **我方链路**：`order_service.py` `/v4/posting/fbs/list` 游标分页实时拉取 + STATUS_MAP 15 raw→7 统一态（L22-43）；`store_sync_service.py:_sync_orders`（L467）增量落 `ozon_orders_cache`（`_persist_orders_continuation`/`_complete_orders_window` 续传窗口）；`orders_routes.py` 16 端点：list/batch labels/batch ship/notes(GET/PUT)/label/ship/cancel-reasons/cancel/message-templates/message/messages；`order_notes` 表手填货源/采购单号/快递/追踪号（NOTES_COLS，L326）；催护照/催取货/索好评三模板+`/v1/chat/start`+`send/message` 落 `order_messages`（order_service.py:580-698）。
- **差距结论**：操作面基本对齐（备货/发运/面单/取消/催评全覆盖）。缺口：①订单利润列=`amount−commission`（L166），无全成本按单归集（竞品口径=有效金额−采购−佣金−物流−退款）；②退货申请仅同步+读（GET /stores/{id}/returns），无批准/拒绝/部分退款操作；③无买家黑名单（grep `buyer_back|买家黑名单` worker/src+webui 0 命中）；④无自动采购追踪（需插件或 1688 API，竞品靠插件回调）。

### 2.2 物流

- **参考项目**：上品帮面单/拣货单批量打印+下载、物流轨迹查看、物流商列表后端统一维护（getLogisticsSimple）。
- **我方链路**：`/v2/posting/fbs/package-label`（单+批量）、`/v4/posting/fbs/ship`、`/v2/warehouse/list`、`/v2/delivery-method/list`；自建 `logistics_rates` 费率表 + `/logistics/quote`（定价唯一入口消费）。
- **差距结论**：面单/发运/仓库对齐。缺口：拣货单合并文档（多单一面）与轨迹查询未做——竞品的轨迹也是弱项（弹窗简单），优先级低。

### 2.3 商品

- **参考项目**：上品帮 GoodsManage 在线商品（改价/改库存/上下架/定时任务）+商品编辑 7 分区；毛子在线商品+有利指数着色。
- **我方链路**：`_sync_products`（store_sync_service.py:783）落 `ozon_products_cache`（库存/tier 价/`_archive_missing` 归档对账 L993）；`products_routes` source/cost/source-candidates/update_images/edit 五端点；`shelf_routes`+`store_actions_routes` 改价/库存/归档/解冻 + bulk_* 批量；`product_task_index` 商品↔任务反查。
- **差距结论**：在线商品管理闭环度好。缺口：在线商品全字段编辑（attributes/update 仅在 retry 链内部使用，未暴露运营端点）——上架前编辑走 drafts 链已覆盖，属 P3。

### 2.4 店铺（分析）

- **参考项目**：上品帮店铺分析 Cookie 驱动六 tab——概览（ordered_units/revenue/session_view/conv_tocart_pdp/cancellations/returns/position_category）、「所有指标」30+ 字段（revenueShare/searchPosition/convSearchViewsToCart/priceIndex/drr/orderedUnitsGrade 等）、销售漏斗、ABC 分析（含结论文案）、领先产品、搜索位置。
- **我方链路**：`_sync_analytics`（store_sync_service.py:300）`/v1/analytics/data` 日表，**metrics 仅 4 个**（`_ANALYTICS_METRICS = ["hits_view_search","hits_view_pdp","orders_count","revenue"]`，L47）+`/v1/rating/summary`+`store_metrics_history` 快照（无成本 profit 写 NULL 不编造）+`store_analysis_service.analyze_store`（summary/profit_trend/low_margin/out_of_stock/promo_ready 四块）+webui DataScreen。
- **差距结论**：**深度差距最大的维度**。竞品 30+ 指标 vs 我方 4 采集指标+派生；ABC 分析/销售漏斗/价格指数/搜索位置全缺。Ozon 官方 `/v1/premium/` 系列（analytics 紧限流 60/min，rate_limiter.py:47 已预留 section）0 调用（grep `premium/` 除 rate_limiter 外 0 命中）——基建预留了、分析未接。

### 2.5 财务（佣金/利润/对账）

- **参考项目**：上品帮 OrderTotal 汇总卡（订单量/有效销售额/采购金额/利润=有效金额−（采购+佣金+物流+退款+其他）/双利润率/汇率/预估回款+已回款结算时间）；佣金三段文本进选品簿。
- **我方链路**：`commission_resolver`（显式>category_commission 缓存表>extensions segments>0.10）+ `category_commission` 三段表 + estimate 端点/webui 定价器 + 订单级 `profit=amount−commission` + `store_metrics_history.profit_amount`（店铺快照粒度）+ `exchange_rates` 表（model.py 已建）。
- **差距结论**：定价侧利润链完整且优于竞品（三档双价格+真实费率）；**经营侧对账缺失**——`/v1/finance/transaction/list`（费率账单/回款/佣金账单）0 实际调用（grep 仅 ozon_rate_limiter.py:13,45 的 section 分类器引用），finance section 限流（100/min）预留未用。利润计算停在「定价预估」与「店铺快照」，未到「按单实际」。

### 2.6 流量（SEO/榜单/关键词）

- **参考项目**：上品帮选品广场六页（大盘/中国馆/类目分析/热销/热词精选/标签反查，后端炼数不可走）；毛子榜单 5 tab；goldminer 热词同步任务（START_HOT_QUERY_SYNC，admin 限定）。
- **我方链路**：skill `ozon_seller_analytics` CDP 借道 seller 后台 what_to_sell v3+all-queries v2（27 字段）→ 上报 blue_ocean_queries/ozon_bestsellers/market_bestsellers → `analytics_routes` 7 端点（market-overview/categories/hot-queries/sales-trend/what-to-sell/seller-sync/sku-metrics）+ `seo/keywords` + `selection_insights` + `sku_metrics_pool`（贡献收包合并+读侧补采指令，模块头自述对标 goldminer 读-回馈+毛子 sku3 指令式）+ 未发版 session 代管直调。
- **差距结论**：数据源与金矿同源（seller 后台），覆盖好。缺口：「标签反查」（keyword→具体商品列表反查）无独立面；搜索位置报告（position_category）依赖 §2.4 premium 未接。

---

## 3. goldminer 工程实践借鉴（源码级）

### 3.1 采集契约 collection-contract.js（8.4KB 纯函数模块）

要点：平台识别→重定向校正→按平台分策略校验，失败返回结构化错误码而非异常字符串。

```js
// collection-contract.js:14-24  URL→平台判定（7 平台白名单正则）
function detectPlatformFromUrl(url) { ... if (/ozon\.ru/i.test(text)) return 'ozon';
  if (/(?:yangkeduo|pinduoduo)\.com/i.test(text)) return 'pdd'; ... return 'unknown'; }
// :26-38  重定向后平台校正（finalPlatform 优先，platformCorrected 标记被劫持）
// :141-197  validateCollectedPayload：按平台分派校验——
//   pdd → validatePddCollectedPayload（:106-139）缺 title/images/skus/price 时
//         返回 {ok:false, code:'pdd_incomplete_payload', details:{missing_fields:[...]}}
//   wildberries → 通用标题检测（:81-86 纯数字/「Интернет-магазин WB」=未加载完）
//   通用 → missing_product_identity / placeholder_skus_only（:57-63 hasUsableSku）
// :199-210  validateMtopDetailResult：ret 数组 FAIL/TOKEN/SESSION_EXPIRED 模式判废
```

- **我方对照**：校验存在但散点——`pdd_client`/`taobao_client` 各自「失败出声绝不半猜」抛类型化异常、`readiness.ensure_pipeline_ready`（readiness.py:209）预检、worker ingest 空 title fail-fast + LOCAL_* 预检错误码。等价能力≈80%，缺的是**单点契约模块 + missing_fields 结构化错误**（我们现在异常 message 人话但不便于上游程序化降级）。
- **可移植建议**：skill 提取 `lib/collection_contract.py` 纯函数层（`detect_platform(url)` / `validate_payload(payload)->{ok,code,message,details.missing_fields}`），三个货源 client 与信封组装共用；worker ingest 复用同一 code 词汇表，扩展错误码表时三处同步成本下降。规模 S。

### 3.2 限流器 ozon-seller-request-limiter.js（102 行）

```js
// :15-54  createTokenBucket：连续补充令牌桶，take() 返回 {granted, waitMs}
//   waitMs = ceil(((1 - tokens) * 1000) / ratePerSecond)  ← 精确到"下一个令牌何时好"
// :56-98  createTokenGate：acquire() Promise 挂 waiters 队列 + drain 定时器
//   无令牌时 setTimeout(drain, waitMs)，避免轮询；snapshot() 暴露 pendingCount
// background.js:146-149  实例化：ratePerSecond/burst 可配 + :7243 每个 seller 请求前 acquire()
```

- **我方对照**：worker 侧已有等价物 `utils/ozon_rate_limiter.py`（section 令牌桶 seller/finance/premium=1000/100/100 per min，`acquire(endpoint, timeout)` 阻塞式）——**服务端 API 面不缺**。skill 侧无显式限流（借浏览器身份+sleep 节奏），aibuy mtop/1688 CDP 无 waitMs 式精确排程。
- **可移植建议**：不搬代码（等价物已存在），学其**waitMs 返回值**语义：skill 侧批量图搜/批量 seller 采集时把「预计等待」透出为用户进度（配合学习清单 S8 进度可视化）。规模 S。

### 3.3 tab 调度 ozon-seller-tab-manager.js（283 行）

```js
// :149-171  创建租约：开新 tab 前写 {leaseToken, leaseExpiresAt=now+30s} 到 storage.session，
//   写后再 scan 一次（pre_create_rescan）+ 二次校验 leaseToken 归属——多 context 并发只放一个创建者
// :191-197  ensureTab() 单飞：ensurePromise 复用进行中的创建 Promise
// :225-266  runShared/runExclusive 读写锁：共享读（多个指标采集并发进同一 tab）+
//   独占写（登录/调试 HOT_QUERY）互斥，exclusive 优先防饿死
// :199-223  reconcile/handleRemoved：tab 被关→状态 'removed'；僵尸 recordedId 对账自愈
```

- **我方对照**：`cdp_client.py` 接口面为 `CdpConnection.new_tab/find_tab/release/close`（L301/384/432/419）+ `CdpTab.evaluate/navigate/wait_for_load`（L138/181/205）。同构点：共享 tab 复用（ozon_seller_analytics `_tab_for_variant_truth` find_tab 命中→release 只读复用不关，≈runShared）。缺：①无跨进程**创建租约**——skill 学习笔记 §0.3 已实证 `CdpConnection._tabs` 是 in-process list（cdp_client.py:267），多 Python 进程同时连 9222 会 WS 归属串错；②无 exclusive 语义——session-sync（cookie 收割）与 discover 采集并发时可能同抢 seller tab。
- **可移植建议**：把租约+单飞+读写锁三件套移植为 Python 层：lease 状态落文件（`data/browser/profiles/<profile>/seller_tab_lease.json`，flock 保护）或 PG 小表；`find_tab 命中=shared`、`session-sync/登录类操作=exclusive`。这与既有「discover-multi 单进程串行滚动」决策兼容（先解决多进程安全，再谈并行）。规模 M。

---

## 4. 缺口清单（核心交付）

> 先声明「已有等价物、勿重复立项」：22 字段粗筛+AI 阶梯（_SELECTION_FIELDS/--rules ai 已实现）、mapping/lookup 端点（W3 已落地 v0.59）、discovery_runs 归档（W10 已落地 v0.56）、MXOU 余额事中复查（v0.62 R1 已做）、listing_templates+store_overrides、定时上架（scheduled_listings 已落地）、催护照/催取货/索好评模板+chat API（order_service 已落地）、数据大屏（DataScreenPanel 已有）、taobao/pdd 货源 client（gov 批 2/3 已落地）、跨进程图搜护栏 aibuy（结构性胜出）。以上均在 2026-08-17 学习笔记中列为缺口，本次逐条 grep 确认已闭合。

### 「参考项目有 + 我方确无」（按优先级）

| 级 | 缺口 | 证据（验证方式） | 建议方案 | 规模 |
|---|---|---|---|---|
| **P0** | 财务对账/回款（/v1/finance/transaction/list 未接；竞品有预估/已回款+全成本利润） | `grep -rn "finance/transaction" worker/src --include=*.py` → 仅 ozon_rate_limiter.py:13,45 分类器，0 实际调用 | store_sync 新增 finance 域（transactions 表+日级节流，复用 finance section 100/min 限流）+ webui 财务页（回款/费用/佣金三表） | M |
| **P0** | Premium 卖家深度分析（ABC/漏斗/价格指数/搜索位置/30+ 指标；竞品六 tab） | `grep -rn "premium/" worker/src` → 仅 rate_limiter.py:47，0 调用；_ANALYTICS_METRICS 仅 4 项（store_sync_service.py:47） | ①短期：/v1/analytics/data 扩 metrics 列表（orderedUnits/revenue 换 conv_tocart_pdp 等同源可算项）；②中期：/v1/premium/* 接入（60/min 已预留）落 premium_analytics 日表 + 店铺分析页六 tab 化 | M |
| **P0** | 订单全成本利润（采购+物流+退款按单归集；竞品 OrderTotal 口径） | order_service.py:166 `profit = amount - commission`；order_notes 有采购单号但无成本归集 | order_lines 级联 product_cost（product_cost_service 已有）+ 物流费率快照 → 订单利润=有效金额−（采购+佣金+物流），汇总进 OrderTotal 式统计端点 | M |
| **P1** | WB/Yandex 第二目标市场（6 家参考 5 家双平台；我方 Ozon only） | `grep -rln "wildberries" skill/scripts --include=*.py` → 0 采集/上架命中 | 战略决策项：插件家族证明单平台专精也可存活；若做，先 WB（goldminer 的 WB 校验契约 + 品类重合度最高） | L |
| **P1** | 退货单操作闭环（批准/拒绝/部分退款；竞品 RefundList 5 操作+拒绝理由枚举） | `grep -n "returns" worker/src/routes/*.py` → 仅 store_sync_routes.py:103 GET；worker 仅 `/v1/returns/list` 同步 | orders 域加 POST /returns/{id}/approve|reject|compensate（Ozon 原生 return action API），拒绝理由表硬编码进 errors 常量 | M |
| **P1** | 买家黑名单（竞品云黑名单跨租户共享+订单列表标记） | `grep -rn "buyer_back\|买家黑名单" worker/src webui/src` → 0 命中 | buyer_blacklist 表（tenant+buyer_id 唯一）+ 订单列表 join 标记；云共享可选出（跨租户脱敏） | S |
| **P1** | 采集契约统一模块（goldminer collection-contract 式纯函数+missing_fields 结构化错误） | 三个货源 client 校验各自为政（pdd_client/taobao_client 模块级异常家族，无共享 code 表） | 按 §3.1 提取 lib/collection_contract.py，错误码与 worker api/errors.py 词汇对齐 | S |
| **P1** | CDP 跨进程 tab 租约+读写锁（goldminer lease/shared/exclusive 三件套） | cdp_client.py:267 `self._tabs` in-process（学习笔记 §0.3 实证多进程不安全）；session-sync 与 discover 可并发抢 seller tab | 按 §3.3 文件锁租约 + exclusive 语义；先落 seller tab 场景（风险最高），1688 采集随后 | M |
| **P2** | Excel 图片嵌入+原子写（竞品 65 列含主图/货源图嵌入、EBUSY 检测） | ozon_discovery.py:2141 docstring「图片嵌入列本期不做」；`grep add_image` 0 命中 | openpyxl add_image 主图列+tmp→rename 原子替换（学习清单 D6 既有结论） | S |
| **P2** | 拓店「猜你喜欢」后备种子（竞品 skuGrid 补池） | `grep -c skuGrid skill/scripts/lib/ozon_fission.py` → 0 | run_fission 加 skuGrid 后备池（学习清单 A3，30 行级） | S |
| **P2** | 拣货单合并打印 + 物流轨迹（竞品批量拣货单） | `grep -rn "拣货单\|picking" worker/src skill/scripts` → 0 | 面单 PDF 合并（pypdf）+ 拣货单 CSV/PDF 导出；轨迹低优先（Ozon 无公开轨迹 API） | S |
| **P2** | 采购自动追踪（竞品插件回调：近 15 天/300 条/次） | order_notes 仅手填 purchase_tracking（order_service.py:326-339），无自动拉取 | 阶段一：1688 AK 订单 API 查运单号批量回填；阶段二：插件回调通道（与采集插件合并形态） | M |
| **P3** | 自动移促销/自动上下架排程（竞品 SaleAutoMove+定时任务管理页） | scheduled_listings 仅覆盖「定时上架」单点（drafts_routes.py:130）；`grep -rn "自动移促销" worker/src` → 0 | 通用 job 表（store_sync_jobs 已有骨架）扩展 action 类型：定时改价/定时上下架/周期移促销 | M |
| **P3** | 子账号权限体系（竞品主/子账号+额度分配） | `grep -rn "sub_account\|子账号" worker/src skill/scripts webui/src` → 0 命中 | Supabase users 表扩展角色列+webui RBAC；多租户模型已就绪 | L |
| **P3** | 浏览器插件形态（前台卡片打标/一键跟卖悬浮窗；毛子/上品帮/goldminer/bcs 四家全有） | 仓库无插件子项目（pounding-sidebar 是 dsh 侧边栏，非浏览器插件） | 战略取舍：agent+webui 路线 vs 插件路线。若服务重度 Ozon 前台用户，评估最小插件（跟卖悬浮窗→submit_draft） | L |

### 「参考项目有 + 我方已有等价物」（无需立项，防止重复实施）

| 参考项目能力 | 我方等价物（位置） |
|---|---|
| 上品帮 18 项 BASE 粗筛+5 条 AI 门槛 | _SELECTION_FIELDS 22 字段 + `--rules ai`（ozon_discovery.py:1526/1573） |
| 上品帮 65 列选品簿（四区结构） | export_to_xlsx 四区选品簿（ozon_discovery.py:2128，列集=CSV 全字段） |
| 上品帮 goodsFilter 云端利润核算 | compute_price 唯一入口 + /estimate + logistics/quote（竞品是黑盒后端，我方公式同源自证） |
| 毛子跨 Tab 借道 seller API（插件 §8.2） | ozon_seller_analytics CDP 借道（what_to_sell v3 契约逐键对齐） |
| 毛子 AI 全链路上架（类目/标题/描述/套图） | worker 13 阶段管线（类目信任序+三档定价+5 槽位生图+retry 闭环） |
| goldminer 读-回馈贡献池 + 毛子 sku3 指令式 | sku_metrics_pool_service（模块头自述对标两者） |
| 上品帮 saveAutoPickRecords 云端归档 | discovery_runs + selection_insights |
| 竞品「帮豆不足即中断」 | MXOU 余额治理（v0.62 R1：401 永久错误分类+低额通知+token 指纹缓存） |
| 竞品崩溃重启/内存监控 | worker PG 队列自愈 + Sentry 全链路 + STALE_RUNNING 恢复 |
| goldminer MTOP 结果判废（FAIL/TOKEN 模式） | aibuy 通道失败分层 + pdd/taobao client 类型化异常家族 |

---

## 5. 资产收敛建议（上品帮题材三处重复）

现状盘点（三处载体、两种形态）：

| 载体 | 位置 | git 状态 | 内容 |
|---|---|---|---|
| ① 调研文档 | `docs/competitor/`（gov+main 两 worktree 同步存在） | **tracked**，5 文件 ~50KB | Web ERP 字段级反编译 + 毛子后台/插件 + skill 学习笔记 |
| ② 解包源码+配套文档 | `docs/参考项目/`（155MB：shopbang 10MB、ozonAI 68MB、YandexAI 35MB…） | git-ignored（`git check-ignore` 确认），本地易失 | shopbang 两份 md + goldminer 未混淆源码 + 6 个插件解包 |
| ③ 归档 zip | `archive/packages/上品帮-源码归档.zip`（8.3MB） | untracked（`git ls-files` 0 命中），本地易失 | ②中 shopbang src 的压缩快照 |

收敛方案（单一真相源 = ①docs/competitor/）：

1. **文档层归一**：`docs/competitor/README.md` 扩为唯一索引，把 `参考项目/shopbang/` 两份 md（客户端 Electron 视角，与 ①的 Web ERP 视角互补不重复）**移入** `docs/competitor/shopbang-client/`（git mv 保历史），并加一行状态标注（对应上品帮 v1.0.22，勿更新为更新源码包）。skill 学习笔记中已闭合的 21 项清单建议在文首加「2026-09-11 审计注：S1/S5/B3/B4/S6/D7'/A7/W3/W5/W10/W12 已闭合，见 A2 §4」防后人按旧清单重复立项。
2. **二进制层防失**：155MB 解包件与 8.3MB zip 均不入库（体积与法务风险），改入 COS 私有桶（项目已有 COS 基建），`docs/competitor/README.md` 登记**来源清单**：每个包的版本号/下载来源/解包日期/sha256/COS key。`archive/packages/上品帮-源码归档.zip` 与 `参考项目/shopbang/src/` 是同物两份——保留 zip（校验和完整）、src 目录可删（或反之），二选一。
3. **生命周期规则**：competitor 文档按「竞品版本号冻结」维护（源码包升级才允许改），audit 目录（本文件所在）只放时点快照，引用 competitor 文档用相对链接，禁止再拷贝正文——本次审计即按此执行：A2 只引用不复制。

---

*审计执行：只读 gov worktree + 主 worktree docs/参考项目/（未跟踪资产）；未修改任何源文件；本文件为唯一写入物。*

# PRD：审计遗留问题修复清单（验证版）

> 日期：2026-08-03 ｜ 状态：**✅ 已实施完成（v0.14.0）**，验证全绿
> 验证方式：4 个只读调查 Agent 逐项读实际代码，所有结论均带 `文件:行号` 证据，非凭注释/记忆
> 目标版本：v0.14.0（2026-08-03 commit，见 CHANGELOG）

## 一、验证结论总览

| # | 问题 | 状态 | 影响 |
|---|------|------|------|
| P0-2 | 跟卖属性链路断（键名不一致） | ✅ 属实 | 跟卖商品属性全部丢失，品牌/原产国不生效 |
| P0-4 | 单SKU/跟卖/发现漏运费 | ✅ 属实 | 单SKU 采购成本不含国内运费，利润失真 |
| P0-3 | GlobalState 缺 dictionary_values/match_confidence | ✅ 属实 | 动态字典选色死代码，类目置信度阻断失效 |
| P0-6 | 竞品价未进入定价链路 | ⚠️ 部分属实 | 字段存在且参与定价，但喂入的是 1688 采购价而非 Ozon 竞品价 |
| P1-1 | quantity 变体定价错 | ✅ 属实 | 数量拆分商品按 1688 裸采购价上架，无利润/佣金加成 |
| P1-4 | pricing 失败兜底 1000 | ✅ 属实 | 定价崩溃仍以 1000 上架，无阻断 |
| P1-5 | parse_error 不读 validation_errors | ✅ 属实 | 本地校验错误退化到通用 LLM 修复 |
| P1-6 | 登录预检无条件 | ✅ 属实 | 已检测到登录仍重复 CDP 页面检查 |
| P1-7 | 佣金死代码（1688 item_id 查 Ozon） | ✅ 属实 | 恒返回空，佣金率回退默认值 |
| P1-3 | 空参考图仍调生图 API | ✅ 属实 | Phase1 失败后 6-7 个 Phase2 节点各浪费 1 次生图 |
| B1 | 属性逐条 LLM 翻译 | ✅ 属实 | 典型产品 8-13 次/最坏 20-40 次 LLM 调用 |
| B2 | call_mxou_chat_api 无重试 | ✅ 属实 | 0 重试；API 故障时级联浪费 |
| B3 | mxou_rate_limiter 死代码 | ✅ 属实 | 全实现零引用（含 429 退避） |
| B4 | variant_primary_loop 串行生图 | ✅ 属实 | 39 变体 = 39 次串行调用，小时级 |
| B5 | 空参考图跳过生图（同 P1-3） | ✅ 属实 | 注释声明了要跳过但代码未实现 |
| E1 | 进度写 PG 每节点写 | ✅ 属实 | 每节点异步写一次 PG，无节流 |
| E2 | cloud_probe import 模块级 discovery 请求 | ✅ 属实 | 每次命令额外 HTTP GET，最长 +10s |
| E3 | cache_set 非原子写 | ✅ 属实 | 并发写可能写坏 JSON（仅致缓存失效） |
| E4 | 手写裸 CDP 重复 4 处 | ✅ 属实 | 4 处各自实现协议，行为不一致 |
| E5 | follow 命令 3-4 次 CDP 连接 | ✅ 属实 | 实际 4-5 个 WebSocket，可合并 |
| E6 | discover 每产品新开 CDP | ⚠️ 部分属实 | 采集阶段已复用；仅 1688 图搜匹配阶段每候选新开 |
| E7 | batch_test O(n²) + finally NameError | ✅ 属实 | 两问题均存在；NameError 掩盖原始异常 |
| E8 | scene max_completion_tokens vs max_tokens | ✅ 属实 | cfg 键被忽略，改 cfg 不生效（当前恰好都=2048） |
| E9 | 并发上限：num_workers 硬编码 10 | ✅ 属实（默认值修正见下） | MAX_CONCURRENT>10 时实际并发封顶 10 |
| C1 | search_categories 无 TTL 缓存 | ✅ 属实 | 每次类目搜索重拉整棵 ~2-5s 树 |
| C2 | 连接复用（widget 侧） | ⚠️ 已修复 | fetch_product_info/sellers 已支持 cdp 复用；ozon_scraper.py:404 与 ozon_image_search.py:226 仍每次新建 |
| C3 | 并发默认值 50 vs 10 | ❌ 用户清单需修正 | 代码默认**10**（非 50）；文档 AGENTS.md 写 50 已过时 |
| C4 | ProgressLogger 每次读磁盘 JSON | ✅ 属实 | 20+ 节点每次实例化都 open+json.load；config_path 参数被忽略 |
| D1 | batch_test（同 E7） | ✅ 属实 | — |
| D2 | ozon_post 裸 requests.post | ✅ 属实 | 无共享 session/重试；retry_loop 绕过 ozon_post 裸调 |
| D3 | config 错位 | ✅ 属实 | graph.py:83 声明 translate_russian_cfg.json 但节点读 attributes_llm_cfg.json；graph.py:63 同理 |
| D4 | 死代码 6 节点 + loop_graph + image_gen_factory | ✅ 属实 | 8 个文件全仓库零引用 |
| D5 | chrome_launcher 杀所有 Chrome | ⚠️ 部分属实 | Electron 已排除（有历史注释）；普通无 debug 端口 Chrome 窗口仍会被杀 |

**合计：31 项中 ✅ 属实 27、⚠️ 部分属实/已修复 4、❌ 不属实 0（仅 E9/C3 的描述数值需修正）。**

---

## 二、P0 正确性修复（优先，直接影响上架结果）

### P0-2 跟卖属性链路断 ✅
- **证据**：三处键名不一致，链路从组装起即断：
  - 产出端 `follow_sell_import_node.py:184-190` 输出 Ozon 格式 `"id"` 键（含 126745801="Нет бренда" 假品牌条目，line 26/185-186）
  - `assemble_ozon_product_node.py:150-152` `_assemble_follow_sell` **不消费** follow 输出，自行重新组装，且把字典值 ID `126745801` 当**属性 ID** 使用
  - 消费端 `prepare_ozon_upload_node.py:1148-1155` 读 `attr.get("attribute_id")` → `"id"` 键取值 None → 「属性ID缺失，跳过」→ 属性全丢
- **影响**：跟卖商品最终 payload 只剩 prepare 兜底追加的属性（9048/8962/4191 等），品牌"Нет бренда"、原产国"Китай"均不生效；`id=126745801` 条目即使格式修复也会因属性 ID 非法被 Ozon 拒绝。
- **方案**：`_assemble_follow_sell` 改为消费 follow 节点输出的 `final_attributes`（统一 attribute_id 键）；删 126745801 假属性条目，品牌用 schema 真实 ID（85/5076）。

### P0-4 单SKU/跟卖/发现漏运费 ✅
- **证据**：`cloud_probe.py:1377` `if len(variants) > 1:` 守卫真实存在，挡在 `_collapse_variants_to_single` 唯一调用点前；函数内部（658-706）已兼容 0/1/N，但单 SKU 时走不到 → `cost_cny` 不含 `freightCny`（line 1426 写入 purchase_cost）。
- **方案**：删守卫，无条件调用（内部已兼容）；改动 1 行，每单必现的利润偏差。

### P0-3 GlobalState 缺字段 ✅
- **证据**：`state.py:15-127` GlobalState 无 `dictionary_values`/`match_confidence`；写入点（assemble:919/924）被图状态合并丢弃，读取点（prepare:685、graph.py:180 `getattr(..., 1.0)`）恒为默认值。
- **影响**：`_get_color_from_dictionary`（prepare:143-165）退化死代码，动态字典选色失效；类目低置信度阻断永不触发。
- **方案**：GlobalState 补 `dictionary_values: Dict = Field(default_factory=dict)`、`match_confidence: float = 1.0` 两字段。

### P0-6 竞品价链路 ⚠️ 部分属实
- **证据**：GlobalState:65 有 `competitor_price`；`pricing_node.py:224-239` 跟卖模式确实用它参与定价（≥成本×1.3 保持竞品价）。**但** skill 侧 envelope 无竞品价字段（`grep` cloud_probe.py 无命中），`follow_sell_import_node.py:159` 读 `draft.get("price")` 实为 **1688 CNY 采购价**（cloud_probe.py:1442），非 Ozon 竞品售价 → 竞品价保护分支实际不生效。
- **方案**：Skill `follow_sell_cloud` 抓 Ozon 竞品价写入 draft（如 `draft["competitor_price"]`）→ worker PricingInput 补字段透传。需先确认 Ozon 竞品价数据源（widget 已能拿价格）。

### P1-1 quantity 变体定价错 ✅
- **证据**：`prepare_ozon_upload_node.py:1671-1686` quantity 拆分路径 `var_price = float(variant.get("price", price))` 直接用 1688 原价；`pricing_node.py:301-333` 已为所有变体算好 `variant_prices` 但 quantity 分支不消费（仅颜色/尺寸分支消费）。
- **影响**：数量拆分商品按裸采购价上架，无利润/佣金/物流加成，可能亏本。
- **方案**：quantity 分支改用 `pricing_info["variant_prices"][i]`（对齐颜色/尺寸分支）。

### P1-4 pricing 失败兜底 1000 ✅
- **证据**：`pricing_node.py:342-349` 异常返回空 pricing_info（无 `[PRICING_FAILED]` 标记）；`assemble_ozon_product_node.py:230` 与 `_assemble_follow_sell:146` 都 `pricing_info.get("price", "1000")`；`graph.py:167` `pricing → assemble` 直连无阻断。
- **方案**：pricing 失败返回 `[PRICING_FAILED]` 标记 + 删 ¥1000 兜底 + graph 加条件路由阻断。

### P1-5 parse_error 不读 validation_errors ✅
- **证据**：`validation_retry_loop.py:351-363` 只读 `state.errors`，`validation_errors` 恒被忽略；经 `ozon_validate` 失败进入 retry 时 errors 为空 → 落入 UNKNOWN → 通用 LLM 修复。
- **方案**：parse_error 合并读取 validation_errors（对齐修复分类）。

### P1-6 登录预检无条件 ✅
- **证据**：`service.py:2343-2345` `_check_1688_login_live(cdp_url)` 无条件执行，无 `not session.get('login_detected')` 守卫（login_detected 标志存在于其他路径 1423/1838/2203/2419 但此处不读）。
- **方案**：加条件守卫。

### P1-7 佣金死代码 ✅
- **证据**：`cloud_probe.py:1470-1478` `fetch_product_commissions(..., [str(item_id)])`——item_id 是 **1688 offer ID**，`ozon_seller.py:84-88` 用 `product_id` filter 调 `/v5/product/info/prices` → 恒返回 `{}` → 佣金率 0。
- **方案**：删除该块（1688 商品无法用 Ozon product_id 查佣金），佣金走 store_config/worker 默认。

---

## 三、成本优化（LLM / 生图，直接省钱）

### B1 属性合并批量翻译 ✅
- 现状：`prepare_ozon_upload_node.py:1141-1208` 逐属性循环，每个含中文/拉丁值单独调 `_translate_to_russian_llm`（每次还带 2 次失败重试：310/332）。
- 影响：典型产品 8-13 次 LLM 调用，最坏 20-40 次。
- 方案：收集所有需翻译值 → 一次 LLM 返回全部（分隔符拆回）；失败再逐条兜底。

### B2 call_mxou_chat_api 加重试退避 ✅（审计描述微调：是"新增"而非"加重"）
- 现状：`mxou_api.py:100-106/141-146` 非 200 直接 return None，0 重试；对照生图 API（250-314）有 `range(max_retries+1)` + 固定 sleep(1)。
- 方案：4xx 不重试、5xx/timeout 指数退避 2 次；复用现成 `handle_mxou_429`（见 B3）。

### B3 MXOU 限流接入 ✅
- 现状：`mxou_rate_limiter.py` 全实现（TokenRateLimiter 450 RPM 滑窗 + handle_mxou_429 退避）但**零引用**（grep 仅命中文件自身与 SOURCES.txt）；文档声称"自动集成"与实际不符。
- 注意：`main.py:169` 的 RateLimiter（每 token 每分钟 10 次提交）是外层接口限流，与此无关。
- 方案：`mxou_api.py` 两个入口（chat/image）调 `mxou_acquire(token)`。**与 B4 组合：并发化必须配限流，否则撞 500 RPM 上限。**

### B4 Phase2 生图并发化 ✅
- 现状：`variant_primary_loop_node.py:61` 串行 for 循环；全 graphs/nodes/ 无任何并发库。
- 方案：ThreadPoolExecutor(4) + 限流（B3）。收益最大项（39 变体小时级→分钟级，不改图数量）。

### B5 空参考图跳过生图 ✅（= P1-3）
- 现状：所有 Phase2 节点 `ref_images` 空仍调 API（main:62-69 等）；`detail_gen_node.py:47-49`、`scene_1_gen_node.py:48-50` 注释**写明要跳过但代码没实现**。
- 方案：`if not ref_images: return XxxOutput(xxx=None)`。零风险立省。

---

## 四、性能优化

### E1 进度写 PG 每节点写 ✅
- 证据：`main.py:128` 每次 `asyncio.create_task(_persist_progress())`；触发源 `task_processor.py:50-54` on_chain_start + `progress_logger.py:109-110`。每节点一次，无节流。
- 方案：节流（如 2s 合并窗口）或仅关键节点写。

### E2 cloud_probe import 模块级 discovery 请求 ✅
- 证据：`cloud_probe.py:199` 模块顶层 `_load_path_registry()` → `:153-158` `requests.get(f"{_discovery_api}/workflows", timeout=10)`；默认 `CLOUD_API_BASE=https://worker.mxou.cn`（_const.py:31）→ 每次 import（graph/follow/publish/batch_test）都发 GET。
- 方案：惰性化（首次需要时再加载）+ 结果缓存 + 失败快速失败（timeout 降低/短缓存）。

### E3 cache_set 非原子写 ✅
- 证据：`cache.py:65` `path.write_text(...)` 直接写，无 os.replace/临时文件+rename；读半截文件被 except 吞掉变缓存未命中。
- 方案：临时文件 + `os.replace()`（Windows 锁重试）。

### E4 手写裸 CDP 重复 4 处 ✅
- 证据：`ozon_scraper.py:315/404`、`cli.py:396/407/463`、`batch_test.py:310/318/339`、`ozon_image_search.py:249-291`（半裸）——各自手写协议，消息 id 递增/recv/超时不一致。
- 方案：统一走 `cdp_client.py`（低优先，改动面大，需回归）。

### E5 follow 命令 3-4 次 CDP 连接 ✅（实际 4-5 个 WS）
- 证据：`follow_sell_cloud`（cloud_probe.py:2335）三步各建连接：`ozon_scraper.py:404` 裸 WS 1 个 + `ozon_image_search.py:226/291` 2 个 + `service.py:1585` 1-2 个。
- 方案：同一 CdpConnection 内复用 tab 完成全部步骤。

### E6 discover 每产品新开 CDP ⚠️ 部分属实
- 证据：采集阶段 `ozon_discovery.py:335-374` 单连接复用 ✅；1688 图搜匹配阶段 `match_selected`（420-424 → 866）每候选新建 CdpConnection+CdpTab。
- 方案：`_search_1688_source` 增加 cdp 复用参数。

### E7 batch_test O(n²) + finally NameError ✅
- 证据：`batch_test.py:456-458` 循环内全量覆写 results；`191-225` finally 引用 try 内才赋值的 `follow_result`/`matches`/`best` → 异常时 NameError 掩盖原始异常。
- 方案：内存累积循环后一次写 + finally 用 `locals().get()` 或初始化默认值。

### C1 search_categories 无 TTL 缓存 ✅
- 证据：`ozon_api.py:83-89` `_query_category_tree` 直调 API 无缓存；`:181` search_categories 每次拉整树（~2-5s）；ozon_api.py 无 import cache.py（现成命名空间缓存未用）。
- 方案：`_query_category_tree` 外包 `cache_get/cache_set("category_tree", language, TTL=数小时)`。

### C2 连接复用 ⚠️ 部分已修复
- `ozon_widget.py:261/373` fetch_product_info/competing_sellers 已支持 `cdp=` 复用（discovery 已传）；`ozon_scraper.py:404`、`ozon_image_search.py:226` 仍每次新建。
- 方案：仅处理后两处（可选，跟随 E4/E5）。

### E9 并发上限 ⚠️ 描述修正，bug 属实
- **修正**：代码默认 `MAX_CONCURRENT="10"`（main.py:419），**非 50**；AGENTS.md 文档写默认 50 已过时。
- bug 属实：`main.py:447` `start_workers(num_workers=10)` **硬编码**，不读 MAX_CONCURRENT → MAX_CONCURRENT>10 时实际并发封顶 10；RATE_LIMIT_PER_MINUTE 默认 10 同理。
- 方案：`start_workers(num_workers=max_concurrent)` + 文档同步。

---

## 五、健壮性 / 代码质量

### D2 ozon_post 裸 requests.post ✅
- 证据：`ozon_client.py:63` `requests.post(...)` 非共享 session；`validation_retry_loop.py:1472/1539/1590/2061/2140/2183` 7 处绕过 ozon_post 裸 `session.post`，错误处理不一致（无 log_ozon_api_call、失败仅返回 False）。
- 方案：ozon_post 内部用共享 session；retry_loop 裸调用改走 ozon_post。

### D3 config 错位 ✅
- 证据：`graph.py:83` metadata 声明 `translate_russian_cfg.json` 但 `prepare_ozon_upload_node.py:220-223` 硬编码读 `attributes_llm_cfg.json`；`graph.py:63` 声明 `product_assembly_cfg.json` 但 assemble 读 `category_match_v2_cfg.json`；唯一对齐的是 scene_generation_llm（node:30 读 metadata，与 graph.py:66 一致）。
- 另 E8：`scene_generation_llm_cfg.json:5` 用 `max_completion_tokens` 键，`scene_generation_llm_node.py:62` 读 `max_tokens` → 键被忽略（当前恰好都=2048 所以没暴露，改 cfg 不生效）。
- 方案：metadata 与节点硬编码路径对齐（或删无用 metadata）；cfg 键名统一。

### C4 ProgressLogger 每次读磁盘 ✅
- 证据：`progress_logger.py:57-64` 每次 `__init__` open+json.load `assets/workflow_progress.json`（20+ 节点触发）；`config_path` 参数（41/48）从未被使用；`NODE_ORDER` 静态字典（11-35）需手动与图同步；附带 bug：line 57 `os.getenv("APP_WORKSPACE_PATH")` 重复两次。
- 方案：模块级 lru_cache/惰性单例只读一次 + config_path 生效 + NODE_ORDER 改为从 graph 注册表生成。

### E3/D1 见上（cache 原子写、batch_test）

### D5 chrome_launcher 杀所有 Chrome ⚠️ 部分属实
- 证据：`chrome_launcher.py:200-240` `_kill_chrome_processes` 对全部匹配进程 SIGTERM，无 port 过滤；Electron 已排除（168-176 历史注释）；但普通无 debug 端口 Chrome 窗口仍被杀。
- 方案：kill 前按 `--remote-debugging-port` 过滤，只杀目标实例。

### D4 死代码清理 ✅
- 废弃节点 6 个（全仓库零引用）：`category_lookup_node.py`、`attributes_fetch_node.py`、`attributes_llm_node.py`、`attributes_learning_node.py`、`error_handler_node.py`、`multi_info_gen_node.py`（graph.py:205 注释确认已移除）
- 另：`loop_graph.py`、`utils/image_gen_factory.py`（v0.13 改回中文 inline prompt 后零引用）
- 方案：删除前跑 pytest/ruff 确认无动态 import。

---

## 六、实施建议（分批）

| 批次 | 内容 | 理由 | 验证 |
|------|------|------|------|
| **批次 A：上架正确性**（v0.14.0） | P0-2 / P0-4 / P0-3 / P1-1 / P1-4 / P1-5 / P1-6 / P1-7 / P0-6(需先确认竞品价数据源) | 每单必现，直接影响上架结果与利润 | Worker mock 测试 + 跟卖/单SKU 两管线本地实测（Docker） |
| **批次 B：成本** | B1 / B2 / B3 / B4 / B5 | 直接省钱；B4 必须配 B3 防撞限流 | mock 生图测试 + 变体并发验证 |
| **批次 C：性能** | E1 / E2 / E3 / E5 / E6 / C1 / E9 / C4 | 降低延迟与资源浪费 | skill 命令本地实测 |
| **批次 D：健壮性** | D2 / D3 / E8 / D4 / D5 / E4 / E7 | 代码质量，风险低 | pytest + CI |

**不建议本 PRD 内做**：
- E4 全量 CDP 重构（改动面大，需多场景回归，建议单独立项）
- D4 删除前需确认无 git 历史引用依赖

## 七、需用户确认的点（2026-08-03 已确认）

| 决策点 | 确认结果 |
|---|---|
| 实施范围 | **全部四批**（A 正确性 + B 成本 + C 性能 + D 健壮性），一次发 v0.14.0 |
| P0-6 竞品价数据源 | **widget 拿竞品价**（`ozon_widget.fetch_product_info` 已支持 cdp 复用），写入 envelope `draft.competitor_price` |
| E9 并发默认值 | **30**（4核4G 服务器分析：内存 ~1-1.5GB 安全；I/O 密集 CPU 非瓶颈；外部 API 由 B3 全局限流器 450 RPM 兜底）。⚠️ E9 与 B3 一起落地 |
| 版本号 | **0.14.0**（当前 0.13.0 已 commit 未部署） |

> 说明：E4（CDP 重构）与 D4（删死代码）改动面大，实施时逐个评估回归风险，必要时拆分为独立 PR。

---

## 八、版本记录

- v0.13.0（2026-08-03，`ad1164c`）：属性字典值兜底修复 + 生图提示词回退中文版（已 commit 未部署）
- v0.14.0（本 PRD）：审计修复全部四批


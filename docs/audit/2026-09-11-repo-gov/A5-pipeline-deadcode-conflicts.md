# A5 — 管线拓扑 / 死代码 / 逻辑冲突审计

- 基线：v0.74.0（VERSION 四源一致），仓库 `/Volumes/os/dev/ozon-worker-gov`
- 日期：2026-09-11；审计方式：静态全扫（graph.py / validation_retry_loop.py / nodes/ / main.py / cli.py / compile.py 逐项核对）+ 全仓 grep 引用计数
- 前置审计：`docs/audit/2026-09-09-race-duplication/findings.md`（26 条，域 A-G）——**已修条目（F-A01~04、F-C01~06、F-D01~05、F-E01、F-E3/4/5、F-F01~03、F-B02~04）本报告不重扫**，只做增量

## 结论先行

1. **拓扑面健康，无幽灵节点**：主图 `graph.py` 25 个 add_node + retry 子图 10 节点全部活接线，路由边与 `docs/WORKER-TOPOLOGY.md` 逐条核对一致；隐式调用（4 个后台循环 + MCP 挂载 + newapi 代理）全部有代码内标注。但拓扑文档**正文停 v0.27、增量节停 v0.70**：主图 ASCII 缺 `fetch_back`，v0.71-0.74 四批增量未记（详见 §1.4）。
2. **最大新发现（P1）：variant_v2 真值链「半接线」**——v0.74 宣称的池重量真值能力**生产不可达**：采集端 `fetch_variant_truth` 零生产调用方、`_giveback_metrics` 的 `variant_payloads` 参数无调用方传值、worker 的 `needs_variant_sync` 补采指令零消费方——三段断链导致 `SkuMetricsPool.variant_payload` **没有任何生产写者**，消费端 `_apply_pool_variant_weight`（cloud_probe.py:3088）恒空转（§3 X-01）。
3. **死代码 11 组确认**（§2）：2 个死配置文件、1 个整模块死代码、1 个死编译模块、4 组断链 setter/getter、6 个零接线错误码、3 个死 shelf 端点、8 个死 state 模型。每条均给出 删除/保留+接线/降级文档化 建议。
4. **逻辑冲突增量 6 条 + 性能矩阵 1 张**（§3）：sku_metrics_pool 写侧 SELECT-then-INSERT 非原子（复刻 F-C06 已修模式，P2）；`report_seller_sync` 同步阻塞 discover 主流程最坏 ~250s（P2-低）；域 B 增量补扫 3 条（X-03/X-06/X-07），无新增高严重。
5. 上一轮 findings.md 的「域 B——待扫描」占位节**已在第二波回填**（F-B01/B02/B03/B04 在案），本报告对域 B 只补 v0.74 新代码增量，无空洞遗留。

---

## §1 全链路拓扑现状核对

### 1.1 主图节点表（graph.py 逐行核对，25 节点全活）

| # | 节点 | 输入 model | 输出 | 外部依赖 | 状态 |
|---|------|-----------|------|---------|------|
| 1 | auth | AuthInput | AuthOutput | Supabase tokens 表、api.mxou.cn 余额、Ozon /v1/seller/info（可选） | 正式 |
| 2 | check_quota | 无注解（读全 state）→ OzonUploadOutput | OzonUploadOutput | Ozon 配额 API（ozon_check_quota） | 正式（auth 后早期闸） |
| 3 | ingest | IngestInput | IngestOutput | 无 | 正式（v0.73 空标题 fail-fast 闸） |
| 4 | follow_sell_import | **GlobalState（未过滤）** | dict | Ozon schema / import-by-sku / import-info 轮询 | 正式（follow 分支） |
| 5 | pricing | PricingInput | PricingOutput | 物流费率（Excel/PG）、佣金缓存表、汇率（12.0 兜底，A3 已立案） | 正式 |
| 6 | assemble_ozon_product | GlobalState 系组装 | — | PG 类目树、Ozon schema/字典 API、MXOU LLM | 正式（llm_cfg=category_match_v2） |
| 7 | scene_generation_llm | SceneGenerationInput | SceneGenerationOutput | MXOU LLM | 正式 |
| 8 | visual_vars_llm | VisualVarsInput | VisualVarsOutput | MXOU LLM（纯文本） | 正式 |
| 9 | white_bg_gen / multi_angle_gen | WhiteBg/MultiAngleInput | 各 Output | MXOU 生图 180s | 正式（Phase1 恒开） |
| 10 | variant_primary_loop | VariantPrimaryLoopInput | VariantPrimaryLoopOutput | MXOU 生图 | 正式（多 SKU 路径；单 SKU 内部跳过） |
| 11 | main_image_gen | MainImageInput | MainImageOutput | MXOU 生图 | 正式（单 SKU 路径） |
| 12 | detail_gen | DetailImageInput | DetailImageOutput | MXOU 生图 | 正式（DEFAULT_PLAN 恒开） |
| 13 | scene_1_gen | — | — | MXOU 生图 | 正式（DEFAULT_PLAN 含 scene_1） |
| 14 | scene_2_gen / scene_3_gen / social_proof_gen / comparison_gen | — | — | MXOU 生图 | **config 门控默认关**（`utils/image_gen_plan.DEFAULT_PLAN` v0.64 精简 10→5；槽位保留于 ALL_SLOTS，经 `config.configurable.image_gen_plan` 可重开） |
| 15 | prepare_ozon_upload | PrepareOzonUploadInput | PrepareOzonUploadOutput | MXOU LLM 翻译、COS、Ozon | 正式（llm_cfg=attributes） |
| 16 | ozon_validate | OzonValidateInput | OzonValidateOutput | 本地校验 + 图片探测（网络故障降 warning） | 正式（含 v0.73 `LOCAL_TITLE_CATEGORY_MISMATCH` 独立分支） |
| 17 | ozon_upload | OzonUploadInput | OzonUploadOutput | Ozon /v3/product/import、find_product_by_offer | 正式；`UPSERT_BY_OFFER` 为**代码常量门控**（ozon_upload_node.py:25，env 不可关——见 D-10 文档漂移） |
| 18 | ozon_status | OzonStatusInput | OzonStatusOutput | Ozon import/info 轮询（≤10 次×3s） | 正式；min_price 补送（v0.73 恢复）挂此节点 |
| 19 | validation_retry_wrapper | ValidationRetryWrapperInput | ValidationRetryWrapperOutput | Ozon + MXOU LLM | 正式（子图见 §1.2） |
| 20 | fetch_back | FetchBackInput | FetchBackOutput | Ozon /v4 product info | 正式（PR-0 回读 diff）——**拓扑文档 ASCII 缺此节点** |
| 21 | learning_record | LearningRecordInput | LearningRecordOutput | PG 写库、Ozon prices 佣金回填 | 正式（moderation_status 已补声明 v0.73-W8） |

路由函数（route_after_auth / route_after_early_quota / route_after_follow_sell_import / route_after_ingest / route_after_pricing / route_after_assemble / should_upload_after_validate / should_handle_error / should_learn_after_repair）9 个与文档条件表一致；`route_after_assemble` 阻断阈值已常量化 `MIN_CONF_BOX`（v0.73）。langgraph 刻意不挂 retry_policy（graph.py:447-453 注释，永久错误 fail-fast 语义）——有意决策非缺漏。

**Input 纪律备注**：`follow_sell_import` 与 `check_quota` 未用过滤型 Input model（读全 state），功能不受 langgraph channel 过滤影响，但与「节点要读的字段必须声明进 Input」的 v0.66 纪律不一致——仅风格问题，不立案。

### 1.2 retry 子图（validation_retry_loop.py:3770-3863，10 节点全活）

parse_error → classify_error → error_repair_llm / repair_prepare / repair_pricing / repair_dimensions → revalidate → reupload → recheck_status → final_result。全部有注册、有消费；错误映射表（WORKER-TOPOLOGY §3）抽查 DESCRIPTION_DECLINE / ML_INCORRECT_VOLUME_WEIGHT / LOCAL_TITLE_CATEGORY_MISMATCH（v0.73 新增，文档映射表未记）均在代码可定位。

### 1.3 隐式调用清单（验收标准：无未标记的隐藏节点 → **通过**）

| 隐式面 | 位置 | 周期/条件 | 状态标注 |
|---|---|---|---|
| 启动僵尸清理 | main.py lifespan（:500s 段） | 启动一次；`SKIP_ZOMBIE_RECOVERY` 可跳 | 有注释 |
| `_periodic_task_cleanup` | main.py:1093（:563 启动） | 60s；stale reset / 30d completed 清 / draft_submissions 对账（F-D04 修复）/ task_image_cache 7d 清 | 有注释；instance_lock("periodic_cleanup") 多副本安全（E-4） |
| 店铺同步 | main.py:572-591 | jobs 版 5s 扫描（SKIP LOCKED 天然安全）或 legacy 15min 轮询（instance_lock） | `SKIP_STORE_SYNC` / `STORE_SYNC_JOBS_ENABLED` 门控，有注释 |
| 指标聚合 | main.py:594 | `METRICS_AGG_INTERVAL_SECONDS`（10min）；instance_lock | 有注释 |
| 任务消费循环 | main.py:598 `start_workers(MAX_CONCURRENT)` | 常驻 | 有注释 |
| 远程 MCP | main.py:678-681 挂 /mcp | `MCP_ENABLED=0` 关；fastmcp 缺失自动降级；22 工具与 docs/MCP-SERVER.md 一致 | 有注释 |
| newapi 代理 | main.py:3079-3084 | catch-all `/api/{path}` 注册于 v1 **之后**（顺序正确） | 有注释；见 §2 D-06 死面判定 |
| 中间件 | — | **无 add_middleware**（鉴权 per-route 依赖 + RateLimiter；MCP 走 ASGI Bearer） | 与文档一致 |

缺口：`MCP_ENABLED` 与 `SKIP_*` 三兄弟不在 `deploy/.env.example`（ops 发现面差，见 D-10）。

### 1.4 文档漂移（WORKER-TOPOLOGY.md）

更新日期标注 v0.70，正文 v0.27 口径 + 头部增量节。未记增量：**v0.71** attr_value_sanitize `cap_attribute_values` 四出口闸；**v0.72** 字典三桶策略（assemble/retry/warm 读写字径变化）；**v0.73** volume_weight_guard 兜底、`LOCAL_TITLE_CATEGORY_MISMATCH` 分支、ingest 闸、min_price 补送恢复、`MIN_CONF_BOX` 常量化；**v0.74** 数据池（REST 面，不改图拓扑）；主图 ASCII 与条件表均缺 `fetch_back`。建议按既有「增量摘要」模式补一节（A1 文档审计可合并处理）。

### 1.5 Skill 25 命令状态全景

SKILL.md §2 列 25 项 = cli.py 23 个 add_parser 子命令 + 2 个独立脚本（batch_test.py / migrate_profile.py），数目闭合无漂移（F-G01 修复未复发）。

| 命令 | 状态 | 备注 |
|---|---|---|
| check / set_store / set_token / set_ak / list_stores | 正式；纯本地 | check 部分 prank 需 Chrome |
| graph / follow | 正式；需本机 Chrome + 凭证 | 提交 worker |
| discover / discover-task / discover-multi / search | 正式；需 Chrome（aibuy 直调可免部分）；`--auto-submit` 需凭证 | 双出口纪律 |
| image_search / probe / get_ak / import-cookies | 正式；需 Chrome（image_search 默认 aibuy 免浏览器） | |
| queries / seller / category | 正式；queries/seller 需 seller 会话（30min 磁盘缓存兜底，F-A04） | |
| query / report / session-sync | 正式；需 MXOU token/凭证 | |
| update | 正式（lib/updater.py，test_updater.py 覆盖） | **非废弃** |
| cleanup | 正式（test_cleanup.py 覆盖） | **非废弃** |
| batch_test.py | 正式（10+ 测试文件覆盖） | 独立脚本 |
| migrate_profile | **疑似废弃/一次性**：非 cli 子命令，独立脚本，**零专属测试**（仅 check_doc_sync.py 做文档一致性校验），用途是历史 profile 路径迁移 | 建议保留但从 SKILL.md 命令表移到「一次性工具」注记 |

---

## §2 死代码与未启用逻辑清单

> 引用计数口径：全仓 grep（worker/src + tests + scripts + webui + pounding-mcp + skill）。建议分三档：**删除** / **保留+接线** / **降级文档化**。

| ID | 对象 | 证据（file:line） | 引用计数 | 建议 |
|---|---|---|---|---|
| D-01 | 错误码 6 个零接线：`TASK_NOT_CANCELLABLE` / `TASK_SUBMIT_FAILED` / `SERVICE_UNAVAILABLE` / `TOKEN_MISSING` / `TOKEN_EXPIRED` / `TOKEN_DISABLED` | api/errors.py:23-42；cancel 实际返 200+`{status:failed}`（main.py:1958-1985），不走 409 | 除 errors.py 外全 0（docs/API-OVERVIEW.md:118 已注明 TOKEN_* 为「映射保留位」） | TOKEN_* 三保留（文档已注明）；`TASK_NOT_CANCELLABLE` **保留+接线**（http_cancel_task 对非 pending 改返 409 error_response，客户端可编程处理）；TASK_SUBMIT_FAILED / SERVICE_UNAVAILABLE 删或接线 |
| D-02 | state.py 死模型 8 个：CategoryLookupInput/Output、AttributesFetchInput/Output、AttributesLLMInput/Output、AttributesLearningInput/Output、VariantLoopInput | graphs/state.py:352-544、772 | 全部 0（含 tests）——旧 4 节点管线被 assemble 替代后的遗骸。⚠️ `VariantLoopOutput` 是**活**的（variant_primary_loop_node.py:11），勿误删 | **删除**（test_compile 式不变式可加防回潮） |
| D-03 | `utils/error_classifier.py` 整模块死 | 其 3 个导出（classify_validation_error/get_retry_count/increment_retry_count）全仓 0 调用；main.py:196 的 ErrorClassifier 来自 runtime/helpers.py:73（另一实现）；其 fix_path 指向已不存在的 retry_detail_gen 等旧拓扑节点 | 模块级 0 import | **删除**（含可能的 tests 引用核查） |
| D-04 | 死配置文件 2 个：`config/category_match_llm_cfg.json`、`config/product_assembly_cfg.json` | worker/src 全仓 0 引用（活配置 6 个 *_cfg + imagegen/image_prompts/attr_disambiguation/synonyms/seed 均有消费） | 0 | **删除**（bind mount 热加载面少两个幽灵文件） |
| D-05 | shelf_router 3 个 bulk 端点死面：POST /products/bulk-prices、/bulk-stocks、/bulk-archive | routes/shelf_routes.py:77-93；现行消费方为 store_actions_routes（同名操作+operation_log，经 MCP run_store_action）；唯一引用者是**已归档的 webui-archive**（pricing.tsx:100）；现行 webui 只用 GET /products（ProductsPanel.tsx:150）与 GET /products/ozon（:145） | 3 | **降级文档化**：标 deprecated 指向 /stores/{id}/actions，或直接删（openapi 快照同步重生成） |
| D-06 | newapi_proxy_router 现行零消费 | routes/newapi_proxy_routes.py；webui 登录/订阅/钱包全走 worker 原生 /mxou/*（client.ts:56-57、KeysPanel、mxou_login_service），`/api/user|subscription` 在 webui/webui-archive/pounding-mcp/skill 均 0 命中 | 0（现行面） | **保留+文档化**：注释已写背景（webui 同源回退位）；建议收窄 `_NEWAPI_PREFIXES`（"status"/"notice"/"pricing"/"log"/"group" 等泛前缀一旦 webui 未来加 /api/xxx 路由会被误代理到 api.mxou.cn） |
| D-07 | local_db_manager 断链 4 组：`set_exchange_rate`（**A3 已立案 G-01，不重复**）、`get_logistics_cost`（:146）、`get_gateway_task`/`set_gateway_task`（:230/:446）、`get_category_cache`（:121，写侧 set_category_cache 活） | 引用计数 0/0/0/0/0（get_exchange_rate 有 2 个活调用） | 5 | gateway_task 对 **删**（PG ozon_product_tasks 已替代 gateway 语义）；get_logistics_cost **删或接** logistics_quote；get_category_cache **接线**（读侧复活 SQLite 缓存语义）或删 |
| D-08 | `skill/scripts/lib/ozon_seller.py` 死编译模块 | 生产零 import（仅 tests/test_premium_coverage.py 与 test_compile_lists.py:54）；仍在 COMPILE_FILES 4 平台编译（compile.py:38） | 0（生产） | **删除或降级**：佣金/属性链已迁 worker（commission_resolver/ozon_client）；若保留 premium spoof 能力则移 AUX_FILES 并同步 test_compile_lists；每版本 4 平台编译浪费构建时间 |
| D-09 | variant_v2 采集端断链（=§3 X-01，此处登记为死链） | fetch_variant_truth（ozon_widget.py:918）零生产调用；`_giveback_metrics` 的 variant_payloads 参数（ozon_discovery.py:652）无调用方传值；`needs_variant_sync`（worker sku_metrics_pool_service.py:104）零消费 | 3 段断链 | **保留+接线**（P1，详见 X-01 修复方向） |
| D-10 | env 门控登记缺口 | `.env.example` 缺：`MCP_ENABLED`（docs/MCP-SERVER.md:15 有）、`SKIP_ZOMBIE_RECOVERY`/`SKIP_STORE_SYNC`/`SKIP_FAILED_REVIVE`、`METRICS_AGG_INTERVAL_SECONDS`、`ORDER_CACHE_RETENTION_DAYS`/`STORE_METRICS_RETENTION_DAYS`；skill 侧 `METRICS_POOL_REPORT/QUERY` 仅存于 PLAN-data-pool-parity-v1.md（归档后失锚）。**文档反向漂移**：AGENTS.md/CHANGELOG 称 UPSERT_BY_OFFER「可关」，实为代码常量（ozon_upload_node.py:20-25），env 不可关 | — | **降级文档化**：.env.example 补齐 + METRICS_POOL_* 写进 SKILL.md 或 CONTRACT；UPSERT_BY_OFFER 措辞改为「代码常量」或补 env 化 |
| D-11 | pounding-sidebar 构建链「半活」 | package.json/tsdown.config.ts 在库；4 个 CI workflow（ci/cd/build-skill/skill-distribute）**零引用**；构建仅手动 | CI 0 | **降级文档化**：README 标注「手动构建、不在 CI」；或加 optional CI job。v0.74 仍在库符合「恢复后保留」口径，无代码死面 |

---

## §3 逻辑冲突与性能风险增量（v0.68-v0.74 新代码）

> 条目格式：域 | 描述 | 证据 | 复现场景 | 建议修复 | 探针方案。域字母沿用 2026-09-09 审计（B=skill 并发/组装、C=worker 任务生命周期、E=缓存/共享表、F=能力重复）。

### X-01 【F/死链·P1】variant_v2 真值链三段断链——池 variant_payload 无生产写者
- **域**：F（能力重复/死链，兼 D-09）
- **描述**：v0.74「毛子移植双件」只接了消费端。①采集端 `fetch_variant_truth`（search-sku-base → create-bundle-by-variant-id）无任何生产调用方；②上报端 `_giveback_metrics(metrics_items, variant_payloads=None)` 的第二参数没有调用方传值（cli.py:3558 与 ozon_discovery.py:802 两处调用均只传 map 行）；③worker 读侧补采指令 `needs_variant_sync`（计算+返回）在 skill 侧零消费——discover 不按它触发补采。结果：`SkuMetricsPool.variant_payload` 列恒 NULL → 消费端 `_apply_pool_variant_weight`（cloud_probe.py:3088，信封重量真值覆盖 + `weight_from_pool_variant` 标记）**生产永不可达**，ML_INCORRECT_VOLUME_WEIGHT 源头缓解实际未生效。
- **证据**：skill/scripts/lib/ozon_widget.py:918；skill/scripts/lib/ozon_discovery.py:652-680、802；skill/scripts/cli.py:3558；worker/src/services/sku_metrics_pool_service.py:104；skill/scripts/cloud_probe.py:3088-3125。测试仅 test_variant_truth_chain.py / test_premium_coverage.py 直测函数本身，测不出「无人调用」。
- **复现场景**：跑任一 discover 全流程 → 查 worker `sku_metrics_pool` 表 → 所有行 variant_payload IS NULL。
- **建议修复**：在 `_enrich_with_seller_metrics` 定稿钩子（ozon_discovery.py:1134 附近，`_giveback_metrics` 同点位）对 stale/缺失 variant 的 sku 调 `fetch_variant_truth`（限流+复用 seller tab）→ 以 `{sku: payload}` 传给 `_giveback_metrics`；skill 读侧对 `needs_variant_sync` 行触发同一补采；或诚实降级——从 CHANGELOG/契约把 variant_v2 标「预留未启用」。
- **探针**：单元级：monkeypatch fetch_variant_truth 断言被 discover 主流程调用；集成级：真实 discover 后 SQL `SELECT count(*) FROM sku_metrics_pool WHERE variant_payload IS NOT NULL` > 0。

### X-02 【E/race·P2】sku_metrics_pool 写侧 SELECT-then-INSERT 非原子 + 归因数组丢更新
- **域**：E（共享表并发语义）
- **描述**：`upsert_seller_sync_items` 先 `query().filter_by(sku=).one_or_none()` 再 add/改（sku_metrics_pool_service.py:51-66）——并发两贡献者同提新 sku：后者撞 `uq_sku_metrics_pool_sku`（model.py:1292-1294）→ IntegrityError → 路由兜底 except 把它当「畸形 item」返 **422**（analytics_routes.py:348-352），该请求整批（≤12 行）全丢；且客户端 fire-and-forget 无重试 → 静默数据损失。已提交行的 `source_company_ids/contributed_by_token_ids` 是 Python 内存 read-modify-write（_cap_append 后整体回写）——并发归因丢一方（与 F-E01 同型，那边已改 SQL 原子表达式）。
- **证据**：worker/src/services/sku_metrics_pool_service.py:29-32、51-66；routes/analytics_routes.py:342-354；model.py:1292-1294。
- **复现场景**：asyncio 两客户端并发 POST /analytics/seller-sync 同含新 sku → 断言一 200 一 422 且 422 方整批丢失（当前即如此 → confirmed）。
- **建议修复**：对齐本仓已修先例（F-C06/F-E01）：INSERT 用 `ON CONFLICT (sku) DO UPDATE`，归因数组改 SQL 侧 `array/jsonb` 原子追加（或 `pg_advisory_xact_lock(hashtext(sku))`）；IntegrityError 单独捕获映射 409/200-partial，不落 422「畸形 item」误导排查。
- **探针**：PG 集成测试双会话并发 upsert 同 sku，断言零异常零丢失 + 归因恰好 2 条。

### X-03 【B/性能·P2-低】report_seller_sync 同步阻塞 discover 主流程
- **域**：B（skill 并发/组装——对上轮域 B 增量补扫）
- **描述**：「fire-and-forget」语义只是「不抛错」，执行仍是**同步**：discover 主流程里 `_giveback_metrics` → `report_seller_sync` 逐批 POST，timeout=10s（metrics_pool_client.py:63-77）。N=300 候选任务 map 行多时 25 批，worker 挂起（不拒连）最坏 250s 纯阻塞。对比：信封关键路径的查池已收紧 3s（cloud_probe.py:3106），上报侧未对齐。
- **证据**：skill/scripts/lib/metrics_pool_client.py:63-77；ozon_discovery.py:802（主流程内联调用点）。
- **复现场景**：本地 worker 加 30s 延迟代理 → discover 单轮耗时增加 ≈ 批数×10s。
- **建议修复**：`report_seller_sync` 丢后台线程（daemon Thread / concurrent.futures 单飞）或 timeout 收紧 3s + 批间不重试；保持「绝不 raise」纪律。
- **探针**：fake worker 端 sleep(10) → 计时 discover map 定稿到候选输出的墙钟差。

### X-04 【E/性能·P3】query_sku_metrics 读侧 N+1
- **域**：E
- **描述**：`query_sku_metrics` 对每行调 `_category_name_zh`（sku_metrics_pool_service.py:102 → 70-82），≤50 行 = 每请求最多 50 条独立 category_tree_nodes 查询；discover 批量查池放大。
- **建议修复**：收集非空 (dc,tp) 去重后一条 `IN` 查询回填；或 category_name_zh 落列（写入时归一）。
- **探针**：SQL 计数器（echo=True）断言单请求语句数 ≤3。

### X-05 【F/覆盖缺口·P3】长尾 SKU 永不进池
- **域**：F（能力重复/数据面）
- **描述**：贡献只覆盖 bestseller-map 命中行（ozon_discovery.py:800-803）；`still_missing` 的逐 SKU `fetch_sales_analytics` 结果（:818-824）只富化候选、不上报池——池分母永远偏向畅销榜，长尾商品读池恒 miss，补采指令（needs_sales_sync）也只对已在池的行有意义。
- **建议修复**：per-SKU 分支收获后追加一次 `_giveback_metrics(per_sku.items())`（同 fire-and-forget 纪律，注意 X-03 先修）。
- **探针**：构造 map-miss sku → discover 后断言池出现该行。

### X-06 【B/观察】CSP 剥除作用于用户自有 tab
- **域**：B（skill 外部交互）
- **描述**：`_ensure_ozon_tab` find_tab 命中用户已开 ozon.ru tab 时：release 防远程关页（好），但仍对该 tab `set_bypass_csp()` + `navigate(target_url)`（ozon_widget.py:114-128）——用户正在看的页面被导航走是既有行为，v4.2 又叠加 CSP bypass 页面标志。风险面：①用户感知突兀；②反爬指纹侧「CSP bypass + CDP 自动化」同框出现（Page.setBypassCSP 是 SiteIsolation 级 flag，风控理论上可探测）。`_tab_for_seller`（:895-917）同模式。
- **建议修复**：观察级；可考虑命中用户 tab 时只读不导航（ seller 侧已有该纪律注释，www 侧未对齐）。
- **探针**：手工在工具 Chrome 开商品页 → 跑 discover → 观察该 tab 是否被导航 + DevTools Page.getFrameTree 验证 bypass 状态。

### X-07 【B/观察】CHIPS 双读 longer-wins 启发式与 1688 侧单读不一致
- **域**：B
- **描述**：ozon 侧 `_fetch_seller_session_cookies` 双读合并「同名取更长值」（ozon_seller_analytics.py:996-1001）——无时间戳比较：若非分区 cookie 已轮换更新但更短，会被旧分区长值覆盖（abt_data 场景理论可达）；1688 侧 cookie 读仍单读 Network（ozon_image_search.py:657），若 1688 未来上 CHIPS 会复现 abt_data 同病。
- **建议修复**：低优先登记；可在合并时优先取带 partitionKey 者仅限 `abt_data` 白名单（ozonAI 同款），非泛化 longer-wins。
- **探针**：构造同名长短值双 cookie 桩 → 断言合并结果与预期。

### X-08 【C/边界·P3】池重量真值与双重兜底链的口径登记
- **域**：C（权重/尺寸链）
- **描述**：variant clamp [10,200_000]g（cloud_probe.py:3114-3118）与 worker normalizer 10g floor、volume_weight_guard 0.40 密度兜底衔接无硬冲突（=10g 边界两侧一致放行），但：①池真值覆盖 draft.weight 后，worker 密度兜底仍可能再抬重 → v0.73 已知「抬重后定价不重算」同样适用于该路径；②`_assemble_discovery_meta`（:3222）在覆盖之后组装，若利润/运费展示取 candidate.weight_g（CDP 值）则与 draft.weight（池真值）分叉——展示口径差。两处均为低影响登记项，防后续排查困惑。
- **建议修复**：discovery_meta 组装前若发生覆盖，给 meta 追加 `weight_source=pool_variant` 标记；长期项同 v0.73 已知问题（抬重重算定价）。
- **探针**：构造 variant_payload.weight_g 与 candidate.weight_g 差异用例 → 断言 discovery_meta 与 draft.weight 关系可解释。

### X-09 【C/边界·P3】sku_key 去重粒度与 9048 前缀语义分叉
- **域**：C
- **描述**：sku_key 只含 `user:store:item_id/ozon_product_id`（main.py:1804-1810），9048 offer 前缀含 `supplier|title` hash（prepare_ozon_upload_node.py:2675-2679）→ 同一 1688 item、不同供应商/标题的两个信封：worker 去重视为同 SKU（并发第二个 409），而 Ozon 侧本会开两张卡（不同 9048 不并卡）。去重比平台并卡更激进；因唯一索引只锁 pending/running，终态后即放行，实际影响限于并发窗口。9048 hash 只用确定性原始字段、不用 LLM 译名（:2670 注释）——无拆卡回归。
- **建议修复**：登记为已知边界；若要精确对齐可把 sku_key 加 hash 后缀，代价是去重面变窄，不建议现在做。
- **探针**：同 item 双 supplier 双并发提交 → 断言第二单 409（现行）并留档。

### X-10 【性能矩阵】外呼超时/资源回收盘点（结论：无裸奔残留）
| 链路 | 超时 | 重试 | 回收 |
|---|---|---|---|
| MXOU chat（LLM） | 90s（mxou_api.py:130） | 429 退避；5xx/timeout ×2 | — |
| MXOU 生图 | 180s（:406，fallback 90s :477） | 同上 | — |
| Ozon 全部（v0.74 收敛后） | ozon_post 统一 + tenacity | typed errors 分类 | 裸直发仅剩 ozon_client 自身（F-F01） |
| store_sync 外呼 | 全部 30s（store_sync_service.py 161/234/256/321/370/505/796/848） | job 级退避 env | zombie_reset JOB_TIMEOUT_MINUTES |
| metrics_pool 上报/查池 | 10s / 10s（信封路径 3s） | 无（有意） | fire-and-forget 吞异常 |
| newapi 代理 | (5,60)s（newapi_proxy_routes.py:47） | 无 | 线程池 run_in_threadpool |
| CDP cookie/variant 采集 | _recv 10s | 无 | finally close（ozon_seller_analytics.py:979-983、ozon_widget.py:945-952） |

结论：X-03 是矩阵中唯一阻塞面缺口；其余无资源泄漏/无超时缺失。

---

## §4 汇总（P0-P3）

| 级别 | 条目 | 一句话 |
|---|---|---|
| **P0** | 无 | — |
| **P1** | X-01/D-09 | variant_v2 真值链三段断链，v0.74 宣称能力生产不可达（接线 or 如实降级标注） |
| **P2** | X-02 | sku_metrics_pool 写竞态：并发同 sku → 422 误标 + 整批丢失 + 归因丢更新（ON CONFLICT 化） |
| **P2** | X-03 | report_seller_sync 同步阻塞 discover 主流程，最坏 ~250s（线程化/收紧 timeout） |
| **P2** | D-01（部分） | TASK_NOT_CANCELLABLE 接线到 cancel_task（409 化）或删 |
| **P2** | D-05 | shelf bulk 3 端点死面（唯一消费方是已归档旧 webui）——deprecated 标注或删 |
| **P2** | D-10 | UPSERT_BY_OFFER「可关」文档漂移（实为代码常量）+ env 登记缺口（MCP_ENABLED/SKIP_*/METRICS_POOL_*） |
| **P3** | D-02/D-03/D-04/D-07/D-08 | 死模型 8 个 / error_classifier 整模块 / 死配置 2 个 / local_db_manager 断链 4 组 / ozon_seller.py 死编译模块——批量清理一个 PR |
| **P3** | D-06/D-11 | newapi_proxy 零现行消费（收窄前缀+文档化）；pounding-sidebar 无 CI（README 标注） |
| **P3** | X-04/X-05/X-06/X-07/X-08/X-09 | N+1 查询 / 长尾不进池 / CSP-用户 tab / CHIPS longer-wins / 池重量口径登记 / sku_key×9048 边界 |
| **P3** | §1.4 | WORKER-TOPOLOGY.md 增量节停 v0.70：补 v0.71-0.74 四批 + fetch_back 入图 |

**域 B 增量补扫说明**（对应上轮 findings.md 占位节）：B 域已回填 F-B01~B04；本轮对 v0.74 新代码补 X-03/X-06/X-07 三条（同步阻塞、CSP 交互、CHIPS 启发式），均中低严重，B 域无新增高严重问题。已修条目（上轮 26 条中 confirmed→已修复 17 条）未重扫。

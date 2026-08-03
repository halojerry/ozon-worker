# Changelog

## [0.14.0] - 2026-08-03

> 8·26 审计遗留修复四批全量实施（PRD: `docs/PRD-audit-fixes-20260803.md`，31 项验证 27 属实 + 4 部分属实）。

### 批次 A：上架正确性（P0/P1，每单必现）

- **P0-2 跟卖属性链路接通**：`_assemble_follow_sell` 消费 follow_sell_import 输出的 final_attributes（统一 attribute_id 键）；删除硬编码 `{"id": 126745801}`（字典值ID被当属性ID）假条目；兜底最小属性集。修复跟卖商品属性全部丢失、品牌/原产国不生效
- **P0-4 单SKU/跟卖/发现漏运费**：`cloud_probe.py` 删 `if len(variants)>1` 守卫，`_collapse_variants_to_single` 无条件调用（内部兼容 0/1/N），单SKU 采购成本含国内运费 freightCny
- **P0-3 GlobalState 补字段**：加 `dictionary_values` / `match_confidence`（动态字典选色 + 类目低置信度阻断复活）
- **P0-6 竞品价链路**：Skill 抓 Ozon 竞品售价（scraper price 字段）→ `draft.competitor_price` → worker `follow_sell_import` 优先读（不再误用 1688 采购价当竞品价）
- **P1-1 quantity 变体定价**：改用 `pricing_info.variant_prices`（含利润/佣金/物流），不再用 1688 裸采购价上架
- **P1-4 定价失败阻断**：pricing 异常返回 `[PRICING_FAILED]` 标记；删 assemble ¥1000/¥1500 兜底；graph 加 `route_after_pricing` 条件边阻断
- **P1-5 parse_error 合并读 validation_errors**：本地校验错误转结构化错误 + 关键词分类，不再退化为 UNKNOWN 通用 LLM 修复
- **P1-6 登录预检条件化**：service.py `_check_1688_login_live` 加 `login_detected` 守卫
- **P1-7 佣金死代码删除**：移除用 1688 item_id 查 Ozon `/v5/product/info/prices` 的恒空块

### 批次 B：成本优化（LLM / 生图）

- **B1 属性合并批量翻译**：含中文/拉丁属性值一次 LLM 调用批量翻译（分隔符拆回，失败逐条兜底），省 40-60% LLM 调用
- **B2 call_mxou_chat_api 重试退避**：4xx（除429）不重试、429 指数退避、5xx/timeout 退避重试 2 次
- **B3 MXOU 限流接入**：`mxou_rate_limiter`（450 RPM 滑窗 + 429 退避）接入 chat/image 两入口（原死代码）
- **B4 变体主图并发生成**：`variant_primary_loop` ThreadPoolExecutor(4) 并行（配 B3 限流），39 变体小时级→分钟级
- **B5 空参考图跳过生图**：7 个 Phase2 节点空 ref 直接返回 None（detail/scene×3/comparison/social/main 连原始图都没有时）

### 批次 C：性能

- **E1 进度写 PG 节流**：每任务 2s 合并窗口（旧每节点异步写一次）
- **E2 cloud_probe import 惰性**：discovery 网络请求移出模块顶层（旧每次命令 +10s），惰性 + 进程级缓存
- **E3 cache 原子写**：临时文件 + os.replace（并发 CLI 不再写坏 JSON）
- **C1 类目树 TTL 缓存**：ozon_api `_query_category_tree` 24h 命名空间缓存（旧每次搜索重拉整树 ~2-5s）
- **E6 discover CDP 复用**：`search_by_image_cdp` 加 `conn` 参数；`match_selected` 批量图搜共享单连接（旧每候选新建）
- **E9 并发上限**：num_workers 联动 `MAX_CONCURRENT`（默认 **30**，4核4G I/O 密集安全值；旧硬编码 10）
- **C4 ProgressLogger 去重读**：进度配置模块级缓存只读一次 + `config_path` 参数生效 + 修复重复 getenv

### 批次 D：健壮性 / 代码质量

- **D2 ozon_post 共享 session**：改用 `utils.http_session` 连接池（旧裸 requests.post 每次新建 TCP）
- **D3 config 错位对齐**：graph.py metadata llm_cfg 对齐节点实际读取（assemble→category_match_v2_cfg、prepare→attributes_llm_cfg）
- **E8 cfg 键名统一**：`scene_generation_llm_cfg.json` `max_completion_tokens` → `max_tokens`（旧键被忽略，改 cfg 不生效）
- **D5 chrome_launcher 端口过滤**：仅杀带 `--remote-debugging-port` 的实例，不误杀用户日常 Chrome
- **E7 batch_test**：finally `follow_result/matches` 用 `locals().get()` 防 NameError 掩盖原异常；进度文件每 5 条增量写 + 循环后全量写（旧 O(n²)）
- **D4 死代码清理**：删除 6 个废弃节点（category_lookup/attributes_fetch/attributes_llm/attributes_learning/error_handler/multi_info_gen）+ `loop_graph.py` + `image_gen_factory.py`
- **D4 死代码清理**：删除 6 个废弃节点（category_lookup/attributes_fetch/attributes_llm/attributes_learning/error_handler/multi_info_gen）+ `loop_graph.py` + `image_gen_factory.py`
- **C4 NODE_ORDER 同步**：progress_logger 节点顺序字典同步真实图节点集
- **E4 裸 CDP 统一封装**：4 处手写 websocket/CDP → `cdp_client`（`scrape_ozon_product_via_cdp` 全量重构 / `cli.py check` 1688+Ozon 检查 / `batch_test.py` 前置检查）；`CdpTab.close(close_remote=)` + `CdpConnection.release()` 新增（复用用户已有 tab 不误关远程）
- **E5 follow_sell_cloud 连接共享**：Step2（抓 Ozon）+ Step3a（1688 图搜）共享一个 `CdpConnection`（省 2-3 个冗余 WS）；envelope 链路（probe_1688_page 会话引导）保持独立更安全

### 验证

- py_compile 全量 ✅；`test_attribute_fill_v013.py` 8/8 ✅；`test_audit_a_fixes.py` 5/5 ✅（P1-4 阻断路由 + P0-2 跟卖属性消费/兜底）；集成验证 ✅；mock 全流程 13/13 ✅；graph 模块导入 ✅（删死代码无残留）

## [0.13.0] - 2026-08-03

### Fixed
- **字典属性手填文本兜底移除（Ozon 上传报错根因）**：字典值未匹配时不再写 `dictionary_value_id=0 + 中文文本`（Ozon 只接受列表中的 dict_id，手填触发「属性值不正确，请从列表中选择一个属性值」——用途/商品颜色/风格报错来源）。三处统一为「未匹配 → 跳过属性，由 `/values/search` 修正或补默认字典值」：
  - `assemble_ozon_product_node.py`：`_build_items_deterministically` 字典未匹配跳过 + `_validate_and_enrich_items` 校验跳过
  - `prepare_ozon_upload_node.py`：字典属性无有效 dict_id → 跳过，绝不文本兜底
  - `validation_retry_loop.py`：`error_repair_llm` 字典修复改走「取字典第一个有效 dict_id」，绝不塞文本默认值
- **可选字典属性盲补移除**（assemble）：不再「取字典第一个值」盲补（语义随机 → 填错值被拒）；仅当字典**唯一值**时才补充，多值一律跳过
- **自由文本属性中文翻译失败防上传**：LLM 翻译失败/仍含中文 → **跳过该属性**，不再回退中文原文或写空值（修复「颜色名称 - 请用俄文填写该字段」）
- **retry 重传防御**（validation_retry_loop `revalidate_node`）：字典属性 + dict_id=0 的文本值不再重传（防死循环）；非翻译名单属性的中文值翻译失败 → 跳过重传
- **品牌属性 dict_id 保留**（prepare，集成测试发现）：品牌 85/5076 强制标记为字典属性，`"Нет бренда"(126745801)` 不再因 schema 缺失被当自由文本归零（否则 Ozon 报「请从列表中选择」）
- **生图提示词回退中文版**：main/scene/comparison/detail/social_proof/white_bg/multi_angle 恢复为 v2 英文 prompt 之前的中文版本（英文版出图质量问题，后续再调）

### Changed
- 颜色属性字典匹配强化：字典值分页拉全（limit 5000），避免大字典（颜色 1494 条）截断导致匹配不到 → 文本兜底 → 报错

## [0.12.0] - 2026-08-01

### Added
- **Discover v2 四阶段重构**（Skill）：先全量采集 → 表格分析 → 挑完再找货源（1688 配额只花在选中产品）；`--rules` 自动筛选、`--min-price/--max-price` 价格区间、无关键词中国站懒加载
- **蓝海评分增强**：sales_growth（需求上升）+ drr 广告占比（低竞争）因子
- **seller.ozon.ru 运营指标借道**（月销量/增长率/广告占比/上架天数，未登录自动降级）
- **Skill 自动更新机制**：COS manifest 检测 → `skill update` 下载/sha256 校验/备份/回滚/保留 data/；每次命令静默检查
- **CDP 图搜匹配修复**：badge 过滤 + RU-ZH 产品词映射 + 相关性护栏 + 重试机制（37/37 匹配率实测）

### Fixed
- 属性缓存预热崩溃根因：全量内存 OOM + 单事务卡死 PG + 429 无限递归 → 逐节点小事务 + 指数退避
- chrome_launcher 误杀 Electron 进程（裸 chrome 匹配）
- Chrome 130+ 默认 profile 禁止远程调试 → 独立 profile
- 采集选择器 :is() 拼接 bug、widget webPrice/评分 key 错误、缓存污染
- compile.py 遗漏 ozon_seller_analytics、__pycache__ 污染 dist
- CI：PR 到 dev 不触发（pull_request 只匹配 main）

### Changed
- service.py 移回明文（探针改动最频繁，需快速迭代）；stealth.py 保留编译
- 统一包机制：一个包全平台（_native/{darwin-arm64,darwin-x86_64,linux,win32}）

## [0.2.0] - 2026-07-18

### Added
- API v1 router (`/api/v1/`) with OpenAPI auto-docs
- Unified error codes (12 `WorkerErrorCode` values)
- Pydantic request/response schemas
- Structured JSON logging with trace_id chain tracing
- Node execution audit (duration, output, errors)
- Ozon API call logging
- Task lifecycle audit (submitted/started/completed/failed/retried)
- Deployment package (`deploy/`): docker-compose, deploy.sh, update.sh
- Auto-init DB on first deploy (category tree + logistics rates)
- CI script (`scripts/ci.sh`)
- API rate limiting (10 req/min/token)
- `MAX_CONCURRENT` env var for concurrency control
- `WORKER_URL` env var for skill-to-worker connection
- `.dockerignore` for smaller images
- Pre-commit hooks (ruff lint)
- LOGGING.md documentation
- CONTRACT.md v3.0

### Fixed
- Multi-SKU variant merge: 9048 now uses `item_id` (deterministic, traceable)
- `double_without_merger_offer` now auto-repairable (appends suffix)
- Variant image fallback uses marketing main_image instead of 1688 alicdn URL
- Deep copy base_item for variant items (prevent shared reference mutation)
- Token prefix handling unified (`replace("sk-", "", 1)`)
- Removed hardcoded Supabase service_role key from 3 files

### Changed
- Dependencies: 27 → 15 (removed opencv, Pillow, langsmith, coze-*, etc.)
- `langchain` → `langchain-core` (only RunnableConfig used)
- `coze-coding-utils` → local `runtime/context.py` stubs
- `memory_saver.py`: psycopg/PostgresSaver lazy-loaded
- Skill `check_task_status()` now queries Worker directly (not n8n)
- Skill `submit_envelope()` now POSTs to Worker directly
- Auth: balance check kept, no deduction (MXOU handles billing)
- Dockerfile: clean pyproject.toml install, HEALTHCHECK on /api/v1/health

## [0.1.0] - 2026-07-01

### Added
- Initial release
- LangGraph 22-node pipeline
- 1688 CDP data extraction
- Ozon product upload
- Multi-SKU variant support
- Self-repair retry loop
- Category/attribute learning

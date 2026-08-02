# Changelog

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

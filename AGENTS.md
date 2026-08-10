# AGENTS.md — ozon-worker 工作区

本文件是工作区级导航。两个子项目各有更详细的文档，改动前请先读对应文档（见「深入阅读」）。

## 最近更新（v0.35.0 — Skill 三模块：SKILL.md 精简 + discover 分析文档 + Skill Sentry 埋点）

> 2026-08-10。SKILL.md 精简为纯操作手册（192→150 行，§6 压缩为排错指引，全文去版本注记）+ discover 选品后自动生成结构性分析文档（MD+JSON，Agent 可直接汇报）+ Skill 端 Sentry 错误上报（复用 pouding_ozon 项目，environment=skill，依赖 3→4）。完整历史见 `CHANGELOG.md`。

- **discover 结构性分析文档**: `export_analysis_report()`（ozon_discovery.py:829）在货源分析后自动写 `data/discovery/analysis_*.md` + `analysis_*.json`（无需 --export）；MD 头部汇总 + 每产品详情块，Agent 直接据此汇报。
- **Skill Sentry 埋点**: `_init_sentry()`/`_capture_exception()`（cli.py:1167/1193）——`SENTRY_DSN` env 启用（environment=skill、release=VERSION），凭证零上传；DSN 未设/sdk 缺失/测试进程静默 no-op。查询错误：`sentry issue list halo-fx/pouding_ozon`（tag environment:skill 区分）。
- **SKILL.md 精简**: §6 工程元信息（自动更新/venv/ABI/profile 迁移）→ 4 行排错指引；8 处版本号注记清除。
- **测试**: skill 24 测试文件全绿（新增 test_discovery_analysis_report 10 断言 + test_sentry_skill 5 断言）；compile.py 9 模块编译 + import 校验通过；ci.sh --quick 通过。
- **Sentry 近期高发问题**（v0.34 Worker 侧）: 翻译/生成失败用中文原文（CA x12 等）→ DESCRIPTION_DECLINE；类目匹配阻断（DF/DC/DH）——详见 Sentry `sentry issue list halo-fx/pouding_ozon`。


## 工作区概述

两段式 Ozon 上架系统，职责严格分离：

| | Skill | Worker |
|---|---|---|
| **角色** | Agent 调用的工具（ZCode/Claude Code 等） | 云端 Docker 管线，消费信封完成上架 |
| **位置** | 客户本地 | 云端服务器（Docker） |
| **入口** | `skill/SKILL.md`（Agent 操作手册） | `worker/src/main.py`（FastAPI + CLI） |
| **职责** | 1688/Ozon CDP 抓取 + 以图搜款 → 组装 GraphInput 信封，**不上架** | 接收信封 → 类目→定价→属性→生图→校验→上传→自学习 |
| **接口** | 输出 `GraphInput` JSON（三层结构 `{draft, source, extensions}`） | 输入 `GraphInput`，输出 `GraphOutput` |

接口契约详见 `docs/CONTRACT-v4.md`（v4.0，最新）。Agent 调用指南详见 `skill/SKILL.md`。部署指南详见 `docs/DEPLOY.md`。

## 目录结构

```
ozon-worker/
├── skill/                      # 客户本地:1688/Ozon 抓取 + 以图搜款 + 信封组装 (Python ≥3.12, pip)
│   ├── compile.py              # Cython 编译脚本（核心库 → .so/.pyd，源码保护）
│   └── scripts/
│       ├── cli.py              # CLI 入口:check/graph/follow/image_search/get_ak/batch_test
│       ├── cloud_probe.py      # build_graph_envelope + follow_sell_cloud + submit_envelope
│       ├── batch_test.py       # 批量处理 URL 列表
│       ├── lib/
│       │   ├── ak_1688_client.py      # 1688 AK API 搜索
│       │   ├── chrome_launcher.py     # 跨平台 Chrome CDP 自动启动（用户零配置）
│       │   ├── ozon_scraper.py        # Ozon 商品页 CDP 抓取（完整字段）
│       │   ├── ozon_image_search.py   # CDP 网页版以图搜款（准确率~100%）
│       │   ├── config_store.py        # 凭证管理
│       │   ├── cdp_client.py          # 原生 CDP WebSocket 客户端（替代 Playwright）
│       │   ├── ozon_widget.py         # Ozon Widget API 客户端（产品信息/跟卖/SKU）
│       │   ├── ozon_seller.py         # Ozon Seller API 客户端（佣金/重量/品牌）
│       │   ├── ozon_discovery.py      # Ozon 选品发现引擎（蓝海评分/1688匹配）
│       │   ├── cache.py              # 通用磁盘缓存（命名空间 + TTL + SHA256 key）
│       │   └── utils.py              # 共享工具函数（parse_price 等）
│       └── capabilities/browser_probe/   # Chrome CDP 探针 + 反检测
├── worker/                     # 云端 Docker:LangGraph 上架工作流 (Python ≥3.12)
│   ├── src/
│   │   ├── main.py             # FastAPI + CLI 入口(-m http/flow/node)
│   │   ├── api/                # 错误码 + Pydantic schemas（自动生成 OpenAPI）
│   │   ├── graphs/
│   │   │   ├── graph.py        # main_graph 编排(auth→...→learning_record)
│   │   │   ├── state.py        # GlobalState / GraphInput / GraphOutput
│   │   │   ├── nodes/          # ~28 个节点
│   │   │   └── validation_retry_loop.py   # 校验失败重试子图
│   │   ├── storage/            # database(PG) / memory(checkpoint)
│   │   └── utils/              # task_processor / logger / ozon_client / ozon_category_query / mxou_api / ...
│   ├── assets/                 # 类目树 JSON、物流费率 Excel、Ozon API 文档
│   ├── config/                 # LLM prompt 配置 (category_match / attributes / ...)
│   ├── tests/                  # pytest 测试
│   └── scripts/                # init_data.py / import_logistics.py / ci.sh
├── deploy/                     # 部署包
│   ├── docker-compose.yml      # 生产环境（含 PG + Worker）
│   ├── deploy.sh               # 一键部署（含自动初始化数据）
│   ├── update.sh               # 一键更新
│   └── .env.example            # 环境变量模板
├── docs/
│   ├── CONTRACT-v4.md          # Skill↔Worker API 契约 v4.0（最新；CONTRACT.md 为 v3.0 旧版）
│   ├── DEPLOY.md               # Worker 云端部署完整指南
│   ├── LOGGING.md              # 日志系统架构 + 查看命令 + 故障排查
│   ├── WORKER-TOPOLOGY.md      # ⭐ Worker 拓扑 + 错误映射 + 数据流 + 改代码快速参考
│   └── ...                     # PRD、Ozon API 文档、物流费率 Excel
└── scripts/
    └── ci.sh                   # 本地 CI（lint → test → build）
```

## Skill 能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 环境检查 | `check` | 自动启动 Chrome、检测登录态、验证凭证 |
| 1688 选品 | `graph` | CDP 抓取 1688 → 组装信封 → 提交 Worker |
| Ozon 跟卖 | `follow` | Ozon 竞品图搜 1688 同款 → 组装信封 → 提交 Worker |
| Ozon 选品 | `discover` | Ozon 中国站/搜索/类目页自动选品，蓝海评分，1688匹配，利润计算，CSV/JSON导出 |
| 以图搜款 | `image_search` | CDP 网页版图搜（准确率~100%） |
| 获取 AK | `get_ak` | 浏览器自动获取 1688 AK |
| 批量处理 | `batch_test` | 批量处理 URL 列表 |
| what-to-sell 查询 | `queries` | Ozon 蓝海/榜单数据查询（v0.34，all-queries/ozon-bestsellers/market-bestsellers，采集后自动上报 worker PG） |

**Chrome 自动启动**：用户零配置，Skill 自动检测系统、启动 Chrome、保留登录态。

**源码保护**：`compile.py` 用 Cython 编译核心库为二进制 `.so`/`.pyd`。当前编译 **8 个**：lib/（ak_1688_client、ak_callback、config_store、image_preprocessor、ozon_scraper、ozon_image_search、reference_images、ozon_api）+ capabilities/browser_probe/stealth.py。以下明文复制（依赖复杂/改动频繁/跨平台编译失败）：cli.py、batch_test.py、cloud_probe.py、lib/（cdp_client、utils、cache、ozon_seller、ozon_widget、ozon_seller_analytics、analytics_upload、ozon_fission、ozon_discovery、updater、task_paths、logging_utils）、capabilities/browser_probe/service.py。
- **cloud_probe.py 明文**（2026-08-02 移回）：非语法问题（macOS 同 Cython 编译成功），是 Cython 生成 65k 行 C + 单个 ~9000 行函数击穿 **MSVC 编译器堆限制**（仅 win32 失败 → 缺 .pyd → graph/follow 报 `No native binary for cloud_probe on win32`）。信封组装核心、改动频繁，明文跨平台一致。
- **service.py 明文**（2026-08-01 移回）：探针改动最频繁。
- **compile.py 编译失败"带响"**（v0.12.0）：失败打印完整 stderr（最后 30 行）+ `failed>0` 时 `sys.exit(1)`，CI 不再静默发布残缺包。CI 另有产物完整性校验（4 平台 × 11 模块共 44 个二进制必须就位）。
- 编译必须用 **Python 3.12**（与目标运行环境 ABI 一致）。

**依赖**：仅 4 个 — `requests`、`websocket-client`、`Pillow`、`sentry-sdk`（Sentry 错误上报，v0.35 起；缺失时 cli.py lazy import 静默降级，不阻塞任何命令）。

**三条管线**：
- **1688 选品**：1688 URL → CDP 抓取 → 组装信封 → Worker 全流程
- **Ozon 跟卖**：Ozon URL → CDP 抓取 → 图搜 1688 → 组装信封 → Worker 跟卖管线
- **Ozon 选品**：Ozon 页面 → CDP 抓取产品列表 → 蓝海评分 → 1688 匹配+利润计算 → 用户确认 → Worker 提交

## Skill → Worker 契约（最重要）

交接载荷是 `GraphInput`，定义在 `worker/src/graphs/state.py`:

```
GraphInput = { token, ozon_client_id, ozon_api_key, envelope }
```

`envelope` 采用**三层结构** `{draft, source, extensions}`:

- **`draft`** — 产品数据:
  - 必填: `item_id`、`title`、`images[]`(str URL 数组)、`weight`(克, int)、`dimensions{length,width,height}`(mm, int)
  - 定价相关: `purchase_cost`(CNY, float)、`purchase_url`、`currency`("CNY")
  - 可选: `attributes{}`(dict[中文属性名→值])、`supplier`、`stock`、`ozon_category{description_category_id,type_id}`
  - 单SKU（默认）: 顶层 `sku_id`、`price`、`original_price`(均平铺在 draft 下)
  - 多SKU 信封: `variants` 最多 1 个元素（Skill 层已折叠）

- **`source`** — 采购源信息: `{purchase_url, purchase_cost}`

- **`extensions`** — 定价配置: `{margin_rate, commission_rate, fx_buffer}`(可选,默认 0.25/0.10/0.05)
- **`extensions.follow_sell`** — 跟卖标记: Worker 走跟卖管线

> ⚠️ **关键约定:**
> - **单产品上传**: Skill 层自动将多变体折叠为单产品（`_collapse_variants_to_single`），一个 1688 item = 一个 Ozon 产品卡。
> - **`purchase_cost` = 代表变体价格 + 1688 国内运费(freightCny)**，已在 Skill 层完成。
> - **`dimensions` 单位 mm**: 1688 原数据 cm → skill ×10。worker 再 /10 转回 cm 定价。
> - **`weight` 单位克**: 直传。

## Worker API 端点

所有端点同时暴露在旧路径和 `/api/v1/` 前缀下（向后兼容）：

| 功能 | v1 路径 | 方法 |
|------|---------|------|
| 提交任务 | `POST /api/v1/submit_task` | POST |
| 鉴权验证 | `POST /api/v1/auth/verify` | POST |
| 查询状态 | `GET /api/v1/task_status/{id}` | GET |
| 取消任务 | `POST /api/v1/cancel_task/{id}` | POST |
| 任务统计 | `GET /api/v1/task_statistics` | GET |
| LangGraph 进度 | `GET /progress/{run_id}` | GET |
| 健康检查 | `GET /api/v1/health` | GET |
| Swagger UI | `GET /api/v1/docs` | GET |
| 蓝海数据上报 | `POST /api/v1/analytics/queries` | POST |
| 畅销榜数据上报 | `POST /api/v1/analytics/ozon-bestsellers` | POST |
| 跨平台畅销榜上报 | `POST /api/v1/analytics/market-bestsellers` | POST |

**`task_status` 返回 `progress` 字段**：`{stage, percent, stages_completed[], stages_remaining[], message}`。
进度基于内存中 12 阶段 `STAGE_ORDER` 计算，节点执行时 `ProgressCallback` 自动更新。
⚠️ 进度存储在内存中，Worker 重启后丢失（task_status 降级为无进度模式）。

鉴权: `token` 字段在请求体中（非 header），通过 Supabase `tokens` 表校验。
限流: 每 token 每分钟 ≤ 300 次（`RATE_LIMIT_PER_MINUTE` 可配置）。
并发: 最多 50 个任务同时执行（`MAX_CONCURRENT` 可配置）。

**`auth/verify` 端点**：Skill 调用的轻量鉴权接口。验证 token 有效性 → MXOU 余额 → 账户状态 → 可选 Ozon API。返回 `{"valid": bool, "reason": "ok|token_invalid|balance_insufficient|account_inactive|service_unavailable", "expires_in": 86400, "ozon_valid": bool|null}`。DB 不可用时安全降级返回 `valid: false`（不会误放行）。
- **余额判定（v0.12.0 修正，2026-08-02 充值实证）**：`_check_mxou_balance()`（main.py）——`tokens.unlimited_quota=true` 直接放行；否则查 `users.quota`（实时剩余额度）> 0 放行。**绝不用 `tokens.remain_quota`**：它是僵尸字段（git 历史移除扣减后从未同步，实证：同用户 3 个 key 数值各异可为负数、充值后仍 0/-10），旧逻辑用它+5.0 阈值导致无限额度/有余额 key 全被误判。`users.quota` 充值直接加、每次调用扣；`used_quota` 是历史累计，判定不参与。auth_verify/submit_task/auth_node 三处一致。

## 架构边界

- **Worker 三层**: FastAPI `/submit_task`(鉴权+入队) → PG 队列 `ozon_product_tasks`(`FOR UPDATE SKIP LOCKED`) → 50 并发 LangGraph worker(`SupabaseTaskProcessor`)
- **Skill 是无状态本地抓取**，不调用任何 Ozon 上架 API
- **编辑时不越界**: 别给 skill 加上架调用，别给 worker 加 1688 抓取
- **错误码**: 统一在 `worker/src/api/errors.py`（12 个 `WorkerErrorCode`）

## ⚠️ Agent 使用 Skill 时的硬约束

**当用户请求涉及 Skill 子项目（1688 抓取、Ozon 跟卖、选品、上架）时，必须遵守：**

1. **先读 `skill/SKILL.md`**，不要凭记忆或自己探索项目结构操作
2. **只用 SKILL.md 中的命令**，不要自己写 Python 代码、不要用 requests/urllib 抓取
3. **严格按意图路由选择管线**（A/B/C/D），不要混用蓝海逻辑和跟卖逻辑
4. **不要修改 Skill 的 Python 代码**，除非用户明确要求改代码
5. **趋势/蓝海选品必须先 web_search**：命令层无 `trend`（v0.31 移除）。流程 = agent 先用 web_search 搜 `"{品类} Ozon 热门趋势 蓝海 细分品类 2025"`（可加俄语/平台角度）+ 自带 LLM 提炼细分关键词 → 再 `discover --keyword <关键词>` 执行；**禁止跳过搜索直接猜关键词**（选品质量明显下降）
6. **每次操作前重新判断用户意图**，不要因为上下文中提过某个概念就默认使用它

违反以上约束会导致：空白 Chrome 窗口泛滥、登录态丢失、管线混乱、数据错误。

## 测试

> ⚠️ **测试环境规范（v0.31 红线）**：本地开发/测试一律走**本地环境**——
> - Worker 功能测试用**本地 Docker**（`cd deploy && docker compose up`，`http://localhost:8080`）；skill 指向本地 `WORKER_URL=http://localhost:8080`
> - **禁止用云端生产环境（worker.mxou.cn）做功能测试**（auth/verify、submit_task 等只读/写操作都不行——生产是真实数据 + 真实上架凭证）
> - 云端只能做「用户视角」验证（如确认服务在线/用户反馈问题复现），做完不留测试痕迹
> - skill 的 Chrome/Ozon 页面抓取测试本身在本地浏览器（用户机器），不涉及云
> - 本地测试前按 zombie 警告清空任务表（`DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`），避免误激活旧任务真实上架

```bash
# Worker 全量测试（关键：必须用 skill venv 的 python——系统 python3 无 pytest/psycopg2/pytest-asyncio；
# 需连本地 Docker PG，端口 5433 密码 localdev123，URL 见下）
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q
# 无本地 PG 时跑单文件（纯 mock 用例）：
cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_defaults_wave1.py -q

# Worker 单元测试（Mock 模式，无需 PG/GPU）
cd worker && PYTHONPATH=src ../skill/.venv314/bin/python tests/test_full_pipeline_mock_images.py

# Skill 单节点测试
cd skill && python3.12 scripts/cli.py graph --url "<1688 URL>"
```

> ⚠️ **测试环境前置（v0.34 实测）**：worker 测试的 pytest 全家桶（pytest-asyncio/psycopg2-binary）装在 `skill/.venv314`；CI（ci.yml）已声明这些依赖，本地需自己 `skill/.venv314/bin/pip install pytest-asyncio psycopg2-binary`。本地 Docker PG 端口 **5433**（非 5432），密码 `localdev123`（见 `deploy/.env` 的 `POSTGRES_PASSWORD`）。

| 子项目 | 命令 |
|---|---|
| skill | `pip install -r requirements.txt` |
| skill | `python3.12 scripts/cli.py check`（环境检查 + 自动启动 Chrome） |
| skill | `python3.12 scripts/cli.py graph --url <1688 URL>`（1688 选品） |
| skill | `python3.12 scripts/cli.py follow --ozon-url <Ozon URL>`（Ozon 跟卖） |
| skill | `python3.12 scripts/cli.py discover --keyword "宠物用品" --max-products 50`（Ozon 选品） |
| skill | `python3.12 scripts/cli.py discover --keyword "..." --export csv --output results.csv`（选品+导出） |
| skill | `python3.12 scripts/cli.py image_search --image <URL>`（以图搜款） |
| skill | `python3.12 scripts/batch_test.py --urls-file urls.txt --client-id xxx --api-key xxx --submit` |
| skill | `python3.12 compile.py`（Cython 编译核心库 → .so/.pyd，必须用 Python 3.12） |
| skill | `python3.12 compile.py --clean`（清理 build/dist 后重新编译） |
| worker | `cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q`（全量，需本地 PG） |
| worker | `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_defaults_wave1.py -q`（单文件，纯 mock 无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_attribute_fill_v013.py`（属性字典值回归，8 断言，无需 PG/GPU） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_audit_a_fixes.py`（A 批审计修复回归：P1-4 阻断路由 + P0-2 跟卖属性，5 断言，无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/integration_attribute_fill_v013.py`（assemble→prepare 全链路，mock 外部 API） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_image_prompts_config.py`（生图提示词配置热加载单测，12 断言，无需 PG/GPU） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_attribute_fill_v016.py`（v0.16 属性填满/中文零容忍/海关跳过单测，10 断言，无需 PG/GPU） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_learning_record_gate.py`（v0.21 成功判据收紧回归，5 用例，无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_hazard_attr_fallback.py`（v0.21 危险品安全兜底回归，7 用例，无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_category_match_v021.py`（v0.21 类目同义词/学习缓存一致性，5 用例） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_language_routing.py`（v0.29 语言路由：1688 中文→ZH_HANS/Ozon 类目名→RU/无中文残留，4 用例） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_shop_usage_stats.py tests/test_analytics_endpoints.py tests/test_llm_suggest_rerank.py -q`（v0.34 C5/C6/类目 suggest 单测，纯 mock 无需 PG） |
| skill | `python3.12 tests/test_updater.py`（v0.18 自动更新器单测，11 断言，mock 网络） |
| skill | `python3.12 tests/test_envelope_fields.py`（v0.21 信封字段完整性，2 用例） |
| worker | `bash scripts/local_run.sh -m flow -i '{...}'` 跑全流程 |
| worker | `bash scripts/local_run.sh -m node -n <节点ID> -i '{...}'` 跑单节点 |
| 本地Docker | `cd deploy && docker compose up -d --build`（启动 Worker + PG） |
| 本地Docker | `docker compose exec worker python scripts/init_data.py --force`（初始化数据） |
| 本地Docker | `docker compose exec worker python scripts/warm_category_cache.py --limit 100`（预热 top-100 类目属性缓存） |
| 本地Docker | `docker compose exec worker python scripts/warm_category_cache.py --all --pg-only`（预热全部 7424 类目，~16h，可screen后台） |
| 本地Docker | `docker compose exec worker python scripts/warm_category_cache.py --export-only`（导出 JSON 到 assets/ 供 git 提交） |
| 本地Docker | `curl http://localhost:8080/api/v1/health`（健康检查） |
| 本地Skill | `WORKER_URL=http://localhost:8080 python3.12 scripts/cli.py check`（指向本地 Worker） |
| CI | `bash scripts/ci.sh`（lint → test → docker build） |
| 部署 | `bash deploy/deploy.sh`（一键部署，含自动初始化数据） |
| 更新 | `bash deploy/update.sh`（git pull → rebuild → restart） |

## 环境与密钥

- **Worker 凭证随请求传**（`GraphInput` 里的 `token`/`ozon_client_id`/`ozon_api_key`），不是环境变量。
- Worker 平台级环境变量（`deploy/.env`，完整模板见 `deploy/.env.example`）:
  - `PGDATABASE_URL` — PostgreSQL 连接串（必填）
  - `SUPABASE_URL` / `SUPABASE_KEY` — Supabase `tokens` 表鉴权
  - `APP_WORKSPACE_PATH` — 定位 `assets/` 和 `config/`（Docker 内 `/app`）
  - `PYTHONPATH=/app/src` — Python 模块路径
  - `GRSAI_API_KEY` — MXOU 生图进度轮询（grsai.dakka.com.cn）
  - `RATE_LIMIT_PER_MINUTE` — API 限流（默认 300）
  - `MAX_CONCURRENT` — 并发任务数（**默认 30**，v0.14 起；4核4G 服务器安全值。⚠️ `num_workers` 已联动此值，旧版硬编码 10 已修）
  - `LOG_FORMAT` / `LOG_LEVEL` / `LOG_FILE` — 日志配置（见 LOGGING.md）
  - `SENTRY_DSN` — Sentry 错误监测（v0.23 起可配，任务异常/超时自动上报）
- Skill 环境变量: `WORKER_URL`（Worker 地址）、`OZON_CLIENT_ID`、`OZON_API_KEY`
- ⚠️ 已移除: `COZE_BUCKET_*`（S3 存储已废弃，图片 URL 直传）、`MXOU_TOKEN`（Worker 从请求 token 获取）

## 部署

详见 **`docs/DEPLOY.md`**。

```bash
# 一键部署
cd deploy
cp .env.example .env  # 填入凭证
bash deploy.sh

# 一键更新
bash update.sh
```

架构：Docker Compose（Worker + PostgreSQL），轻量级，主要瓶颈在外部 API（MXOU/Ozon），不在本地服务器。

> 💡 **config 热加载**：`deploy/docker-compose.yml` 把 `../worker/config:/app/config:ro` bind mount——宿主机改任何 config JSON（LLM prompt、生图提示词、同义词表）**无需重建镜像/重启容器**，保存即生效（下次调用）。改提示词流程：服务器 `vim worker/config/image_prompts.json` → 保存生效。

> ⚠️ **Docker 清理（v0.34）**：升级走 `cos-update.sh`（服务器无法访问 GitHub → COS 分发），每次 `docker compose build --no-cache` 全量构建会累积历史镜像层 + BuildKit 缓存。**升级成功后脚本自动清理**（builder prune + dangling image + 旧 ozon-worker 非 latest 镜像）；安全边界：不用 `prune -a`（防误伤服务器其他项目）。手动清理可跑 `docker image prune -f && docker builder prune -a -f`。

## GitHub 仓库

- 地址: https://github.com/halojerry/ozon-worker （私有仓库）
- 克隆: `git clone https://github.com/halojerry/ozon-worker.git`

## 数据初始化

首次部署时 `deploy.sh` 自动运行 `scripts/init_data.py`:
- `CREATE TABLE`（全部表，幂等）
  - 导入类目树 → `category_tree_nodes`
  - 导入物流费率 → `logistics_rates`
  - 重复运行安全：已有数据跳过；`--force` 强制覆盖

部署后 `deploy.sh` 后台运行 `warm_category_cache.py --limit 200 --pg-only`，预热 top-200 类目属性到 PG（~5 分钟）。

**为什么不用 JSON 文件存储属性缓存：**
- JSON 裸文件：全量 ~70GB（太大，不能 git）
- PG JSONB（TOAST 压缩）：全量 ~600MB（完全可行）
- 策略：属性 schema + 字典值直接写 PG，运行时懒加载补全
- **v0.11.5 补充**：top-200 子集 JSON（~2MB）提交 git 随 Docker 镜像分发，`init_data.py` 启动时直接导入（详见 `CHANGELOG.md` 0.11.5 段）

### 属性缓存机制

```
1688 中文属性 "白色"
  → PG dictionary_value_cache (ZH_HANS) 查找
  → 命中 → dict_id=61571 ✅（跨语言通用！）
  → 未命中 → Ozon /values API (ZH_HANS) → 写入 PG → 匹配
  → 上传: { dictionary_value_id: 61571, value: "Белый" }
```

dictionary_value_id **跨语言通用**：ZH_HANS 的 `id=61571` 在 RU 下展示为 `"Белый"`，是同一个 ID。

### 属性缓存脚本

> ⚠️ **v1.1 修复（2026-08-01 云端崩溃根因）**：原 `warm_category_cache.py` 把
> 全部类目数据攒内存（峰值 1.5GB+ OOM）且单事务提交全部（PG 内存暴涨锁表 →
> 服务卡死）。已改：**逐节点小事务写 PG**（--pg-only 内存 O(单节点)）、429 限流
> 指数退避上限 3 次（原无限递归）、并发 3→2、API_DELAY 0.05→0.3、导出流式写。
> 全量预热建议分片：`--offset N --pg-only` 每 1000 个跑一次。

```bash
# 预热 top-200 类目（部署后自动跑）
python scripts/warm_category_cache.py --limit 200

# 预热全部 7424 类目（~16 小时，建议分片跑，每 1000 个一段）
python scripts/warm_category_cache.py --all --pg-only
python scripts/warm_category_cache.py --all --offset 1000 --pg-only
python scripts/warm_category_cache.py --all --offset 2000 --pg-only

# 导出 JSON 到 assets/（提交 git，部署时自动导入）
python scripts/warm_category_cache.py --limit 500 --export-only

# 从 JSON 导入到 PG（部署时 init_data.py 自动调用）
python scripts/warm_category_cache.py --import-only

# 断点续传
python scripts/warm_category_cache.py --all --offset 2000 --pg-only
```

## 日志系统

结构化 JSON 日志，四种审计类型：

| 类型 | logger | 说明 |
|------|--------|------|
| 任务生命周期 | `task.lifecycle` | submitted/started/completed/failed/retried |
| 节点执行 | `node.{name}` | 开始/完成/失败 + 耗时 + 输出摘要 |
| Ozon API | `ozon.api` | 方法/端点/状态码/耗时 + 请求/响应摘要 |
| 链路追踪 | 所有日志自动携带 | trace_id / task_id / user_id |

环境变量：`LOG_FORMAT=json`（生产）、`LOG_LEVEL=INFO`、`LOG_FILE`（可选）

代码中使用：
```python
from utils.logger import get_logger, set_trace_context, log_task_event, log_ozon_api_call, audit_node
```

详见 **`docs/LOGGING.md`**。

## 版本管理

- 版本号: `VERSION` 文件（语义化版本 `MAJOR.MINOR.PATCH`）
- 变更记录: `CHANGELOG.md`
- 发版: 改 VERSION → 更新 CHANGELOG → `git tag v{x.y.z}` → `VERSION={ver} bash deploy/deploy.sh`

## 开发规范

- Commit: `<type>(<scope>): <中文描述>`（如 `feat(worker): 结构化日志`）
- 分支: `feat/`、`fix/`、`refactor/`、`docs/`、`hotfix/`
- Pre-commit: `git config core.hooksPath .githooks`（自动检查 .env + 密钥 + 语法）
- 详见 **`docs/CONVENTIONS.md`**

## 深入阅读（改前先看）

- **`skill/SKILL.md`** — ⭐ Agent 调用指南（Chrome 启动、选品、跟卖、以图搜款、批量处理）
- **`docs/DEPLOY.md`** — ⭐ Worker 云端部署完整指南（Docker、Nginx、HTTPS、运维）
- **`docs/WORKER-TOPOLOGY.md`** — Worker 拓扑与错误处理手册（节点流、错误映射、数据流、改代码快速参考）
- **`docs/CONTRACT-v4.md`** — ⭐ Skill↔Worker API 契约 v4.0（端点、请求/响应、错误码、节点合约；`CONTRACT.md` 是 v3.0 旧版）
- **`docs/LOGGING.md`** — 日志系统架构 + 查看命令 + 故障排查流程
- **`docs/CONVENTIONS.md`** — 分支命名 + commit 规范 + 发版流程
- **`docs/OZON-ATTRIBUTE-API.md`** — ⭐ Ozon 属性/类目 API 参考（5 接口定义 + 属性填满策略 + 关键属性 ID 表，开发直接查）
- **`worker/AGENTS.md`** — Worker 完整文档：节点流程、Ozon API 坑
- **`worker/src/api/errors.py`** — 统一错误码（改错误响应前必看）
- **`worker/src/api/schemas.py`** — Pydantic schemas（改 API 前必看）
- **`worker/config/*.json`** — LLM prompt 配置（均走 mxou deepseek-v4-flash）

## 需牢记的约定

- **单产品上传**：Skill 层折叠变体（`_collapse_variants_to_single`），一个 1688 item = 一个 Ozon 产品卡。
  - 数量变体 → 选"1只装"
  - 颜色/尺寸变体 → 中位数选价
  - 采购成本 = 代表变体价格 + 1688 国内运费(`freightCny`)
  - 标题不加颜色/数量后缀
  - Worker 层零改动：`variants=[]` 走现有单产品路径
- **类目树 ID 跨语言一致**：`category_tree_nodes` 存中俄双语（`language=ZH_HANS` / `language=RU`），同一 `description_category_id`/`type_id` 跨语言一致。类目匹配用 `ZH_HANS` 搜索（与 1688 中文类目名匹配），上传时 `dictionary_value_id` 跨语言通用。属性 schema 从 Ozon API 获取时也用 `ZH_HANS`（v0.5.0 起从 `RU` 改为 `ZH_HANS`），与 1688 产品属性名匹配。
- **品牌默认无品牌**：所有产品强制默认为 `Нет бренда`（dictionary_value_id=126745801）。不管 1688 数据或 LLM 匹配到什么品牌，一律覆盖。品牌属性不存在时自动补充。代码位置：`assemble_ozon_product_node.py:1007-1022`。
- **制造商用 supplier 填充**：attr=23487（Производитель）是自由文本属性（dictionary_id=0），不是字典属性。用 `draft.supplier`（1688供应商名）填充，不写空值。
- **描述强制净化**：`_sanitize_description()` 在翻译后移除拉丁文、中文、URL、邮件、电话、营销词。代码位置：`prepare_ozon_upload_node.py`。
- **9782 危险品等级安全兜底**（v0.21 修正）：attr=9782 是某些类目的必填属性，从 SKIP_ATTR_IDS 中移除（3处：prepare/validate/status），但取值**只挑「非危险」安全默认** `get_safe_hazard_default`，取不到则跳过——删除「取第一个字典值」兜底（曾填成「爆炸物 Category 1」被拒 BR_hazard_class1）。代码位置见 `WORKER-TOPOLOGY.md` 关键属性ID表。
- **cm→mm 阈值 200**：`max_dim < 200` 判断为 cm 转 mm（原 50 太保守，推车等大物品被误判）。
- **小重量自动乘 1000**：`weight_g < 10g` 但尺寸 > 50mm 时自动乘 1000（疑似 kg→g 单位错误）。
- **物流费率表必须初始化**：`logistics_rates` 表为空时兜底费率 `weight * 0.15 CNY` 严重虚高。deploy.sh 须确保 `init_data.py` 在 worker 启动前执行完毕。
- **定价公式已修正**：CNY 店铺不使用 fx_buffer（无汇率风险），佣金公式改为 `售价 = 总成本 * (1+利润率) / (1-佣金率)`。兜底物流费率从 0.15 降到 0.05 CNY/g。
- **图片顺序规范**：`primary_image` = `main_image`（营销主图，单独指定），`images` 数组按 IMG_ORDER：social_proof → detail → scene_1 → scene_2 → scene_3 → comparison → multi_angle（倒数第二）→ white_bg（最后）。
- **变体图片降级**：白底图生成失败 → 统一营销主图（非 1688 alicdn 原图）。
- WARNING 级 Ozon 错误过滤不算失败；`ozon_status` 返回 `pending` 视为软成功（**仅限审核中状态路由**；v0.21 起成功判据收紧——learning_record 只认 `moderate_status=="approved"`，「pending+product_id 视为成功」已删除）。
- `GlobalState` 自定义 reducer：`progress_counter`=max、`error_message`=覆盖、`failed_stage`/`stages`=合并。
- **Docker 部署**: `deploy/docker-compose.yml` 含 PG + Worker，`HEALTHCHECK` 已配置。
- **API 版本化**: 新端点走 `/api/v1/`，旧路径保持兼容。

### v0.34 新增关键约定（改类目匹配/品牌/Sentry/analytics 前必看）

- **竞品尺寸重量兜底**：`prepare_ozon_upload_node.py` 的 `_resolve_weight_dimensions(draft, extensions)`——draft 原值 → `extensions.competitor_weight_g/competitor_dimensions_mm`（skill 信封 extensions 传入）→ 100g/300×200×50mm 硬编码三级兜底；`draft_sanity` 对 weight=0+竞品数据放行。改 prepare 重量/尺寸逻辑必须走此函数（v0.34 抽取，勿改回内联死代码）。
- **类目末级词搜索**：`specific_terms = cat_terms[-1:]`（原 `[-2:]` 会被上级词 token 稀释——「科教玩具 其他益智玩具」分词后 sim 0.5→0.333 错配）。只留末级词整体辨识度最高。改 `assemble_ozon_product_node.py` 类目匹配时勿改回 `[-2:]`。
- **LLM 类目 fallback max_tokens=4096**：deepseek-v4-flash 推理模型 `reasoning_tokens` 吃 `max_tokens` 配额，10/200 输出必空 → fallback 恒失败。改 `_llm_rank_categories` 时勿改小 max_tokens。
- **suggest 二次搜索**：`_llm_rank_categories` 返回 `{"_llm_suggest": True, "suggest_keywords": ...}` 时，上层**必须重跑 LLM 排名**（合并候选后）或从合并后 candidates top1 回退——否则 best_by_llm 无 full_path → 重叠检查恒失败 → 硬阻断（review 修复的 dead code）。
- **品牌 85/31/5076 直写无品牌**：`BRAND_ATTRIBUTE_IDS` 强制段统一补充 `Нет бренда`(126745801)，missing_required 循环里跳过品牌 ID（不走字典兜底，避免误导性 ERROR + 无谓 API 拉取）。
- **规格表中文属性名净化**：`_append_spec_table` 对属性名 `name` 也要做中文/拉丁净化（schema ZH_HANS 中文属性名进规格表 → 描述含中文 → validate 拦截）。改描述净化时同步 name。
- **analytics 端点安全**：`/api/v1/analytics/*` 三端点——按 token 限流（复用 RateLimiter）+ 单次 ≤2000 条 + 错误不回显内部异常。`contributed_by_token_id` 存完整 token key（与 payload 同先例）。
- **Sentry token 指纹**：`_token_fingerprint`（前 8 位 + sha1 前 6）不泄露明文；mxou 错误分支 + capture_task_error 都带用户上下文——云端可按 username 筛选错误定位「哪个账号余额不足」。

### v0.30 新增关键约定（改 retry/属性匹配/学习闭环/CDP 前必看）

- **retry 字典属性纪律(测试锁定)**: 语义匹配 → type_id → 标题2-gram → 唯一值 → None（绝不取第一个）。`validation_retry_loop.py` Step 2.5 与 assemble/prepare 已统一，改 retry 属性修复勿再引入盲补首值。
- **revalidate 双守卫**: hazard（9782 只放行安全默认）+ is_aspect（schema `is_aspect=true` 属性创建后不可改，retry 跳过）。`attribute_utils.is_aspect_attr` 是唯一入口，schema 缺字段时按名称关键词兜底。
- **fetch-back 是唯一 dict 漂移校准机制**: approved 后 `fetch_back_node` 调 `/v4/product/info/attributes` 回读 → diff → `attr.outcome` 遥测。改 graph 路由时**不要移除** `成功 → fetch_back → learning_record` 边。
- **学习门**: 被擦除（erased）/Ozon 自动填默认（`attributes_with_defaults`）的属性**不写入** `ozon_attribute_mappings`。
- **provenance 消费**: `ozon_attribute_mappings.source` 列——learned_approved/fetch_back_corrected 可复用；default_fallback 可出场但 success_count 不增长；retry_recovered 隔离待 fetch-back 确认；fabricated `[{name}]` source_value 一律跳过。历史数据用 `worker/scripts/backfill_mapping_source.py` 回填。
- **skill 顶层 preflight**: `_preflight_runtime`（Python≥3.12+requests/websocket/PIL）在 `main()` 解析后立即执行，缺依赖 return 1。新命令要豁免需显式加入豁免清单。
- **Chrome profile 统一**: 工具 Chrome 全部用 `data/browser/profiles/1688/default`（`chrome_launcher._default_profile_dir`/`cli._chrome_profile_dir`/`service._profile_dir` 三处一致）。改 Chrome 启动逻辑勿再引入第二 profile 路径；老路径迁移用 `scripts/migrate_profile.py`。
- **find_tab 释放契约**: `find_tab` 命中**用户已有 tab** → 必须 `cdp.release(tab)`，否则 `conn.close()` 远程关闭用户标签页。新代码复用已有 tab 一律先 release。
- ⚠️ **zombie recovery 本地陷阱**: 启动清理复活 failed→pending，本地测试会误激活旧任务真实上架。本地 Docker 测试前先 `DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`。

### v0.29 新增关键约定（改属性匹配/运费/Chrome/余额前必看）

- **属性语言路由(测试锁定)**: `values/search` **无 language 参数**(语言无关, 中文直查); schema/values 全量接口 language 决定返回文本语言。1688 中文值 → 中文直查; Ozon 类目名 → RU。字典属性 value 文本**禁止中文**(命中 ZH_HANS 缓存时置空/用 RU, dict_id 权威)。
- **运费端点**: `POST /api/v1/logistics/quote`(worker)与 `utils/logistics_quote.py` 公共模块同源, pricing_node 已改用它。skill 端 `_query_logistics_from_worker` 失败降级本地 40 CNY/kg。
- **Chrome 常驻**: 工具 Chrome 独立 profile(`data/browser/profile`)+ 常驻, **命令出口不关**; close_tool_chrome 仅显式调用。用户手动关后下次命令独立 profile 重启必成功(无单实例锁)。
- **余额统一**: Worker auth/submit/auth_node + skill check 全部查 MXOU 平台真实余额(`users.quota`), 不用 `remain_quota`(僵尸字段)。
- **Sentry**: `~/.sentryclirc`(sntryu_ token, us.sentry.io, pouding_ozon); 查错误用 `sentry-cli issues list` 或 REST API。

### v0.17-v0.25 新增关键约定（改跟卖/成功判据/属性兜底前必看）

- **跟卖双模式** `extensions.follow_type`：hand（默认，CREATE 重建防侵权）/ api（import-by-sku 复制）；skill 有货源→hand、无货源→api，worker hand 缺货源数据自动降级 api。
- **offer_id 统一 `follow_{竞品ID}`**：import-by-sku/assemble/prepare 三处一致，防 api 模式双卡（旧代码不一致 → import-by-sku 建一张 + upload 又 CREATE 一张）。
- **成功判据收紧**：learning_record 只认 `moderate_status=="approved"`；假成功三处已改（imported 即 success / pending+product_id / 不可修复标 success → pending_moderation/rejected_unfixable）。学习缓存污染用 `worker/scripts/clean_category_mapping.py` 清理。
- **危险品安全兜底**：9782 必填但只挑非危险默认 `get_safe_hazard_default`，不取第一个字典值。
- **竞品数据兜底**：`apply_competitor_fallback` 用竞品重量/尺寸填补 1688 缺失；`ozon_attributes` 竞品俄语属性值优先填充。
- **禁竞品图补位**：AI 生图不足 10 张不用竞品 ir.ozone.ru 图补（整卡 0 图下架根因）。
- **draft_sanity 入队防线**：`worker/src/utils/draft_sanity.py`——weight>50kg/单边>5m 信封 submit 直接 INVALID_REQUEST。

### v0.13/v0.14 新增关键约定（改属性/图搜/CDP 前必看）

- **字典属性绝不手填文本**：Ozon 字典属性只接受列表中的 `dictionary_value_id`，手填 `dictionary_value_id=0 + 文本` → 报「属性值不正确，请从列表中选择」（用途/商品颜色/风格报错来源）。三处（assemble/prepare/retry_loop）统一为「未匹配 → 跳过该属性」，由 `/values/search` 修正或补默认字典值。
- **自由文本属性翻译失败跳过**：含中文值的自由文本属性（颜色名称等）LLM 翻译失败/仍含中文 → 跳过该属性，绝不回退中文或写空值上传（否则报「请用俄文填写该字段」）。
- **可选字典属性不盲补**：仅当字典**唯一值**时才补；多值且无 1688 匹配 → 跳过（避免填语义错误值）。
- **品牌 85/5076 强制保留 dict_id**：`"Нет бренда"(126745801)` 在 prepare 层强制标记为字典属性，防止因 schema 缺失被当自由文本归零。
- **定价失败阻断**：pricing 异常返回 `[PRICING_FAILED]` 标记，graph 路由阻断，**不再 ¥1000 兜底上架**。
- **竞品价字段**：skill 抓取 Ozon 竞品售价 → `draft.competitor_price`（独立字段，勿用 `draft.price`——那是 1688 CNY 采购价）。worker `follow_sell_import_node` 优先读 `competitor_price`。
- **quantity 变体定价**：数量拆分 SKU 用 `pricing_info.variant_prices[i]`（含利润/佣金/物流），**绝不直接用 1688 采购价当售价**。
- **图搜相关性护栏**：follow 图搜结果经 `_pick_best_match`（ozon_discovery.py）筛选——badge「符合0/N」跳过、RU→ZH 标题重叠打分、badge 轻微匹配(<0.5)但标题相关性弱(conf<0.3)拒绝。拒绝时 `no_relevant_match=true` 不组装信封。改图搜代码勿绕过此护栏。
- **图搜弹窗**：1688 图搜 `window.open` 已被 Chrome `--disable-popup-blocking`（chrome_launcher 启动参数）+ JS 层 `window.open` 覆盖（image_search）双保险解决，无需手动放行站点。
- **CDP 统一走 cdp_client.py**：E4 后 4 处裸 websocket/CDP 已统一封装。新代码必须用 `CdpConnection`/`CdpTab`，勿手写 `websocket.create_connection`。复用用户已有 tab 时用 `conn.release(tab)` + `tab.close(close_remote=False)`，否则会误关用户浏览器标签页。
- **生图提示词为中文版**（v0.13 回退）：main/scene/comparison/detail/social/white_bg/multi_angle 均用中文 inline prompt（v2 英文版出图质量问题已回退）。调提示词时勿改回英文版。
- **Skill 验证环境**：本机可用 `/Volumes/OS/opt/homebrew/bin/python3.14` + `skill/.venv314`（已装 requests/websocket-client/Pillow）；`check`/`follow` 真实冒烟需 Chrome CDP 9222 运行且 1688/Ozon 已登录。
- **COS 只随 release 分发**：skill 包 COS 上传仅在 `skill-distribute.yml`（release published 触发）；tag push 时 build-skill 编译需 20-30 分钟，distribute 会轮询等待包就位（竞态已修）。日常 CI（push/PR）不发 COS。

## 已知坑

- **进度已持久化**（v0.9）：`_task_progress` 同时写内存和 PG `progress` 列，重启后从 PG 恢复。`task_processor.py` 注入 `task_id` 到 payload 修复了 key="unknown" 的问题。
- **deepseek-v4-flash reasoning tokens**：该模型默认启用推理，`reasoning_tokens` 消耗 `max_tokens` 配额。翻译/生图 prompt 的 `max_tokens` 至少设为 200，否则输出为空。
- **DESCRIPTION_DECLINE 多重根因**：
  1. 产品名含拉丁/中文字符 → `ozon_validate_node` 应阻断（已修复）
  2. 属性值含中文 → 俄语类目树 ID 映射解决（`language=RU` 字典值直连）
  3. 图片含文字/URL/物流信息 → AI 模型局限性，标记为 warning 不阻断（已修复）
  4. 类目不匹配 → 已添加一致性检查 + 俄语标题重新匹配（已修复）
- **LLM 类目匹配**：v0.5.0 起主路径不用 LLM 选类目（pg_trgm + jieba 末级词），但低置信度时 `_llm_rank_categories` 作为 fallback（v0.34 修复 max_tokens 后可用）——LLM 输出 candidate_index 或 suggest_keywords 二次搜索。类目一致性检查保留但不阻断上传（保留原 category ID 让 Ozon 验证）。
- **物流费率表为空导致价格虚高**：兜底费率 `weight * 0.15 CNY` 是实际费率的 3-4 倍。部署时必须确保 `import_logistics.py` 先于 worker 执行（已修复，Dockerfile 加 openpyxl）。
- **Chrome 重启后 probe 偶发失败**：Skill 的 `probe_1688_page` 在 Chrome 崩溃后自动重启时，内部的 `_resolve_browser_session` 二次调用可能导致 session 状态不一致。直接使用 `CdpTab` + `_single_pass_probe` 可绕过。受影响命令：`graph`/`follow`（偶发），不影响 Worker。
- **属性ID细节**：
  - 9782（危险品等级）：字典属性，某些类目必填，不能跳过
  - 22508（品牌注册国）：自由文本属性，需硬编码为"Китай"
  - 23487（制造商）：自由文本属性，用 `draft.supplier` 填充
  - 23536（标记码）：Ozon 自动设置，必须跳过
	- **`validation_retry_loop` 修复记录**（v0.5.0）：
	  1. `state.draft` 为空 → 已修复
	  2. `recheck_status_node` 额外轮询 `moderate_status` → 已实现
	  3. `type_id=0` → 多层防御修复
	  4. `recheck_status_node` UUID 解析崩溃 → 已加 UUID 格式检测
		- **`init_data.py` `walk` 函数**：`description_category_id` 需从父节点继承，`disabled` 字段 NOT NULL 需填 `false`。中文树是 `{"result":[...]}` dict，俄语树是 `[...]` 直接 list，walk 调用需兼容两种格式。

### v0.13/v0.14 修复记录（2026-08-03，commit ad1164c/8041b3d/b78fe64/8231639/93ddd1a）

**v0.13（属性字典兜底 + 生图回退）**：
- 字典属性未匹配不再手填文本（3 处：assemble/prepare/retry_loop）→ 报「请从列表中选择」消除
- 自由文本翻译失败跳过（修复「请用俄文填写」）；可选字典不盲补；品牌 85/5076 保留 dict_id
- 生图提示词回退中文版（v2 英文版出图质量差）

**v0.14（审计四批 + CI + CDP）**：
- P0-2 跟卖属性链路（`_assemble_follow_sell` 消费 follow 输出，删 126745801 假属性）
- P0-4 单SKU 补运费（删 `len(variants)>1` 守卫）；P0-6 竞品价 `draft.competitor_price`
- P1-1 quantity 定价用 variant_prices；P1-4 定价失败 `[PRICING_FAILED]` 阻断；P1-5 parse_error 读 validation_errors
- B1 属性批量翻译 / B3 mxou_rate_limiter 接入（450 RPM 滑窗）/ B4 变体主图并发(4线程) / B5 空参考图跳过生图
- E1 进度写 PG 节流(2s) / E9 num_workers 联动 MAX_CONCURRENT=30 / C1 类目树 TTL 缓存 / E3 cache 原子写
- E4 裸 CDP 统一封装（cdp_client，勿再手写 websocket）/ E5 follow 连接共享
- 图搜弹窗修复（`--disable-popup-blocking` + window.open 覆盖）+ 多重新搜（badge≤1 重搜 2 次）
- 图搜标题相关性护栏（`_pick_best_match`，不同产品不再组装信封）
- CI：skill-distribute 轮询等待 skill 包（tag 竞态）；COS 仅 release 触发（现状合规）

### v0.9 深度审计 — 已知未修问题（低优先级）

> ⚠️ 以下多项已在 **v0.14** 修复，标注「✅已修」；未标注的仍开放。

#### Skill 侧 (11 个)
- **`ozon_api.search_categories`**：每次调都重新拉整棵类目树（~2-5s），无 TTL 缓存 → ✅已修（v0.14 C1，24h 缓存）
- **`ozon_discovery._calculate_profit`**：物流费固定 15 CNY，不管实际重量/尺寸
- **`ozon_discovery.calculate_blue_ocean_score`**：commission_fbp/fbs 字段名可能不一致
- **`reference_images.get_best_product_images`**：URL 带 query 参数时 `.jpg` 拼接到查询串后导致链接失效
- **`chrome_launcher.ensure_chrome_cdp`**：杀死所有 Chrome 进程而非仅 debug 端口 → ✅已修（v0.14 D5，仅杀带 `--remote-debugging-port` 的实例）
- **`cli.py` `check` 命令**：创建 Chrome tab 后不关闭，多次跑会累积空白 tab → ✅已修（v0.14 E4-2，`close(close_remote=False)` 只关 WS 不关远程）
- **`config_store` / `cache.py`**：无文件锁，并发 CLI 进程可能写坏 JSON → ✅已修（v0.14 E3，临时文件 + os.replace 原子写）
- **`EXTRACT_1688_JS`**：694 行 JS 字符串内嵌 Python 源码，无法 lint，改起来困难
- **`service.py` 连接重复检查**：`connect_existing_chrome` step 2/3 几乎重复
- **`stealth.py`**：`hardwareConcurrency`/`deviceMemory` 每次读取随机变值，是检测信号；`navigator.webdriver` 返回 `undefined` 而非 `false`
- **`batch_test.py`**：每个 URL 都全量覆写结果文件，O(n²) 写入 → ✅已修（v0.14 E7，每 5 条增量写 + 循环后全量写）

#### Worker 侧 (8 个)
- **`mxou_rate_limiter.py`**：整个文件未被引用，无 MXOU API 限流 → ✅已修（v0.14 B3，接入 chat/image 两入口，450 RPM 滑窗）
- **Phase2 生图节点**：Phase1 失败时用原始图但 prompt 仍针对 Phase1 输出设计，图质量差 → ✅已修（v0.14 B5，空参考图跳过生图）
- **`ozon_upload_node.py`**：绕过 `ozon_post()` 直接调 `session.post()`，错误处理不一致
- **`follow_sell_import_node.py`**：schema API 拉取失败时静默降级，缺失属性校验
- **`pricing_node.py`**：物流配置查询无重试，失败默认为 `("RETS", "Standard")`
- **`state.py` `_overwrite_str`**：空字符串会覆盖有效值
- **`progress_logger.py`**：`NODE_ORDER` 静态字典需手动与图定义同步；`config_path` 参数被忽略 → ✅已修（v0.14 C4/D4，模块级缓存只读一次 + NODE_ORDER 同步真实节点集 + config_path 生效）
- **`assemble_ozon_product_node.py`**：`ozon_payloads` 列表写入后从未被消费（被 prepare 覆盖）

## CDP 稳定性注意事项

CDP（Chrome DevTools Protocol）是 Skill 的核心数据通道。全部通过 `cdp_client.py` 的 `CdpConnection`/`CdpTab` 操作。改 CDP 相关代码时注意：

- **Tab 泄漏**：CDP 打开的 tab 必须在 finally 中关闭（`GET /json/close/{tabId}`）。`ozon_scraper.py` 和 `ozon_image_search.py` 已修复，新代码必须遵循。
- **消息 ID 碰撞**：CDP WebSocket 是共享通道，`Runtime.evaluate` 的 `id` 必须全局唯一。`CdpTab` 用 `itertools.count()` 原子计数器。
- **导航等待**：用 `Page.loadEventFired` 事件驱动（`CdpTab.navigate()` 已封装），不要 `time.sleep()` 硬等。
- **致命断连检测**：`CdpTab` 检测 `Target closed`/`Browser closed` 等异常时应立即退出轮询。
- **进程 kill 等待**：Chrome 多 tab 时 SIGTERM 可能需要 5-10s，用轮询 + SIGKILL 回退（`chrome_launcher.py` 已实现）。
- **验证码暂停**：1688 滑块验证时 Skill 自动暂停，提示用户在浏览器中滑动后按 Enter 继续。
- **连接复用**：`fetch_product_info`/`fetch_competing_sellers` 支持可选 `cdp` 参数复用连接，避免 N*2 冗余连接。
- **裸 CDP 已统一封装（v0.14 E4）**：手写 websocket/CDP 的 4 处已改为 `cdp_client`（ozon_scraper/cli.py check/batch_test/ozon_image_search）。新代码必须用 `CdpConnection`/`CdpTab`。复用用户已有 tab 时先 `conn.release(tab)` 再 `tab.close(close_remote=False)`，否则 `conn.close()` 会误关用户浏览器标签页。图搜/登录弹窗已被 `--disable-popup-blocking` + JS `window.open` 覆盖解决。

## Windows 兼容性

Skill 已适配 Windows，但有以下注意事项：

- **进程扫描**：用 `_list_browser_commands()` 辅助函数（`service.py`），Windows 用 `wmic`，macOS/Linux 用 `ps -axo`。不要直接调 `ps`。
- **进程启动**：Windows 不支持 `start_new_session=True`，用 `creationflags=CREATE_NEW_PROCESS_GROUP`。
- **路径提取**：用 `Path(p).name` 或 `os.path.basename(p)`，不要 `.split('/')`。
- **文件锁**：`os.replace()` 在 Windows 上可能因文件锁失败，需重试。
- **headless 检测**：Windows 通过 `SESSIONNAME` 环境变量判断（无则为服务/CI 环境）。
- **wmic 废弃**：`wmic` 在 Windows 10 21H1+ 已废弃但仍在工作，未来可迁移到 `Get-CimInstance`。
- **编译产物**：Windows 需 `.pyd` 文件（`win32` 或 `win_amd64`），在 Windows 机器上运行 `python3.12 compile.py` 生成。

## Skill dist 分发

`compile.py` 生成自包含的 `skill/dist/` 目录：

- `scripts/lib/_native/{platform}/` — 编译后的二进制（darwin-arm64、win32、linux）
- `scripts/lib/*.py` — 自动加载 stub（检测平台 → 加载对应二进制）
- `scripts/capabilities/browser_probe/stealth.py` — stub 位于原始目录（非 lib/），指向 `../../lib/_native/`
- `scripts/lib/ozon_api.py` — 纯 Python 复制（不编译）
- `scripts/lib/cdp_client.py` — 原生 CDP 客户端（纯 Python 复制）
- `scripts/lib/ozon_widget.py` — Ozon Widget API（纯 Python 复制）
- `scripts/lib/ozon_seller.py` — Ozon Seller API（纯 Python 复制）
- `scripts/lib/ozon_discovery.py` — 选品发现引擎（纯 Python 复制）
- `scripts/lib/utils.py` — 共享工具函数（纯 Python 复制）
- `data/config/settings.json` / `stores.json` — **空模板**（编译时自动生成，不泄露凭证）

跨平台分发流程：在 macOS/Windows/Linux 各跑一次 `python3.12 compile.py`，合并 `_native/` 目录后打包。

## CI/CD

GitHub Actions 自动检查每次 push/PR：
- **Syntax**: 全量 .py 文件语法检查（阻断）
- **Quality**: pyflakes 快速质量检查
- **Import**: Worker + Skill 核心模块导入验证（阻断）
- **Docker**: 镜像构建验证（阻断）
- **CD**: `git tag v*` → Docker build → push ghcr.io → GitHub Release
- **Skill 自动更新**: `git tag v*` → build-skill.yml 打包 4 平台 → 上传 COS
  （`/skill/<包>.tar.gz` + `/manifest.json`）→ 用户每次命令静默检查，`skill update`
  应用（sha256 校验 + 备份 + 保留 data/）。需配置 GitHub Secrets：
  `COS_SECRET_ID/COS_SECRET_KEY/COS_BUCKET/COS_REGION/COS_MANIFEST_BASE_URL`。

本地: `bash scripts/ci.sh [--quick] [--strict]`
Pre-commit: `git config core.hooksPath .githooks`（语法 + 密钥拦截）

⚠️ **密钥轮换**: MXOU_TOKEN、1688 AK、Ozon API Key 曾暴露在 git 历史中，已移除追踪但历史仍存在，请尽快轮换。


# AGENTS.md — ozon-worker 工作区

本文件是工作区级导航。两个子项目各有更详细的文档，改动前请先读对应文档（见「深入阅读」）。

## 工作区概述

两段式 Ozon 上架系统，职责严格分离：

| | Skill | Worker |
|---|---|---|
| **角色** | Agent 调用的工具（ZCode/Claude Code 等） | 云端 Docker 管线，消费信封完成上架 |
| **位置** | 客户本地 | 云端服务器（Docker） |
| **入口** | `skill/SKILL.md`（Agent 操作手册） | `worker/src/main.py`（FastAPI + CLI） |
| **职责** | 1688/Ozon CDP 抓取 + 以图搜款 → 组装 GraphInput 信封，**不上架** | 接收信封 → 类目→定价→属性→生图→校验→上传→自学习 |
| **接口** | 输出 `GraphInput` JSON（三层结构 `{draft, source, extensions}`） | 输入 `GraphInput`，输出 `GraphOutput` |

接口契约详见 `docs/CONTRACT.md`。Agent 调用指南详见 `skill/SKILL.md`。部署指南详见 `docs/DEPLOY.md`。

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
│   ├── CONTRACT.md             # Skill↔Worker API 契约 v3.0
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

**Chrome 自动启动**：用户零配置，Skill 自动检测系统、启动 Chrome、保留登录态。

**源码保护**：`compile.py` 用 Cython 将 9 个核心库编译为二进制 `.so`/`.pyd`（ak_1688_client、ak_callback、chrome_launcher、config_store、image_preprocessor、ozon_scraper、ozon_image_search、reference_images、stealth）。以下文件因依赖复杂仅复制不编译：cli.py、cloud_probe.py、batch_test.py、service.py、ozon_api.py、cdp_client.py、ozon_widget.py、ozon_seller.py、ozon_discovery.py、utils.py。编译必须用 **Python 3.12**（与目标运行环境 ABI 一致）。

**依赖**：仅 3 个 — `requests`、`websocket-client`、`Pillow`（Playwright 已移除，统一用原生 CDP）。

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

**`task_status` 返回 `progress` 字段**：`{stage, percent, stages_completed[], stages_remaining[], message}`。
进度基于内存中 12 阶段 `STAGE_ORDER` 计算，节点执行时 `ProgressCallback` 自动更新。
⚠️ 进度存储在内存中，Worker 重启后丢失（task_status 降级为无进度模式）。

鉴权: `token` 字段在请求体中（非 header），通过 Supabase `tokens` 表校验。
限流: 每 token 每分钟 ≤ 300 次（`RATE_LIMIT_PER_MINUTE` 可配置）。
并发: 最多 50 个任务同时执行（`MAX_CONCURRENT` 可配置）。

**`auth/verify` 端点**：Skill 调用的轻量鉴权接口。验证 token 有效性 → MXOU 余额 → 账户状态 → 可选 Ozon API。返回 `{"valid": bool, "reason": "ok|token_invalid|balance_insufficient|account_inactive|service_unavailable", "expires_in": 86400, "ozon_valid": bool|null}`。DB 不可用时安全降级返回 `valid: false`（不会误放行）。

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
5. **每次操作前重新判断用户意图**，不要因为上下文中提过某个概念就默认使用它

违反以上约束会导致：空白 Chrome 窗口泛滥、登录态丢失、管线混乱、数据错误。

## 测试

```bash
# Worker 单元测试（Mock 模式，无需 PG/GPU）
cd worker && PYTHONPATH=src python3 tests/test_full_pipeline_mock_images.py

# Worker 全量测试（需要 PG）
cd worker && PYTHONPATH=src python3 -m pytest tests/ -v

# Skill 单节点测试
cd skill && python3.12 scripts/cli.py graph --url "<1688 URL>"
```

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
| worker | `cd worker && PYTHONPATH=src python3 -m pytest tests/ -v` |
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
  - `MAX_CONCURRENT` — 并发任务数（默认 50）
  - `LOG_FORMAT` / `LOG_LEVEL` / `LOG_FILE` — 日志配置（见 LOGGING.md）
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

```bash
# 预热 top-200 类目（部署后自动跑）
python scripts/warm_category_cache.py --limit 200

# 预热全部 7424 类目（~16 小时，建议 screen/tmux）
python scripts/warm_category_cache.py --all --pg-only

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
- **`docs/CONTRACT.md`** — Skill↔Worker API 契约 v3.0（端点、请求/响应、错误码）
- **`docs/LOGGING.md`** — 日志系统架构 + 查看命令 + 故障排查流程
- **`docs/CONVENTIONS.md`** — 分支命名 + commit 规范 + 发版流程
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
- **9782 危险品等级不能跳过**：attr=9782 是某些类目的必填属性，从 SKIP_ATTR_IDS 中移除（3处：prepare/validate/status）。代码位置见 `WORKER-TOPOLOGY.md` 关键属性ID表。
- **cm→mm 阈值 200**：`max_dim < 200` 判断为 cm 转 mm（原 50 太保守，推车等大物品被误判）。
- **小重量自动乘 1000**：`weight_g < 10g` 但尺寸 > 50mm 时自动乘 1000（疑似 kg→g 单位错误）。
- **物流费率表必须初始化**：`logistics_rates` 表为空时兜底费率 `weight * 0.15 CNY` 严重虚高。deploy.sh 须确保 `init_data.py` 在 worker 启动前执行完毕。
- **定价公式已修正**：CNY 店铺不使用 fx_buffer（无汇率风险），佣金公式改为 `售价 = 总成本 * (1+利润率) / (1-佣金率)`。兜底物流费率从 0.15 降到 0.05 CNY/g。
- **图片顺序规范**：`primary_image` = `main_image`（营销主图，单独指定），`images` 数组按 IMG_ORDER：detail → scene_1/2/3 → comparison → social_proof → multi_angle（倒数第二）→ white_bg（最后）。
- **变体图片降级**：白底图生成失败 → 统一营销主图（非 1688 alicdn 原图）。
- WARNING 级 Ozon 错误过滤不算失败；`ozon_status` 返回 `pending` 视为软成功。
- `GlobalState` 自定义 reducer：`progress_counter`=max、`error_message`=覆盖、`failed_stage`/`stages`=合并。
- **Docker 部署**: `deploy/docker-compose.yml` 含 PG + Worker，`HEALTHCHECK` 已配置。
- **API 版本化**: 新端点走 `/api/v1/`，旧路径保持兼容。

## 已知坑

- **进度已持久化**（v0.9）：`_task_progress` 同时写内存和 PG `progress` 列，重启后从 PG 恢复。`task_processor.py` 注入 `task_id` 到 payload 修复了 key="unknown" 的问题。
- **deepseek-v4-flash reasoning tokens**：该模型默认启用推理，`reasoning_tokens` 消耗 `max_tokens` 配额。翻译/生图 prompt 的 `max_tokens` 至少设为 200，否则输出为空。
- **DESCRIPTION_DECLINE 多重根因**：
  1. 产品名含拉丁/中文字符 → `ozon_validate_node` 应阻断（已修复）
  2. 属性值含中文 → 俄语类目树 ID 映射解决（`language=RU` 字典值直连）
  3. 图片含文字/URL/物流信息 → AI 模型局限性，标记为 warning 不阻断（已修复）
  4. 类目不匹配 → 已添加一致性检查 + 俄语标题重新匹配（已修复）
- **LLM 类目匹配已移除**：v0.5.0 起不再用 LLM 选类目，改为 pg_trgm 相似度排名 + jieba 分词末级类目 + 泛化词过滤。类目一致性检查保留但不再阻断上传（保留原 category ID 让 Ozon 验证）。
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

### v0.9 深度审计 — 已知未修问题（低优先级）

#### Skill 侧 (11 个)
- **`ozon_api.search_categories`**：每次调都重新拉整棵类目树（~2-5s），无 TTL 缓存
- **`ozon_discovery._calculate_profit`**：物流费固定 15 CNY，不管实际重量/尺寸
- **`ozon_discovery.calculate_blue_ocean_score`**：commission_fbp/fbs 字段名可能不一致
- **`reference_images.get_best_product_images`**：URL 带 query 参数时 `.jpg` 拼接到查询串后导致链接失效
- **`chrome_launcher.ensure_chrome_cdp`**：杀死所有 Chrome 进程而非仅 debug 端口，用 `LOCK_NB` 可能抛异常
- **`cli.py` `check` 命令**：创建 Chrome tab 后不关闭，多次跑会累积空白 tab
- **`config_store` / `cache.py`**：无文件锁，并发 CLI 进程可能写坏 JSON
- **`EXTRACT_1688_JS`**：694 行 JS 字符串内嵌 Python 源码，无法 lint，改起来困难
- **`service.py` 连接重复检查**：`connect_existing_chrome` step 2/3 几乎重复
- **`stealth.py`**：`hardwareConcurrency`/`deviceMemory` 每次读取随机变值，是检测信号；`navigator.webdriver` 返回 `undefined` 而非 `false`
- **`batch_test.py`**：每个 URL 都全量覆写结果文件，O(n²) 写入

#### Worker 侧 (8 个)
- **`mxou_rate_limiter.py`**：整个文件未被引用，无 MXOU API 限流
- **Phase2 生图节点**：Phase1 失败时用原始图但 prompt 仍针对 Phase1 输出设计，图质量差
- **`ozon_upload_node.py`**：绕过 `ozon_post()` 直接调 `session.post()`，错误处理不一致
- **`follow_sell_import_node.py`**：schema API 拉取失败时静默降级，缺失属性校验
- **`pricing_node.py`**：物流配置查询无重试，失败默认为 `("RETS", "Standard")`
- **`state.py` `_overwrite_str`**：空字符串会覆盖有效值
- **`progress_logger.py`**：`NODE_ORDER` 静态字典需手动与图定义同步；`config_path` 参数被忽略
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

## 最近更新（v0.9.0 — 全链路健壮性 + 管线优化）

> 2026-07-28 基于 9 个 Ozon 产品实地调研 + 完整节点数据流分析 + PG 持久化审计。

### P0 修复
- **check_quota 移到管线开头**：`auth` 后立即检查店铺配额，阻断时不浪费 GPU/LLM。`graph.py` 边改为 `auth→check_quota→route`，`ozon_validate→ozon_upload` 直接连接。
- **discover 管线补全 AK+CDP**：`build_envelope_from_discovery()` 改为调用 `build_graph_envelope_with_retry()`，不再手工组装空属性/零尺寸信封。含降级兼容。

### P1 修复
- **面包屑传数字 ID**：`ozon_scraper.py` 用 `link.count("/category/") == 1` 识别真实类目（跳过 segs=2 的品牌页），优先传数字 `description_category_id`。Worker 侧 `follow_sell_import_node` 新增 `_resolve_category_by_id()` 直查 `category_tree_nodes`，跳过 pg_trgm。新增 `_detect_language()` 自动检测 RU/ZH_HANS。
- **task_id 注入 + 进度持久化**：`task_processor.py` 将 PG UUID 注入 `payload["task_id"]`。`main.py` 新增 `_persist_progress()` 异步写 PG `progress` 列，`get_progress()` 内存优先→PG 回退。`model.py` 新增 `progress JSONB` 列。

### P2 修复
- **retry loop Ozon API 优先**：`validation_retry_loop.py` 的 `DESCRIPTION_DECLINE` 修复改为先调 Ozon API `_find_alternative_type_id()`，pg_trgm 降级为 fallback（原逻辑相反）。
- **富文本描述（属性 4191 HTML）**：`prepare_ozon_upload_node.py` 新增 `_generate_rich_description()`（LLM 生成俄语 HTML）、`_sanitize_rich_description()`（保留标签）。4191 自动追加到 `final_attributes`。

### P3 修复
- **product_id 拆分**：`GlobalState` 新增 `ozon_task_id` 字段。`OzonUploadOutput` 同时写 `product_id`+`ozon_task_id`。`ozon_status_node` 优先读 `ozon_task_id`。
- **跟卖拉取属性 schema**：`follow_sell_import_node` 在解析类目后调用 `POST /v1/description-category/attribute` 拉取真实 schema（不再用 `[]`）。

### 新增 Ozon API 能力
| API | 用途 |
|-----|------|
| `POST /v1/product/pictures/import` | 增量更新图片，无需完整重传 |
| `POST /v4/product/info/attributes` | 新版商品特征查询 |

### 测试
- `worker/tests/test_full_pipeline_mock_images.py` — Mock 生图全流程测试（12 项），秒级验证上下文传递。运行：`PYTHONPATH=src python3 tests/test_full_pipeline_mock_images.py`

---

## 历史更新（v0.6.0 — 靶向修复 + 生产级稳定性）

> 2026-07-26 全链路重构：retry loop 靶向路由器、字典缓存多语言、标题 SEO、type pg_trgm、follow-sell 管线、稳定性加固。

### retry loop 靶向路由器

`validation_retry_loop.py` 重构为三路靶向路由器。当 `product_id` 已存在时根据错误类型选择最优 Ozon API：

| 错误类型 | API | 特点 |
|---------|-----|------|
| 属性错误 (11种) | `POST /v1/product/attributes/update` | 增量，~3s，无需审核 |
| 价格错误 (2种) | `POST /v1/product/import/prices` | 增量，~3s，无需审核 |
| 类目/尺寸/描述错误 | `POST /v3/product/import` + `product_id` | UPDATE 模式，需审核 |
| 不可修复 (9种) | 无 | 标记 success，不重试 |

⚠️ **关键 bug 修复**：`product_id` 之前未传入 retry loop（`ValidationRetryLoopInput` 缺少该字段），导致 retry 创建重复产品（无图片 `image_absent` 错误）。已在 `state.py`、`validation_retry_loop.py`、`validation_retry_wrapper_node.py` 三处修复。

⚠️ **字典缓存多语言分离**：`_cache_dict_values()` 和 `_fetch_dict_values_from_ozon()` 均加了 `language` 参数。fetch(ZH_HANS)→cache(ZH_HANS)→read(ZH_HANS)，fetch(RU)→cache(RU)→read(RU)。`_validate_and_enrich_items` 的 RU 路径新增缓存写入。方法名 `write_dict_cache`→`set_dictionary_value_cache`。

### DESCRIPTION_DECLINE + attr 8229（类型不匹配）修复

`error_repair_llm_node` 中用 pg_trgm `search_nodes(product_name, node_type="type", language="RU")` 搜索 `category_tree_nodes` 表替代盲选备选 type_id。关键词重叠验证后取最佳匹配替代原 `_find_alternative_type_id()`。

### 标题 SEO 优化

- prepare 节点 title 限制 **50→80 字符**（Ozon 实际支持 80）
- `_sanitize_title` 重写：+拉丁/中文移除 +营销词过滤，对齐 prepare 节点逻辑
- 生图标题清洗：`utils/mxou_api.py` 新增 `clean_title_for_image_prompt()`（80+ 平台/营销垃圾词正则过滤，5 大类：平台名/跨境黑话/营销吹嘘/电商套话/通用填充）。7 个生图节点 + scene_gen 调用。

### 跟卖管线全面重构

跟卖不再是 `follow_sell_import → END`，改为走完整管线：

```
follow_sell_import → pricing → assemble → scene → 10x 图片生成 → prepare → validate → upload → status
```

- **竞品图片作为 AI 生图参考**：`follow_sell_import_node` 提取竞品 `images[]` → `state.original_images` → Phase 1/2 生图（跟 1688 管线相同逻辑，参考图不同）
- **prepare 节点图片策略**：AI 生成图优先，竞品 Ozon 原图兜底补足 10 张
- **竞品价格保护**：竞品价 ≥ 成本*1.3 时保留（更有竞争力），否则公式重算
- **属性硬化**：import-by-sku 后强制 `brand=Нет бренда`(126745801), `country=Китай`(90296)
- **定价修正**：不再硬编码 10/12，用 `purchase_cost * (1+margin)/(1-commission)`

### 稳定性加固

| 功能 | 位置 | 说明 |
|------|------|------|
| 僵尸任务恢复 | `main.py` lifespan | 启动时 running→pending, failed→pending(可重试) |
| 定时清理 | `main.py` `_periodic_task_cleanup` | 每 60s 重置 stale running(>30min), 清理 7天前 completed |
| 健康检查增强 | `GET /health` | +`queue` 字段 (pending/running/completed/failed 统计) |
| 店铺配额监控 | `GET /api/v1/store/health` | 查询 Ozon 配额 (total/daily usage/limit) |
| 日志持久化 | `docker-compose.yml` | `LOG_FILE=/app/logs/worker.log` + `logs` volume |

## 历史更新（v0.5.0）

> 2026-07-25 本地全链路测试：Skill ↔ Docker Worker，两条管线各成功上架 1 个产品。

### 阻断性 Bug 修复（11 个）

| # | Bug | 文件 |
|---|-----|------|
| 1 | `EXTRACT_1688_JS` 裸箭头函数，CDP `Runtime.evaluate` 不执行 | `service.py`：`()=>{}` → `(()=>{})()` |
| 2 | `parse_price` 无法解析多价格 `¥12.70 ¥22.00` | `utils.py`：`re.search` → `re.findall` 取第一个数 |
| 3 | `ProgressCallback` 缺少 LangChain >=0.3 回调属性 | `task_processor.py`：加 `run_inline`/`ignore_chain` 等 7 个 |
| 4 | `init_data.py` category 节点 `type_id=0` | `init_data.py`：`0` → `None`（与 runtime sync 一致） |
| 5 | `search_nodes` fallback 不过滤 `type_id=NULL/0` | `ozon_category_query.py`：加 `type_id IS NOT NULL AND > 0` |
| 6 | 属性缓存 list/dict 格式不兼容 | `assemble_ozon_product_node.py`：兼容两种格式 |
| 7 | 类目一致性失败返回空 dict → `type_id` 归零 | `assemble_ozon_product_node.py`：保留原 ID |
| 8 | Ozon API 属性 schema 用 `language=RU`，无法匹配 1688 中文 | `assemble_ozon_product_node.py`：改为 `ZH_HANS` |
| 9 | 字典值缓存写入 `RU` 但查询默认 `ZH_HANS` | `assemble_ozon_product_node.py`：统一 `ZH_HANS` |
| 10 | `recheck_status_node` 将系统 UUID 当 Ozon task_id 解析 | `validation_retry_loop.py`：UUID 格式检测 + 提前返回 |
| 11 | LLM 类目匹配严重跑偏（喷水玩具→鞋类） | `assemble_ozon_product_node.py`：去掉 LLM，用 pg_trgm + jieba 直接匹配 |

### 基础设施改进

- **`pyproject.toml`**：加 `openpyxl` → 物流费率 142 条成功导入
- **`_const.py`**：`CLOUD_API_BASE` 支持 `WORKER_URL` 环境变量覆盖（本地测试用 `http://localhost:8080`）
- **`config_store`**：`margin_rate`/`commission_rate`/`fx_buffer` 持久化到 stores.json
- **类目匹配改进**：pg_trgm 直接取最高相似度 + jieba 分词末级类目 + 泛化词黑名单（"运动"/"休闲"/"传统"等）

### 本地测试基准

| 管线 | Ozon 产品 ID | 状态 |
|------|-------------|------|
| 1688 直连（喷水玩具） | `5663394290` | ✅ |
| Ozon 跟卖（泡沫喷壶） | `5663485462` | ✅ |

### 稳定性测试

| 测试 | 结果 |
|------|------|
| Worker 重启恢复 | ✅ PG 持久化 |
| 无效信封 | ✅ 401 不崩溃 |
| 超时 (5s) | ✅ 自动重试 2 次 |
| 限流 10/min | ✅ 429 |
| 并发 7 任务 | ✅ 全部入队 |
| Chrome 崩溃恢复 | ✅ 自动重启 + 登录保持（graph 命令偶发 probe 失败，直接 CdpTab 操作正常） |

---

## 历史更新（v0.4.0）

### Skill 信封增强

- **定价参数注入**：`build_graph_envelope` 从 `get_store_profile()` 读取 `margin_rate`/`commission_rate`/`fx_buffer`，写入 `extensions` → Worker 直接用，不调 Ozon API
- **Ozon 真实佣金率**：调用 `fetch_product_commissions()` 获取产品级佣金，覆盖店铺默认值
- **description 字段**：从 CDP 数据提取 1688 商品描述，传入 `draft.description`（不再为空），Worker LLM 用做俄语翻译源材料
- **店铺配置扩展**：`set_store` 新增 `--margin-rate`/`--commission-rate`/`--fx-buffer`（`None` 哨兵支持清零）

### Skill 缓存机制

- **模块**：`scripts/lib/cache.py`（磁盘 JSON，命名空间 + SHA256 key + TTL）
- **集成**：`ak_1688_client.get_product_details()`（24h）、`ozon_widget.fetch_product_info()`（1h）、`ozon_image_search.search_by_image_cdp()`（6h）
- **命令**：`python3.12 scripts/cli.py cache --stats` / `--clear`
- **降级数据不缓存**：Widget 只缓存有 title 或 price 的有效数据

### Skill 日志增强

- **信封完整性审计**：记录 title/images/weight/dimensions/category/commission 各字段是否齐全
- **定价参数审计**：记录 margin_rate/commission_rate/fx_buffer 及其来源（store_config/ozon_api）
- **图搜结果审计**：记录 result_count、best_title、image_url

### Worker 定价修正

- **变体公式统一**：变体公式从 `* (1+commission)` 改为 `/ (1-commission)`，与主公式一致
- **除零保护**：变体复用主公式的 `commission_divisor`（floor 0.9）
- **CNY 不加 fx_buffer**：变体 CNY 定价不再错误应用汇率缓冲

### CDP 稳定性修复

- **登录检查**：从 `document.cookie` 改为页面行为检测（导航到产品页，检查是否加载真实内容），HttpOnly cookie 不可读
- **Stealth 注入**：`_check_1688_login_live` 打开前注入 `STEALTH_JS` + `REALISTIC_UA`
- **Profile 匹配**：`_resolve_browser_session` 不再因 profile 不匹配杀 Chrome——CDP 可用就直接用
- **CdpConnection 泄漏**：3 个函数 finally 块增加 `conn.close()`，find_tab 结果改为 `tab.close()` 而非只关 WebSocket
- **结果标签页去重**：图搜轮询时记录已有标签页 ID，只匹配新标签页（防止复用旧结果）

# AGENTS.md — ozon-worker 工作区

本文件是工作区级导航。两个子项目各有更详细的文档，改动前请先读对应文档（见「深入阅读」）。

## 工作区概述

两段式 Ozon 上架系统，职责严格分离：

| | Skill | Worker |
|---|---|---|
| **角色** | Agent 调用的工具（ZCode/Claude Code 等） | 云端 Docker 管线，消费信封完成上架 |
| **位置** | 客户本地 | 云端服务器（宝塔/Docker） |
| **入口** | `skill/SKILL.md`（Agent 操作手册） | `worker/src/main.py`（FastAPI + CLI） |
| **职责** | 1688 CDP 抓取 → 组装 GraphInput 信封，**不上架** | 接收信封 → 类目→定价→属性→生图→校验→上传→自学习 |
| **接口** | 输出 `GraphInput` JSON（三层结构 `{draft, source, extensions}`） | 输入 `GraphInput`，输出 `GraphOutput` |

接口契约详见 `docs/CONTRACT.md`。Agent 调用指南详见 `skill/SKILL.md`。

## 目录结构

```
ozon-worker/
├── skill/                      # 客户本地:1688 抓取 + 信封组装 (Python ≥3.9, pip)
│   └── scripts/
│       ├── cli.py              # CLI 入口:search / probe / graph
│       ├── cloud_probe.py      # build_graph_envelope + submit_envelope + check_task_status
│       ├── lib/                # ak_1688_client / ozon_api / config_store / ...
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
│   ├── LOGGING.md              # 日志系统架构 + 查看命令 + 故障排查
│   ├── WORKER-TOPOLOGY.md      # ⭐ Worker 拓扑 + 错误映射 + 数据流 + 改代码快速参考
│   ├── PRD-worker-improvements.md    # PRD v1: Worker Pipeline 质量改进
│   ├── PRD-worker-stability-v2.md    # PRD v2: 稳定性改进（成功率44%→58%）
│   ├── PRD-worker-stability-v3.md    # PRD v3: 价格修正 + 5个失败产品修复
│   └── ...                     # Ozon API 文档、物流费率 Excel
└── scripts/
    └── ci.sh                   # 本地 CI（lint → test → build）
```

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
| 查询状态 | `GET /api/v1/task_status/{id}` | GET |
| 取消任务 | `POST /api/v1/cancel_task/{id}` | POST |
| 健康检查 | `GET /api/v1/health` | GET |
| Swagger UI | `GET /api/v1/docs` | GET |

鉴权: `token` 字段在请求体中（非 header），通过 Supabase `tokens` 表校验。
限流: 每 token 每分钟 ≤ 10 次（`RATE_LIMIT_PER_MINUTE` 可配置）。

## 架构边界

- **Worker 三层**: FastAPI `/submit_task`(鉴权+入队) → PG 队列 `ozon_product_tasks`(`FOR UPDATE SKIP LOCKED`) → 10 并发 LangGraph worker(`SupabaseTaskProcessor`)
- **Skill 是无状态本地抓取**，不调用任何 Ozon 上架 API
- **编辑时不越界**: 别给 skill 加上架调用，别给 worker 加 1688 抓取
- **错误码**: 统一在 `worker/src/api/errors.py`（12 个 `WorkerErrorCode`）

## 常用命令

| 子项目 | 命令 |
|---|---|
| skill | `pip install -r requirements.txt && playwright install chromium` |
| skill | `python3 scripts/cli.py search "<词>"` / `probe --url <url>` / `graph --item-id <id>` |
| worker | `cd worker && PYTHONPATH=src python3 -m pytest tests/ -v` |
| worker | `bash scripts/local_run.sh -m flow -i '{...}'` 跑全流程 |
| worker | `bash scripts/local_run.sh -m node -n <节点ID> -i '{...}'` 跑单节点 |
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
  - `RATE_LIMIT_PER_MINUTE` — API 限流（默认 10）
  - `MAX_CONCURRENT` — 并发任务数（默认 10）
  - `LOG_FORMAT` / `LOG_LEVEL` / `LOG_FILE` — 日志配置（见 LOGGING.md）
- Skill 环境变量: `WORKER_URL`（Worker 地址）、`MXOU_TOKEN`、`OZON_CLIENT_ID`、`OZON_API_KEY`

## GitHub 仓库

- 地址: https://github.com/halojerry/ozon-worker （私有仓库）
- 克隆: `git clone https://github.com/halojerry/ozon-worker.git`

## 数据初始化

首次部署时 `deploy.sh` 自动运行 `scripts/init_data.py`:
- `CREATE TABLE`（全部表，幂等）
  - 导入类目树 → `category_tree_nodes`（从 `assets/category_tree.json` + `category_tree_ru.json`，中俄双语，各 ~8000 节点）
  - 导入物流费率 → `logistics_rates`（从 `assets/` 下的 Excel，142 条）
  - 重复运行安全：已有数据跳过；`--force` 强制覆盖

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

- **`docs/WORKER-TOPOLOGY.md`** — ⭐ Worker 拓扑与错误处理手册（节点流、错误映射、数据流、改代码快速参考）
- **`docs/CONTRACT.md`** — Skill↔Worker API 契约 v3.0（端点、请求/响应、错误码）
- **`docs/LOGGING.md`** — 日志系统架构 + 查看命令 + 故障排查流程
- **`docs/CONVENTIONS.md`** — 分支命名 + commit 规范 + 发版流程
- **`skill/SKILL.md`** — Agent 调用指南（入参、返回格式、提交 Worker、错误处理）
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
- **俄语类目树 ID 映射**：`category_tree_nodes` 存中俄双语（`language=ZH_HANS` / `language=RU`），同一 `description_category_id`/`type_id` 跨语言一致。`assemble_ozon_product_node` 属性 schema + 字典值已切换到 `language=RU`，LLM 不再翻译属性值。
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

- **deepseek-v4-flash reasoning tokens**：该模型默认启用推理，`reasoning_tokens` 消耗 `max_tokens` 配额。翻译/生图 prompt 的 `max_tokens` 至少设为 200，否则输出为空。
- **DESCRIPTION_DECLINE 多重根因**：
  1. 产品名含拉丁/中文字符 → `ozon_validate_node` 应阻断（已修复）
  2. 属性值含中文 → 俄语类目树 ID 映射解决（`language=RU` 字典值直连）
  3. 图片含文字/URL/物流信息 → AI 模型局限性，标记为 warning 不阻断（已修复）
  4. 类目不匹配 → 已添加一致性检查 + 俄语标题重新匹配（已修复）
- **LLM 翻译对专业术语失败率高**：3D 打印、儿童用品等词导致翻译三连失败，最终用错误类目名作兜底标题。已改为优先用 1688 属性关键词生成标题（已修复）。
- **物流费率表为空导致价格虚高**：兜底费率 `weight * 0.15 CNY` 是实际费率的 3-4 倍。部署时必须确保 `import_logistics.py` 先于 worker 执行（已修复）。
- **属性ID细节**：
  - 9782（危险品等级）：字典属性，某些类目必填，不能跳过
  - 22508（品牌注册国）：自由文本属性，需硬编码为"Китай"
  - 23487（制造商）：自由文本属性，用 `draft.supplier` 填充
  - 23536（标记码）：Ozon 自动设置，必须跳过
- **`validation_retry_loop` 三大缺陷**（已修复）：
  1. `state.draft` 为空 → LLM 收不到产品上下文 → 回退到 `ozon_payload.items[0].name`
  2. `recheck_status_node` 在 `imported` 即宣告成功 → 已改为额外轮询 `moderate_status`
  3. `type_id=0` 导致 `/v3/product/import` 报错 → 模板含 `type_id` 字段
- **`init_data.py` `walk` 函数**：`description_category_id` 需从父节点继承，`disabled` 字段 NOT NULL 需填 `false`。中文树是 `{"result":[...]}` dict，俄语树是 `[...]` 直接 list，walk 调用需兼容两种格式。

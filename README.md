# POUNDING Ozon 上架助手（ozon-worker）

> 1688 选品 → AI 生图 → 自动上架 Ozon 的一站式跨境电商工具。把「人工选品、翻译、做图、填属性、等审核」的繁琐流程，压缩成几条命令。

当前版本：**v0.35.0**（详见 [`CHANGELOG.md`](CHANGELOG.md)）

---

## ✨ 核心特性

- **全自动上架管线**：1688 商品 → 类目匹配 → 定价 → 属性填充 → AI 生图（10 张）→ 俄语化 → 校验 → 上传 Ozon → 审核状态跟踪，全程无需人工干预
- **类目映射学习闭环**：每次成功上架回写类目映射缓存，越用越准（`category_mapping` 学习表 + 同义词外置配置）
- **Ozon 跟卖双模式**：`hand`（防侵权，CREATE 重建）/ `api`（import-by-sku 复制），自动降级
- **竞品属性优先填充**：跟卖时用竞品俄语属性值填充，缺失重量/尺寸自动用竞品数据兜底
- **生图模型路由**：主图/social_proof 用 gpt-image-2，其余节点用 banana；超时自动降级，提示词 JSON 外置热加载
- **危险品安全兜底**：必填危险品等级属性只选「非危险」默认值，绝不盲填
- **中文零容忍**：标题/描述/属性/制造商名全部俄语化，含中文直接拒绝上传（Ozon 硬性要求）
- **定价引擎**：CNY 店铺定价（含佣金、物流、利润率），重量/尺寸异常自动修正，入队前防线拦截非法信封
- **可观测性**：结构化 JSON 日志、链路追踪（trace_id/task_id）、Sentry 任务异常/超时上报、任务进度持久化
- **自动更新**：Skill 每次命令静默检测新版本，sha256 校验 + 备份 + 失败回滚

## 🏗️ 系统架构

两段式设计，职责严格分离：

```
┌────────────────────────── 客户本地 ──────────────────────────┐
│  Skill（pounding-ozon-probe）                                │
│  1688/Ozon CDP 抓取 · 以图搜款 · 信封组装 · 不上架           │
│  输出 GraphInput 信封 {draft, source, extensions}            │
└─────────────────────────────┬────────────────────────────────┘
                              │ HTTPS
┌─────────────────────────────▼────────────────────────────────┐
│  Worker（云端 Docker + PostgreSQL）                           │
│  鉴权 → 类目匹配 → 定价 → 属性填充 → AI 生图 → 校验 →        │
│  上传 → 审核跟踪 → 学习回写（~24 个 LangGraph 节点）          │
└──────────────────────────────────────────────────────────────┘
```

- **Skill**：客户本地 Python 工具，自动启动 Chrome（CDP 抓取，保留登录态），核心库 Cython 编译为二进制分发（源码保护），依赖仅 `requests` / `websocket-client` / `Pillow` / `sentry-sdk`（Sentry 上报，缺失时静默降级）
- **Worker**：云端 FastAPI + LangGraph 工作流，默认 30 并发任务（`MAX_CONCURRENT` 可配），PG 队列 + 断点恢复 + 僵尸任务清理
- **接口契约**：`GraphInput` 三层信封结构（`worker/src/graphs/state.py`），详见 [`docs/CONTRACT-v4.md`](docs/CONTRACT-v4.md)

### 三条业务管线

| 管线 | 命令 | 流程 |
|------|------|------|
| **A. 1688 选品上架** | `graph --url <1688 URL>` | 抓 1688 → 组装信封 → Worker 全流程上架 |
| **B. Ozon 跟卖** | `follow --ozon-url <Ozon URL>` | 抓竞品 → 以图搜 1688 同款 → 跟卖管线（防侵权/复制双模式） |
| **C. Ozon 选品发现** | `discover --keyword "..."` | 采集 Ozon 商品 → 蓝海评分 → 1688 匹配 + 利润计算 → 确认提交 |

## 🤝 Skill 与 Worker 如何配合

两段式流水线，交接物是 **`GraphInput` 信封**。Skill 只负责「采集 + 组装」，Worker 只负责「消费 + 上架」，中间通过一条 HTTPS 请求衔接：

```
Agent（ZCode / Claude Code 等）
  │  调用 CLI 命令（graph / follow / discover / ...）
  ▼
Skill（客户本地）
  │  1688 / Ozon CDP 抓取 · 以图搜款 · 变体折叠 · 信封组装
  │  ⚠️ 不上架 —— 只产出 GraphInput 信封
  ▼
POST /api/v1/submit_task（GraphInput JSON）
  ▼
Worker（云端 Docker）
  │  类目匹配 → 定价 → 属性填充 → AI 生图 → 俄语化 → 校验 → 上传 → 审核跟踪 → 学习回写
  ▼
返回 task_id → Agent 用 `query <task_id>` 轮询状态 → 向用户汇报
```

### 职责分工

| | Skill | Worker |
|---|---|---|
| **位置** | 客户本地 | 云端 Docker |
| **角色** | Agent 调用的工具（ZCode / Claude Code 等） | 消费信封完成上架 |
| **入口** | `skill/SKILL.md`（Agent 操作手册） | `worker/src/main.py`（FastAPI + CLI） |
| **职责** | 1688 / Ozon CDP 抓取、以图搜款、组装信封 | 类目 → 定价 → 属性 → 生图 → 校验 → 上传 → 自学习 |
| **硬约束** | **不上架**（不调用任何 Ozon 上架 API） | **不抓取**（只消费信封，不做 1688 采集） |

### GraphInput 信封（三层结构）

定义于 `worker/src/graphs/state.py`，契约详见 [`docs/CONTRACT-v4.md`](docs/CONTRACT-v4.md)：

```json
{
  "token": "sk-...",
  "ozon_client_id": "4718259",
  "ozon_api_key": "...",
  "envelope": {
    "draft": {
      "item_id": "980815374096",
      "title": "宠物自动饮水器...",
      "images": ["https://cbu01.alicdn.com/..."],
      "weight": 227,
      "dimensions": {"length": 120, "width": 80, "height": 60},
      "purchase_cost": 5.5,
      "purchase_url": "https://detail.1688.com/offer/980815374096.html",
      "attributes": {"品牌": "...", "材质": "..."},
      "ozon_category": {"description_category_id": "17028929", "type_id": "504866264"}
    },
    "source": {"purchase_url": "...", "purchase_cost": 5.5},
    "extensions": {
      "margin_rate": 0.25,
      "commission_rate": 0.10,
      "fx_buffer": 0.05,
      "follow_sell": true
    }
  }
}
```

关键约定：

- **`draft`** — 产品数据：`title` / `images[]` / `weight`（克）/ `dimensions`（mm，1688 cm 已在 Skill 层 ×10）/ `purchase_cost`（CNY，已含 1688 国内运费）
- **`source`** — 采购源信息；**`extensions`** — 定价配置 + `follow_sell` 跟卖标记（Worker 据此走跟卖管线）
- **单产品上传**：Skill 层把多变体折叠为单产品（一个 1688 item = 一个 Ozon 产品卡），Worker 零改动
- **多 SKU**：`variants` 最多 1 个元素（Skill 层已折叠），其余字段平铺在 `draft` 下

### 一次上架的完整时序

1. Agent 收到用户请求 → 按「意图路由」选管线（见下节）
2. Agent 调用 Skill 命令（`graph` / `follow` / `discover` / ...）→ Skill 本地 Chrome CDP 抓取 + 组装信封
3. Skill 将 `GraphInput` POST 到 Worker `/api/v1/submit_task` → 返回 `task_id`（UUID）
4. Worker 云端执行约 24 个 LangGraph 节点：鉴权 → 类目匹配 → 定价 → 属性填充 → AI 生图（10 张）→ 俄语化净化 → Ozon 校验 → 上传 → 审核状态轮询 → 类目映射学习回写
5. Agent 用 `query <task_id>` 轮询进度 → 向用户汇报结果（Ozon 商品链接、利润率、审核状态）

## 🤖 Agent 如何调用

Agent（ZCode / Claude Code 等）的操作手册是 **`skill/SKILL.md`** —— 意图路由决策表、命令速查、决策边界、越界行为全在其中，Agent 必须先读它再动手。

### 触发规则

用户消息出现以下关键词时触发 `pounding-ozon-probe` skill：1688 / Ozon 链接、上架/上传/发布产品、跟卖、选品/蓝海/推荐、以图搜款。

### 意图路由（决策表摘要）

先判断用户意图再选管线，**每次操作前重新判断**，不因上下文惯性选择：

| 用户输入 | 管线 | 命令 |
|---|---|---|
| 1688 商品链接 | A. 直接上架 | `graph --url <1688 URL>` |
| Ozon 商品链接 | B. 跟卖 | `follow --ozon-url <URL>` |
| Ozon 搜索页/类目页 | C. 采集选品 | `discover --url <URL>` |
| 图片（无 URL） | D1. 以图搜款 | `image_search --image <URL>` → 展示候选 → 用户确认 → `graph` |
| 「趋势/热卖/爆款」+ 品类 | E. 趋势选品 | agent 先 web_search + LLM 提炼关键词 → `discover --keyword <关键词>`（命令层无 trend，必须先 web_search 再选品） |
| 多 URL / 批量 | F. 批量处理 | `batch_test --urls-file urls.txt --submit` |
| 「蓝海」无 URL | C. 跟卖选品 | `discover --keyword "..."` |
| 「选品/上架」无 URL | D. 选品上架 | `discover --keyword "..."` → 候选展示 → 确认提交 |
| 指代不清 / 数量不符 / 重上 | — | 追问核对，禁止猜测 |

### 决策边界

| 操作 | 策略 |
|---|---|
| `check`、`set_store`、`set_token`、`set_ak`（环境准备） | 自动执行，无需确认 |
| `graph` / `follow`（用户已给明确 URL） | 自动执行 |
| `discover` 选品后的最终提交、批量操作 | **必须用户明确确认** |
| 利润率高低、候选产品优劣 | 展示不表态，让用户决定 |

### 对 Agent 的硬约束

- **只用 SKILL.md 中的命令**，不自己写 Python 代码、不用 requests/urllib 抓取
- 不给 Skill 加上架调用、不给 Worker 加 1688 抓取（架构边界）
- 「提交/上架」前必须等用户明确确认
- 管线 E（趋势选品）禁止跳过 web_search 直接跑

## 🚀 快速开始

### 1. 部署 Worker（云端，一次性）

```bash
cd deploy
cp .env.example .env        # 填入 PGDATABASE_URL / SUPABASE_URL / SUPABASE_KEY 等
bash deploy.sh              # 一键部署（Docker Compose：Worker + PostgreSQL，自动初始化数据）
```

更新：`bash deploy/update.sh`（git pull → rebuild → restart）或 `bash deploy/cos-update.sh`（读取 COS manifest 一键升级，sha256 校验 + 自动回滚）。完整指南见 [`docs/DEPLOY.md`](docs/DEPLOY.md)。

### 2. 安装 Skill（客户本地）

```bash
pip install -r requirements.txt        # Python ≥3.12；Chrome 自动启动，无需 Playwright
python3.12 scripts/cli.py check        # 环境检查：自动启动 Chrome、检测登录态、验证凭证
```

凭证（1688 AK / Ozon API Key / 店铺配置）存于本地 `data/config/`，用 `set_token` / `set_ak` / `set_store` 配置。

### 3. 上架第一个商品

```bash
# 1688 选品上架
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html"

# Ozon 跟卖
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/"

# 查看任务状态
python3.12 scripts/cli.py query <任务ID>
```

## 📦 命令速查

| 命令 | 说明 |
|------|------|
| `check` | 环境检查 + 自动启动 Chrome + 凭证验证 |
| `graph --url <1688 URL>` | 1688 选品上架（管线 A） |
| `follow --ozon-url <URL>` | Ozon 跟卖（管线 B） |
| `discover --keyword "..."` | Ozon 蓝海选品，表格分析 + 批量货源（管线 C） |
| `image_search --image <URL>` | 以图搜款（1688 网页版图搜） |
| `get_ak` | 浏览器自动获取 1688 AK |
| `batch_test --urls-file urls.txt` | 批量处理 URL 列表 |
| `query <任务ID>` | 查询任务状态 / 耗时 / 产品明细 |
| `cache --stats / --clear` | 磁盘缓存查看 / 清理 |

> Agent 调用完整手册见 [`skill/SKILL.md`](skill/SKILL.md)。维护者代码说明见 [`skill/README.md`](skill/README.md)。

## 🔌 Worker API 端点

| 功能 | 路径 | 方法 |
|------|------|------|
| 提交任务 | `/api/v1/submit_task` | POST |
| 鉴权验证 | `/api/v1/auth/verify` | POST |
| 查询状态 | `/api/v1/task_status/{id}` | GET |
| 取消任务 | `/api/v1/cancel_task/{id}` | POST |
| 任务统计 | `/api/v1/task_statistics` | GET |
| 进度查询 | `/progress/{run_id}` | GET |
| 健康检查 | `/api/v1/health` | GET |
| API 文档 | `/api/v1/docs` | GET |

（旧路径同时保持兼容。鉴权通过请求体 `token` 字段，经 Supabase `tokens` 表校验。）

## 🗂️ 目录结构

```
ozon-worker/
├── skill/                  # 客户本地：抓取 + 信封组装（Python ≥3.12）
│   ├── SKILL.md            # ⭐ Agent 操作手册
│   ├── scripts/cli.py      # CLI 入口（check/graph/follow/discover/...）
│   └── scripts/lib/        # CDP 客户端、1688/Ozon API、缓存、凭证
├── worker/                 # 云端：LangGraph 上架工作流（Docker）
│   ├── src/main.py         # FastAPI + CLI 入口
│   ├── src/graphs/         # 主图编排 + ~24 个节点
│   ├── src/storage/        # PG 队列 / checkpoint
│   ├── src/utils/          # Ozon 客户端、定价、生图、日志
│   ├── assets/             # 类目树 JSON、物流费率、Ozon API 文档
│   └── config/             # LLM prompt / 生图提示词配置（热加载）
├── deploy/                 # Docker Compose + 一键部署/更新脚本
├── docs/                   # 架构、契约、部署、日志、PRD 文档
├── scripts/ci.sh           # 本地 CI（lint → test → build）
├── AGENTS.md               # ⭐ AI Agent 工作区导航（改代码前必读）
└── CHANGELOG.md            # 版本变更记录
```

## 📚 文档导航

| 文档 | 内容 |
|------|------|
| [`skill/SKILL.md`](skill/SKILL.md) | Agent 调用指南（Chrome 启动、选品、跟卖、以图搜款、批量处理） |
| [`docs/CONTRACT-v4.md`](docs/CONTRACT-v4.md) | Skill ↔ Worker API 契约 v4.0（端点、信封结构、错误码、节点合约） |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Worker 云端部署完整指南（Docker、Nginx、HTTPS、运维） |
| [`docs/WORKER-TOPOLOGY.md`](docs/WORKER-TOPOLOGY.md) | Worker 拓扑 + 错误映射 + 数据流 + 改代码快速参考 |
| [`docs/LOGGING.md`](docs/LOGGING.md) | 日志系统架构 + 查看命令 + 故障排查 |
| [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | 分支命名 + commit 规范 + 发版流程 |
| [`AGENTS.md`](AGENTS.md) | 工作区导航 + 最近更新 + 已知坑（改代码前必读） |

## 🧪 测试

```bash
# Worker 单元测试（Mock 模式，无需 PG/GPU）
cd worker && PYTHONPATH=src python3 tests/test_full_pipeline_mock_images.py

# Worker 全量测试（需要 PG）
cd worker && PYTHONPATH=src python3 -m pytest tests/ -v

# Skill 单节点冒烟
cd skill && python3.12 scripts/cli.py graph --url "<1688 URL>"
```

## 🔄 版本与更新

- 版本号：`VERSION` 文件（语义化版本）
- 变更记录：`CHANGELOG.md`
- Worker：`deploy/update.sh` 或 `deploy/cos-update.sh` 升级
- Skill：每次命令静默检测新版本，`SKILL_AUTO_UPDATE=0` 退回手动

## ⚠️ 已知约束

- **单产品上传**：一个 1688 item = 一个 Ozon 产品卡（Skill 层自动折叠多 SKU 变体）
- **品牌强制「无品牌」**：所有产品默认 `Нет бренда`，不写品牌名
- **描述强制净化**：上传前移除拉丁/中文/URL/电话/营销词
- **危险品必填属性安全兜底**：9782 只填「非危险」默认值

## 📄 License

私有仓库。详细开发规范见 [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md)。

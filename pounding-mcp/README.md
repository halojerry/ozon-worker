# pounding-mcp

把 ozon-worker 的能力包装成 MCP 工具（25 个：20 个 skill CLI 封装 + 5 个 worker REST 直调），供 DeepSeek Harness（dsh）等 Agent 调用。

> 薄封装：业务逻辑（CDP 采集 / 选品引擎 / 上架组装）全在 `../skill/` 与 `../worker/`，这里只做「参数映射 CLI + 调 subprocess」和「HTTP 直调 worker REST」。

## 目录

```
pounding-mcp/
├── pyproject.toml            FastMCP 依赖 + 入口（pounding-mcp = pounding_mcp.server:main）
├── cordis.patch.yml          挂载到 dsh 的 patch 配置示例
├── pounding_mcp/
│   ├── server.py             FastMCP 工厂 + 25 个工具（20 CLI 封装 + 5 worker REST 直调）
│   ├── skill_runner.py       run_skill_command 薄封装
│   ├── tasks.py              命令运行记录（run_and_record）
│   ├── tasks_server.py       运行记录查询服务（POST /ask 对话入口）
│   ├── router.py             意图路由层（URL 正则 + 意图词表 → pipeline）
│   ├── worker_http.py        worker REST 直调（analyze_store/run_store_action/report_issue/...）
│   └── http_server.py        tasks_server 的 HTTP 包装
└── tests/                    pytest（router 意图路由 / smoke / store / error-report 工具）
```

## 快速开始

```bash
# 1. 安装（建议独立 venv）
cd pounding-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. 配置 skill 路径（环境变量）
export OZON_SKILL_DIR=/Volumes/os/dev/ozon-worker/skill
export OZON_SKILL_PYTHON=/Volumes/os/dev/ozon-worker/skill/.venv314/bin/python3

# 3. 跑冒烟测试
python -m pytest tests/ -v

# 4. 手动验证单个工具
pounding-mcp   # 启动 MCP stdio 服务
```

## 挂载到 dsh

参照官方 `examples/mcp-memory`，把 `cordis.patch.yml` 的 `insert` 段合并到
`$DSH_HOME/profiles/web/cordis.patch.yml`，或临时：

```bash
dsh web --patch "$PWD/cordis.patch.yml"
```

工具在 dsh 中可见为 `mcp__pounding__<tool>`（如 `mcp__pounding__graph`）。

### uvx 安装（发包后）

PyPI 发包后，dsh 的 MCP 配置可直接用 uvx 拉起（无需本地 venv）：

```yaml
command: uvx
args: ["pounding-mcp"]
```

即 `uvx pounding-mcp`。过渡期（未发包/本地开发）仍用：

```bash
cd pounding-mcp && pip install -e .
python -m pounding_mcp.server
```

（与 `docs/PLAN-harness-mcp-adoption-v1.md` §1 的接入方式一致。）

## 安全分级（审批在 dsh 侧）

本 server **不做审批**——它是独立进程，拿不到 dsh 的 `ctx.approval`。
审批 answerer 由 **dsh-web-app 自带**（`@deepseek-ai/dsh-host-apiproxy`，`cordis.patch.yml:105-106` 以 `api-gateway` id 挂载），非本 server 职责。
三级安全门控（read/write/destructive）由 dsh 侧的 `tools/pre-execute` 钩子实现，
依据 `docs/ozonharness/MCP-TOOLS.md` §七 的 SAFETY_MAP。

## 工具清单（25 个）

### skill CLI 封装（20 个）

参数 1:1 映射 CLI flag，经 subprocess 调 `../skill/scripts/cli.py`。

| 工具 | 说明（docstring 首句） |
|---|---|
| `check` | 诊断前置条件（Chrome / 凭证 / Worker / Ozon API 是否就绪）。只读。 |
| `list_stores` | 列出所有已配置的 Ozon 店铺。只读。 |
| `set_store` | 配置 Ozon 店铺凭证。写操作（敏感）。 |
| `set_token` | 设置 MXOU 平台 token。写操作（敏感）。 |
| `set_ak` | 手动设置 1688 Access Key。写操作（敏感）。 |
| `get_ak` | 浏览器自动获取 1688 AK。写操作（需本地 Chrome）。 |
| `search` | 搜索 1688 商品。只读；auto_submit=True 时批量提交上架（dsh 侧审批）。 |
| `probe` | CDP 探针抓取 1688 商品详情页。只读。 |
| `image_search` | 以图搜款（上传图片找 1688 同款）。只读。source: aibuy/ak/cdp。 |
| `category` | 查询 Ozon 类目（关键词 → 候选类目）。只读。lang: ZH_HANS/EN/RU。 |
| `follow` | 跟卖 Ozon 商品（竞品 → 找 1688 同款 → 上架）。auto_submit/to_box 触发 dsh 侧审批。 |
| `discover` | Ozon 选品 v2（采集 → 分析 → 挑货）。只读；auto_submit/to_box/fission 触发 dsh 侧审批。 |
| `discover_multi` | 多关键词批量选品。keywords 逗号分隔。auto_submit/to_box 触发 dsh 侧审批。 |
| `discover_task` | 任务式全自动选品（漏斗 v2）：采集 → ai 粗筛 → 自动 1688 匹配（限额+早停）→ 利润精筛。 |
| `seller` | 卖家店铺全产品运营分析（跟卖前 20 名卖家 → 店铺选品）。只读。 |
| `queries` | what-to-sell 榜单查询。type: all-queries/ozon-bestsellers/market-bestsellers。只读。 |
| `graph` | 组装 GraphInput 信封并提交上架。默认直接提交（dsh 侧 pre-execute 审批）。 |
| `query` | 查询 Worker 任务状态。只读。watch=True 轮询直到终态。 |
| `update` | 检查并应用 skill 自动更新。写操作（维护）。 |
| `cleanup` | 清理缓存/临时数据。默认预演（--all --dry-run）不真删；破坏性操作（dsh 侧双重确认）。 |

### worker REST 直调（5 个）

直接 HTTP 调 worker API（非 skill CLI subprocess），鉴权/租户与 REST 同源。

| 工具 | 说明（docstring 首句） |
|---|---|
| `analyze_store` | 整店分析（读）：利润率/库存/候选清单（summary + profit_trend + 三组清单）。 |
| `run_store_action` | 单店执行（写，dsh 侧审批）：改价/stocks/归档/活动报名/自建促销。 |
| `report_issue` | 用户问题反馈 → 错误报告入 worker 跟踪队列（模板化，v0.69）。 |
| `list_error_reports` | 查看本租户已提交的错误报告（列表或单条详情，只读）。 |
| `get_task_forensics` | 任务取证一站式只读聚合（v0.70）：任务快照 + 上架留存 + 类目/属性匹配审计。 |

详见 `../docs/ozonharness/MCP-TOOLS.md`。

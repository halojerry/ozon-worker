---
name: pounding-ozon-probe
version: "0.38.0"
agent_created: true
description: >
  Ozon 跨境电商上架工具。此技能在以下场景触发：用户发送 1688 商品链接时
  直接上架到 Ozon；用户发送 Ozon 商品链接时跟卖；用户发送图片时以图搜款找
  1688 同款；用户请求选品（找蓝海/热卖/趋势商品）时搜索 Ozon 中国站并匹配
  1688 货源；用户发送多个链接时批量处理。覆盖选品、跟卖、上架、以图搜款、
  趋势选品全流程。
---

# pounding-ozon-probe — 工具手册

## 0. 定位 Skill 目录

所有命令在 skill 根目录（含 `scripts/cli.py` 的目录）下执行。确定方式按优先级：
`$SKILL_DIR` → 当前目录含 `scripts/cli.py` → 向上级目录查找。Python 要求 ≥ 3.12。

## 1. 意图路由

先判断用户意图，再选管线。每次操作前重新判断，不因上下文而惯性选择。

> **完整意图路由决策树见 `references/command-reference.md`**（各管线触发条件 + 输入输出）。
> 要点速记：① 有 URL 先判类型（1688商品页→A / Ozon商品页→B / 搜索类目页→C / 批量→F）；
> ② 无 URL 按意图词：趋势→E（先 web_search）、跟卖→C、裂变→C `--fission`、上架→D、蓝海→C；
> ③ 指代不清 / 数量不符 / 重上 → 必须追问核对，禁止猜测。

### 关键规则

- **有 URL 时先判类型**：搜索页/类目页 URL 走 C（discover --url），**绝不去 B 跟卖单商品**（解析会失败）
- 无 URL = 按意图词优先级选 C / D / E
- **蓝海评分只在管线 C 中使用**；「蓝海」默认路由 C，避免与 E 的「趋势」触发词冲突
- 管线 C（跟卖选品）和管线 D（选品上架）命令相同（discover），仅用途叙述与是否 `--auto-submit` 的差别；discover **无 follow_type 参数**，跟卖标记由 follow 内部注入
- ⚠️ **趋势选品**：命令层无 trend，流程 = agent web_search + LLM 提炼 → `discover --keyword`（细则见 `references/trend-selection.md`）
- ⚠️ **裂变选品（discover --fission）**：种子基础上再深挖一层，有硬性预算默认不无限跑（细则见 `references/discover-fission.md`）
- ⚠️ **管线 B 降级语义变化**：Ozon 页面禁止复制时降级走管线 A（直采重建）——是**重建直采卡，不是跟卖复制**（offer_id/定价都会变），必须向用户说明
- **复合意图消歧**：输入同时含趋势词（爆款/热卖）与上架词（上架/整一批）时，追问：「要趋势出款（agent 分析 + discover）还是按词选品直接上（D）？」；批量上架需链接列表走 batch，选品数量不是批量参数
- **数量词**：「选 N 个/来 N 个」→ 追问意图（跟卖/上架/趋势选品）+ 确认 N 是挑选规模还是硬性要求；discover 是展示候选→挑选，不直接产 N 个结果
- **截图处理**：收到的图片需先保存为本地路径或转 URL 供 `image_search --image` 使用；**截图即目标商品时**，可先向用户索要该商品 1688 链接走 A（省图搜配额），图搜用于「找同款」场景
- **URL + 弱化意图词**：用户说"看看/评估/能不能上"时，先用 `graph --no-submit` 展示再等确认，不直接提交

## 2. 命令速查表

> 完整参数与示例见 `references/command-reference.md`（或 `--help`）。

| 命令 | 用途 | 关键参数 | 副作用 | 适用场景 |
|---|---|---|---|---|
| `check` | 验证环境（全量诊断，缺依赖/无浏览器也继续探测 Worker/MXOU/凭证） | 无 | 无 | 首次使用 / 排错 |
| `set_store` | 配置 Ozon 店铺 | `--name --client-id --api-key` | 写 `data/config/` | 首次配置 |
| `set_token` | 配置 MXOU_TOKEN | `--token` | 写 `data/config/` | 首次配置 |
| `set_ak` | 配置 1688 AK | `--ak` | 写 `data/config/` | 首次配置 / AK 过期 |
| `update` | 检查并应用自动更新（跨进程锁，并发命令不会竞态破坏安装） | 无 | **覆盖 skill 文件**（备份 + 保留 data/） | 版本升级 |
| `migrate_profile` | 迁移 Chrome profile 到统一路径（旧 `data/browser/profile` → `profiles/1688/default`） | `[--apply] [--check]` | 复制 profile（默认 dry-run 不写） | 升级后首次使用（登录态迁移） |
| `query` | 查询 Worker 任务状态 | `<任务ID>`（位置参数，submit/follow 返回的 UUID） | 只读，无副作用 | 提交后查进度/成败/产品明细 |
| `seller` | 卖家店铺全产品运营分析 | `--seller-id [--max-products] [--max-skus]` | 查 seller.ozon.ru 运营指标（逐 SKU，受限速） | 跟卖前20名卖家 → 店铺选品 |
| `get_ak` | 浏览器自动获取 1688 AK | `--timeout` | 无 | AK 过期时刷新 |
| `list_stores` | 列出已配置店铺 | 无 | 无 | 查看配置 |
| `graph` | 1688 上架 | `--url/--item-id --store [--no-submit] [--category-query] [--ozon-ref-url]` | 提交 Worker（除非 `--no-submit`） | 用户发 1688 商品链接（`--ozon-ref-url` 传 Ozon 竞品参考链接, 同类目属性复用） |
| `follow` | Ozon 跟卖 | `--ozon-url --store [--auto-submit]` | 提交 Worker（加 `--auto-submit`） | 用户发 Ozon 商品链接 |
| `image_search` | 以图搜款 | `--image [--source cdp] [--sort] [--limit]` | 耗 1688 图搜配额 | 用户发图片 / 找同款 |
| `discover` | Ozon 选品 | `--keyword/--url [--local] [--rules] [--export] [--auto-submit] [--fission] [--max-depth 2] [--non-interactive] [--blue-ocean-source csv --blue-ocean-csv <path>]` | 查 seller.ozon.ru 运营指标；`--auto-submit` 提交 Worker；`--fission` 裂变选品；`--blue-ocean-source` 用 all_queries CSV 反哺蓝海评分（默认找 `/tmp/queries_all.csv`，无数据降级原流程） | 找蓝海 / 跟卖选品 / 趋势选品执行 / 裂变选品；货源分析后自动生成 `data/discovery/analysis_*.md`\|`json` 结构性分析文档（Agent 可直接汇报） |
| `search` | 1688 关键词搜索 | `query [--page-size]` | 耗 1688 搜索配额 | 按词找货 |
| `probe` | CDP 探针抓取单个 1688 商品 | `--url [--timeout]` | 无 | 调试单个商品 |
| `queries` | what-to-sell 蓝海/榜单查询 | `--type all-queries\|ozon-bestsellers\|market-bestsellers [--keyword] [--sku] [--category-id] [--price-min] [--price-max] [--export csv\|json] [--output <path>]` | 查 seller.ozon.ru what-to-sell SPA 数据；成功后**自动上报 worker PG**（异步，无 token 跳过）；可 `--export` 本地 CSV/JSON | 选品前查关键词蓝海（count/ca/uniq_sellers）、Ozon 畅销榜、跨平台畅销榜 |
| `batch_test.py` | 批量处理 URL 列表 | `--urls-file [--submit] [--wait] [--dry-run]` | 提交 Worker（加 `--submit`） | 批量上架 / 回归 |

## 3. 决策边界

| 操作 | 策略 | 说明 |
|------|------|------|
| `check`、`pip install`、`set_store`、`set_token`、`set_ak` | 自动执行 | 环境准备类操作，无需确认 |
| `graph`、`follow`（含 `--auto-submit`） | 自动执行 | 用户给了明确 URL，直接上架 |
| `discover` 选品后的最终提交 | 必须确认 | 展示候选列表，等用户说"提交" |
| 批量处理 | 必须确认 | 影响面大，需用户明确确认 |
| 利润率高低、候选产品优劣 | 展示不表态 | 陈列数据，不替用户判断 |

## 4. 常见越界行为

> 越界行为对照表（错误行为 → 后果 → 正确做法）见 `references/anti-patterns.md`。
> 核心纪律：只用本文档中的命令、不自己写代码抓取、提交前等用户明确确认、每次操作前重读 §1。

## 5. 参考文件索引

| 文件 | 何时读取 | 内容概要 |
|------|----------|----------|
| `references/command-reference.md` | 选定管线前 → 查意图路由决策树；选定后执行前 → 查完整参数和示例 | 意图路由决策树 + 各管线触发条件、完整参数、输入输出、示例 |
| `references/trend-selection.md` | 用户要"趋势/热卖/新品风向"时 → 查趋势选品流程 | web_search → LLM 提炼 → discover 执行三步法 + 纪律 |
| `references/discover-fission.md` | 用户要"找更多同类/挖同行货源"时 → 查裂变细则 | 裂变选品流程、预算限制、参数、数据字段、注意事项 |
| `references/anti-patterns.md` | 每次操作前 → 自查越界行为 | 越界行为 → 后果 → 正确做法对照表 + 核心纪律 |
| `references/error-codes.md` | 命令执行出错、或用户问进度时 → 查错误码和回复模板 | Worker 错误码表 + 进度查询口径 + CLI 错误处理 + 错误恢复决策 |
| `references/output-schema.md` | 命令执行成功、需要向用户汇报结果时 → 查字段解析和汇报模板 | submit_result / check_task_status / product_summary 字段解析 + JSON 示例 + 汇报模板 |
| `references/env-setup.md` | 首次使用、check 失败、或用户问凭证配置时 → 查环境准备和故障排查 | 环境准备 + 凭证 + check 故障排查 + data/ 目录语义 |
| `envelope_example.json` | 需要确认信封字段结构时 → 查示例。读取路径：`_单SKU选品.envelope`（直采）或 `_跟卖示例.envelope`（跟卖）；`_说明`/`_关键约定` 是文档说明非数据字段 | 完整信封结构示例（单 SKU + 跟卖两种模式） |
| `field_mapping.md` | 需要确认 1688/Ozon 字段如何映射到信封时 → 查映射规则 | 1688/Ozon 字段 → 信封字段的映射规则 |

## 6. 常见问题与升级

- 缺依赖（提示 `pip install`）→ 运行 `pip install -r requirements.txt`
- `graph`/`follow` 提示「缺少依赖模块」或「未找到 scripts.cloud_probe」→ 走 bootstrap 升级（`python3.12 bootstrap_update.py` 或重新下载最新包）

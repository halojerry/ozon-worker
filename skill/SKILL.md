---
name: pounding-ozon-probe
version: "0.62.1"
agent_created: true
compatibility: Requires Python >=3.12, Google Chrome (auto-launched via CDP), network access to 1688/Ozon/Worker
license: Proprietary
description: >
  Ozon 跨境电商上架工具：1688 选品上架、Ozon 跟卖、以图搜款找同款、蓝海/趋势选品、批量处理。
  只要用户消息出现以下任一情况，就必须使用本技能：发送 1688 商品链接（detail.1688.com/offer/…）
  要上架到 Ozon；发送 Ozon 商品链接（ozon.ru/product/…）要跟卖或复制竞品；发送商品图片要找 1688
  同款货源；说"选品/蓝海/热卖/爆款/趋势/有什么好卖的/卖得动"要找 Ozon 货源并匹配 1688 供应商；
  说"上架/上货/上点/整一批/发布产品"要创建 Ozon 商品；发送多个链接要批量处理；问"任务进度/完成了吗"
  要查询上架任务状态。即使没明确说"上架"，只要提到 1688/Ozon 商品、选品、跟卖、图搜、蓝海、趋势，
  就用本技能。关键词：1688、Ozon、ozon.ru、跟卖、选品、蓝海、以图搜款、上架、跨境电商。
---

# pounding-ozon-probe — 工具手册

## 0. 定位 Skill 目录

所有命令在 skill 根目录（含 `scripts/cli.py` 的目录）下执行。确定方式按优先级：
`$SKILL_DIR` → 当前目录含 `scripts/cli.py` → 向上级目录查找。Python ≥ 3.12。

## 1. 意图路由

先判断用户意图，再选管线。每次操作前重新判断，不因上下文而惯性选择。

> 完整意图路由决策树见 `references/command-reference.md`（各管线触发条件 + 输入输出）。
> 要点速记：① 有 URL 先判类型（1688商品页→A / Ozon商品页→B / 搜索类目页→C / 批量→F）；
> ② 无 URL 按意图词：趋势→E（先 web_search）、跟卖→C、裂变→C `--fission`、上架→D、蓝海→C；
> ③ 指代不清 / 数量不符 / 重上 → 必须追问核对，禁止猜测。

### 关键规则

> ① 有 URL 先判类型：搜索页/类目页走 C（discover --url），绝不去 B 跟卖单商品 ② 无 URL 按意图词：
> 趋势→E（先 web_search）、跟卖/蓝海→C、裂变→C `--fission`、上架→D ③ 趋势选品命令层无 trend，
> agent 先 web_search + LLM 提炼再 discover（`references/trend-selection.md`）④ 裂变硬预算默认不无限跑
> （`references/discover-fission.md`）⑤ 管线 B 禁止复制时降级 A（直采重建，offer_id/定价会变，须说明）
> ⑥ 复合意图（趋势+上架）→ 追问「趋势出款还是按词直接上（D）？」 ⑦ 「选 N 个」→ 追问意图 + 规模/硬性
> ⑧ 截图：先转 URL 供 image_search；截图即目标商品 → 索要 1688 链接走 A（省图搜配额）
> ⑨ URL+弱化词（"看看/能不能上"）→ 先 `graph --no-submit` 展示等确认 ⑩ 指代不清/数量不符/重上 → 追问核对
> ⑪ C（跟卖选品）与 D（上架）命令相同（discover），仅 `--auto-submit` 差别；discover 无 `follow_type`

## 2. 命令速查表

> **所有命令都是黑盒**：先跑 `python3 scripts/cli.py <命令> --help` 看用法，**不要读 `cli.py` 源码**（Cython 编译，读了浪费上下文）。命令输出即结果。
> 完整参数与示例见 `references/command-reference.md`。

| 命令 | 用途 | 关键参数 | 副作用 | 适用场景 |
|---|---|---|---|---|
| `check` | 环境检查（全量诊断） | 无 | 无 | 首次使用 / 排错 |
| `set_store` | 配置 Ozon 店铺 | `--name --client-id --api-key [--currency]` | 写 `data/config/` | 首次配置 |
| `set_token` | 配置 MXOU_TOKEN | `--token` | 写 `data/config/` | 首次配置 |
| `set_ak` | 配置 1688 AK | `--ak` | 写 `data/config/` | 首次配置 / AK 过期 |
| `update` | 应用自动更新 | 无 | **覆盖 skill 文件**（备份+保留 data/） | 版本升级 |
| `migrate_profile` | 迁移 Chrome profile 统一路径 | `[--apply] [--check]` | 复制 profile（默认 dry-run） | 升级后迁移登录态 |
| `query` | 查询 Worker 任务状态 | `<任务ID> [--watch]` | 只读 | 查进度/成败/明细 |
| `seller` | 卖家店铺全产品运营分析 | `--seller-id [--max-products]` | 查 seller.ozon.ru（限速） | 跟卖卖家 → 店铺选品 |
| `get_ak` | 浏览器自动获取 1688 AK | `--timeout` | 无 | AK 过期刷新 |
| `list_stores` | 列出已配置店铺 | 无 | 无 | 查看配置 |
| `graph` | 1688 上架 | `--url/--item-id --store [--no-submit] [--ozon-ref-url]` | 提交 Worker（除非 `--no-submit`） | 用户发 1688 商品链接 |
| `follow` | Ozon 跟卖 | `--ozon-url --store [--auto-submit] [--review]` | 提交 Worker（加 `--auto-submit`） | 用户发 Ozon 商品链接 |
| `image_search` | 以图搜款 | `--image [--source cdp] [--sort] [--limit]` | 耗 1688 图搜配额 | 用户发图片 / 找同款 |
| `discover` | Ozon 选品 | `--keyword/--url [--local] [--rules] [--auto-submit] [--fission] [--blue-ocean-source]` | `--auto-submit` 提交 Worker；货源分析后生成 `data/discovery/analysis_*.md` | 找蓝海 / 跟卖选品 / 趋势执行 / 裂变 |
| `search` | 1688 关键词搜索 | `query [--page-size]` | 耗 1688 搜索配额 | 按词找货 |
| `probe` | CDP 探针抓取单个 1688 商品 | `--url [--timeout]` | 无 | 调试单个商品 |
| `queries` | what-to-sell 蓝海/榜单查询 | `--type all-queries\|ozon-bestsellers\|market-bestsellers [--keyword] [--export]` | 成功后自动上报 worker PG；可 `--export` CSV/JSON | 选品前查蓝海/畅销榜 |
| `category` | 查询 Ozon 类目 | `<关键词> [--lang ZH_HANS\|EN\|RU] [--max N]` | 只读 | 类目确认 / 排查类目匹配 |
| `cleanup` | 磁盘清理 | `[--profile-cache] [--cache] [--temp] [--old-results --days N]` | 删缓存/孤儿文件（登录态保留） | 磁盘占用高 |
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

> 越界对照表见 `references/anti-patterns.md`。核心纪律：只用本文档命令、不自己写代码抓取、
> 提交前等用户明确确认、每次操作前重读 §1。

## 5. 参考文件索引

按需读取，不预先加载：
- `command-reference.md` — 路由决策树 + 各管线完整参数/示例（选管线前、执行前查）
- `error-codes.md` — 错误码表 + 回复模板 + 进度口径（出错/问进度时查）
- `output-schema.md` — 输出字段解析 + 汇报模板（成功汇报时查）
- `env-setup.md` — 凭证/环境/check 排查（首次使用查）
- `trend-selection.md` / `discover-fission.md` — 趋势/裂变细则（对应场景查）
- `anti-patterns.md` — 越界行为对照（每次操作前自查）
- `envelope_example.json` — 信封结构示例；`field_mapping.md` — 字段映射规则

## 6. 常见问题与升级

- 缺依赖 → `pip install -r requirements.txt`；`graph`/`follow` 提示缺模块 → bootstrap 升级
  （`python3.12 bootstrap_update.py` 或重新下载最新包）
